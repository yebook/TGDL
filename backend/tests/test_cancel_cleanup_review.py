"""Independent cancellation cleanup regressions with isolated real media bytes."""
import hashlib
from uuid import uuid4

import pytest
from sqlalchemy import select
from test_retained_cache import seed

from app.core.errors import AppError
from app.core.security import digest
from app.db.models import (
    CacheAction,
    CacheCleanup,
    CancelCleanup,
    FileDeletionOperation,
    FileReservation,
    Job,
    MediaItem,
    MediaRequest,
    RetainedCache,
    StoredFile,
)
from app.storage.files import publish_no_replace


def group(state, paths=("review/a.bin", "review/b.bin"), *, active="queued"):
    storage = state.storage.get("default")
    storage.initialize()
    with state.db.transaction() as session:
        request = MediaRequest(bot_id=7, chat_id=42, user_id=42, kind="group", status=active, collect_until=0)
        session.add(request)
        session.flush()
        for index, path in enumerate([*paths, "review/unfinished.bin"]):
            item = MediaItem(request_id=request.id, bot_id=7, chat_id=42, message_id=int(uuid4().hex[:12], 16),
                             file_id=uuid4().hex, media_type="document", size_bytes=6)
            session.add(item)
            session.flush()
            done = index < len(paths)
            job = Job(request_id=request.id, item_id=item.id, state="succeeded" if done else active,
                      relative_path=path, completed_bytes=6 if done else 0, acquisition_mode="cloud")
            session.add(job)
            session.flush()
            if done:
                final = storage.root / path
                final.parent.mkdir(parents=True, exist_ok=True)
                final.write_bytes(b"abcdef")
                session.add(StoredFile(bot_id=7, file_unique_id=uuid4().hex, job_id=job.id,
                    root_id="default", relative_path=path, size_bytes=6, sha256=hashlib.sha256(b"abcdef").hexdigest()))
        return request.id, [storage.root / path for path in paths]


def remover(calls, failing):
    """Synthetic platform boundary; coordinator still uses actual files and DB."""
    def unlink(storage, entry):
        path = storage.root / entry["relative_path"]
        calls.append(entry["relative_path"])
        if entry["relative_path"] in failing:
            return "failed"
        if not path.exists():
            return "missing"
        assert len(path.read_bytes()) == entry["size_bytes"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == entry["sha256"]
        path.unlink()
        return "deleted"
    return unlink


async def test_partial_retry_does_not_remove_restored_successful_path(authenticated, monkeypatch):
    state = authenticated.app.state
    request_id, paths = group(state)
    calls, failing = [], {"review/b.bin"}
    monkeypatch.setattr("app.services.saved_media.remove_verified", remover(calls, failing))
    state.tasks.cancel(request_id)
    assert all(path.read_bytes() == b"abcdef" for path in paths)
    await state.runtime.cancel_cleanup.run_due()
    assert not paths[0].exists() and paths[1].exists()
    assert state.tasks.detail(request_id)["cancel_cleanup"]["state"] == "partial"
    paths[0].write_bytes(b"abcdef")  # Even identical restored bytes no longer belong to this deletion intent.
    failing.clear()
    state.tasks.cancel(request_id)
    await state.runtime.cancel_cleanup.run_due()
    assert paths[0].read_bytes() == b"abcdef" and not paths[1].exists()
    assert calls.count("review/a.bin") == 1
    assert state.tasks.detail(request_id)["cancel_cleanup"]["state"] == "complete"


async def test_one_unknown_owner_does_not_starve_other_cancelled_task(authenticated, monkeypatch):
    state = authenticated.app.state
    blocked, original = group(state, ("blocked/a.bin",), active="saving")
    ready, paths = group(state, ("ready/a.bin",))
    calls = []
    monkeypatch.setattr("app.services.saved_media.remove_verified", remover(calls, set()))
    state.tasks.cancel(blocked)
    state.tasks.cancel(ready)
    await state.runtime.cancel_cleanup.run_due()
    await state.runtime.cancel_cleanup.run_due()
    assert original[0].read_bytes() == b"abcdef" and not paths[0].exists()
    assert state.tasks.detail(blocked)["cancel_cleanup"]["state"] == "pending"
    assert state.tasks.detail(ready)["cancel_cleanup"]["state"] == "complete"
    with state.db.sessions() as session:
        assert session.get(MediaRequest, blocked).deleted_at is None
        assert session.get(MediaRequest, ready).deleted_at is None


async def test_completed_restart_operation_never_unlinks_restored_path(authenticated, monkeypatch):
    state = authenticated.app.state
    request_id, paths = group(state, ("review/restore.bin",))
    calls = []
    monkeypatch.setattr("app.services.saved_media.remove_verified", remover(calls, set()))
    state.tasks.cancel(request_id)
    # Simulate process exit after durable filesystem results but before coordinator projection.
    with state.db.transaction() as session:
        row = session.get(CancelCleanup, request_id)
        operation_id = uuid4().hex
        state.tasks.saved_media.prepare(session, operation_id, [request_id], cancellation=True)
        row.file_operation_id, row.state = operation_id, "running"
    state.tasks.saved_media.execute(operation_id)
    paths[0].write_bytes(b"abcdef")
    state.tasks.saved_media.recover()
    await state.runtime.cancel_cleanup.run_due()
    assert paths[0].read_bytes() == b"abcdef"
    assert calls == ["review/restore.bin"]
    assert state.tasks.detail(request_id)["cancel_cleanup"]["state"] == "complete"


@pytest.mark.parametrize("partial", [False, True])
async def test_queued_same_file_without_cache_protects_then_all_cancel_removes(client, config, partial):
    state, native, request_id, _, job_id, _ = seed(client, config, partial=partial)
    with state.db.transaction() as session:
        original = session.get(Job, job_id)
        original.state = "queued"
        item = session.get(MediaItem, original.item_id)
        sibling = MediaRequest(bot_id=7, chat_id=42, user_id=42, status="queued", collect_until=0)
        session.add(sibling)
        session.flush()
        sibling_id = sibling.id
        other = MediaItem(request_id=sibling.id, bot_id=7, chat_id=42, message_id=999,
                          file_id=item.file_id, media_type="document", size_bytes=6)
        session.add(other)
        session.flush()
        session.add(Job(request_id=sibling.id, item_id=other.id, state="queued", relative_path="other.bin",
                        acquisition_mode="local"))
        for reservation in session.scalars(select(FileReservation).where(FileReservation.job_id == job_id)):
            session.delete(reservation)
    expected = native.source.read_bytes()
    state.tasks.cancel(request_id)
    await state.runtime.cancel_cleanup.run_due()
    assert native.source.read_bytes() == expected and native.deletes == 0
    assert state.tasks.detail(request_id)["cancel_cleanup"]["entries"][0]["outcome"] == "retained_shared"
    state.tasks.cancel(sibling_id)
    state.tasks.cancel(request_id)
    await state.runtime.cancel_cleanup.run_due()
    await state.runtime.cancel_cleanup.run_due()
    assert not native.source.exists() and native.deletes == 1


async def test_unknown_cache_can_recover_after_original_connection_returns(client, config):
    state, native, request_id, _, job_id, cache_id = seed(client, config)
    with state.db.transaction() as session:
        job = session.get(Job, job_id)
        job.state = "queued"
        cache = session.get(RetainedCache, cache_id)
        cache.state, cache.completeness = "uncertain", "unknown"
        cache.path_encrypted, cache.path_digest, cache.fingerprint = None, None, None
        for reservation in session.scalars(select(FileReservation).where(FileReservation.job_id == job_id)):
            session.delete(reservation)
    state.runtime.client = None
    state.tasks.cancel(request_id)
    await state.runtime.cancel_cleanup.run_due()
    assert native.source.exists() and native.deletes == 0
    assert state.tasks.detail(request_id)["cancel_cleanup"]["state"] == "partial"
    state.runtime.client = native
    state.tasks.cancel(request_id)
    await state.runtime.cancel_cleanup.run_due()
    assert not native.source.exists() and native.deletes == 1
    assert state.tasks.detail(request_id)["cancel_cleanup"]["state"] == "complete"


async def test_recovered_publication_winner_is_included_in_cancel_cleanup(authenticated, monkeypatch):
    state = authenticated.app.state
    request_id, _ = group(state, (), active="saving")
    with state.db.sessions() as session:
        job = session.scalar(select(Job).where(Job.request_id == request_id))
        job_id, relative = job.id, job.relative_path
    storage = state.storage.get("default")
    final = storage.root / relative
    final.parent.mkdir(parents=True, exist_ok=True)
    temporary = final.with_name(".part.review")
    temporary.write_bytes(b"abcdef")
    state.runtime.worker.journal_copying(job_id, temporary.relative_to(storage.root).as_posix(), relative)
    state.runtime.worker.journal_ready(job_id, 6, hashlib.sha256(b"abcdef").hexdigest())
    publish_no_replace(temporary, final)  # Irreversible link won; success transaction was interrupted.
    state.tasks.cancel(request_id)
    await state.runtime.cancel_cleanup.run_due()
    assert final.read_bytes() == b"abcdef"  # Journal is still a recovery authority.
    state.runtime.worker.recover(False)
    with state.db.transaction() as session:
        session.get(CancelCleanup, request_id).next_run_at = 0
    calls = []
    monkeypatch.setattr("app.services.saved_media.remove_verified", remover(calls, set()))
    await state.runtime.cancel_cleanup.run_due()
    assert not final.exists() and calls == [relative]
    assert state.tasks.detail(request_id)["cancel_cleanup"]["state"] == "complete"


async def test_all_cancelled_shared_saved_aliases_remove_one_file_without_queue_deadlock(authenticated, monkeypatch):
    state = authenticated.app.state
    first, paths = group(state, ("shared/review.bin",))
    second, _ = group(state, ("shared/review.bin",))
    with state.db.transaction() as session:
        original = session.scalar(select(Job).where(Job.request_id == first, Job.state == "succeeded"))
        alias = session.scalar(select(Job).where(Job.request_id == second, Job.state == "succeeded"))
        alias.state, alias.linked_job_id = "already_exists", original.id
        session.delete(session.scalar(select(StoredFile).where(StoredFile.job_id == alias.id)))
    # An unrelated queue must not force global queue draining before file cleanup.
    unrelated, other = group(state, ("unrelated/review.bin",))
    state.tasks.cancel(first)
    state.tasks.cancel(second)
    calls = []
    monkeypatch.setattr("app.services.saved_media.remove_verified", remover(calls, set()))
    await state.runtime.cancel_cleanup.run_due()
    await state.runtime.cancel_cleanup.run_due()
    assert not paths[0].exists()
    assert calls.count("shared/review.bin") <= 2  # Second alias may observe proven absence.
    assert other[0].read_bytes() == b"abcdef"
    assert state.tasks.detail(unrelated)["status"] == "queued"
    assert all(state.tasks.detail(request_id)["cancel_cleanup"]["state"] == "complete"
               for request_id in (first, second))


async def test_settled_native_absent_alias_releases_unknown_mutation_but_keeps_orphan(client, config):
    state, native, _, _, job_id, cache_id = seed(client, config)
    operation_id = uuid4().hex
    with state.db.transaction() as session:
        session.add(CacheAction(id=operation_id, job_id=job_id, cache_id=cache_id, kind="clean", state="uncertain",
            attempt=1, generation="generation1", credential=digest(native.bot.token)))

    async def forgotten(*args, **kwargs):
        return {"completeness": "absent", "settled": True}

    native.inspect_file_cache = forgotten
    await state.runtime.retained_cache.recover()
    with state.db.sessions() as session:
        action = session.get(CacheAction, operation_id)
        assert action.state == "failed" and action.error_code == "CACHE_CLEAN_RETRY_REQUIRED"
        assert session.get(RetainedCache, cache_id).state == "retained"
    assert native.source.read_bytes() == b"abcdef" and native.deletes == 0


async def test_completed_cleanup_cannot_reactivate_old_failed_or_cached_members(client, config):
    state, native, request_id, item_id, job_id, cache_id = seed(client, config)
    with state.db.transaction() as session:
        request = session.get(MediaRequest, request_id)
        request.status = "queued"
        other = MediaItem(request_id=request_id, bot_id=7, chat_id=42, message_id=999,
                          file_id="other", media_type="document", size_bytes=6)
        session.add(other)
        session.flush()
        session.add(Job(request_id=request_id, item_id=other.id, state="queued", relative_path="other.bin"))
        for reservation in session.scalars(select(FileReservation).where(FileReservation.job_id == job_id)):
            session.delete(reservation)
    state.tasks.cancel(request_id)
    await state.runtime.cancel_cleanup.run_due()
    detail = state.tasks.detail(request_id)
    assert detail["cancel_cleanup"]["state"] == "complete" and detail["can_delete"]
    assert not detail["can_retry"] and not detail["can_cancel"]
    cache = next(item for item in detail["items"] if item["id"] == item_id)["cache"]
    assert not any(cache[key] for key in ("can_clean", "can_save", "can_resume_download", "can_redownload"))
    assert "重新发送" in cache["unavailable_reason"]
    with pytest.raises(AppError, match="重新发送"):
        state.tasks.retry(request_id, None)
    for action in ("save", "download", "redownload"):
        with pytest.raises(AppError, match="重新发送"):
            await state.runtime.retained_cache.submit(request_id, item_id, action, uuid4().hex,
                                                       cache_id=cache_id, confirmed=True)
    with state.db.sessions() as session:
        assert session.get(Job, job_id).state == "failed"
    assert not native.source.exists() and native.calls == 0 and native.deletes == 1


async def test_uncertain_final_absence_retires_only_exact_old_indexes_and_cleanup(authenticated, monkeypatch):
    state = authenticated.app.state
    request_id, paths = group(state, ("review/uncertain.bin",))
    other_id, other_paths = group(state, ("review/other.bin",))
    with state.db.transaction() as session:
        for owner in (request_id, other_id):
            job = session.scalar(select(Job).where(Job.request_id == owner, Job.state == "succeeded"))
            fact = session.scalar(select(StoredFile).where(StoredFile.job_id == job.id))
            session.add(CacheCleanup(job_id=job.id, bot_id=7, file_id=owner,
                source_path_encrypted=state.settings.secrets.encrypt("unused"), root_id=fact.root_id,
                relative_path=fact.relative_path, size_bytes=fact.size_bytes, sha256=fact.sha256))

    calls = []
    def unlink_uncertain(storage, entry):
        calls.append(entry["relative_path"])
        (storage.root / entry["relative_path"]).unlink()
        return "uncertain"

    monkeypatch.setattr("app.services.saved_media.remove_verified", unlink_uncertain)
    state.tasks.cancel(request_id)
    await state.runtime.cancel_cleanup.run_due()
    assert not paths[0].exists()
    with state.db.sessions() as session:
        assert len(list(session.scalars(select(CacheCleanup)))) == 2
    state.tasks.cancel(request_id)
    await state.runtime.cancel_cleanup.run_due()
    assert calls == ["review/uncertain.bin"] and other_paths[0].read_bytes() == b"abcdef"
    assert state.tasks.detail(request_id)["cancel_cleanup"]["state"] == "complete"
    with state.db.sessions() as session:
        assert [row.relative_path for row in session.scalars(select(StoredFile))] == ["review/other.bin"]
        assert [row.relative_path for row in session.scalars(select(CacheCleanup))] == ["review/other.bin"]


async def test_unknown_locator_absent_alias_cannot_discard_existing_orphan(client, config):
    state, native, request_id, _, job_id, cache_id = seed(client, config)
    with state.db.transaction() as session:
        job = session.get(Job, job_id)
        job.state = "queued"
        cache = session.get(RetainedCache, cache_id)
        cache.state, cache.completeness = "uncertain", "unknown"
        cache.path_encrypted, cache.path_digest, cache.fingerprint = None, None, None
        for reservation in session.scalars(select(FileReservation).where(FileReservation.job_id == job_id)):
            session.delete(reservation)

    async def forgotten(*args, **kwargs):
        return {"completeness": "absent", "settled": True}

    native.inspect_file_cache = forgotten
    state.tasks.cancel(request_id)
    await state.runtime.cancel_cleanup.run_due()
    detail = state.tasks.detail(request_id)
    assert detail["cancel_cleanup"]["state"] == "partial"
    assert detail["cancel_cleanup"]["entries"][0]["outcome"] == "retained_unverified"
    assert native.source.read_bytes() == b"abcdef" and native.deletes == 0


async def test_exited_deletion_thread_failure_becomes_uncertain_without_replay(authenticated, monkeypatch):
    state = authenticated.app.state
    request_id, paths = group(state, ("review/interrupted.bin",))
    calls = []
    original = state.tasks.saved_media.execute
    def interrupted(operation_id):
        calls.append(operation_id)
        if len(calls) > 1:
            return original(operation_id)
        paths[0].unlink()
        raise RuntimeError("Synthetic commit failure after owned unlink")
    monkeypatch.setattr(state.tasks.saved_media, "execute", interrupted)
    state.tasks.cancel(request_id)
    await state.runtime.cancel_cleanup.run_due()
    detail = state.tasks.detail(request_id)
    assert detail["cancel_cleanup"]["state"] == "partial"
    assert detail["cancel_cleanup"]["entries"][0]["outcome"] == "uncertain"
    with state.db.sessions() as session:
        assert session.get(FileDeletionOperation, calls[0]).state == "complete"
    paths[0].write_bytes(b"abcdef")
    state.tasks.cancel(request_id)
    await state.runtime.cancel_cleanup.run_due()
    assert len(calls) == 2  # New retry contains no pending unlink entries.
    assert paths[0].read_bytes() == b"abcdef"
