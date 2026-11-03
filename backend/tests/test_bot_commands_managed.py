from unittest.mock import AsyncMock

import pytest
import test_local_api as local_api_tests
from sqlalchemy import select
from test_bot_commands import TOKEN, seed
from test_local_api import HASH, heartbeat, save

from app.bot.client import TelegramClient
from app.db.models import MediaRequest, Notification

managed = local_api_tests.managed


def ready_local(runtime, generation="current-child"):
    runtime.local_api.update(phase="idle", applied_mode="local", command_id=None,
                             applied_command_id="verified-command", applied_credential_revision=1)
    heartbeat(runtime.local_api, command_id="verified-command", credential_revision=1,
              proxy_signature=runtime.settings.proxy_signature(runtime.settings.read()[1]),
              state="running", generation=generation)


@pytest.mark.parametrize("mode", ["cloud", "local"])
@pytest.mark.parametrize("protection", ["cooldown", "auth_paused"])
async def test_current_managed_identity_consumes_inbox_during_protection_without_remote_call(
        managed, monkeypatch, mode, protection):
    state, runtime = managed.app.state, managed.app.state.runtime
    save(managed, bot_token=TOKEN, allowed_user_ids=[42], bot_api_mode=mode,
         telegram_api_id="1234", telegram_api_hash=HASH)
    runtime.local_api.update(phase="idle", applied_mode=mode)
    if mode == "local":
        ready_local(runtime)
    monkeypatch.setattr(TelegramClient, "identity", AsyncMock(return_value={"id": 7, "username": "fixture_bot"}))
    assert await runtime.ensure_client()
    telegram = runtime.client
    target = seed(state)
    runtime.ingest.persist_batch(7, [{"update_id": 100, "message": {
        "message_id": 100, "from": {"id": 42}, "chat": {"id": 42, "type": "private"}, "text": "/cancel_all"}}])
    runtime.safety.observe(TOKEN, None, retry_after=600 if protection == "cooldown" else None,
                           unauthorized=protection == "auth_paused")
    remote = AsyncMock(side_effect=AssertionError("protection must prevent remote calls"))
    monkeypatch.setattr(telegram.bot.session, "make_request", remote)

    async def stop_after_defer(delay):
        runtime.stopping = True

    monkeypatch.setattr("app.workers.runtime.asyncio.sleep", stop_after_defer)
    await runtime.poll_loop()
    if runtime.menu_task:
        await runtime.menu_task
    assert runtime.ingest.pending(7) == []
    with state.db.sessions() as session:
        assert session.get(MediaRequest, target).status == "cancelled"
        assert session.scalar(select(Notification.id).where(Notification.event_key.like("reply:%")))
    remote.assert_not_awaited()
    assert runtime.safety.status(TOKEN)["state"] == ("throttled" if protection == "cooldown" else "auth_paused")


@pytest.mark.parametrize("stale", ["signature", "generation"])
async def test_stale_managed_identity_keeps_pending_command_for_reverification(managed, monkeypatch, stale):
    state, runtime = managed.app.state, managed.app.state.runtime
    save(managed, bot_token=TOKEN, allowed_user_ids=[42], bot_api_mode="local",
         telegram_api_id="1234", telegram_api_hash=HASH)
    ready_local(runtime)
    monkeypatch.setattr(TelegramClient, "identity", AsyncMock(return_value={"id": 7, "username": "fixture_bot"}))
    assert await runtime.ensure_client()
    target = seed(state)
    runtime.ingest.persist_batch(7, [{"update_id": 100, "message": {
        "message_id": 100, "from": {"id": 42}, "chat": {"id": 42, "type": "private"}, "text": "/cancel_all"}}])
    runtime.safety.observe(TOKEN, None, retry_after=600)
    if stale == "signature":
        runtime.client_signature = "old-signature"
    else:
        ready_local(runtime, "replacement-child")

    async def stop_wait(delay):
        runtime.stopping = True

    monkeypatch.setattr("app.workers.runtime.asyncio.sleep", stop_wait)
    await runtime.poll_loop()
    assert len(runtime.ingest.pending(7)) == 1
    with state.db.sessions() as session:
        assert session.get(MediaRequest, target).status == "queued"
