"""HTTP recovery operations do not queue behind a slow connection transition."""
import json
import time
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from app.bot.client import TelegramClient
from app.core.security import private_file
from app.db.models import Job
from app.schemas.contracts import SettingsPatch
from app.services import connection_waits as waits
from tests.test_local_api import HASH, heartbeat
from tests.test_pipeline import confirm_all, message


@pytest.mark.parametrize("managed", [False, True])
def test_busy_network_lock_does_not_queue_handlers_or_block_ordinary_save(authenticated, config, managed):
    config.managed_local_api = managed
    state, runtime = authenticated.app.state, authenticated.app.state.runtime
    runtime.local_api.update(action="cloud_reconnect", operation_id="inflight", phase="draining")
    original = runtime.local_api.read()
    revision = state.settings.read()[0]
    authenticated.portal.call(runtime.connection_lock.acquire)
    with ThreadPoolExecutor(max_workers=1) as executor:
        try:
            cases = [
                ("patch", "/api/v1/settings", {"revision": revision, "proxy": {"enabled": True, "host": "proxy.lan"}}),
                ("post", "/api/v1/settings/validate", {}),
                ("post", "/api/v1/system/local-api/retry", {}),
                ("post", "/api/v1/system/local-api/restart", {}),
                ("post", "/api/v1/system/bot/retry", {}),
            ]
            for method, path, body in cases:
                response = executor.submit(getattr(authenticated, method), path, json=body).result(timeout=2)
                assert response.status_code == 409, path
                assert runtime.local_api.read() == original
            saved = executor.submit(authenticated.patch, "/api/v1/settings",
                                    json={"revision": revision, "allowed_user_ids": [42]}).result(timeout=2)
            assert saved.status_code == 200
            assert saved.json()["values"]["allowed_user_ids"] == [42]
            assert runtime.local_api.read() == original
        finally:
            authenticated.portal.call(runtime.connection_lock.release)


def test_timed_out_consumers_block_validation_and_auth_probe(authenticated, monkeypatch):
    state, runtime = authenticated.app.state, authenticated.app.state.runtime
    token = "123:synthetic-recovery"
    patch = SettingsPatch(revision=state.settings.read()[0], bot_token=token, allowed_user_ids=[42])
    state.settings.save(patch, state.settings.candidate(patch))
    runtime.local_api.update(action="cloud_reconnect", operation_id="blocked-consumer", phase="failed",
                             error_code="LOCAL_API_DRAIN_TIMEOUT")
    runtime.safety.observe(token, None, unauthorized=True)
    identity = AsyncMock(side_effect=AssertionError("Unresolved consumer must not permit a probe"))
    monkeypatch.setattr(TelegramClient, "identity", identity)
    before = runtime.local_api.read()
    for path in ("/api/v1/settings/validate", "/api/v1/system/bot/retry"):
        response = authenticated.post(path, json={})
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "LOCAL_API_BUSY"
    identity.assert_not_awaited()
    assert runtime.local_api.read() == before


def test_repeating_an_accepted_retry_is_rejected_without_replacing_operation(authenticated):
    runtime = authenticated.app.state.runtime
    runtime.local_api.update(action="cloud_reconnect", operation_id="retry-target", phase="failed")
    response = authenticated.post("/api/v1/system/local-api/retry", json={})
    assert response.status_code == 202
    accepted = runtime.local_api.read()
    assert accepted["phase"] == "draining"
    assert authenticated.post("/api/v1/system/local-api/retry", json={}).status_code == 409
    assert runtime.local_api.read() == accepted


async def test_acknowledged_restart_without_generation_recovery_becomes_blocked(authenticated, config, monkeypatch):
    config.managed_local_api = True
    config.local_api_control_dir = config.data_dir / "control"
    state, runtime = authenticated.app.state, authenticated.app.state.runtime
    patch = SettingsPatch(revision=state.settings.read()[0], bot_api_mode="local", bot_token="123:synthetic-recovery",
                          allowed_user_ids=[42], telegram_api_id="1234", telegram_api_hash=HASH)
    state.settings.save(patch, state.settings.candidate(patch))
    runtime.ingest.message(7, message(), "uncertain")
    confirm_all(state)
    with state.db.transaction() as session:
        job = session.scalar(select(Job))
        job.state, job.acquisition_mode, job.boot_id = "recovery_blocked", "local", "old-owner"
        frozen = job.id, job.relative_path, job.acquisition_mode, job.boot_id
    heartbeat(runtime.local_api)
    values = state.settings.read()[1]
    runtime.local_api.update(action="restart", phase="starting", applied_mode="local", target_mode="local",
                             command_id="accepted-restart", command_issued_at=time.time() - waits.GENERATION_TIMEOUT - 1,
                             credential_revision=state.settings.read()[0], logout_done=True)
    private_file(config.local_api_control_dir / "desired.json", json.dumps({"command_id": "accepted-restart"}).encode())
    monkeypatch.setattr(runtime.local_api, "applied", lambda: {
        "state": "running", "command_id": "accepted-restart", "credential_revision": state.settings.read()[0],
        "proxy_signature": state.settings.proxy_signature(values), "generation": "new-generation"})
    monkeypatch.setattr(runtime.local_api, "command", lambda *args: pytest.fail("Accepted restart must not be replayed"))
    identity = AsyncMock(side_effect=AssertionError("Unresolved acquisition must not allow identity verification"))
    monkeypatch.setattr(TelegramClient, "identity", identity)
    safety = runtime.safety.status(values.bot_token)
    assert await runtime.local_api.reconcile(runtime)
    assert runtime.local_api.read()["phase"] == "blocked"
    assert runtime.local_api.status()["can_restart"]
    with state.db.sessions() as session:
        job = session.get(Job, frozen[0])
        assert (job.id, job.relative_path, job.acquisition_mode, job.boot_id) == frozen
        assert job.state == "recovery_blocked"
    assert runtime.safety.status(values.bot_token) == safety
    identity.assert_not_awaited()
