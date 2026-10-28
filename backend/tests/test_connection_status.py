import asyncio
from unittest.mock import AsyncMock

import pytest
import test_local_api as local_api_tests
from test_local_api import HASH, heartbeat, save

from app.bot.client import TelegramClient
from app.core.errors import AppError
from app.schemas.contracts import SettingsPatch

managed = local_api_tests.managed


def configure(state, token="123456789:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"):
    patch = SettingsPatch(revision=state.settings.read()[0], bot_token=token, bot_api_mode="cloud", allowed_user_ids=[42])
    state.settings.save(patch, state.settings.candidate(patch))
    return state.settings.read()[1]


def test_http_status_projects_current_connection_and_hides_replaced_token(authenticated, monkeypatch):
    state = authenticated.app.state
    runtime = state.runtime
    configure(state)
    monkeypatch.setattr(TelegramClient, "identity", AsyncMock(return_value={"id": 7, "username": "http_current_bot"}))
    asyncio.run(runtime.ensure_client())
    response = authenticated.get('/api/v1/system/status')
    assert response.status_code == 200
    status = response.json()
    assert status['bot']['state'] == 'online' and status['bot']['username'] == 'http_current_bot'
    assert status['local_api']['desired_mode'] == status['local_api']['applied_mode'] == 'cloud'
    assert status['proxy']['state'] == 'disabled'
    configure(state, "987654321:BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB")
    runtime.poll_result(runtime.client)
    status = authenticated.get('/api/v1/system/status').json()
    assert status['bot']['username'] == '' and status['bot']['state'] == 'connecting'
    asyncio.run(runtime.client.close())


@pytest.mark.asyncio
async def test_transient_poll_error_preserves_identity_but_needs_success_to_recover(client, monkeypatch):
    state = client.app.state
    runtime = state.runtime
    configure(state)
    identity = AsyncMock(return_value={"id": 7, "username": "current_bot"})
    monkeypatch.setattr(TelegramClient, "identity", identity)
    assert await runtime.ensure_client()
    verified = runtime.client
    runtime.poll_result(verified, error="暂时连接失败")
    assert runtime.connection_snapshot()[1] == {
        "state": "error", "username": "current_bot", "message": "暂时连接失败",
        "request_safety": runtime.safety.status(state.settings.read()[1].bot_token),
    }
    assert await runtime.ensure_client()
    assert runtime.bot_status["state"] == "error"  # Reuse itself is not recovery.
    runtime.poll_result(verified)
    assert runtime.connection_snapshot()[1]["state"] == "online"
    assert runtime.connection_snapshot()[1]["username"] == "current_bot"
    assert identity.await_count == 1
    await verified.close()


@pytest.mark.asyncio
async def test_token_change_hides_previous_identity_and_ignores_late_result(client, monkeypatch):
    state = client.app.state
    runtime = state.runtime
    configure(state)
    identity = AsyncMock(return_value={"id": 7, "username": "old_bot"})
    monkeypatch.setattr(TelegramClient, "identity", identity)
    await runtime.ensure_client()
    old = runtime.client
    configure(state, "987654321:BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB")
    assert runtime.connection_snapshot()[1]["username"] == ""
    runtime.poll_result(old)
    assert runtime.connection_snapshot()[1]["state"] == "connecting"
    identity.return_value = {"id": 8, "username": "new_bot"}
    await runtime.ensure_client()
    runtime.poll_result(old, error="旧连接失败")
    assert runtime.connection_snapshot()[1]["username"] == "new_bot"
    assert runtime.connection_snapshot()[1]["state"] == "online"
    await runtime.client.close()


@pytest.mark.asyncio
async def test_failed_current_identity_remains_visible_without_published_client(client, monkeypatch):
    state = client.app.state
    configure(state)
    monkeypatch.setattr(TelegramClient, "identity", AsyncMock(side_effect=AppError("BOT_BUSY", "不能更换 Bot 身份", 409)))
    with pytest.raises(AppError):
        await state.runtime.ensure_client()
    bot = state.runtime.connection_snapshot()[1]
    assert bot["state"] == "error" and bot["message"] == "不能更换 Bot 身份"
    assert bot["username"] == ""


@pytest.mark.asyncio
async def test_managed_poll_retries_error_and_recovers_without_new_identity(client, monkeypatch):
    runtime = client.app.state.runtime
    configure(client.app.state)
    monkeypatch.setattr(TelegramClient, "identity", AsyncMock(return_value={"id": 7, "username": "retry_bot"}))
    await runtime.ensure_client()
    runtime.config.managed_local_api = True
    runtime.local_api.update(phase="idle", applied_mode="cloud")
    runtime.poll_result(runtime.client, error="暂时连接失败")

    async def updates(_offset):
        runtime.stopping = True
        return []

    monkeypatch.setattr(runtime.client, "updates", updates)
    await runtime.poll_loop()
    assert runtime.bot_status == {"state": "online", "username": "retry_bot", "message": ""}
    await runtime.client.close()


@pytest.mark.asyncio
async def test_real_poll_app_error_recovers_identity_on_same_client(client, monkeypatch):
    state = client.app.state
    runtime = state.runtime
    configure(state)
    identity = AsyncMock(return_value={"id": 7, "username": "recover_bot"})
    monkeypatch.setattr(TelegramClient, "identity", identity)
    await runtime.ensure_client()
    original = runtime.client
    calls = 0

    async def updates(_offset):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise AppError("NETWORK_ERROR", "暂时连接失败", 503)
        assert runtime.bot_status["state"] == "error"
        runtime.stopping = True
        return []

    async def sleep(_delay):
        assert runtime.bot_status["username"] == "recover_bot"

    monkeypatch.setattr(runtime.client, "updates", updates)
    monkeypatch.setattr("app.workers.runtime.asyncio.sleep", sleep)
    await runtime.poll_loop()
    assert calls == 2 and runtime.client is original and identity.await_count == 1
    assert runtime.bot_status == {"state": "online", "username": "recover_bot", "message": ""}
    await original.close()


@pytest.mark.asyncio
async def test_managed_identity_requires_current_generation_command_and_proxy(managed, monkeypatch):
    state = managed.app.state
    runtime = state.runtime
    save(managed, bot_token="123456789:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA", bot_api_mode="local",
         telegram_api_id="1234", telegram_api_hash=HASH, allowed_user_ids=[42])
    runtime.local_api.update(phase="idle", applied_mode="local", command_id=None,
                             applied_command_id="current-command", applied_credential_revision=2)
    values = state.settings.read()[1]
    ack = {"state": "running", "generation": "generation-one", "command_id": "current-command",
           "credential_revision": 2, "proxy_signature": state.settings.proxy_signature(values)}
    heartbeat(runtime.local_api, **ack)
    monkeypatch.setattr(TelegramClient, "identity", AsyncMock(return_value={"id": 7, "username": "local_bot"}))
    assert await runtime.ensure_client()
    assert runtime.connection_snapshot()[1]["username"] == "local_bot"
    heartbeat(runtime.local_api, **(ack | {"command_id": "obsolete-command"}))
    assert runtime.connection_snapshot()[1]["username"] == ""
    heartbeat(runtime.local_api, **(ack | {"generation": "generation-two"}))
    assert runtime.connection_snapshot()[1]["state"] == "connecting"
    heartbeat(runtime.local_api, **ack)
    runtime.safety.observe(values.bot_token, None, unauthorized=True)
    bot = runtime.connection_snapshot()[1]
    assert bot["username"] == "local_bot" and bot["state"] == "error"
    assert bot["request_safety"]["state"] == "auth_paused"
    await runtime.client.close()
