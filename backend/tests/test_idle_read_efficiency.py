from concurrent.futures import ThreadPoolExecutor
from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import event, select

from app.db.models import Job, LocalApiRuntime, MediaItem, MediaRequest, Notification, Settings
from app.schemas.contracts import SettingsPatch, UserSettings


def test_settings_reuses_decoding_but_reads_fresh_values(client: TestClient) -> None:
    service = client.app.state.settings
    initial_revision = service.read()[0]
    patch_value = SettingsPatch(revision=initial_revision, bot_token="123:synthetic", allowed_user_ids=[42])
    service.save(patch_value, service.candidate(patch_value))
    with patch.object(service.secrets, "decrypt", wraps=service.secrets.decrypt) as decrypt:
        service.read()
        service.read()
        assert decrypt.call_count == 0  # save's public projection warmed the cache
        with service.db.transaction() as session:
            row = session.get(Settings, 1)
            row.values = dict(row.values) | {"bot_token": service.secrets.encrypt("456:changed")}
        # Even external writes that forgot to advance revision cannot retain old credentials.
        assert service.read()[1].bot_token == "456:changed"
        assert decrypt.call_count == 1
        service.read()
        assert decrypt.call_count == 1


def test_settings_env_and_mutable_copies(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    service = client.app.state.settings
    revision, values, sources = service.read()
    values.allowed_user_ids.append(999)
    values.proxy.host = "mutated"
    sources["bot_token"] = "mutated"
    again = service.read()
    assert again[1].allowed_user_ids == [] and again[1].proxy.host == ""
    assert again[2]["bot_token"] != "mutated"
    monkeypatch.setenv("TGD_DOWNLOAD_CONCURRENCY", "7")
    assert service.read()[1].download_concurrency == 7
    assert service.read()[2]["download_concurrency"] == "environment"
    monkeypatch.setenv("TGD_DOWNLOAD_CONCURRENCY", "99")
    with pytest.raises(ValidationError):
        service.read()
    monkeypatch.delenv("TGD_DOWNLOAD_CONCURRENCY")
    assert service.read()[0] == revision
    assert service.read()[1].download_concurrency == 3
    # These credentials deliberately have no environment override.
    monkeypatch.setenv("TGD_TELEGRAM_API_ID", "999")
    assert service.read()[1].telegram_api_id == ""


def test_concurrent_readers_observe_saved_revision(client: TestClient) -> None:
    service = client.app.state.settings
    revision = service.read()[0]
    with ThreadPoolExecutor(max_workers=4) as pool:
        old_reads = list(pool.map(lambda _: service.read(), range(20)))
        change = SettingsPatch(revision=revision, download_concurrency=6)
        candidate = service.candidate(change)
        saving = pool.submit(service.save, change, candidate)
        overlapping = list(pool.map(lambda _: service.read(), range(20)))
        saving.result(timeout=10)
        reads = list(pool.map(lambda _: service.read(), range(20)))
    assert all(result[0] == revision for result in old_reads)
    assert all((result[0], result[1].download_concurrency) in {(revision, 3), (revision + 1, 6)}
               for result in overlapping)
    assert all(result[0] == revision + 1 and result[1].download_concurrency == 6 for result in reads)
    reads[0][1].allowed_user_ids.append(999)
    assert reads[1][1].allowed_user_ids == []


@pytest.mark.parametrize("count", [0, 1, 20])
def test_task_list_batches_queries_and_matches_individual_projection(authenticated: TestClient, count: int) -> None:
    state = authenticated.app.state
    with state.db.transaction() as session:
        for index in range(count):
            request = MediaRequest(bot_id=7, chat_id=42, user_id=42, status="partial_success",
                                   title=f"batch-{index}", collect_until=0)
            session.add(request)
            session.flush()
            for offset in range(2):
                item = MediaItem(request_id=request.id, bot_id=7, chat_id=42, message_id=index * 2 + offset,
                                 file_id=f"file-{index}-{offset}", media_type="video",
                                 size_bytes=None if offset else 123)
                session.add(item)
                session.flush()
                session.add(Job(request_id=request.id, item_id=item.id,
                                state="failed" if offset else "succeeded", completed_bytes=100,
                                relative_path=f"day/{index}-{offset}.mp4"))
            session.add(Notification(request_id=request.id, event_key=f"batch-{index}",
                                     chat_id=42, state="failed" if index % 2 else "pending", payload={}))
    with state.db.sessions() as session:
        expected = {row.id: state.tasks._dto(session, row) for row in session.scalars(select(MediaRequest))}
    queries: list[str] = []

    def capture(_connection: Any, _cursor: Any, statement: str, *_args: Any) -> None:
        if statement.lstrip().upper().startswith("SELECT"):
            queries.append(statement)

    event.listen(state.db.engine, "before_cursor_execute", capture)
    try:
        response = authenticated.get("/api/v1/tasks?page=1&page_size=20")
    finally:
        event.remove(state.db.engine, "before_cursor_execute", capture)
    assert response.status_code == 200
    assert len(queries) == (7 if count else 4)
    data = response.json()
    assert data["total"] == count
    assert {item["id"]: item for item in data["items"]} == expected


def test_repeated_settings_read_validates_only_once(client: TestClient) -> None:
    service = client.app.state.settings
    service._decoded_key = None
    with patch.object(UserSettings, "model_validate", wraps=UserSettings.model_validate) as validate:
        service.read()
        service.read()
        assert validate.call_count == 1


def test_runtime_read_observes_commits_and_isolates_mutable_values(client: TestClient) -> None:
    service = client.app.state.runtime.local_api
    service.update(phase="draining", snapshot={"generation": "one"})
    original = service.read()
    original["snapshot"]["generation"] = "mutated"
    assert service.read()["snapshot"]["generation"] == "one"
    with pytest.raises(RuntimeError), service.db.transaction() as session:
        row = session.get(LocalApiRuntime, 1)
        assert row is not None
        row.values = dict(row.values) | {"phase": "failed"}
        session.flush()
        assert service.read()["phase"] == "draining"
        raise RuntimeError("rollback")
    assert service.read()["phase"] == "draining"
    service.update(phase="idle", snapshot={"generation": "two"})
    assert service.read()["phase"] == "idle"
    assert service.read()["snapshot"]["generation"] == "two"


def test_batched_task_page_keeps_filter_order_and_associations(authenticated: TestClient) -> None:
    state = authenticated.app.state
    ids: list[str] = []
    with state.db.transaction() as session:
        for index in range(7):
            request = MediaRequest(bot_id=7, chat_id=42, user_id=42, collect_until=0,
                                   title="other" if index == 5 else "matching", created_at=index + 1,
                                   status="failed" if index == 4 else "succeeded",
                                   deleted_at=10 if index == 6 else None)
            session.add(request)
            session.flush()
            ids.append(request.id)
            session.add(MediaItem(request_id=request.id, bot_id=7, chat_id=42, message_id=index,
                                  file_id=f"file-{index}", media_type="video", size_bytes=index + 10))
    response = authenticated.get("/api/v1/tasks", params={"q": "matching", "status": "succeeded",
                                                        "page": 2, "page_size": 2})
    assert response.status_code == 200
    page = response.json()
    assert page["total"] == 4
    assert [item["id"] for item in page["items"]] == [ids[1], ids[0]]
    assert [item["total_bytes"] for item in page["items"]] == [11, 10]
    assert all(item["item_count"] == 1 and item["notification_status"] == "sent" for item in page["items"])
