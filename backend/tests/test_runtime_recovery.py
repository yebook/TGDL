import asyncio
import json
import threading
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi.testclient import TestClient
from test_pipeline import FakeClient, confirm_all, message, setup_pipeline

from app.core.config import Config
from app.core.errors import AppError
from app.core.security import digest
from app.db.models import MediaRequest, Notification
from app.schemas.contracts import SettingsPatch


async def test_api_restart_waits_for_live_file_owner(client: TestClient, config: Config) -> None:
    runtime = client.app.state.runtime
    runtime.worker.recover = Mock()
    state = client.app.state
    patch = SettingsPatch(revision=1, bot_api_mode="local")
    state.settings.save(patch, state.settings.candidate(patch))
    runtime.api_generation = "old"
    marker = config.bot_api_root / ".tgd-api-generation"
    marker.write_text("new")
    persisted = config.data_dir / "api-generation.json"
    persisted.write_text(json.dumps({"generation": "old"}))
    release = asyncio.Event()
    owner = asyncio.create_task(release.wait())
    runtime.running = {"copy-in-progress": owner}

    runtime.recover_generation()
    runtime.worker.recover.assert_not_called()
    assert runtime.api_generation == "old"
    assert json.loads(persisted.read_text())["generation"] == "old"

    release.set()
    await owner
    runtime.recover_generation()
    runtime.worker.recover.assert_called_once_with(True)
    assert runtime.api_generation == "new"
    assert json.loads(persisted.read_text())["generation"] == "new"


def test_runtime_identity_guard_covers_environment_replacement(client: TestClient) -> None:
    runtime = client.app.state.runtime
    with runtime.db.transaction() as session:
        session.add(MediaRequest(bot_id=7, chat_id=42, user_id=42, collect_until=0))
    with pytest.raises(AppError, match="存在原 Bot"):
        runtime.remember_identity({"id": 8, "username": "replacement"})
    runtime.remember_identity({"id": 7, "username": "original"})


def test_notification_never_crosses_bot_identity(client: TestClient) -> None:
    runtime = client.app.state.runtime
    runtime.bot_id = 8
    with runtime.db.transaction() as session:
        row = Notification(event_key="old-result", chat_id=42,
                           payload={"bot_id": 7, "text": "old bot result"})
        session.add(row)
        session.flush()
        row_id = row.id
    assert runtime.next_notification() is None
    with runtime.db.sessions() as session:
        assert session.get(Notification, row_id).state == "obsolete"


async def test_saved_replacement_cannot_activate_over_existing_requests(authenticated: TestClient, monkeypatch) -> None:
    from app.bot.client import TelegramClient

    runtime = authenticated.app.state.runtime
    with runtime.db.transaction() as session:
        session.add(MediaRequest(bot_id=7, chat_id=42, user_id=42, collect_until=0))
    monkeypatch.setattr(TelegramClient, "identity", AsyncMock(return_value={"id": 8, "username": "replacement"}))
    response = authenticated.patch("/api/v1/settings", json={
        "revision": 1, "bot_token": "888888:synthetic-replacement", "allowed_user_ids": [42]})
    assert response.status_code == 200
    with pytest.raises(AppError, match="存在原 Bot"):
        await runtime.ensure_client()
    assert runtime.client is None
    assert runtime.bot_id is None


async def test_offline_token_change_pauses_dispatch_but_retains_live_owner(client: TestClient) -> None:
    state, ingest = setup_pipeline(client)
    ingest.message(7, message(), "in")
    request_id = confirm_all(state)
    runtime = state.runtime
    old_token = "777777:synthetic-original"
    patch = SettingsPatch(revision=2, bot_token="888888:synthetic-replacement")
    state.settings.save(patch, state.settings.candidate(patch))
    runtime.client = Mock()
    runtime.bot_id, runtime.token_hash = 7, digest(old_token)
    runtime.bot_status["state"] = "online"
    runtime.worker.claim = Mock()
    owner = asyncio.create_task(asyncio.Event().wait())
    runtime.running = {"old-owner": owner}
    tick = asyncio.create_task(runtime.tick_loop())
    try:
        await asyncio.sleep(0.65)
        runtime.worker.claim.assert_not_called()
        assert not owner.done()
        assert state.tasks.detail(request_id)["status"] == "queued"
    finally:
        tick.cancel()
        owner.cancel()
        await asyncio.gather(tick, owner, return_exceptions=True)


def test_worker_claim_is_bound_to_verified_bot(client: TestClient) -> None:
    state, ingest = setup_pipeline(client)
    ingest.message(7, message(), "in")
    request_id = confirm_all(state)
    assert state.runtime.worker.claim(8) is None
    assert state.tasks.detail(request_id)["status"] == "queued"
    assert state.runtime.worker.claim(7) is not None
    assert state.tasks.detail(request_id)["status"] == "fetching"


def test_task_search_accepts_displayed_identifier(authenticated: TestClient) -> None:
    database = authenticated.app.state.db
    with database.transaction() as session:
        request = MediaRequest(bot_id=7, chat_id=42, user_id=42, collect_until=0,
                               title="Different title")
        session.add(request)
        session.flush()
        request_id = request.id
    response = authenticated.get("/api/v1/tasks", params={"q": request_id})
    assert response.status_code == 200
    assert [item["id"] for item in response.json()["items"]] == [request_id]
    assert authenticated.get("/api/v1/tasks", params={"q": "missing"}).json()["items"] == []


@pytest.mark.integration
@pytest.mark.parametrize("stage", ["copy", "publish", "journal_copying", "journal_ready"])
async def test_shutdown_joins_file_and_journal_threads(client: TestClient, config: Config,
                                                      monkeypatch: pytest.MonkeyPatch, stage: str) -> None:
    from app.workers import download

    state, ingest = setup_pipeline(client)
    source = config.bot_api_root / "source"
    source.write_bytes(b"abcdef")
    ingest.message(7, message(), "in")
    request_id = confirm_all(state)
    worker = state.runtime.worker
    job_id = worker.claim()
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    owner = download if stage == "copy" else worker
    attribute = "copy_stream" if stage == "copy" else stage
    operation = getattr(owner, attribute)

    def controlled(*args):
        entered.set()
        assert release.wait(5)
        try:
            return operation(*args)
        finally:
            finished.set()

    monkeypatch.setattr(owner, attribute, controlled)
    task = asyncio.create_task(worker.run(FakeClient(source), job_id))
    try:
        async with asyncio.timeout(5):
            while not entered.is_set():
                await asyncio.sleep(0.01)
        task.cancel()
        await asyncio.sleep(0.05)
        assert not task.done(), "shutdown returned while the OS thread still owned state"
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert finished.is_set()
        status = state.tasks.detail(request_id)["status"]
        assert status == ({"publish": "succeeded", "journal_ready": "recovery_blocked"}.get(stage, "retry_wait"))
        if stage in {"copy", "journal_copying"}:
            assert not list(config.download_root.rglob(".part.*"))
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
