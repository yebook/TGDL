"""Race and restart regressions for manual cache operations."""
import asyncio
import errno
import os
import time
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import delete, event, select
from test_pipeline import confirm_all, message, setup_pipeline
from test_retained_cache import TOKEN, seed

from app.core.errors import AppError
from app.core.security import digest
from app.db.models import (
    CacheAction,
    CacheCleanup,
    FileReservation,
    Job,
    MediaItem,
    MediaRequest,
    Notification,
    PublishJournal,
    RetainedCache,
    StoredFile,
)
from app.schemas.contracts import SettingsPatch
from app.storage.space import SpaceAssessment, SpaceVolume
from app.workers import download


def test_repeated_native_observation_reuses_physical_cache_row(client, config):
    state, _, request_id, _, job_id, cache_id = seed(client, config, partial=True)
    first = config.bot_api_root / "owned-cache"
    second = config.bot_api_root / "rotated-cache"
    second.write_bytes(first.read_bytes())
    state.runtime.retained_cache.capture(job_id, str(second), completeness="partial",
        generation="generation1", acquisition_id="acquisition-1", native_identity="a" * 64,
        resumable=True)
    state.runtime.retained_cache.capture(job_id, str(first), completeness="partial",
        generation="generation1", acquisition_id="acquisition-1", native_identity="a" * 64,
        resumable=True)
    with state.db.sessions() as session:
        rows = list(session.scalars(select(RetainedCache).where(RetainedCache.job_id == job_id)))
        assert len(rows) == 1
        assert rows[0].id == cache_id
        assert rows[0].native_identity == "a" * 64
        assert state.tasks.detail(request_id)["items"][0]["cache"]["path"]


def test_repeated_unknown_observation_is_one_row_per_attempt(client, config):
    state, _, _, _, job_id, cache_id = seed(client, config, partial=True)
    with state.db.transaction() as session:
        job = session.get(Job, job_id)
        assert job
        job.cache_id = None
        session.delete(session.get(RetainedCache, cache_id))
        attempt = job.attempt_sequence
    state.runtime.retained_cache.capture_unknown(job_id, "generation-unknown", attempt)
    state.runtime.retained_cache.capture_unknown(job_id, "generation-unknown", attempt)
    with state.db.sessions() as session:
        rows = list(session.scalars(select(RetainedCache).where(
            RetainedCache.job_id == job_id, RetainedCache.attempt == attempt)))
        assert len(rows) == 1


@pytest.mark.parametrize("automatic", [False, True])
async def test_no_space_admission_terminal_no_timer_polling(client, config, monkeypatch, automatic):
    state, ingest = setup_pipeline(client)
    if automatic:
        patch = SettingsPatch(revision=state.settings.read()[0], auto_download=True)
        state.settings.save(patch, state.settings.candidate(patch))
    ingest.message(7, message(), "owned")
    if automatic:
        state.tasks.freeze_due()
        with state.db.sessions() as session:
            request_id = session.scalar(select(MediaRequest.id))
    else:
        request_id = confirm_all(state)
    assessment = SpaceAssessment("before_download", (SpaceVolume("target+cache", 5, 12, 1024, 0, 1031),))
    monkeypatch.setattr("app.workers.download.assess_space", lambda *args: assessment)
    assert state.runtime.worker.claim() is None
    with state.db.sessions() as session:
        job = session.scalar(select(Job))
        assert job.state == "failed" and job.error_code == "NO_SPACE" and job.finished_at
        assert session.scalar(select(FileReservation)) is None
        assert "缓存与保存盘" in session.scalar(select(Notification).where(Notification.event_key.like("result:%"))).payload["text"]
    monkeypatch.setattr("app.workers.download.assess_space", lambda *args: SpaceAssessment("before_download", ()))
    assert state.runtime.worker.claim() is None
    assert state.tasks.detail(request_id)["status"] == "failed"


async def test_duplicate_resume_requests_coalesce_after_bounded_probe(client, config):
    state, fake, request_id, item_id, _, cache_id = seed(client, config, partial=True)
    original = fake.inspect_file_cache
    arrived = 0
    ready = asyncio.Event()

    async def inspect(*args, **kwargs):
        nonlocal arrived
        arrived += 1
        if arrived == 2:
            ready.set()
        await ready.wait()
        return await original(*args, **kwargs)

    fake.inspect_file_cache = inspect
    operation = uuid4().hex
    replies = await asyncio.gather(*(state.runtime.retained_cache.submit(request_id, item_id, "download",
        operation, cache_id=cache_id) for _ in range(2)))
    assert all(reply["items"][0]["cache"]["operation"]["id"] == operation for reply in replies)
    with state.db.sessions() as session:
        assert len(list(session.scalars(select(CacheAction)))) == 1


async def test_late_probe_failure_cannot_overwrite_new_operation(client, config):
    state, fake, request_id, item_id, job_id, cache_id = seed(client, config, partial=True)
    entered, finish = asyncio.Event(), asyncio.Event()

    async def late(*args, **kwargs):
        entered.set()
        await finish.wait()
        raise AppError("CACHE_INSPECTION_UNAVAILABLE", "unavailable")

    fake.inspect_file_cache = late
    operation = uuid4().hex
    old = asyncio.create_task(state.runtime.retained_cache.submit(request_id, item_id, "download", operation, cache_id=cache_id))
    await entered.wait()
    await state.runtime.retained_cache.submit(request_id, item_id, "redownload", uuid4().hex, cache_id=cache_id, confirmed=True)
    assert state.runtime.worker.claim() == job_id
    finish.set()
    with pytest.raises(AppError, match="状态已改变"):
        await old
    with state.db.sessions() as session:
        assert session.get(Job, job_id).state == "fetching"
        assert session.get(RetainedCache, cache_id).resumable
        assert session.get(CacheAction, operation) is None


async def test_orphan_complete_native_absent_cannot_confirm_old_path_deleted(client, config):
    state, fake, request_id, _, job_id, cache_id = seed(client, config)
    operation = uuid4().hex
    with state.db.transaction() as session:
        session.get(RetainedCache, cache_id).native_identity = None
        session.add(CacheAction(id=operation, job_id=job_id, cache_id=cache_id, kind="clean", state="uncertain",
            attempt=1, generation="old-child", credential=digest(TOKEN)))

    async def forgot(*args, **kwargs):
        return {"completeness": "absent", "settled": True}

    fake.inspect_file_cache = forgot
    await state.runtime.retained_cache.recover()
    with state.db.sessions() as session:
        action = session.get(CacheAction, operation)
        assert action.state == "failed" and action.error_code == "CACHE_CLEAN_RETRY_REQUIRED"
        assert session.get(RetainedCache, cache_id).state == "retained"
    assert fake.source.exists() and fake.deletes == 0


async def test_queued_resume_connection_unavailable_ends_without_getfile(client, config):
    state, fake, request_id, item_id, job_id, cache_id = seed(client, config, partial=True)
    await state.runtime.retained_cache.submit(request_id, item_id, "download", uuid4().hex, cache_id=cache_id)
    state.runtime.bot_status["state"] = "offline"
    with state.db.transaction() as session:
        session.scalar(select(CacheAction)).created_at = time.time() - 11
    await state.runtime.retained_cache.recover()
    with state.db.sessions() as session:
        assert session.get(Job, job_id).state == "failed"
        assert session.scalar(select(CacheAction)).state == "failed"
    assert fake.calls == 0


def test_verified_child_replacement_releases_space_stop_on_full_target(client, config, monkeypatch):
    state, _, request_id, _, job_id, _ = seed(client, config, partial=True)
    with state.db.transaction() as session:
        job = session.get(Job, job_id)
        job.state, job.phase = "recovery_blocked", "fetching"
        job.storage_failure = {"phase": "downloading", "sufficient": False, "volumes": []}
    storage = state.storage.get("default")
    original = storage.check

    def full_disk(required=0, *, check_space=True):
        if check_space:
            raise AppError("NO_SPACE", "synthetic full disk")
        return original(required, check_space=False)

    monkeypatch.setattr(storage, "check", full_disk)
    state.runtime.worker.recover(True)
    assert state.tasks.detail(request_id)["status"] == "failed"
    assert state.runtime.counts()[0] == 0


def test_cancel_before_stop_terminal_preserves_space_context(client, config):
    state, _, request_id, _, job_id, _ = seed(client, config, partial=True)
    with state.db.transaction() as session:
        job = session.get(Job, job_id)
        job.state, job.phase = "fetching", "fetching"
        job.storage_failure = {"phase": "downloading", "sufficient": False, "volumes": []}
    state.tasks.cancel(request_id)
    state.runtime.worker.failed(job_id, "NO_SPACE", "缓存盘空间不足，已停止", False)
    with state.db.sessions() as session:
        job = session.get(Job, job_id)
        assert job.state == "cancelled" and job.error_code == "NO_SPACE" and job.storage_failure


@pytest.mark.parametrize("outcome", ["missing_root", "already_exists"])
async def test_manual_save_claim_early_terminal_finishes_operation(client, config, monkeypatch, outcome):
    state, fake, request_id, item_id, job_id, cache_id = seed(client, config)
    await state.runtime.retained_cache.submit(request_id, item_id, "save", uuid4().hex, cache_id=cache_id)
    if outcome == "missing_root":
        def unavailable(*args):
            raise AppError("ROOT_UNAVAILABLE", "owned unavailable root")
        monkeypatch.setattr(state.storage, "get", unavailable)
    else:
        (config.download_root / "existing").write_bytes(b"abcdef")
        with state.db.transaction() as session:
            item = session.get(MediaItem, item_id)
            session.add(StoredFile(bot_id=7, file_unique_id=item.file_unique_id, job_id=job_id,
                root_id="default", relative_path="existing", size_bytes=6, sha256="a" * 64))
    assert state.runtime.worker.claim() is None
    with state.db.sessions() as session:
        action = session.scalar(select(CacheAction))
        assert action.state == ("failed" if outcome == "missing_root" else "succeeded")
        assert session.get(Job, job_id).state == ("failed" if outcome == "missing_root" else "already_exists")
    assert fake.calls == 0


async def test_detail_refresh_restores_transient_resume_capability_without_getfile(client, config):
    state, fake, request_id, item_id, _, cache_id = seed(client, config, partial=True)
    original = fake.inspect_file_cache
    async def offline(*args, **kwargs):
        raise AppError("CACHE_INSPECTION_UNAVAILABLE", "owned offline client")
    fake.inspect_file_cache = offline
    with pytest.raises(AppError):
        await state.runtime.retained_cache.submit(request_id, item_id, "download", uuid4().hex, cache_id=cache_id)
    assert not state.tasks.detail(request_id)["items"][0]["cache"]["can_resume_download"]
    fake.inspect_file_cache = original
    await state.runtime.retained_cache.refresh(request_id)
    assert state.tasks.detail(request_id)["items"][0]["cache"]["can_resume_download"]
    assert fake.calls == 0


async def test_unknown_cache_visible_without_erasing_known_locator(client, config):
    state, fake, request_id, _, job_id, cache_id = seed(client, config, partial=True)
    async def unknown(*args, **kwargs):
        return {"completeness": "unknown", "settled": False}
    fake.inspect_file_cache = unknown
    await state.runtime.retained_cache.capture_native(fake, job_id, "generation1")
    assert state.tasks.detail(request_id)["items"][0]["cache"]["path"]
    with state.db.transaction() as session:
        session.get(Job, job_id).cache_id = None
        session.flush()
        session.execute(delete(RetainedCache).where(RetainedCache.id == cache_id))
    await state.runtime.retained_cache.capture_native(fake, job_id, "generation1")
    cache = state.tasks.detail(request_id)["items"][0]["cache"]
    assert cache["state"] == "uncertain" and cache["completeness"] == "unknown"
    assert cache["path"] is None and not cache["can_clean"] and cache["can_redownload"]
    assert cache["recovery_guidance"]


async def test_idle_cache_recovery_never_reserves_write(client):
    state = client.app.state
    statements = []
    event.listen(state.db.engine, "before_cursor_execute", lambda _c, _u, sql, _p, _x, _m: statements.append(sql))
    for _ in range(3):
        await state.runtime.retained_cache.recover()
    assert not any(sql.startswith("BEGIN") for sql in statements)


@pytest.mark.parametrize("linked", [False, True])
async def test_no_space_at_publication_resolves_immediately(client, config, monkeypatch, linked):
    state, fake, request_id, item_id, job_id, cache_id = seed(client, config)
    await state.runtime.retained_cache.submit(request_id, item_id, "save", uuid4().hex, cache_id=cache_id)
    assert state.runtime.worker.claim() == job_id
    def exhausted(temporary, final):
        if linked:
            os.link(temporary, final)
        raise OSError(errno.ENOSPC, "owned publication no space")
    monkeypatch.setattr("app.workers.download.publish_no_replace", exhausted)
    await state.runtime.worker.run(fake, job_id)
    with state.db.sessions() as session:
        job = session.get(Job, job_id)
        action = session.scalar(select(CacheAction))
        assert job.state == action.state == ("succeeded" if linked else "failed")
        assert linked or job.error_code == "NO_SPACE"
    assert fake.source.exists() and not fake.calls
    assert state.runtime.counts()[0] == 0
    assert not list(config.download_root.rglob(".part.*"))


async def test_manual_save_preserves_changed_mount_error(client, config, monkeypatch):
    state, _, request_id, item_id, job_id, cache_id = seed(client, config)
    assessment = SpaceAssessment("before_save", (SpaceVolume("target", None, 6, 1024, 0, None, "STORAGE_CHANGED"),))
    monkeypatch.setattr("app.services.retained_cache.assess_space", lambda *args: assessment)
    await state.runtime.retained_cache.submit(request_id, item_id, "save", uuid4().hex, cache_id=cache_id)
    with state.db.sessions() as session:
        assert session.get(Job, job_id).error_code == "STORAGE_CHANGED"
        assert session.scalar(select(CacheAction)).error_code == "STORAGE_CHANGED"


async def test_protected_automatic_cleanup_does_not_starve_other_cache(client, config, monkeypatch):
    from test_cache_cleanup import published, ready

    state, _, _, other_job, other_source = await published(client)
    # A second retained job owns another source. Its queued housekeeping row is
    # older, but must not stop the independent successful source being cleaned.
    with state.db.transaction() as session:
        existing = session.get(CacheCleanup, other_job)
        existing.next_run_at = time.time() - 1
        job = session.get(Job, other_job)
        item = MediaItem(request_id=job.request_id, bot_id=7, chat_id=42, message_id=1000,
                         file_id="protected-file", media_type="document")
        session.add(item)
        session.flush()
        session.add(Job(id="f" * 32, request_id=job.request_id, item_id=item.id,
            root_id="default", relative_path="protected", state="failed", phase="failed"))
        session.flush()
        protected = config.bot_api_root / "protected"
        protected.write_bytes(b"retained")
        session.add(RetainedCache(job_id="f" * 32, bot_id=7, file_id="protected-file", attempt=1,
            path_digest=digest(str(protected)), completeness="complete", state="retained"))
        session.add(CacheCleanup(job_id="f" * 32, bot_id=7, file_id="protected-file", source_path_encrypted=state.settings.secrets.encrypt(str(protected)),
            root_id="default", relative_path="protected", size_bytes=8, sha256="a" * 64, next_run_at=time.time() - 2))
    telegram = ready(state.runtime, monkeypatch)
    assert await state.runtime.cleanup_cache()
    assert telegram.delete_file_cache.await_args.args[0] == "file-1"
    with state.db.sessions() as session:
        assert session.get(CacheCleanup, other_job) is None
        assert session.get(CacheCleanup, "f" * 32).attempts == 0
    assert protected.exists() and other_source.exists()


async def test_target_part_cleanup_failure_displays_path_and_retry_preserves_journal(client, config, monkeypatch):
    state, fake, request_id, item_id, job_id, cache_id = seed(client, config)
    original_unlink = Path.unlink
    original_copy = download.copy_stream
    part = None

    def exhausted(source, temporary, *args):
        nonlocal part
        part = temporary
        temporary.write_bytes(b"owned partial target")
        raise OSError(errno.ENOSPC, "owned no space")

    def deny_cleanup(path, *args, **kwargs):
        if path == part:
            raise PermissionError(errno.EACCES, "owned target permission")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr("app.workers.download.copy_stream", exhausted)
    monkeypatch.setattr(Path, "unlink", deny_cleanup)
    for attempt in range(2):
        await state.runtime.retained_cache.submit(request_id, item_id, "save", uuid4().hex, cache_id=cache_id)
        assert state.runtime.worker.claim() == job_id
        await state.runtime.worker.run(fake, job_id)
        with state.db.sessions() as session:
            journal = session.get(PublishJournal, job_id)
            assert journal.stage == "cleanup_failed"
            assert journal.temporary_path in state.tasks.detail(request_id)["items"][0]["error_message"]
            assert session.get(Job, job_id).error_code == ("NO_SPACE" if attempt == 0 else "CACHE_TEMP_CLEANUP_FAILED")
        assert part.read_bytes() == b"owned partial target"
    monkeypatch.setattr(Path, "unlink", original_unlink)
    monkeypatch.setattr("app.workers.download.copy_stream", original_copy)
    await state.runtime.retained_cache.submit(request_id, item_id, "save", uuid4().hex, cache_id=cache_id)
    assert state.runtime.worker.claim() == job_id
    await state.runtime.worker.run(fake, job_id)
    assert state.tasks.detail(request_id)["status"] == "succeeded"
    assert not part.exists() and fake.source.exists() and fake.calls == 0


def test_offline_complete_cache_explains_clean_block_while_save_available(client, config):
    state, _, request_id, _, _, _ = seed(client, config)
    state.runtime.bot_status["state"] = "error"
    cache = state.tasks.detail(request_id)["items"][0]["cache"]
    assert cache["can_save"] and not cache["can_clean"]
    assert "Bot" in cache["unavailable_reason"]


@pytest.mark.parametrize("explicit", [False, True])
def test_group_retry_skips_partial_cache_unless_explicitly_selected(client, config, explicit):
    state, fake, request_id, partial_item_id, partial_job_id, _ = seed(client, config, partial=True)
    with state.db.transaction() as session:
        item = MediaItem(request_id=request_id, bot_id=7, chat_id=42, message_id=1000,
                         file_id="other-file", media_type="document", size_bytes=6)
        session.add(item)
        session.flush()
        other = Job(request_id=request_id, item_id=item.id, root_id="default", relative_path="other-file",
                    state="failed", phase="failed")
        session.add(other)
        session.flush()
        other_item_id, other_job_id = item.id, other.id
    assert state.tasks.detail(request_id)["can_retry"]
    if explicit:
        with pytest.raises(AppError) as rejected:
            state.tasks.retry(request_id, [other_item_id, partial_item_id])
        assert rejected.value.code == "CACHE_CHOICE_REQUIRED"
    else:
        state.tasks.retry(request_id, None)
    with state.db.sessions() as session:
        assert session.get(Job, partial_job_id).state == "failed"
        assert session.get(Job, other_job_id).state == ("failed" if explicit else "queued")
    assert fake.calls == 0 and fake.source.read_bytes() == b"abc"
