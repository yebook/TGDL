"""Real SQLite/filesystem integration; every native side effect is an owned fake."""
import errno
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import select
from test_pipeline import confirm_all, message, setup_pipeline

from app.bot.client import CacheCleanupError
from app.core.errors import AppError
from app.core.security import digest
from app.db.models import CacheAction, CacheCleanup, Job, Notification, RetainedCache
from app.schemas.contracts import SettingsPatch
from app.services.cache_maintenance import maintenance_blocked
from app.services.retained_cache import RetainedCacheService

TOKEN = "123456:" + "a" * 35
IDENTITY = "a" * 64


class Native:
    mode = "local"
    bot = SimpleNamespace(token=TOKEN)

    def __init__(self, source, *, partial=False):
        self.source, self.partial = source, partial
        self.calls, self.deletes, self.prepares = 0, 0, []
        self.owner, self.state = "1" * 64, "cancelled"
        self.lose_reply = False

    async def inspect_file_cache(self, file_id, generation, **kwargs):
        exists = self.source.exists()
        return {"generation": generation, "completeness": "partial" if self.partial else "complete" if exists else "absent",
                "path": str(self.source) if exists else "", "size": self.source.stat().st_size if exists else 0,
                "allocated_bytes": 4096 if exists else 0, "identity": IDENTITY if exists else "",
                "resumable": self.partial and exists, "reason": "", "settled": True}

    async def file_acquisition(self, generation, *, file_id=None, acquisition_id=None, cancel=False,
                               expected_cache_identity=None):
        if file_id:
            self.prepares.append(expected_cache_identity)
            self.state = "prepared"
        elif cancel:
            self.state = "cancelled"
        return {"generation": generation, "acquisition_id": self.owner, "state": self.state,
                "settled": self.state in {"completed", "cancelled", "failed"}}

    async def get_file(self, file_id, **kwargs):
        self.calls += 1
        self.source.write_bytes(b"abcdef")
        self.partial, self.state = False, "completed"
        return str(self.source)

    async def delete_file_cache(self, file_id, expected_path, expected_size, generation, *, expected_sha256):
        assert expected_path == str(self.source)
        self.deletes += 1
        self.source.unlink()
        if self.lose_reply:
            raise CacheCleanupError("cache_timeout", 0)
        return "deleted"

    async def delete_partial_file_cache(self, file_id, expected_path, expected_size, generation, *, expected_identity):
        assert expected_identity == IDENTITY
        return await self.delete_file_cache(file_id, expected_path, expected_size, generation, expected_sha256="fake")


def seed(client, config, *, partial=False, cancelled=False):
    state, ingest = setup_pipeline(client)
    revision = state.settings.read()[0]
    patch = SettingsPatch(revision=revision, bot_token=TOKEN, allowed_user_ids=[42], bot_api_mode="local")
    state.settings.save(patch, state.settings.candidate(patch))
    source = config.bot_api_root / "owned-cache"
    source.write_bytes(b"abc" if partial else b"abcdef")
    ingest.message(7, message(), "owned")
    request_id = confirm_all(state)
    job_id = state.runtime.worker.claim()
    assert job_id
    fake = Native(source, partial=partial)
    runtime = state.runtime
    runtime.client, runtime.bot_id, runtime.client_generation = fake, 7, "generation1"
    runtime.client_signature = state.settings.client_signature(state.settings.read()[1])
    runtime.bot_status["state"] = "online"
    runtime.acquisition_generation = lambda: "generation1"
    runtime.worker.acquisition_generation = lambda: "generation1"
    with state.db.transaction() as session:
        job = session.get(Job, job_id)
        job.state, job.phase = ("cancelled", "cancelled") if cancelled else ("failed", "failed")
        job.cancel_requested = cancelled
        item_id = job.item_id
    cache_id = runtime.retained_cache.capture(job_id, str(source), completeness="partial" if partial else "complete",
        generation="generation1", credential=digest(TOKEN), native_identity=IDENTITY, resumable=partial)
    return state, fake, request_id, item_id, job_id, cache_id


@pytest.mark.asyncio
async def test_complete_continue_save_uses_no_network_and_preserves_frozen_path(client, config):
    state, fake, request_id, item_id, job_id, cache_id = seed(client, config, cancelled=True)
    before = state.tasks.detail(request_id)["items"][0]["relative_path"]
    operation = uuid4().hex
    detail = await state.runtime.retained_cache.submit(request_id, item_id, "save", operation, cache_id=cache_id)
    assert detail["items"][0]["cache"]["operation"]["state"] == "queued"
    assert state.runtime.worker.claim() == job_id
    await state.runtime.worker.run(fake, job_id)
    detail = state.tasks.detail(request_id)
    assert detail["status"] == "succeeded"
    assert detail["items"][0]["relative_path"] == before
    assert fake.calls == 0
    assert (config.download_root / before).read_bytes() == b"abcdef"
    assert detail["items"][0]["cache"]["operation"]["state"] == "succeeded"


@pytest.mark.asyncio
async def test_complete_capture_precedes_space_check_and_cancel(client, config, monkeypatch):
    state, ingest = setup_pipeline(client)
    source = config.bot_api_root / "source"
    source.write_bytes(b"abcdef")
    ingest.message(7, message(), "owned")
    request_id = confirm_all(state)
    worker = state.runtime.worker
    job_id = worker.claim()
    calls = []

    def no_space(*args):
        calls.append("check")
        raise AppError("NO_SPACE", "目标盘空间不足", details={"phase": "before_save", "volumes": []})

    monkeypatch.setattr(worker, "check_space", no_space)
    await worker.run(Native(source), job_id)
    item = state.tasks.detail(request_id)["items"][0]
    assert item["status"] == "failed" and item["error_code"] == "NO_SPACE"
    assert item["cache"]["completeness"] == "complete" and source.exists()
    assert calls == ["check"]
    assert not list(config.download_root.rglob(".part.*"))
    with state.db.sessions() as session:
        assert "完整缓存已保留" in session.scalar(select(Notification).where(Notification.event_key.like("result:%"))).payload["text"]


@pytest.mark.asyncio
async def test_cancel_after_full_get_file_keeps_complete_source_without_copy(client, config):
    state, ingest = setup_pipeline(client)
    source = config.bot_api_root / "source"
    source.write_bytes(b"abcdef")
    ingest.message(7, message(), "owned")
    request_id = confirm_all(state)
    worker = state.runtime.worker
    job_id = worker.claim()

    class CancelOnComplete:
        async def get_file(self, file_id):
            state.tasks.cancel(request_id)
            return str(source)

    await worker.run(CancelOnComplete(), job_id)
    item = state.tasks.detail(request_id)["items"][0]
    assert item["status"] == "cancelled" and item["cache"]["completeness"] == "complete"
    assert not list(config.download_root.rglob(".part.*"))


@pytest.mark.asyncio
async def test_queued_save_cancel_finishes_operation_and_no_io(client, config):
    state, fake, request_id, item_id, job_id, cache_id = seed(client, config)
    operation = uuid4().hex
    await state.runtime.retained_cache.submit(request_id, item_id, "save", operation, cache_id=cache_id)
    detail = state.tasks.cancel(request_id)
    assert detail["items"][0]["cache"]["operation"]["state"] == "cancelled"
    assert state.runtime.worker.claim() is None
    assert fake.calls == 0 and fake.source.exists()


@pytest.mark.asyncio
async def test_manual_clean_is_precise_idempotent_and_keeps_final_media(client, config):
    state, fake, request_id, item_id, _, cache_id = seed(client, config)
    final, unrelated = config.download_root / "real-final", config.bot_api_root / "unrelated"
    final.write_bytes(b"actual media")
    unrelated.write_bytes(b"other cache")
    operation = uuid4().hex
    for _ in range(2):
        detail = await state.runtime.retained_cache.submit(request_id, item_id, "clean", operation,
            cache_id=cache_id, confirmed=True)
    assert detail["items"][0]["cache"]["state"] == "deleted" and fake.deletes == 1
    assert final.read_bytes() == b"actual media" and unrelated.read_bytes() == b"other cache"


@pytest.mark.asyncio
async def test_partial_download_new_attempt_native_proof_and_no_implicit_save(client, config):
    state, fake, request_id, item_id, job_id, cache_id = seed(client, config, partial=True, cancelled=True)
    detail = state.tasks.detail(request_id)
    assert detail["items"][0]["cache"]["can_resume_download"]
    assert not detail["items"][0]["cache"]["can_save"]
    with pytest.raises(AppError, match="不能继续保存"):
        await state.runtime.retained_cache.submit(request_id, item_id, "save", uuid4().hex, cache_id=cache_id)
    await state.runtime.retained_cache.submit(request_id, item_id, "download", uuid4().hex, cache_id=cache_id)
    assert state.runtime.worker.claim() == job_id
    await state.runtime.worker.run(fake, job_id)
    assert state.tasks.detail(request_id)["status"] == "succeeded"
    assert fake.prepares == [IDENTITY] and fake.calls == 1
    with state.db.sessions() as session:
        assert session.get(Job, job_id).attempt_sequence == 2


@pytest.mark.asyncio
async def test_restart_disables_resume_explicit_redownload_keeps_old_cache(client, config):
    state, fake, request_id, item_id, job_id, cache_id = seed(client, config, partial=True)
    state.runtime.acquisition_generation = lambda: "generation2"
    state.runtime.worker.acquisition_generation = lambda: "generation2"
    cache = state.tasks.detail(request_id)["items"][0]["cache"]
    assert not cache["can_resume_download"] and "重启" in cache["resume_unavailable_reason"]
    with pytest.raises(AppError, match="已重启"):
        await state.runtime.retained_cache.submit(request_id, item_id, "download", uuid4().hex, cache_id=cache_id)
    assert state.runtime.worker.claim() is None and fake.calls == 0
    with pytest.raises(AppError, match="确认"):
        await state.runtime.retained_cache.submit(request_id, item_id, "redownload", uuid4().hex, cache_id=cache_id)
    await state.runtime.retained_cache.submit(request_id, item_id, "redownload", uuid4().hex,
        cache_id=cache_id, confirmed=True)
    assert fake.source.read_bytes() == b"abc" and fake.deletes == 0
    assert state.runtime.worker.claim() == job_id


@pytest.mark.asyncio
async def test_probe_failure_is_bounded_and_no_retry_or_network(client, config):
    state, fake, request_id, item_id, job_id, cache_id = seed(client, config, partial=True)

    async def unsupported(*args, **kwargs):
        raise AppError("CACHE_INSPECTION_UNSUPPORTED", "unsupported")

    fake.inspect_file_cache = unsupported
    with pytest.raises(AppError, match="无法确认"):
        await state.runtime.retained_cache.submit(request_id, item_id, "download", uuid4().hex, cache_id=cache_id)
    with state.db.sessions() as session:
        assert session.get(Job, job_id).state == "failed"
        assert session.scalar(select(CacheAction)).state == "failed"
    assert fake.calls == 0 and state.runtime.worker.claim() is None


@pytest.mark.asyncio
async def test_lost_clean_reply_retains_fence_reconciles_without_replay(client, config):
    state, fake, request_id, item_id, _, cache_id = seed(client, config)
    fake.lose_reply = True
    operation = uuid4().hex
    detail = await state.runtime.retained_cache.submit(request_id, item_id, "clean", operation,
        cache_id=cache_id, confirmed=True)
    assert detail["items"][0]["cache"]["operation"]["state"] == "uncertain"
    with state.db.sessions() as session:
        assert maintenance_blocked(session)
    with pytest.raises(AppError):
        state.tasks.retry(request_id, None)
    with pytest.raises(AppError):
        state.tasks.delete([request_id])
    await state.runtime.retained_cache.recover()
    assert fake.deletes == 1
    assert state.tasks.detail(request_id)["items"][0]["cache"]["operation"]["state"] == "succeeded"
    with state.db.sessions() as session:
        assert not maintenance_blocked(session)


@pytest.mark.asyncio
async def test_changed_same_size_cache_rejected_without_download(client, config):
    state, fake, request_id, item_id, _, cache_id = seed(client, config)
    fake.source.write_bytes(b"xxxxxx")
    with pytest.raises(AppError, match="已改变"):
        await state.runtime.retained_cache.submit(request_id, item_id, "save", uuid4().hex, cache_id=cache_id)
    assert fake.calls == 0 and fake.deletes == 0


@pytest.mark.asyncio
async def test_enospc_during_copy_preserves_complete_source_and_no_final(client, config, monkeypatch):
    state, fake, request_id, item_id, job_id, cache_id = seed(client, config)
    await state.runtime.retained_cache.submit(request_id, item_id, "save", uuid4().hex, cache_id=cache_id)
    assert state.runtime.worker.claim() == job_id

    def exhausted(*args):
        raise OSError(errno.ENOSPC, "synthetic owned filesystem exhausted")

    monkeypatch.setattr("app.workers.download.copy_stream", exhausted)
    await state.runtime.worker.run(fake, job_id)
    detail = state.tasks.detail(request_id)
    assert detail["items"][0]["error_code"] == "NO_SPACE"
    assert detail["items"][0]["cache"]["can_save"] and fake.source.exists()
    assert not list(config.download_root.rglob(".part.*"))
    assert fake.calls == 0


def test_cache_paths_encrypted_and_http_rejects_browser_paths(authenticated, config):
    state, _, request_id, item_id, _, cache_id = seed(authenticated, config)
    with state.db.sessions() as session:
        row = session.get(RetainedCache, cache_id)
        assert str(config.bot_api_root) not in row.path_encrypted
    response = authenticated.post(f"/api/v1/tasks/{request_id}/items/{item_id}/cache/clean",
        json={"operation_id": uuid4().hex, "confirmed": True, "expected_path": "arbitrary"})
    assert response.status_code == 422


def test_shared_retained_source_blocks_auto_cleanup_and_manual_clean(client, config):
    state, fake, request_id, item_id, job_id, cache_id = seed(client, config)
    with state.db.transaction() as session:
        session.add(RetainedCache(job_id=job_id, bot_id=7, file_id="file-1", attempt=1,
            path_digest=digest(str(fake.source)), completeness="complete", state="retained"))
    cache = state.tasks.detail(request_id)["items"][0]["caches"]
    assert not next(row for row in cache if row["id"] == cache_id)["can_clean"]
    cleanup = CacheCleanup(job_id="other", bot_id=7, file_id="file-1", source_path_encrypted=state.settings.secrets.encrypt(str(fake.source)),
        root_id="default", relative_path="final", size_bytes=6, sha256="a" * 64)
    assert not state.runtime.retained_cache.automatic_allowed(cleanup)


def test_restarting_service_does_not_replay_mutation(client, config):
    state, _, _, _, job_id, cache_id = seed(client, config)
    with state.db.transaction() as session:
        session.add(CacheAction(id=uuid4().hex, job_id=job_id, cache_id=cache_id, kind="clean",
            state="running", attempt=1, generation="generation1", credential=digest(TOKEN)))
    RetainedCacheService(state.runtime)
    with state.db.sessions() as session:
        assert session.scalar(select(CacheAction)).state == "uncertain"
