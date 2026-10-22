"""Connection watchdogs fail recoverably without discarding existing ownership."""
import asyncio
import time
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from app.bot.client import TelegramClient
from app.core.errors import AppError
from app.main import create_app
from app.services import connection_waits as waits
from app.services.local_api import LocalApiService
from tests.test_cloud_reconnect import configure, save
from tests.test_cloud_reconnect import deployment as cloud_deployment_fixture
from tests.test_local_api import HASH, heartbeat

deployment = cloud_deployment_fixture


async def test_hung_identity_is_joined_before_recoverable_failure(deployment, monkeypatch):
    state, runtime = deployment, deployment.runtime
    await configure(state, monkeypatch)
    save(state, proxy={"enabled": True, "host": "proxy.lan"})
    cancelled = asyncio.Event()

    async def hang(client):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    monkeypatch.setattr(TelegramClient, "identity", hang)
    monkeypatch.setattr(waits, "IDENTITY_TIMEOUT", 0.02)
    assert await runtime.local_api.reconcile(runtime)
    assert cancelled.is_set()
    assert runtime.local_api.read()["phase"] == "failed"
    assert runtime.local_api.read()["error_code"] == "LOCAL_API_OPERATION_TIMEOUT"
    assert runtime.local_api.status()["can_retry"]
    runtime.local_api.action(False)
    assert runtime.local_api.read()["verification_started_at"] is None
    monkeypatch.setattr(TelegramClient, "identity", AsyncMock(return_value={"id": 7, "username": "offline"}))
    assert not await runtime.local_api.reconcile(runtime)


async def test_stuck_old_close_keeps_client_until_safe_retry(deployment, monkeypatch):
    state, runtime = deployment, deployment.runtime
    await configure(state, monkeypatch)
    old = runtime.client
    save(state, proxy={"enabled": True, "host": "proxy.lan"})
    original = TelegramClient.close
    cancelled = asyncio.Event()

    async def close(client):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    monkeypatch.setattr(TelegramClient, "close", close)
    monkeypatch.setattr(waits, "CLOSE_TIMEOUT", 0.02)
    assert await runtime.local_api.reconcile(runtime)
    assert cancelled.is_set() and runtime.client is old
    assert runtime.local_api.read()["phase"] == "failed"
    monkeypatch.setattr(TelegramClient, "close", original)
    runtime.local_api.action(False)
    assert not await runtime.local_api.reconcile(runtime)


@pytest.mark.parametrize("consumer", ["poll_lock", "notify_lock"])
async def test_consumer_timeout_keeps_owner_and_retry_restarts_clock(deployment, monkeypatch, consumer):
    state, runtime = deployment, deployment.runtime
    await configure(state, monkeypatch)
    save(state, proxy={"enabled": True, "host": "proxy.lan"})
    monkeypatch.setattr(waits, "BARRIER_POLL_TIMEOUT", 0.01)
    lock = getattr(runtime, consumer)
    async with lock:
        assert await runtime.local_api.reconcile(runtime)
        assert runtime.local_api.read()["drain_wait_started_at"] is not None
        runtime.local_api.update(drain_wait_started_at=time.time() - waits.BARRIER_TIMEOUT - 1)
        assert await runtime.local_api.reconcile(runtime)
        assert lock.locked()
        assert runtime.local_api.read()["phase"] == "failed"
        assert runtime.local_api.read()["error_code"] == "LOCAL_API_DRAIN_TIMEOUT"
        with pytest.raises(AppError):
            runtime.local_api.recovery_candidate(state.settings.read()[1])
        runtime.local_api.action(False)
        assert runtime.local_api.read()["drain_wait_started_at"] is None
        assert await runtime.local_api.reconcile(runtime)
        assert runtime.local_api.read()["phase"] == "draining"
    assert not await runtime.local_api.reconcile(runtime)


async def test_long_download_wait_is_not_connection_watchdog_time(deployment, monkeypatch):
    state, runtime = deployment, deployment.runtime
    await configure(state, monkeypatch)
    save(state, proxy={"enabled": True, "host": "proxy.lan"})
    release = asyncio.Event()
    owner = asyncio.create_task(release.wait())
    runtime.running["long-download"] = owner
    runtime.local_api.update(drain_wait_started_at=time.time() - 86400, verification_started_at=time.time() - 86400)
    assert await runtime.local_api.reconcile(runtime)
    assert not owner.cancelled()
    assert runtime.local_api.read()["phase"] == "draining"
    assert runtime.local_api.read()["verification_started_at"] is None
    release.set()
    await owner
    assert not await runtime.local_api.reconcile(runtime)


async def test_verification_watchdog_survives_restart_but_excludes_protection_waits(deployment, monkeypatch):
    state, runtime = deployment, deployment.runtime
    await configure(state, monkeypatch)
    save(state, proxy={"enabled": True, "host": "proxy.lan"})
    runtime.local_api.update(phase="verifying", verification_started_at=time.time() - waits.VERIFY_TIMEOUT - 1)
    runtime.local_api = LocalApiService(state.settings)
    identity = AsyncMock()
    monkeypatch.setattr(TelegramClient, "identity", identity)
    assert await runtime.local_api.reconcile(runtime)
    assert runtime.local_api.read()["phase"] == "failed"
    assert runtime.local_api.read()["error_code"] == "LOCAL_API_VERIFY_TIMEOUT"
    identity.assert_not_awaited()
    runtime.local_api.action(False)
    runtime.local_api.update(verification_started_at=time.time() - waits.VERIFY_TIMEOUT - 1)
    monkeypatch.setattr(runtime.safety, "status", lambda token: {
        "state": "backoff", "retry_at": "2099-01-01T00:00:00Z", "message": "waiting", "can_retry": False})
    assert await runtime.local_api.reconcile(runtime)
    assert runtime.local_api.read()["phase"] == "verifying"
    assert runtime.local_api.read()["verification_started_at"] > time.time() - 10
    identity.assert_not_awaited()


@pytest.fixture
def native(config):
    config.managed_local_api = True
    config.local_api_control_dir = config.data_dir / "control"
    with TestClient(create_app(config)) as client:
        heartbeat(client.app.state.runtime.local_api)
        yield client.app.state


@pytest.mark.parametrize("cleanup", ["hang", "raise"])
async def test_logout_timeout_and_cleanup_never_replay_or_clear_uncertainty(native, monkeypatch, cleanup):
    state, runtime = native, native.runtime
    save(state, bot_token="123:synthetic-offline", allowed_user_ids=[42], bot_api_mode="local",
         telegram_api_id="1234", telegram_api_hash=HASH)
    monkeypatch.setattr(TelegramClient, "identity", AsyncMock(return_value={"id": 7, "username": "offline"}))
    cancelled = asyncio.Event()

    async def logout(client):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    async def close(client):
        if cleanup == "raise":
            raise RuntimeError("synthetic cleanup failure")
        await asyncio.Event().wait()

    logout_call = AsyncMock(side_effect=logout)
    # Class attributes that are AsyncMock do not bind self.
    async def hanging_logout():
        return await logout(None)
    logout_call.side_effect = hanging_logout
    monkeypatch.setattr(TelegramClient, "logout", logout_call)
    monkeypatch.setattr(TelegramClient, "close", close)
    monkeypatch.setattr(waits, "LOGOUT_TIMEOUT", 0.02)
    monkeypatch.setattr(waits, "CLOSE_TIMEOUT", 0.02)
    assert await runtime.local_api.reconcile(runtime)
    assert cancelled.is_set()
    assert runtime.local_api.read()["phase"] == "logout_uncertain"
    assert runtime.local_api.read()["logout_uncertain"]
    assert runtime.local_api.read()["cloud_available_at"] > time.time()
    with pytest.raises(AppError):
        runtime.local_api.action(False)
    assert await runtime.local_api.reconcile(runtime)
    logout_call.assert_awaited_once()


async def test_cancelled_logout_same_process_next_tick_is_uncertain(native, monkeypatch):
    state, runtime = native, native.runtime
    save(state, bot_token="123:synthetic-offline", allowed_user_ids=[42], bot_api_mode="local",
         telegram_api_id="1234", telegram_api_hash=HASH)
    monkeypatch.setattr(TelegramClient, "identity", AsyncMock(return_value={"id": 7, "username": "offline"}))
    entered = asyncio.Event()

    async def logout():
        entered.set()
        await asyncio.Event().wait()

    logout_call = AsyncMock(side_effect=logout)
    monkeypatch.setattr(TelegramClient, "logout", logout_call)
    operation = asyncio.create_task(runtime.local_api.reconcile(runtime))
    await entered.wait()
    operation.cancel()
    with pytest.raises(asyncio.CancelledError):
        await operation
    assert runtime.local_api.read()["logout_uncertain"]
    assert await runtime.local_api.reconcile(runtime)
    assert runtime.local_api.read()["phase"] == "logout_uncertain"
    logout_call.assert_awaited_once()
