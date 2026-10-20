import asyncio
import time
from unittest.mock import AsyncMock

import pytest
import test_local_api as local_api_tests
from fastapi.testclient import TestClient
from test_local_api import HASH, save
from test_pipeline import confirm_all, message, setup_pipeline

from app.bot.client import TelegramClient
from app.core.security import digest
from app.db.models import Job, Notification
from app.services.request_safety import SafetyDeferred

TOKEN = "123456789:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
managed = local_api_tests.managed


@pytest.mark.parametrize("failure", ["before_identity", "before_logout", "rejected_logout"])
async def test_managed_safety_wait_never_becomes_uncertain_logout(managed, monkeypatch, failure):
    save(managed, bot_token=TOKEN, allowed_user_ids=[42], bot_api_mode="local",
         telegram_api_id="1234", telegram_api_hash=HASH)
    runtime = managed.app.state.runtime
    remote_logout = AsyncMock(return_value=True)
    if failure == "before_identity":
        runtime.safety.observe(TOKEN, None, retry_after=600)
        # Real identity method must defer without reaching Bot transport.
        monkeypatch.setattr("aiogram.Bot.get_me", AsyncMock(side_effect=AssertionError("must not call Telegram")))
    else:
        monkeypatch.setattr(TelegramClient, "identity", AsyncMock(return_value={"id": 7, "username": "test"}))

    async def logout(self):
        if failure == "before_logout":
            # Simulate another in-flight call returning 429 after the preflight.
            runtime.safety.observe(TOKEN, None, retry_after=600)
            await self.safety.before(TOKEN)
        if failure == "rejected_logout":
            raise SafetyDeferred("Telegram rejected logout", sent=True)
        return await remote_logout()

    monkeypatch.setattr(TelegramClient, "logout", logout)
    assert await runtime.local_api.reconcile(runtime)
    state = runtime.local_api.read()
    assert state["phase"] == "draining"
    assert not state.get("logout_uncertain") and not state.get("logout_done")
    assert not (runtime.config.local_api_control_dir / "desired.json").exists()
    remote_logout.assert_not_awaited()


async def test_worker_deferral_keeps_job_retry_budget_and_frozen_path(client: TestClient):
    state, ingest = setup_pipeline(client)
    ingest.message(7, message(), "safety-ingest")
    request_id = confirm_all(state)
    job_id = state.runtime.worker.claim(7)
    with state.db.sessions() as session:
        job = session.get(Job, job_id)
        original = (job.relative_path, job.auto_retries, job.request_id)

    class DeferredClient:
        async def get_file(self, file_id):
            raise SafetyDeferred("not sent", time.time() + 600)

    await state.runtime.worker.run(DeferredClient(), job_id)
    with state.db.sessions() as session:
        job = session.get(Job, job_id)
        assert job.state == "retry_wait" and job.next_run_at > time.time() + 590
        assert (job.relative_path, job.auto_retries, job.request_id) == original
        assert job.request_id == request_id
        assert job.state != "recovery_blocked"


def test_notification_race_deferral_restores_attempt_budget(client: TestClient):
    runtime = client.app.state.runtime
    with runtime.db.transaction() as session:
        row = Notification(event_key="deferral", chat_id=42, payload={"text": "message"})
        session.add(row)
        session.flush()
        row_id = row.id
    assert runtime.next_notification(7)[0] == row_id
    runtime.notification_result(row_id, None, False, 600, True)
    with runtime.db.sessions() as session:
        row = session.get(Notification, row_id)
        assert row.attempts == 0 and row.state == "pending"
        assert row.next_run_at > time.time() + 590


async def test_paused_credential_does_not_interrupt_existing_local_owner(client: TestClient):
    runtime = client.app.state.runtime
    owner = asyncio.create_task(asyncio.sleep(0.05))
    runtime.running["existing-owner"] = owner
    runtime.safety.observe(TOKEN, None, unauthorized=True)
    runtime.worker.credential_hash = digest(TOKEN)
    assert runtime.worker.claim(7) is None
    assert not owner.cancelled()
    await owner
    assert owner.done() and not owner.cancelled()


async def test_managed_auth_pause_recovery_is_identity_only_and_keeps_switch_explicit(managed, monkeypatch):
    save(managed, bot_token=TOKEN, allowed_user_ids=[42], bot_api_mode="local",
         telegram_api_id="1234", telegram_api_hash=HASH)
    runtime = managed.app.state.runtime
    runtime.safety.observe(TOKEN, None, unauthorized=True)
    logout = AsyncMock(return_value=True)
    monkeypatch.setattr(TelegramClient, "logout", logout)
    assert await runtime.local_api.reconcile(runtime)
    assert runtime.local_api.read()["phase"] == "failed"
    assert runtime.safety.status(TOKEN)["can_retry"]
    saved = managed.app.state.settings.read()[1]
    candidate = runtime.local_api.recovery_candidate(saved)
    assert candidate.bot_api_mode == "cloud" and saved.bot_api_mode == "local"
    assert candidate.bot_token == saved.bot_token

    async def identity(self, *, probe=False):
        return await self.request(AsyncMock(return_value={"id": 7, "username": "fixture"}), probe=probe)

    monkeypatch.setattr(TelegramClient, "identity", identity)
    client = TelegramClient(candidate.bot_token, runtime.config, candidate)
    client.safety = runtime.safety
    try:
        await client.identity(probe=True)
    finally:
        await client.close()
    assert runtime.safety.status(TOKEN)["state"] == "ready"
    assert runtime.local_api.read()["phase"] == "failed"  # recovery does not execute switch
    logout.assert_not_awaited()
    # A new valid saved token can also be edited while preflight is failed.
    save(managed, bot_token="987654321:BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB")
    assert runtime.local_api.read()["phase"] == "draining"
    from app.schemas.contracts import UserSettings
    cloud = UserSettings.model_validate_json(runtime.settings.secrets.decrypt(runtime.local_api.read()["cloud_snapshot"]))
    assert cloud.bot_token == "987654321:BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB"


@pytest.mark.parametrize("updates", [
    {"logout_uncertain": True}, {"logout_done": True}, {"applied_mode": "local"},
    {"cloud_available_at": time.time() + 600}, {"phase": "logging_out"},
])
def test_manual_recovery_never_bypasses_logout_or_target_login_barrier(managed, updates):
    runtime = managed.app.state.runtime
    runtime.local_api.update(phase="failed", applied_mode="cloud", target_mode="local", **{
        key: value for key, value in updates.items() if key not in {"phase", "applied_mode"}})
    if "phase" in updates or "applied_mode" in updates:
        runtime.local_api.update(**updates)
    saved = managed.app.state.settings.read()[1].model_copy(update={"bot_api_mode": "local"})
    from app.core.errors import AppError
    with pytest.raises(AppError):
        runtime.local_api.recovery_candidate(saved)


def test_postlogout_recovery_requires_exact_running_target_and_keeps_barrier(managed):
    from test_local_api import heartbeat

    from app.core.errors import AppError

    save(managed, bot_token=TOKEN, allowed_user_ids=[42], bot_api_mode="local",
         telegram_api_id="1234", telegram_api_hash=HASH)
    runtime = managed.app.state.runtime
    service = runtime.local_api
    service.update(phase="failed", logout_done=True, logout_uncertain=False, command_id="target-start")
    saved = runtime.settings.read()[1]
    runtime.safety.observe(TOKEN, None, unauthorized=True)
    heartbeat(service, command_id="old-command", state="running", generation="old")
    with pytest.raises(AppError):
        service.recovery_candidate(saved)
    heartbeat(service, command_id="target-start", state="running", generation="new")
    assert service.recovery_candidate(saved).bot_api_mode == "local"
    assert service.read()["phase"] == "failed" and service.read()["applied_mode"] == "cloud"
    with pytest.raises(AppError):
        service.recovery_candidate(saved.model_copy(update={"bot_token": "987654321:BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB"}))
    service.update(logout_uncertain=True)
    with pytest.raises(AppError):
        service.recovery_candidate(saved)
