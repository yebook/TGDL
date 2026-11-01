import os
from contextlib import contextmanager
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from test_saved_media import remove, saved

from app.db.models import FileDeletionOperation, Job, MediaRequest, StoredFile

POSIX = pytest.mark.skipif(os.name != "posix", reason="Descriptor-relative removal requires POSIX")


def test_alias_observations_release_sessions_and_probe_path_once(authenticated: TestClient, monkeypatch) -> None:
    first, _ = saved(authenticated)
    second, _ = saved(authenticated, alias=True)
    database = authenticated.app.state.db
    original_sessions = database.sessions
    active = 0

    @contextmanager
    def sessions():
        nonlocal active
        with original_sessions() as session:
            active += 1
            try:
                yield session
            finally:
                active -= 1

    import app.services.tasks as module
    original_observe = module.observe
    calls = []

    def observe(*args):
        assert active == 0, "A slow filesystem probe must not retain a database read session"
        calls.append(args[1:])
        return original_observe(*args)

    monkeypatch.setattr(database, "sessions", sessions)
    monkeypatch.setattr(module, "observe", observe)
    page = authenticated.get("/api/v1/tasks").json()
    assert {row["id"] for row in page["items"]} == {first, second}
    assert all(row["local_files"]["present"] == 1 for row in page["items"])
    assert calls == [("day/video.mp4", 6)]
    calls.clear()
    assert authenticated.get(f"/api/v1/tasks/{first}").json()["items"][0]["local_file_state"] == "present"
    assert len(calls) == 1


def test_mixed_busy_batch_is_rejected_before_any_record_or_file_change(authenticated: TestClient) -> None:
    first, first_path = saved(authenticated)
    second, second_path = saved(authenticated, "day/second.mp4")
    with authenticated.app.state.db.transaction() as session:
        session.get(MediaRequest, second).status = "saving"
        session.scalar(select(Job).where(Job.request_id == second)).state = "saving"
    operation = uuid4().hex
    response = remove(authenticated, [first, second], operation)
    assert response.status_code == 409 and response.json()["error"]["code"] == "TASK_DELETE_BUSY"
    assert first_path.read_bytes() == second_path.read_bytes() == b"abcdef"
    with authenticated.app.state.db.sessions() as session:
        assert session.get(FileDeletionOperation, operation) is None
        assert all(session.get(MediaRequest, request).deleted_at is None for request in [first, second])


@POSIX
def test_partial_filesystem_failure_has_stable_precise_outcomes(authenticated: TestClient, monkeypatch) -> None:
    first, first_path = saved(authenticated, "day/a.mp4")
    second, second_path = saved(authenticated, "day/b.mp4")
    import app.storage.saved_media as module
    original = module.hash_stream
    calls = 0

    def hash_stream(stream):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise PermissionError("isolated simulated read denial")
        return original(stream)

    monkeypatch.setattr(module, "hash_stream", hash_stream)
    operation = uuid4().hex
    response = remove(authenticated, [first, second], operation)
    assert response.status_code == 200
    assert response.json()["files"] == {"deleted": 1, "missing": 0, "retained_shared": 0,
                                         "retained_unverified": 0, "failed": 1, "uncertain": 0}
    assert not first_path.exists() and second_path.read_bytes() == b"abcdef"
    first_path.write_bytes(b"new unrelated replacement")
    assert remove(authenticated, [second, first], operation).json() == response.json()
    assert calls == 2 and first_path.read_bytes() == b"new unrelated replacement"
    with authenticated.app.state.db.sessions() as session:
        assert all(session.get(MediaRequest, request).deleted_at is not None for request in [first, second])
        assert len(list(session.scalars(select(StoredFile)))) == 1


@POSIX
def test_historical_missing_path_does_not_redirect_to_new_saved_copy(authenticated: TestClient) -> None:
    old, old_path = saved(authenticated)
    old_path.unlink()
    new, new_path = saved(authenticated, "day/new-copy.mp4")
    with authenticated.app.state.db.transaction() as session:
        old_index = session.scalar(select(StoredFile).where(StoredFile.relative_path == "day/video.mp4"))
        new_index = session.scalar(select(StoredFile).where(StoredFile.relative_path == "day/new-copy.mp4"))
        unique_id = old_index.file_unique_id
        session.delete(old_index)
        session.flush()
        new_index.file_unique_id = unique_id
        new_index_id = new_index.id
    detail = authenticated.get(f"/api/v1/tasks/{old}").json()
    assert detail["status"] == "succeeded" and detail["completed_count"] == 1
    assert detail["items"][0]["relative_path"] == "day/video.mp4"
    assert detail["items"][0]["local_file_state"] == "missing"
    assert authenticated.get(f"/api/v1/tasks/{new}").json()["local_files"]["present"] == 1
    assert remove(authenticated, [old]).json()["files"]["missing"] == 1
    assert new_path.read_bytes() == b"abcdef"
    with authenticated.app.state.db.sessions() as session:
        assert session.get(StoredFile, new_index_id) is not None


@POSIX
def test_single_delete_route_requires_explicit_file_intent_and_stable_identity(authenticated: TestClient) -> None:
    request, path = saved(authenticated)
    assert authenticated.delete(f"/api/v1/tasks/{request}?delete_files=true").status_code == 422
    operation = uuid4().hex
    route = f"/api/v1/tasks/{request}?delete_files=true&operation_id={operation}"
    response = authenticated.delete(route)
    assert response.status_code == 200 and response.json()["files"]["deleted"] == 1
    path.write_bytes(b"replacement after removal")
    assert authenticated.delete(route).json() == response.json()
    assert path.read_bytes() == b"replacement after removal"
