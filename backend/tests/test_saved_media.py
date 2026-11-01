import hashlib
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db.models import CacheCleanup, FileDeletionOperation, Job, MediaItem, MediaRequest, StoredFile
from app.storage.saved_media import observe, remove_verified

POSIX = pytest.mark.skipif(os.name != "posix", reason="Real descriptor-relative unlink requires POSIX")


def saved(client: TestClient, path: str = "day/video.mp4", *, alias: bool = False) -> tuple[str, Path]:
    state = client.app.state
    storage = state.storage.get("default")
    storage.initialize()
    final = storage.root / path
    final.parent.mkdir(exist_ok=True)
    final.write_bytes(b"abcdef")
    with state.db.transaction() as session:
        request = MediaRequest(bot_id=7, chat_id=42, user_id=42, status="succeeded", collect_until=0)
        session.add(request)
        session.flush()
        item = MediaItem(request_id=request.id, bot_id=7, chat_id=42, message_id=int(uuid4().hex[:12], 16),
                         file_id="file", media_type="video", size_bytes=6)
        session.add(item)
        session.flush()
        job = Job(request_id=request.id, item_id=item.id, state="succeeded", relative_path=path, completed_bytes=6)
        if alias:
            stored = session.scalar(select(StoredFile).where(StoredFile.relative_path == path))
            job.linked_job_id = stored.job_id if stored else None
        session.add(job)
        session.flush()
        if not alias:
            session.add(StoredFile(bot_id=7, file_unique_id=uuid4().hex, job_id=job.id, root_id="default",
                                   relative_path=path, size_bytes=6, sha256=hashlib.sha256(b"abcdef").hexdigest()))
        return request.id, final


def remove(client: TestClient, ids: list[str], operation: str | None = None, **extra: object):
    return client.post("/api/v1/tasks/delete", json={"ids": ids, "delete_files": True,
                                                    "operation_id": operation or uuid4().hex, **extra})


def test_availability_tracks_external_changes_without_changing_history(authenticated: TestClient) -> None:
    request, path = saved(authenticated)
    def state():
        detail = authenticated.get(f"/api/v1/tasks/{request}").json()
        assert detail["status"] == "succeeded" and detail["completed_count"] == 1
        return detail["items"][0]["local_file_state"]
    assert state() == "present"
    path.unlink()
    assert state() == "missing"
    assert authenticated.get("/api/v1/tasks").json()["items"][0]["local_files"]["missing"] == 1
    path.write_bytes(b"a")
    assert state() == "changed"
    path.write_bytes(b"abcdef")
    assert state() == "present"
    storage = authenticated.app.state.storage.get("default")
    storage.minimum_free = 2**60
    assert state() == "present"
    storage.identity_path.unlink()
    assert state() == "unavailable"
    assert not storage.identity_path.exists()


def test_root_missing_or_replaced_is_unavailable(authenticated: TestClient) -> None:
    request, path = saved(authenticated)
    storage = authenticated.app.state.storage.get("default")
    old = storage.root.with_name("original")
    storage.root.rename(old)
    assert observe(storage, "day/video.mp4", 6) == "unavailable"
    storage.root.mkdir()
    assert observe(storage, "day/video.mp4", 6) == "unavailable"
    assert path.exists() is False
    assert authenticated.get(f"/api/v1/tasks/{request}").json()["local_files"]["unavailable"] == 1


def test_strict_opt_in_and_record_only_stable_replay(authenticated: TestClient) -> None:
    request, path = saved(authenticated)
    for value in ["true", 1, None]:
        assert remove(authenticated, [request], delete_files=value).status_code == 422
    assert authenticated.post("/api/v1/tasks/delete", json={"ids": [request], "delete_files": True}).status_code == 422
    operation = uuid4().hex
    body = {"ids": [request], "operation_id": operation}
    result = authenticated.post("/api/v1/tasks/delete", json=body)
    assert result.status_code == 200 and path.exists()
    assert authenticated.post("/api/v1/tasks/delete", json=body).json() == result.json()
    assert remove(authenticated, [request], operation).status_code == 409


@POSIX
def test_missing_historical_file_without_current_index_is_already_absent(authenticated: TestClient) -> None:
    request, path = saved(authenticated)
    path.unlink()
    with authenticated.app.state.db.transaction() as session:
        session.delete(session.scalar(select(StoredFile)))
    assert remove(authenticated, [request]).json()["files"]["missing"] == 1


@POSIX
def test_explicit_delete_replay_does_not_remove_replacement(authenticated: TestClient) -> None:
    request, path = saved(authenticated)
    operation = uuid4().hex
    response = remove(authenticated, [request], operation)
    assert response.status_code == 200 and response.json()["files"]["deleted"] == 1
    assert not path.exists()
    path.write_bytes(b"replacement")
    assert remove(authenticated, [request], operation).json() == response.json()
    assert path.read_bytes() == b"replacement"
    assert remove(authenticated, ["other"], operation).status_code == 409


@POSIX
@pytest.mark.parametrize("batch", [False, True])
def test_shared_path_preserved_unless_all_visible_references_selected(authenticated: TestClient, batch: bool) -> None:
    first, path = saved(authenticated)
    second, _ = saved(authenticated, alias=True)
    ids = [first, second] if batch else [first]
    result = remove(authenticated, ids).json()["files"]
    assert result["deleted" if batch else "retained_shared"] == 1
    assert path.exists() is not batch
    if not batch:
        assert remove(authenticated, [second]).json()["files"]["deleted"] == 1


@POSIX
@pytest.mark.parametrize("kind", ["missing", "hash", "size", "directory", "symlink", "parent_symlink"])
def test_unowned_replacements_retained_and_missing_idempotent(authenticated: TestClient, kind: str) -> None:
    request, path = saved(authenticated)
    path.unlink()
    if kind == "hash":
        path.write_bytes(b"ABCDEF")
    elif kind == "size":
        path.write_bytes(b"b")
    elif kind == "directory":
        path.mkdir()
    elif kind == "symlink":
        path.symlink_to(path.parent / "outside")
    elif kind == "parent_symlink":
        path.parent.rename(path.parent.with_name("other"))
        path.parent.symlink_to(path.parent.with_name("other"), target_is_directory=True)
    result = remove(authenticated, [request]).json()["files"]
    assert result["missing" if kind == "missing" else "retained_unverified"] == 1


@POSIX
def test_exact_cleanup_retired_only_after_success(authenticated: TestClient) -> None:
    request, path = saved(authenticated)
    state = authenticated.app.state
    with state.db.transaction() as session:
        stored = session.scalar(select(StoredFile))
        session.add(CacheCleanup(job_id=stored.job_id, bot_id=7, file_id="file", source_path_encrypted="opaque",
                                 root_id=stored.root_id, relative_path=stored.relative_path,
                                 size_bytes=stored.size_bytes, sha256=stored.sha256))
    assert remove(authenticated, [request]).json()["files"]["deleted"] == 1
    with state.db.sessions() as session:
        assert session.scalar(select(CacheCleanup)) is None
        assert session.scalar(select(StoredFile)) is None
    assert not path.exists()


def test_restart_never_replays_uncertain_unlink(authenticated: TestClient) -> None:
    request, path = saved(authenticated)
    operation = uuid4().hex
    with authenticated.app.state.db.transaction() as session:
        session.get(MediaRequest, request).deleted_at = 1
        session.add(FileDeletionOperation(id=operation, request_ids=[request], entries=[{
            "root_id": "default", "relative_path": "day/video.mp4", "outcome": "pending"}]))
    authenticated.app.state.tasks.saved_media.recover()
    result = remove(authenticated, [request], operation)
    assert result.status_code == 200 and result.json()["files"]["uncertain"] == 1
    assert path.read_bytes() == b"abcdef"


def test_later_final_failure_cannot_erase_unknown_cache_mutation(authenticated: TestClient) -> None:
    request, _ = saved(authenticated)
    state = authenticated.app.state
    with state.db.transaction() as session:
        stored = session.scalar(select(StoredFile))
        job_id = stored.job_id
        session.add(CacheCleanup(job_id=job_id, bot_id=7, file_id="file", source_path_encrypted="opaque",
                                 root_id=stored.root_id, relative_path=stored.relative_path,
                                 size_bytes=stored.size_bytes, sha256=stored.sha256))
    state.runtime.cache_cleanup.mutation_started(job_id)
    state.runtime.cache_cleanup.retry(job_id, "CACHE_FINAL_UNAVAILABLE")
    assert remove(authenticated, [request]).status_code == 409
    with state.db.sessions() as session:
        assert session.get(CacheCleanup, job_id).error_code == "CACHE_CLEANUP_UNAVAILABLE"


@pytest.mark.parametrize("kind", ["queued", "recovery_blocked", "cleanup_inflight", "cleanup_unknown", "worker_tail"])
def test_destructive_owners_reject_without_marking_records(authenticated: TestClient, kind: str) -> None:
    from types import SimpleNamespace
    request, path = saved(authenticated)
    state = authenticated.app.state
    with state.db.transaction() as session:
        job = session.scalar(select(Job))
        if kind in {"queued", "recovery_blocked"}:
            job.state = kind
        elif kind.startswith("cleanup_"):
            stored = session.scalar(select(StoredFile))
            session.add(CacheCleanup(job_id=job.id, bot_id=7, file_id="file", source_path_encrypted="opaque",
                                     root_id=stored.root_id, relative_path=stored.relative_path,
                                     size_bytes=stored.size_bytes, sha256=stored.sha256,
                                     error_code="CACHE_CLEANUP_IN_FLIGHT" if kind == "cleanup_inflight" else "CACHE_CLEANUP_UNAVAILABLE"))
    if kind == "worker_tail":
        state.runtime.running["tail"] = SimpleNamespace(done=lambda: False)
    try:
        assert remove(authenticated, [request]).status_code == 409
        with state.db.sessions() as session:
            assert session.get(MediaRequest, request).deleted_at is None
        assert path.read_bytes() == b"abcdef"
    finally:
        state.runtime.running.clear()


@POSIX
def test_newer_stored_record_at_historical_path_does_not_grant_ownership(authenticated: TestClient) -> None:
    old, path = saved(authenticated)
    newer, _ = saved(authenticated, "day/newer.mp4")
    with authenticated.app.state.db.transaction() as session:
        original = session.scalar(select(StoredFile).where(StoredFile.relative_path == "day/video.mp4"))
        replacement = session.scalar(select(StoredFile).where(StoredFile.relative_path == "day/newer.mp4"))
        session.delete(original)
        replacement.relative_path = "day/video.mp4"
        session.get(MediaRequest, newer).deleted_at = 1
    assert remove(authenticated, [old]).json()["files"]["retained_unverified"] == 1
    assert path.read_bytes() == b"abcdef"


@POSIX
def test_permission_error_is_unavailable_and_preserves_file(authenticated: TestClient) -> None:
    request, path = saved(authenticated)
    path.parent.chmod(0)
    try:
        assert observe(authenticated.app.state.storage.get("default"), "day/video.mp4", 6) == "unavailable"
        assert remove(authenticated, [request]).json()["files"]["failed"] == 1
    finally:
        path.parent.chmod(0o700)
    assert path.read_bytes() == b"abcdef"


@POSIX
def test_fifo_never_hangs_or_is_removed(authenticated: TestClient) -> None:
    request, path = saved(authenticated)
    path.unlink()
    os.mkfifo(path)
    assert remove(authenticated, [request]).json()["files"]["retained_unverified"] == 1


@POSIX
def test_crash_after_unlink_before_bookkeeping_is_not_replayed(authenticated: TestClient, monkeypatch) -> None:
    request, path = saved(authenticated)
    operation = uuid4().hex
    service = authenticated.app.state.tasks.saved_media
    import app.services.saved_media as module
    original = module.remove_verified
    def interrupted(*args):
        assert original(*args) == "deleted"
        raise RuntimeError("simulated abrupt exit before result commit")
    monkeypatch.setattr(module, "remove_verified", interrupted)
    with pytest.raises(RuntimeError, match="simulated abrupt exit"):
        remove(authenticated, [request], operation)
    assert not path.exists()
    path.write_bytes(b"abcdef")
    service.recover()
    assert remove(authenticated, [request], operation).json()["files"]["uncertain"] == 1
    assert path.exists()


@POSIX
def test_hash_releases_database_but_retains_dispatch_and_cleanup_gate(authenticated: TestClient, monkeypatch) -> None:
    request, path = saved(authenticated)
    started, finish = Event(), Event()
    import app.services.saved_media as module
    original = module.remove_verified
    def waiting(*args):
        started.set()
        assert finish.wait(10)
        return original(*args)
    monkeypatch.setattr(module, "remove_verified", waiting)
    operation = uuid4().hex
    with ThreadPoolExecutor() as pool:
        future = pool.submit(remove, authenticated, [request], operation)
        assert started.wait(10)
        try:
            with authenticated.app.state.db.transaction() as session:
                assert session.get(FileDeletionOperation, operation).state == "running"
            assert authenticated.app.state.runtime.worker.claim(7) is None
            from app.core.errors import AppError
            with pytest.raises(AppError):
                authenticated.app.state.runtime.cache_maintenance._admit("generation", "a" * 64)
            assert remove(authenticated, [request], operation).status_code == 409
        finally:
            finish.set()
        assert future.result().json()["files"]["deleted"] == 1
    assert not path.exists()


@POSIX
def test_parent_swap_during_hash_retains_target(authenticated: TestClient, monkeypatch) -> None:
    _, path = saved(authenticated)
    storage = authenticated.app.state.storage.get("default")
    import app.storage.saved_media as module
    original = module.hash_stream
    def swapped(stream):
        result = original(stream)
        path.parent.rename(path.parent.with_name("old"))
        path.parent.mkdir()
        path.write_bytes(b"abcdef")
        return result
    monkeypatch.setattr(module, "hash_stream", swapped)
    entry = {"relative_path": "day/video.mp4", "size_bytes": 6, "sha256": hashlib.sha256(b"abcdef").hexdigest()}
    assert remove_verified(storage, entry) == "retained_unverified"
    assert path.read_bytes() == b"abcdef"


@pytest.mark.skipif(os.name == "posix", reason="Unsupported platform failure path")
def test_unsupported_platform_preserves_bytes(authenticated: TestClient) -> None:
    request, path = saved(authenticated)
    assert remove(authenticated, [request]).json()["files"]["retained_unverified"] == 1
    assert path.read_bytes() == b"abcdef"
