import asyncio
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from test_pipeline import confirm_all, message, setup_pipeline

from app.core.errors import AppError
from app.db.models import Job, MediaItem, MediaRequest, Notification, StoredFile
from app.services.tasks import project


def seed(client: TestClient, status: str = "cancelled", job_state: str | None = None) -> str:
    with client.app.state.db.transaction() as session:
        request = MediaRequest(bot_id=7, chat_id=42, user_id=42, status=status, collect_until=0)
        session.add(request)
        session.flush()
        if job_state:
            item = MediaItem(request_id=request.id, bot_id=7, chat_id=42,
                             message_id=int(request.created_at * 1e6), file_id="file", media_type="video")
            session.add(item)
            session.flush()
            session.add(Job(request_id=request.id, item_id=item.id, state=job_state, relative_path="2026-10-21/video.mp4"))
        return request.id


@pytest.mark.parametrize("status", ["succeeded", "already_exists", "partial_success", "failed", "cancelled", "expired"])
def test_terminal_removal_hides_every_api_and_survives_restart(authenticated: TestClient, status: str) -> None:
    client = authenticated
    request_id = seed(client, status, "failed" if status == "failed" else None)
    assert client.get(f"/api/v1/tasks/{request_id}").json()["can_delete"] is True
    assert client.delete(f"/api/v1/tasks/{request_id}").json() == {"deleted_ids": [request_id]}
    client.app.state.db.engine.dispose()
    assert client.get("/api/v1/tasks").json()["total"] == 0
    for method, suffix in [("get", ""), ("post", "/cancel"), ("post", "/retry"), ("delete", "")]:
        assert getattr(client, method)(f"/api/v1/tasks/{request_id}{suffix}").status_code == 404
    with client.app.state.db.transaction() as session:
        request = session.get(MediaRequest, request_id)
        assert request and request.deleted_at is not None
        before = request.status, request.result_revision
        project(session, request)
        assert (request.status, request.result_revision) == before


@pytest.mark.parametrize("status,job_state", [
    ("awaiting_confirmation", None), ("collecting", None), ("queued", "queued"),
    ("fetching", "fetching"), ("cancelled", "recovery_blocked"), ("failed", "retry_wait"),
    ("succeeded", "saving"), ("cancelled", "cancelling"), ("failed", "waiting_existing"),
])
def test_busy_batch_is_atomic_and_dto_agrees(authenticated: TestClient, status: str, job_state: str | None) -> None:
    client = authenticated
    removable = seed(client)
    busy = seed(client, status, job_state)
    assert client.get(f"/api/v1/tasks/{busy}").json()["can_delete"] is False
    response = client.post("/api/v1/tasks/delete", json={"ids": [removable, busy]})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "TASK_DELETE_BUSY"
    assert client.get(f"/api/v1/tasks/{removable}").status_code == 200


def test_batch_missing_deleted_and_validation_are_atomic(authenticated: TestClient) -> None:
    client = authenticated
    first, second = seed(client), seed(client)
    for body in [{"ids": []}, {"ids": [first, first]}, {"ids": [""]}, {"ids": [" "]},
                 {"ids": [str(i) for i in range(101)]}, {"ids": [123]}, {"ids": [first], "files": True}]:
        assert client.post("/api/v1/tasks/delete", json=body).status_code == 422
    assert client.post("/api/v1/tasks/delete", json={"ids": [first, "missing"]}).status_code == 404
    assert client.get(f"/api/v1/tasks/{first}").status_code == 200
    assert client.delete(f"/api/v1/tasks/{second}").status_code == 200
    assert client.post("/api/v1/tasks/delete", json={"ids": [first, second]}).status_code == 404
    assert client.get(f"/api/v1/tasks/{first}").status_code == 200
    third = seed(client)
    assert client.post("/api/v1/tasks/delete", json={"ids": [first, third]}).json() == {"deleted_ids": [first, third]}


def test_delete_auth_origin_and_csrf(authenticated: TestClient) -> None:
    client = authenticated
    request_id = seed(client)
    for method, path, body in [("delete", f"/api/v1/tasks/{request_id}", None),
                               ("post", "/api/v1/tasks/delete", {"ids": [request_id]})]:
        assert client.request(method, path, json=body, headers={"Origin": "https://attacker.invalid"}).status_code == 403
        csrf = client.headers.pop("X-CSRF-Token")
        assert client.request(method, path, json=body).status_code == 403
        client.headers["X-CSRF-Token"] = csrf
    assert client.get(f"/api/v1/tasks/{request_id}").status_code == 200
    client.cookies.clear()
    assert client.delete(f"/api/v1/tasks/{request_id}").status_code == 401
    assert client.post("/api/v1/tasks/delete", json={"ids": [request_id]}).status_code == 401


def test_delete_preserves_files_dedup_ingestion_and_frozen_history(authenticated: TestClient) -> None:
    state, ingest = setup_pipeline(authenticated)
    ingest.message(7, message(), "in1")
    request_id = confirm_all(state)
    job_id = state.runtime.worker.claim(7)
    source = state.runtime.config.bot_api_root / "synthetic.mp4"
    source.write_bytes(b"abcdef")

    class FakeClient:
        async def get_file(self, file_id: str) -> str:
            return str(source)

    asyncio.run(state.runtime.worker.run(FakeClient(), job_id))
    with state.db.sessions() as session:
        request = session.get(MediaRequest, request_id)
        snapshot = request.title, request.destination, request.nonce
        job = session.get(Job, job_id)
        path = state.runtime.config.download_root / job.relative_path
        original_path = job.relative_path
    assert authenticated.delete(f"/api/v1/tasks/{request_id}").status_code == 200
    ingest.message(7, message(), "replay")
    ingest.message(7, message(2) | {"video": message()["video"]}, "in2")
    next_id = confirm_all(state)
    assert next_id != request_id
    assert state.runtime.worker.claim(7) is None  # Dedup resolves without getFile.
    assert state.tasks.detail(next_id)["status"] == "already_exists"
    assert path.read_bytes() == b"abcdef"
    with state.db.sessions() as session:
        old = session.get(MediaRequest, request_id)
        assert (old.title, old.destination, old.nonce) == snapshot
        assert session.get(Job, job_id).relative_path == original_path
        assert session.scalar(select(func.count()).select_from(StoredFile)) == 1
        assert session.scalar(select(func.count()).select_from(MediaItem)) == 2
    for user_id, expected in [(42, "任务记录已删除"), (43, None)]:
        payload = {"data": f"y:{request_id}:{snapshot[2]}", "from": {"id": user_id}, "message": {"chat": {"id": 42}}}
        if expected:
            assert expected in ingest.callback(payload)
        else:
            with pytest.raises(AppError, match="无法操作"):
                ingest.callback(payload)
    ingest.message(7, {"message_id": 3, "from": {"id": 42}, "chat": {"id": 42, "type": "private"}, "text": "/status"}, "status")
    with state.db.sessions() as session:
        reply = session.scalar(select(Notification).where(Notification.event_key == "reply:status"))
        # Distinct requests may intentionally share their frozen display title.
        assert snapshot[0] in reply.payload["text"]
        assert request_id not in reply.payload["text"]
        assert next_id in reply.payload["text"]


def test_removed_notification_results_never_revive_or_retry(authenticated: TestClient) -> None:
    state = authenticated.app.state
    request_id = seed(authenticated)
    with state.db.transaction() as session:
        note = Notification(request_id=request_id, chat_id=42, event_key="late", payload={"text": "result", "bot_id": 7})
        session.add(note)
        session.flush()
        note_id = note.id
    assert state.runtime.next_notification(7)[0] == note_id
    assert authenticated.delete(f"/api/v1/tasks/{request_id}").status_code == 200
    for message_id in [None, 123]:
        state.runtime.notification_result(note_id, message_id, False)
        assert state.runtime.next_notification(7) is None
        with state.db.sessions() as session:
            assert session.get(Notification, note_id).state == "obsolete"


def test_deletion_retry_race_serializes(authenticated: TestClient) -> None:
    state = authenticated.app.state
    request_id = seed(authenticated, "failed", "failed")

    def operation(delete: bool) -> str:
        try:
            if delete:
                state.tasks.delete([request_id])
            else:
                state.tasks.retry(request_id, None)
            return "deleted" if delete else "retried"
        except AppError as exc:
            return exc.code

    with ThreadPoolExecutor(2) as pool:
        outcomes = list(pool.map(operation, [True, False]))
    assert set(outcomes) in [{"deleted", "NOT_FOUND"}, {"TASK_DELETE_BUSY", "retried"}]


def test_late_worker_failure_cannot_requeue_removed_record(authenticated: TestClient) -> None:
    state = authenticated.app.state
    request_id = seed(authenticated, "failed", "failed")
    assert authenticated.delete(f"/api/v1/tasks/{request_id}").status_code == 200
    with state.db.sessions() as session:
        job_id = session.scalar(select(Job.id).where(Job.request_id == request_id))
    state.runtime.worker.failed(job_id, "NETWORK_ERROR", "synthetic late failure", True)
    assert state.runtime.worker.claim(7) is None
    state.runtime.worker.recover(True)
    with state.db.sessions() as session:
        assert session.get(Job, job_id).state == "failed"
        assert session.get(MediaRequest, request_id).status == "failed"
