"""No Telegram calls: exercise durable request gates and their actual seams."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import AsyncMock, Mock

import pytest
from aiogram.exceptions import (
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
    TelegramUnauthorizedError,
)
from aiogram.methods import GetMe
from fastapi.testclient import TestClient

from app.bot.client import TelegramClient
from app.core.security import digest
from app.db.models import Notification, RequestSafety
from app.services.request_safety import RequestSafetyService, SafetyDeferred

TOKEN = "123456789:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
OTHER = "987654321:BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB"


def guard(client: TestClient) -> tuple[RequestSafetyService, list[float]]:
    now = [1000.0]
    return RequestSafetyService(client.app.state.db, lambda: now[0], lambda: 0), now


def test_transactional_start_reservations_and_per_chat_spacing(client: TestClient) -> None:
    safety, now = guard(client)
    with ThreadPoolExecutor(max_workers=8) as pool:
        starts = list(pool.map(lambda _: safety.reserve(TOKEN), range(8)))
    assert starts.count(1000) == 1
    assert starts.count(1001) == 7  # waiting callers have not reserved future starts
    now[0] = 1001
    assert safety.reserve(TOKEN, -42) == 1001
    now[0] = 1002
    assert safety.reserve(TOKEN, -42) == 1004.1
    assert safety.reserve(TOKEN, 42) == 1002  # another chat can make progress
    now[0] = 1003
    assert safety.reserve(TOKEN, 42) == 1003.1


def test_cooldown_survives_service_recreation_success_and_candidate_token(client: TestClient) -> None:
    safety, now = guard(client)
    safety.reserve(TOKEN)
    safety.observe(TOKEN, None, retry_after=60)
    safety.observe(TOKEN, None, retry_after=10)
    safety.observe(TOKEN, None)  # an earlier in-flight success cannot clear the wait
    restarted = RequestSafetyService(client.app.state.db, lambda: now[0])
    with pytest.raises(SafetyDeferred):
        restarted.reserve(TOKEN, probe=True)
    assert restarted.reserve(OTHER) == now[0]
    assert restarted.status(TOKEN)["state"] == "throttled"
    with client.app.state.db.sessions() as session:
        values = session.get(RequestSafety, digest(TOKEN)).values
        assert values["cooldown"] == 1061
        assert TOKEN not in str(values)


def test_auth_pause_is_credential_specific_and_manual_probe_is_bounded(client: TestClient) -> None:
    safety, now = guard(client)
    safety.observe(TOKEN, None, unauthorized=True)
    with pytest.raises(SafetyDeferred):
        safety.reserve(TOKEN)
    assert safety.reserve(OTHER) == now[0]
    assert safety.status(TOKEN)["can_retry"]
    safety.reserve(TOKEN, probe=True)
    safety.observe(TOKEN, None, probe=True)
    assert safety.status(TOKEN)["state"] == "ready"
    now[0] += 2
    with pytest.raises(SafetyDeferred):
        safety.reserve(TOKEN, probe=True)


def test_preexisting_success_and_racing_auth_failure_do_not_unpause(client: TestClient) -> None:
    safety, now = guard(client)
    safety.observe(TOKEN, None, unauthorized=True)
    safety.observe(TOKEN, None)
    assert safety.status(TOKEN)["state"] == "auth_paused"
    safety.reserve(TOKEN, probe=True)
    safety.observe(TOKEN, None, unauthorized=True)  # concurrent response newer than probe
    safety.observe(TOKEN, None, probe=True)
    assert safety.status(TOKEN)["state"] == "auth_paused"
    now[0] += 60
    safety.reserve(TOKEN, probe=True)
    safety.observe(TOKEN, None, probe=True)
    assert safety.status(TOKEN)["state"] == "ready"


def test_network_backoff_grows_caps_and_success_resets_streak(client: TestClient) -> None:
    safety, now = guard(client)
    for expected in (5, 10, 20, 40, 80, 120, 120):
        safety.observe(TOKEN, TelegramNetworkError(GetMe(), "secret-bearing upstream error"))
        with pytest.raises(SafetyDeferred) as error:
            safety.reserve(TOKEN)
        assert error.value.retry_at == now[0] + expected
        assert "secret" not in safety.status(TOKEN)["message"]
        now[0] += expected
    safety.observe(TOKEN, None)
    safety.observe(TOKEN, TimeoutError())
    with pytest.raises(SafetyDeferred) as error:
        safety.reserve(TOKEN)
    assert error.value.retry_at == now[0] + 5


@pytest.mark.parametrize("operation", ["identity", "updates", "get_file", "send", "answer", "logout"])
async def test_all_api_methods_gate_before_actual_transport(client: TestClient, operation: str) -> None:
    safety, _ = guard(client)
    safety.observe(TOKEN, None, retry_after=600)
    telegram = TelegramClient(TOKEN, client.app.state.runtime.config)
    telegram.safety = safety
    fake = Mock(token=TOKEN)
    fake.get_me = AsyncMock()
    fake.send_message = AsyncMock()
    fake.answer_callback_query = AsyncMock()
    fake.log_out = AsyncMock()
    telegram.bot = fake
    args = {"identity": (), "updates": (0,), "get_file": ("file",), "send": (42, "message"),
            "answer": ("query", "message"), "logout": ()}[operation]
    with pytest.raises(SafetyDeferred):
        await getattr(telegram, operation)(*args)
    assert not fake.mock_calls


async def test_long_poll_does_not_hold_request_lock(client: TestClient) -> None:
    safety, now = guard(client)
    telegram = TelegramClient(TOKEN, client.app.state.runtime.config)
    telegram.safety = safety
    entered, release = asyncio.Event(), asyncio.Event()

    async def poll() -> None:
        entered.set()
        await release.wait()

    task = asyncio.create_task(telegram.request(poll))
    await entered.wait()
    now[0] += 1
    sent = AsyncMock(return_value="ok")
    assert await telegram.request(sent, 42) == "ok"
    release.set()
    await task
    await telegram.close()


@pytest.mark.parametrize("error,expected", [
    (TelegramRetryAfter(GetMe(), "limited", 30), "throttled"),
    (TelegramUnauthorizedError(GetMe(), "bad credential"), "auth_paused"),
    (TelegramForbiddenError(GetMe(), "chat blocked"), "ready"),
])
async def test_transport_observes_limits_and_auth_but_not_chat_forbidden(
        client: TestClient, error: Exception, expected: str) -> None:
    safety, _ = guard(client)
    telegram = TelegramClient(TOKEN, client.app.state.runtime.config)
    telegram.safety = safety
    with pytest.raises((SafetyDeferred, TelegramForbiddenError)):
        await telegram.request(AsyncMock(side_effect=error))
    assert safety.status(TOKEN)["state"] == expected
    await telegram.close()


def test_status_and_manual_retry_auth_csrf_saved_token_only(authenticated: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    state = authenticated.app.state
    revision = state.settings.public()["revision"]
    assert authenticated.patch("/api/v1/settings", json={"revision": revision,
        "bot_token": TOKEN, "bot_api_mode": "cloud", "allowed_user_ids": [42]}).status_code == 200
    state.runtime.safety.observe(TOKEN, None, unauthorized=True)
    status = authenticated.get("/api/v1/system/status").json()["bot"]
    assert status["state"] == "error" and status["request_safety"]["can_retry"]

    async def identity(self: TelegramClient, *, probe: bool = False) -> dict[str, object]:
        return await self.request(AsyncMock(return_value={"id": 7, "username": "fixture"}), probe=probe)

    monkeypatch.setattr(TelegramClient, "identity", identity)
    csrf = authenticated.headers.pop("X-CSRF-Token")
    assert authenticated.post("/api/v1/system/bot/retry").status_code == 403
    authenticated.headers["X-CSRF-Token"] = csrf
    result = authenticated.post("/api/v1/system/bot/retry")
    assert result.status_code == 200 and result.json()["state"] == "ready"
    assert authenticated.post("/api/v1/system/bot/retry").status_code == 409


def test_paused_notification_does_not_consume_attempt_and_claim_stops(authenticated: TestClient) -> None:
    state = authenticated.app.state
    revision = state.settings.public()["revision"]
    assert authenticated.patch("/api/v1/settings", json={"revision": revision, "bot_token": TOKEN, "allowed_user_ids": [42]}).status_code == 200
    state.runtime.safety.observe(TOKEN, None, unauthorized=True)
    with state.db.transaction() as session:
        row = Notification(event_key="safety", chat_id=42, payload={"text": "hello"})
        session.add(row)
        session.flush()
        row_id = row.id
    assert state.runtime.next_notification(7) is None
    with state.db.sessions() as session:
        assert session.get(Notification, row_id).attempts == 0
    state.runtime.worker.credential_hash = digest(TOKEN)
    assert state.runtime.worker.claim(7) is None


async def test_guard_storage_failure_blocks_transport(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    safety, _ = guard(client)
    monkeypatch.setattr(safety, "_reserve", Mock(side_effect=OSError("private path")))
    telegram = TelegramClient(TOKEN, client.app.state.runtime.config)
    telegram.safety = safety
    operation = AsyncMock()
    with pytest.raises(SafetyDeferred, match="请求保护状态不可用") as error:
        await telegram.request(operation)
    assert error.value.reason == "protection_unavailable" and not error.value.sent
    operation.assert_not_awaited()
    await telegram.close()


@pytest.mark.parametrize("values,reason", [
    ({"next_start": 1005}, "pacing"),
    ({"cooldown": 1100}, "cooldown"),
    ({"backoff": 1100}, "backoff"),
    ({"cooldown": 1050, "backoff": 1100}, "cooldown"),
    ({"auth_paused": True}, "auth_paused"),
])
async def test_deferral_evidence_preserves_protection_and_never_sends(client: TestClient, values, reason) -> None:
    safety, _ = guard(client)
    with client.app.state.db.transaction() as session:
        session.add(RequestSafety(credential=digest(TOKEN), values=values))
    telegram = TelegramClient(TOKEN, client.app.state.runtime.config)
    telegram.safety = safety
    operation = AsyncMock()
    try:
        with pytest.raises(SafetyDeferred) as error:
            await telegram.request(operation)
        assert error.value.reason == reason and not error.value.sent
        operation.assert_not_awaited()
        with client.app.state.db.sessions() as session:
            assert session.get(RequestSafety, digest(TOKEN)).values == values
    finally:
        await telegram.close()
