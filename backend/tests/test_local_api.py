import asyncio
import json
import os
import time
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from test_pipeline import confirm_all, message

from app.bot.client import TelegramClient
from app.core.config import Config
from app.core.errors import AppError
from app.db.models import IngestUpdate, Notification, Settings
from app.main import create_app
from app.schemas.contracts import SettingsPatch
from app.services.local_api import LocalApiService

HASH = "a" * 32


@pytest.fixture
def managed(config: Config):
    config.managed_local_api = True
    config.local_api_control_dir = config.data_dir / "control"
    with TestClient(create_app(config)) as client:
        service = client.app.state.runtime.local_api
        heartbeat(service)
        yield client


def heartbeat(service, **updates):
    directory = service.config.local_api_control_dir
    directory.mkdir(exist_ok=True, mode=0o700)
    path = directory / "applied.json"
    path.write_text(json.dumps({"version": 1, "command_id": None, "state": "stopped", "generation": None,
                                "heartbeat_at": time.time(), "error_code": "", **updates}))
    os.chmod(path, 0o600)


def save(client, **updates):
    service = client.app.state.settings
    revision = service.read()[0]
    patch = SettingsPatch(revision=revision, **updates)
    return service.save(patch, service.candidate(patch))


def test_credentials_encrypted_and_blank_preserves(managed):
    result = save(managed, telegram_api_id="1234", telegram_api_hash=HASH)
    assert result["configured"]["telegram_api_hash"]
    assert result["values"]["telegram_api_hash"] == ""
    with managed.app.state.db.sessions() as session:
        assert HASH not in json.dumps(session.get(Settings, 1).values)
    runtime = managed.app.state.runtime
    runtime.local_api.update(phase="idle")
    save(managed, telegram_api_hash="", download_concurrency=4)
    assert managed.app.state.settings.read()[1].telegram_api_hash == HASH
    save(managed, clear_telegram_api_hash=True)
    assert not managed.app.state.settings.public()["configured"]["telegram_api_hash"]


def test_stale_status(managed):
    service = managed.app.state.runtime.local_api
    heartbeat(service, state="running", heartbeat_at=time.time() - 60)
    assert service.status()["state"] == "unavailable"
    with pytest.raises(AppError) as exc:
        service.action(True)
    assert exc.value.status == 503


def test_retention_changes_preserve_operation_and_connected_client(managed):
    save(managed, bot_token="123456:synthetic-never-real-token", allowed_user_ids=[42])
    runtime = managed.app.state.runtime
    runtime.local_api.update(phase="idle", applied_mode="cloud")
    _, configured, _ = runtime.settings.read()
    client = Mock(close=AsyncMock())
    runtime.client = client
    runtime.client_signature = runtime.settings.client_signature(configured)
    original_operation = runtime.local_api.read()
    save(managed, diagnostic_log_retention_days=2)
    assert runtime.local_api.read() == original_operation
    asyncio.run(runtime.ensure_client())
    assert runtime.client is client
    client.close.assert_not_awaited()


def test_external_compatibility(client):
    assert client.app.state.runtime.local_api.status()["managed"] is False


def test_persisted_logout_intent_and_explicit_recovery(managed):
    service = managed.app.state.runtime.local_api
    service.update(phase="logging_out", target_mode="local", cloud_available_at=time.time() + 600)
    reloaded = LocalApiService(managed.app.state.settings)
    assert reloaded.read()["phase"] == "logout_uncertain"
    assert reloaded.status()["can_restart"] is False
    with pytest.raises(AppError) as exc:
        reloaded.action(False)
    assert exc.value.code == "LOCAL_API_LOGOUT_UNCERTAIN"
    reloaded.action(False, True)
    assert reloaded.read()["logout_done"] is True
    assert reloaded.read()["cloud_available_at"] > time.time()


async def test_logout_only_once_then_ack_start_and_verify(managed, monkeypatch):
    save(managed, bot_token="123:abc", allowed_user_ids=[42], bot_api_mode="local",
         telegram_api_id="1234", telegram_api_hash=HASH)
    runtime = managed.app.state.runtime
    service = runtime.local_api
    identity = AsyncMock(return_value={"id": 123, "username": "test"})
    logout = AsyncMock(return_value=True)
    monkeypatch.setattr(TelegramClient, "identity", identity)
    monkeypatch.setattr(TelegramClient, "logout", logout)
    assert await service.reconcile(runtime)
    desired = json.loads((service.config.local_api_control_dir / "desired.json").read_text())
    assert desired["action"] == "start" and HASH not in json.dumps(desired)
    assert logout.await_count == 1
    assert await service.reconcile(runtime)
    assert logout.await_count == 1
    heartbeat(service, command_id=desired["command_id"], state="running", generation="new")
    assert not await service.reconcile(runtime)
    assert service.read()["applied_mode"] == "local"
    assert service.read()["cloud_available_at"] > time.time()
    assert identity.await_count == 2


async def test_ambiguous_logout_never_replays_or_cloud_reconnects(managed, monkeypatch):
    save(managed, bot_token="123:abc", allowed_user_ids=[42], bot_api_mode="local",
         telegram_api_id="1234", telegram_api_hash=HASH)
    runtime = managed.app.state.runtime
    monkeypatch.setattr(TelegramClient, "identity", AsyncMock(return_value={"id": 123, "username": "test"}))
    logout = AsyncMock(side_effect=TimeoutError())
    monkeypatch.setattr(TelegramClient, "logout", logout)
    assert await runtime.local_api.reconcile(runtime)
    assert runtime.local_api.read()["phase"] == "logout_uncertain"
    assert await runtime.local_api.reconcile(runtime)
    assert logout.await_count == 1
    assert not (runtime.config.local_api_control_dir / "desired.json").exists()
    with pytest.raises(AppError):
        runtime.local_api.validate_allowed(managed.app.state.settings.read()[1])


async def test_drain_joins_poll_and_preserves_owner(managed, monkeypatch):
    save(managed, bot_api_mode="local", telegram_api_id="1234", telegram_api_hash=HASH)
    runtime = managed.app.state.runtime
    logout = AsyncMock(return_value=True)
    monkeypatch.setattr(TelegramClient, "logout", logout)
    release = asyncio.Event()
    owner = asyncio.create_task(release.wait())
    runtime.running["owner"] = owner
    assert await runtime.local_api.reconcile(runtime)
    logout.assert_not_awaited()
    assert not owner.cancelled()
    release.set()
    await owner
    async with runtime.poll_lock:
        step = asyncio.create_task(runtime.local_api.reconcile(runtime))
        await asyncio.sleep(0.01)
        assert not step.done()
        logout.assert_not_awaited()
    await step


def test_claim_barrier_and_candidate_validation(managed):
    runtime = managed.app.state.runtime
    save(managed, allowed_user_ids=[42])
    runtime.ingest.message(123, message(), "test")
    confirm_all(managed.app.state)
    save(managed, bot_api_mode="local", telegram_api_id="1234", telegram_api_hash=HASH)
    assert runtime.worker.claim(123, "cloud") is None
    assert runtime.worker.claim(123, "local") is None
    with pytest.raises(AppError):
        runtime.local_api.validate_allowed(managed.app.state.settings.read()[1])
    runtime.local_api.update(phase="idle", applied_mode="cloud", cloud_available_at=time.time() + 600)
    candidate = managed.app.state.settings.read()[1].model_copy(update={"bot_api_mode": "cloud"})
    with pytest.raises(AppError) as exc:
        runtime.local_api.validate_allowed(candidate)
    assert exc.value.code == "LOCAL_API_COOLDOWN"
    runtime.local_api.update(phase="idle", applied_mode="local", cloud_available_at=0)
    assert runtime.worker.claim(123, "local") is not None


async def test_restart_waits_for_owners_and_blocked_generation(managed):
    runtime = managed.app.state.runtime
    save(managed, bot_api_mode="local", telegram_api_id="1234", telegram_api_hash=HASH)
    runtime.local_api.update(phase="idle", applied_mode="local", snapshot=None)
    runtime.local_api.action(True)
    runtime.counts = Mock(return_value=(1, 1, 0))
    runtime.recover_generation = Mock()
    assert await runtime.local_api.reconcile(runtime)
    desired = json.loads((runtime.config.local_api_control_dir / "desired.json").read_text())
    heartbeat(runtime.local_api, command_id=desired["command_id"], state="running", generation="new")
    assert await runtime.local_api.reconcile(runtime)
    runtime.recover_generation.assert_called_once()
    assert runtime.local_api.read()["phase"] != "idle"


def test_unrelated_save_does_not_restart_and_environment_fallback(managed, monkeypatch):
    monkeypatch.setenv("TELEGRAM_API_ID", "1234")
    monkeypatch.setenv("TELEGRAM_API_HASH", HASH)
    save(managed, download_concurrency=4)
    assert managed.app.state.runtime.local_api.read()["phase"] == "idle"
    assert managed.app.state.settings.public()["editable"]["telegram_api_hash"]
    save(managed, telegram_api_hash="b" * 32)
    assert managed.app.state.settings.read()[1].telegram_api_hash == "b" * 32


async def test_missing_credentials_can_be_fixed_without_redeploy(managed, monkeypatch):
    save(managed, bot_token="123:abc", allowed_user_ids=[42], bot_api_mode="local")
    runtime = managed.app.state.runtime
    logout = AsyncMock(return_value=True)
    monkeypatch.setattr(TelegramClient, "logout", logout)
    monkeypatch.setattr(TelegramClient, "identity", AsyncMock(return_value={"id": 123, "username": "test"}))
    assert await runtime.local_api.reconcile(runtime)
    assert runtime.local_api.read()["phase"] == "failed"
    logout.assert_not_awaited()
    save(managed, telegram_api_id="1234", telegram_api_hash=HASH)
    assert await runtime.local_api.reconcile(runtime)
    logout.assert_awaited_once()


async def test_preflight_failure_never_becomes_ambiguous_logout(managed, monkeypatch):
    save(managed, bot_token="123:abc", allowed_user_ids=[42], bot_api_mode="local",
         telegram_api_id="1234", telegram_api_hash=HASH)
    runtime = managed.app.state.runtime
    logout = AsyncMock()
    monkeypatch.setattr(TelegramClient, "logout", logout)
    monkeypatch.setattr(TelegramClient, "identity", AsyncMock(side_effect=TimeoutError()))
    assert await runtime.local_api.reconcile(runtime)
    assert runtime.local_api.read()["phase"] == "failed"
    assert not runtime.local_api.read().get("logout_uncertain")
    logout.assert_not_awaited()


async def test_invalid_control_key_fails_before_cloud_logout(managed, monkeypatch):
    save(managed, bot_token="123:abc", allowed_user_ids=[42], bot_api_mode="local",
         telegram_api_id="1234", telegram_api_hash=HASH)
    runtime = managed.app.state.runtime
    key = runtime.config.local_api_control_dir / "control.key"
    key.write_bytes(b"broken")
    key.chmod(0o600)
    identity, logout = AsyncMock(), AsyncMock()
    monkeypatch.setattr(TelegramClient, "identity", identity)
    monkeypatch.setattr(TelegramClient, "logout", logout)
    assert await runtime.local_api.reconcile(runtime)
    assert runtime.local_api.read()["phase"] == "failed"
    logout.assert_not_awaited()
    identity.assert_not_awaited()


async def test_cloud_cooldown_survives_service_restart_and_no_probe(managed, monkeypatch):
    runtime = managed.app.state.runtime
    service = runtime.local_api
    save(managed, bot_api_mode="local")
    service.update(phase="idle", applied_mode="local", cloud_available_at=time.time() + 600, logout_done=True)
    save(managed, bot_api_mode="cloud")
    identity = AsyncMock()
    monkeypatch.setattr(TelegramClient, "identity", identity)
    runtime.local_api = LocalApiService(managed.app.state.settings)
    assert await runtime.local_api.reconcile(runtime)
    assert runtime.local_api.read()["phase"] == "cooldown"
    identity.assert_not_awaited()


async def test_ensure_client_never_connects_desired_before_transition(managed, monkeypatch):
    save(managed, bot_token="123:abc", allowed_user_ids=[42], bot_api_mode="local",
         telegram_api_id="1234", telegram_api_hash=HASH)
    identity = AsyncMock()
    monkeypatch.setattr(TelegramClient, "identity", identity)
    runtime = managed.app.state.runtime
    await runtime.ensure_client()
    identity.assert_not_awaited()


def test_postlogout_token_replacement_is_blocked(managed):
    save(managed, bot_token="123:abc", allowed_user_ids=[42], bot_api_mode="local",
         telegram_api_id="1234", telegram_api_hash=HASH)
    managed.app.state.runtime.local_api.update(phase="failed", logout_done=True)
    with pytest.raises(AppError) as exc:
        save(managed, bot_token="999:replacement")
    assert exc.value.code == "LOCAL_API_BUSY"


def test_control_endpoints_require_auth_and_csrf(managed):
    assert managed.post("/api/v1/system/local-api/restart").status_code == 401
    bootstrap = (managed.app.state.runtime.config.data_dir / "bootstrap.key").read_text()
    assert managed.post("/api/v1/setup", json={"bootstrap_secret": bootstrap, "username": "admin", "password": "123456"}).status_code == 200
    response = managed.post("/api/v1/auth/login", json={"username": "admin", "password": "123456"})
    assert managed.post("/api/v1/system/local-api/retry").status_code == 403
    managed.headers["X-CSRF-Token"] = response.json()["csrf_token"]
    response = managed.get("/api/v1/system/status")
    assert response.status_code == 200
    assert response.json()["local_api"]["managed"] is True
    assert managed.post("/api/v1/system/local-api/retry").status_code == 202


async def test_switch_waits_for_poll_and_notification_durable_results(managed, monkeypatch):
    save(managed, bot_token="123:abc", allowed_user_ids=[42], telegram_api_id="1234", telegram_api_hash=HASH)
    runtime = managed.app.state.runtime
    runtime.local_api.update(phase="idle", applied_mode="cloud")
    poll_entered, send_entered, release_poll, release_send = (asyncio.Event() for _ in range(4))

    async def updates(offset):
        poll_entered.set()
        await release_poll.wait()
        return [{"update_id": 10, "message": message()}]

    async def send(*args):
        send_entered.set()
        await release_send.wait()
        return 88

    fake = Mock(updates=updates, send=send, close=AsyncMock())
    runtime.client, runtime.bot_id = fake, 123
    runtime.client_signature = runtime.settings.client_signature(runtime.settings.read()[1])
    runtime.bot_status = {"state": "online", "username": "test", "message": ""}
    runtime.ensure_client = AsyncMock()
    with runtime.db.transaction() as session:
        session.add(Notification(id="notice", event_key="notice", chat_id=42, payload={"bot_id": 123, "text": "hello"}))
    monkeypatch.setattr(TelegramClient, "identity", AsyncMock(return_value={"id": 123, "username": "test"}))
    logout = AsyncMock(return_value=True)
    monkeypatch.setattr(TelegramClient, "logout", logout)
    consumers = [asyncio.create_task(runtime.poll_loop()), asyncio.create_task(runtime.notify_loop())]
    try:
        await asyncio.wait_for(asyncio.gather(poll_entered.wait(), send_entered.wait()), 3)
        save(managed, bot_api_mode="local")
        step = asyncio.create_task(runtime.local_api.reconcile(runtime))
        await asyncio.sleep(0.01)
        assert not step.done()
        logout.assert_not_awaited()
        release_poll.set()
        await asyncio.sleep(0.01)
        logout.assert_not_awaited()
        release_send.set()
        assert await asyncio.wait_for(step, 3)
        logout.assert_awaited_once()
        with runtime.db.sessions() as session:
            assert session.scalar(select(IngestUpdate)).update_id == 10
            notice = session.get(Notification, "notice")
            assert (notice.state, notice.message_id) == ("sent", 88)
        assert runtime.ingest.offset(123) == 11
    finally:
        runtime.stopping = True
        for task in consumers:
            task.cancel()
        await asyncio.gather(*consumers, return_exceptions=True)
