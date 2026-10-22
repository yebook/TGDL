"""Cloud proxy changes reuse the connection barrier without local API commands."""
import asyncio
import json
import threading
import time
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from app.bot.client import TelegramClient
from app.core.errors import AppError
from app.db.models import BotIdentity
from app.main import create_app
from app.schemas.contracts import SettingsPatch
from tests.test_pipeline import confirm_all, message


@pytest.fixture(params=[False, True], ids=["external", "managed"])
def deployment(config, request):
    config.managed_local_api = request.param
    config.local_api_control_dir = config.data_dir / "control"
    with TestClient(create_app(config)) as client:
        yield client.app.state


def save(state, **updates):
    patch = SettingsPatch(revision=state.settings.read()[0], **updates)
    return state.settings.save(patch, state.settings.candidate(patch))


async def configure(state, monkeypatch):
    identity = AsyncMock(return_value={"id": 7, "username": "offline"})
    monkeypatch.setattr(TelegramClient, "identity", identity)
    save(state, bot_token="123:synthetic-offline", allowed_user_ids=[42])
    await state.runtime.ensure_client()
    return identity


async def test_cloud_proxy_serializes_saves_and_drains_without_supervisor(deployment, monkeypatch):
    state, runtime = deployment, deployment.runtime
    identity = await configure(state, monkeypatch)
    logout = AsyncMock(side_effect=AssertionError("Cloud reconnect must never log out"))
    monkeypatch.setattr(TelegramClient, "logout", logout)
    monkeypatch.setattr(runtime.local_api, "command", lambda *args: pytest.fail("No supervisor command"))
    runtime.ingest.message(7, message(), "queued")
    confirm_all(state)
    saved = save(state, proxy={"enabled": True, "host": "proxy.lan"})
    operation = runtime.local_api.read()
    assert operation["action"] == "cloud_reconnect" and operation["phase"] == "draining"
    with pytest.raises(AppError, match="正在应用"):
        runtime.local_api.validate_allowed(state.settings.read()[1])
    with pytest.raises(AppError):
        runtime.local_api.recovery_candidate(state.settings.read()[1])
    assert runtime.worker.claim(7, "cloud") is None
    with pytest.raises(AppError) as error:
        save(state, proxy={"enabled": False})
    assert error.value.code == "LOCAL_API_BUSY" and error.value.status == 409
    assert state.settings.read()[0] == saved["revision"]
    save(state, download_concurrency=4)
    assert runtime.local_api.read() == operation
    release = asyncio.Event()
    owner = asyncio.create_task(release.wait())
    runtime.running["existing"] = owner
    assert await runtime.local_api.reconcile(runtime)
    assert not owner.cancelled() and identity.await_count == 1
    release.set()
    await owner
    assert not await runtime.local_api.reconcile(runtime)
    assert runtime.local_api.read()["phase"] == "idle"
    assert runtime.bot_status["state"] == "online"
    assert state.settings.public()["proxy_status"]["state"] == "active"
    assert state.settings.read()[1].download_concurrency == 4
    assert identity.await_count == 2
    assert not await runtime.local_api.reconcile(runtime)
    assert identity.await_count == 2
    logout.assert_not_awaited()
    assert not (runtime.config.local_api_control_dir / "desired.json").exists()


async def test_disabled_proxy_edits_do_not_reconnect(deployment, monkeypatch):
    state = deployment
    identity = await configure(state, monkeypatch)
    original, signature = state.runtime.client, state.runtime.client_signature
    save(state, proxy={"enabled": False, "scheme": "http", "host": "unused.lan", "password": "unused"})
    await state.runtime.ensure_client()
    assert state.runtime.client is original and state.runtime.client_signature == signature
    assert state.runtime.local_api.read()["phase"] == "idle"
    identity.assert_awaited_once()


@pytest.mark.parametrize("proxy", [{"host": "unused.lan"}, {"enabled": True, "host": "proxy.lan"}])
def test_environment_proxy_defaults_do_not_schedule_unrelated_save(deployment, monkeypatch, proxy):
    monkeypatch.setenv("TGD_PROXY", json.dumps(proxy))
    save(deployment, download_concurrency=4)
    assert deployment.runtime.local_api.read()["phase"] == "idle"


async def test_failed_cloud_reconnect_can_retry_without_management_component(deployment, monkeypatch):
    state, runtime = deployment, deployment.runtime
    await configure(state, monkeypatch)
    save(state, proxy={"enabled": True, "host": "proxy.lan"})
    monkeypatch.setattr(TelegramClient, "identity", AsyncMock(side_effect=RuntimeError("offline")))
    assert await runtime.local_api.reconcile(runtime)
    assert runtime.local_api.read()["phase"] == "failed"
    assert runtime.local_api.status()["can_retry"]
    assert runtime.local_api.recovery_candidate(state.settings.read()[1]).bot_api_mode == "cloud"
    runtime.local_api.action(False)
    identity = AsyncMock(return_value={"id": 7, "username": "offline"})
    monkeypatch.setattr(TelegramClient, "identity", identity)
    assert not await runtime.local_api.reconcile(runtime)
    assert runtime.local_api.read()["phase"] == "idle"
    identity.assert_awaited_once()


@pytest.mark.parametrize("supersede", ["configuration", "operation"])
async def test_late_identity_does_not_publish_old_connection(deployment, monkeypatch, supersede):
    state, runtime = deployment, deployment.runtime
    save(state, bot_token="123:synthetic-offline", allowed_user_ids=[42])
    entered, release = asyncio.Event(), asyncio.Event()

    async def identity(client):
        entered.set()
        await release.wait()
        return {"id": 7, "username": "stale"}

    close = AsyncMock()
    monkeypatch.setattr(TelegramClient, "identity", identity)
    monkeypatch.setattr(TelegramClient, "close", close)
    connecting = asyncio.create_task(runtime.ensure_client())
    await entered.wait()
    if supersede == "configuration":
        save(state, proxy={"enabled": True, "host": "new-proxy.lan"})
    else:
        runtime.local_api.update(phase="draining", operation_id="new-operation", action="cloud_reconnect")
    release.set()
    await connecting
    assert runtime.client is None and runtime.bot_status["state"] != "online"
    assert state.settings.cloud_applied_signature == ""
    close.assert_awaited_once()
    with state.db.sessions() as session:
        assert session.get(BotIdentity, 7) is None


async def test_cloud_proxy_without_bot_finishes_without_network(deployment, monkeypatch):
    identity = AsyncMock(side_effect=AssertionError("No Bot configured"))
    monkeypatch.setattr(TelegramClient, "identity", identity)
    save(deployment, proxy={"enabled": True, "host": "proxy.lan"})
    assert not await deployment.runtime.local_api.reconcile(deployment.runtime)
    assert deployment.runtime.local_api.read()["phase"] == "idle"
    identity.assert_not_awaited()


async def test_unrelated_save_during_identity_keeps_current_attempt(deployment, monkeypatch):
    state, runtime = deployment, deployment.runtime
    save(state, bot_token="123:synthetic-offline", allowed_user_ids=[42])
    entered, release = asyncio.Event(), asyncio.Event()

    async def identity(client):
        entered.set()
        await release.wait()
        return {"id": 7, "username": "current"}

    monkeypatch.setattr(TelegramClient, "identity", identity)
    connecting = asyncio.create_task(runtime.ensure_client())
    await entered.wait()
    save(state, download_concurrency=5)
    release.set()
    await connecting
    assert runtime.bot_status["state"] == "online"
    assert state.settings.read()[1].download_concurrency == 5


async def test_cloud_reconnect_preserves_cloud_cooldown(deployment, monkeypatch):
    state, runtime = deployment, deployment.runtime
    identity = await configure(state, monkeypatch)
    save(state, proxy={"enabled": True, "host": "proxy.lan"})
    deadline = time.time() + 600
    runtime.local_api.update(cloud_available_at=deadline)
    assert await runtime.local_api.reconcile(runtime)
    assert runtime.local_api.read()["phase"] == "cooldown"
    assert runtime.local_api.read()["cloud_available_at"] == deadline
    await runtime.ensure_client()
    identity.assert_awaited_once()
    assert not (runtime.config.local_api_control_dir / "desired.json").exists()


async def test_final_identity_publication_and_save_cannot_interleave(deployment, monkeypatch):
    state, runtime = deployment, deployment.runtime
    save(state, bot_token="123:synthetic-offline", allowed_user_ids=[42])
    values = state.settings.read()[1]
    client = TelegramClient(values.bot_token, runtime.config, values)
    entered, release = threading.Event(), threading.Event()
    original = runtime.remember_identity

    def registration(identity):
        entered.set()
        assert release.wait(3)
        original(identity)

    monkeypatch.setattr(runtime, "remember_identity", registration)
    publishing = asyncio.create_task(asyncio.to_thread(
        runtime.publish_client, client, {"id": 7, "username": "offline"}, values, "", runtime.local_api.read()))
    try:
        assert await asyncio.to_thread(entered.wait, 3)
        saving = asyncio.create_task(asyncio.to_thread(save, state, download_concurrency=5))
        await asyncio.sleep(0.02)
        assert not saving.done()
    finally:
        release.set()
    assert await publishing
    await saving
    assert runtime.client is client and runtime.bot_status["state"] == "online"
    assert state.settings.read()[1].download_concurrency == 5
