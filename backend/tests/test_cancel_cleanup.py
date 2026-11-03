"""Cancellation cleanup uses real files/SQLite and precise fake native mutations."""
import asyncio
import hashlib

import pytest
from sqlalchemy import select
from test_bot_commands import reply, send
from test_bot_commands import seed as seed_command
from test_pipeline import setup_pipeline
from test_retained_cache import seed

from app.bot.client import CacheCleanupError
from app.core.errors import AppError
from app.db.models import (
    CacheAction,
    CancelCleanup,
    FileReservation,
    Job,
    MediaRequest,
    Notification,
    RetainedCache,
    StoredFile,
)
from app.services.cancel_cleanup import CancelCleanupService


def cancel_ready(state, request_id, job_id):
    with state.db.transaction() as session:
        job = session.get(Job, job_id)
        request = session.get(MediaRequest, request_id)
        job.state, job.phase, request.status = "queued", "queued", "queued"
        for reservation in session.scalars(select(FileReservation).where(FileReservation.job_id == job_id)):
            session.delete(reservation)  # This seed's failed owner is synthetic and already stopped.
    return state.tasks.cancel(request_id)


def due(state, request_id):
    with state.db.transaction() as session:
        session.get(CancelCleanup, request_id).next_run_at = 0


@pytest.mark.asyncio
@pytest.mark.parametrize("partial", [False, True])
async def test_cancel_deletes_exact_cache_and_retains_task_record(client, config, partial):
    state, fake, request_id, _, job_id, cache_id = seed(client, config, partial=partial)
    unrelated = config.bot_api_root / "unrelated-cache"
    unrelated.write_bytes(b"keep")
    detail = cancel_ready(state, request_id, job_id)
    assert detail["cancel_cleanup"]["state"] == "pending"
    assert not detail["can_retry"] and not detail["can_delete"]
    assert not any(detail["items"][0]["cache"][flag] for flag in ("can_clean", "can_save", "can_resume_download", "can_redownload"))
    await state.runtime.cancel_cleanup.run_due()
    detail = state.tasks.detail(request_id)
    assert detail["status"] == "cancelled" and detail["cancel_cleanup"]["state"] == "complete"
    assert not fake.source.exists() and fake.deletes == 1 and unrelated.read_bytes() == b"keep"
    assert detail["can_delete"]
    with state.db.sessions() as session:
        assert session.get(MediaRequest, request_id).deleted_at is None
        assert session.get(RetainedCache, cache_id).state == "deleted"
    assert not state.tasks.cancel(request_id)["can_cancel"]
    await state.runtime.cancel_cleanup.run_due()
    assert fake.deletes == 1


@pytest.mark.asyncio
async def test_lost_cache_reply_keeps_final_and_recovers_without_mutation_replay(client, config, monkeypatch):
    state, fake, request_id, _, job_id, _ = seed(client, config)
    final = config.download_root / "saved.bin"
    final.write_bytes(b"abcdef")
    cancel_ready(state, request_id, job_id)
    with state.db.transaction() as session:
        job = session.get(Job, job_id)
        job.state, job.relative_path = "succeeded", "saved.bin"  # publication won before cancellation saw it
        session.add(StoredFile(bot_id=7, file_unique_id="saved-unique", job_id=job_id, root_id="default",
            relative_path="saved.bin", size_bytes=6, sha256=hashlib.sha256(b"abcdef").hexdigest()))
    fake.lose_reply = True
    await state.runtime.cancel_cleanup.run_due()
    assert fake.deletes == 1 and final.exists()
    detail = state.tasks.detail(request_id)
    assert detail["cancel_cleanup"]["state"] == "pending"
    assert detail["cancel_cleanup"]["entries"][0]["outcome"] == "uncertain"
    assert not detail["can_delete"]
    with pytest.raises(AppError, match="清理"):
        state.tasks.retry(request_id, None)
    with pytest.raises(AppError, match="清理"):
        state.tasks.delete([request_id])
    await state.runtime.retained_cache.recover()
    # The native deletion already removed the source; only read reconciliation runs.
    due(state, request_id)
    await state.runtime.cancel_cleanup.run_due()
    assert fake.deletes == 1
    with state.db.sessions() as session:
        assert not list(session.scalars(select(CacheAction).where(CacheAction.state == "uncertain")))


@pytest.mark.asyncio
async def test_cancel_waits_for_worker_tail_and_survives_coordinator_restart(client, config):
    state, fake, request_id, _, job_id, _ = seed(client, config, partial=True)
    cancel_ready(state, request_id, job_id)
    gate = asyncio.Event()
    task = asyncio.create_task(gate.wait())
    state.runtime.running[job_id] = task
    await state.runtime.cancel_cleanup.run_due()
    assert fake.source.exists() and fake.deletes == 0
    gate.set()
    await task
    state.runtime.cancel_cleanup = CancelCleanupService(state.runtime)
    due(state, request_id)
    await state.runtime.cancel_cleanup.run_due()
    assert not fake.source.exists()
    assert state.tasks.detail(request_id)["cancel_cleanup"]["state"] == "complete"


@pytest.mark.asyncio
async def test_changed_cache_retained_with_reason_and_retry_action(client, config):
    state, fake, request_id, _, job_id, _ = seed(client, config)
    cancel_ready(state, request_id, job_id)
    fake.source.write_bytes(b"replacement")
    await state.runtime.cancel_cleanup.run_due()
    detail = state.tasks.detail(request_id)
    assert detail["cancel_cleanup"]["state"] == "partial"
    assert detail["can_cancel"] and detail["cancel_cleanup"]["can_retry"]
    assert not detail["can_retry"] and not detail["can_delete"]
    assert detail["cancel_cleanup"]["entries"][0]["outcome"] == "retained_unverified"
    assert fake.deletes == 0 and fake.source.read_bytes() == b"replacement"
    fake.source.unlink()
    state.tasks.cancel(request_id)
    await state.runtime.cancel_cleanup.run_due()
    assert state.tasks.detail(request_id)["cancel_cleanup"]["state"] == "complete"


@pytest.mark.asyncio
async def test_bot_all_cancel_cleanup_does_not_touch_unrelated_completed_task(client, config):
    state, ingest = setup_pipeline(client)
    cancelled = seed_command(state, "queued")
    completed = seed_command(state, "succeeded")
    inbox, _ = send(ingest, "/cancel_all")
    assert "尚未确认完成" in reply(state, inbox)
    await state.runtime.cancel_cleanup.run_due()
    assert state.tasks.detail(cancelled)["cancel_cleanup"]["state"] == "complete"
    assert state.tasks.detail(completed)["cancel_cleanup"] is None
    with state.db.sessions() as session:
        assert session.get(MediaRequest, completed).status == "succeeded"
        notice = session.scalar(select(Notification).where(Notification.event_key.startswith(f"cancel-cleanup:{cancelled}")))
        assert "已清理" in notice.payload["text"]


@pytest.mark.asyncio
async def test_saved_deletion_unknown_is_not_replayed_after_same_content_restored(client, config, monkeypatch):
    state, _ = setup_pipeline(client)
    state.storage.get("default").initialize()
    request_id = seed_command(state, "fetching", ["succeeded", "fetching"])
    with state.db.transaction() as session:
        jobs = list(session.scalars(select(Job).where(Job.request_id == request_id)))
        saved = next(job for job in jobs if job.state == "succeeded")
        saved.relative_path = "saved.bin"
        session.add(StoredFile(bot_id=7, file_unique_id="unknown-file", job_id=saved.id, root_id="default",
            relative_path="saved.bin", size_bytes=6, sha256=hashlib.sha256(b"abcdef").hexdigest()))
    final = config.download_root / "saved.bin"
    final.write_bytes(b"abcdef")
    state.tasks.cancel(request_id)
    with state.db.transaction() as session:
        for job in session.scalars(select(Job).where(Job.request_id == request_id, Job.state == "cancelling")):
            job.state = "cancelled"
    calls = []
    def uncertain(storage, entry):
        calls.append(entry["relative_path"])
        final.unlink()
        return "uncertain"
    monkeypatch.setattr("app.services.saved_media.remove_verified", uncertain)
    await state.runtime.cancel_cleanup.run_due()
    assert calls == ["saved.bin"]
    assert state.tasks.detail(request_id)["cancel_cleanup"]["state"] == "partial"
    final.write_bytes(b"abcdef")
    state.tasks.cancel(request_id)
    await state.runtime.cancel_cleanup.run_due()
    assert calls == ["saved.bin"] and final.read_bytes() == b"abcdef"
    detail = state.tasks.detail(request_id)
    assert detail["cancel_cleanup"]["state"] == "partial"
    assert "禁止重复删除" in detail["cancel_cleanup"]["entries"][0]["message"]
    final.unlink()
    state.tasks.cancel(request_id)
    await state.runtime.cancel_cleanup.run_due()
    assert state.tasks.detail(request_id)["cancel_cleanup"]["state"] == "complete"


@pytest.mark.asyncio
async def test_lost_reply_with_settled_retained_source_ends_wait_for_manual_retry(client, config, monkeypatch):
    state, fake, request_id, _, job_id, _ = seed(client, config)
    cancel_ready(state, request_id, job_id)
    async def timed_out(*args, **kwargs):
        fake.deletes += 1
        raise CacheCleanupError("cache_timeout", 0)
    original = fake.delete_file_cache
    monkeypatch.setattr(fake, "delete_file_cache", timed_out)
    await state.runtime.cancel_cleanup.run_due()
    assert state.tasks.detail(request_id)["cancel_cleanup"]["state"] == "pending"
    await state.runtime.retained_cache.recover()
    due(state, request_id)
    await state.runtime.cancel_cleanup.run_due()
    detail = state.tasks.detail(request_id)
    assert detail["cancel_cleanup"]["state"] == "partial"
    assert fake.source.exists() and fake.deletes == 1
    monkeypatch.setattr(fake, "delete_file_cache", original)
    state.tasks.cancel(request_id)
    await state.runtime.cancel_cleanup.run_due()
    assert state.tasks.detail(request_id)["cancel_cleanup"]["state"] == "complete"
    assert not fake.source.exists() and fake.deletes == 2
