import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from aiogram.client.telegram import TelegramAPIServer
from aiohttp import ClientError, web
from sqlalchemy import select
from test_cache_cleanup import published, ready
from test_cloud_api import TOKEN, fake_api

from app.bot.client import CacheCleanupError, TelegramClient
from app.db.models import CacheCleanup, Job, Notification, RequestSafety
from app.schemas.contracts import SettingsPatch, UserSettings
from app.services.diagnostics import DiagnosticLog, project_event

SHA256 = "a" * 64


@pytest.mark.parametrize(("status", "description", "reason"), [
    (404, "Not Found: cache cleanup unavailable", "cache_unavailable"),
    (404, "Not Found: progress unavailable", "cache_unavailable"),
    (400, "Bad Request: method not found", "cache_unsupported"),
    (400, "Bad Request: invalid cache cleanup request", "cache_invalid_request"),
    (400, "Bad Request: invalid cache path", "cache_path_invalid"),
    (409, "Conflict: cache generation changed", "cache_generation_changed"),
    (409, "Conflict: cache cleanup busy", "cache_busy"),
    (409, "Conflict: cache file busy", "cache_busy"),
    (409, "Conflict: cache identity unavailable", "cache_identity_unavailable"),
    (409, "Conflict: cache identity changed", "cache_identity_changed"),
    (500, "Internal Server Error: cache removal unconfirmed", "cache_removal_unconfirmed"),
    (409, "Conflict: cache recovery busy", "cache_busy"),
    (409, "Conflict: cache recovery identity changed", "cache_recovery_identity_changed"),
    (409, "Conflict: cache recovery content mismatch", "cache_recovery_content_mismatch"),
    (500, "Internal Server Error: cache recovery unavailable", "cache_recovery_unavailable"),
    # Neither arbitrary upstream text nor a recognized string with the wrong
    # status can impersonate a trusted native rejection.
    (502, "token/private/path/credential", "cache_http_error"),
    (500, "Conflict: cache generation changed", "cache_http_error"),
])
async def test_native_failure_categories_are_fixed_and_exported(config, tmp_path, status, description, reason):
    async def handler(request):
        data = await request.post()
        assert data["expected_sha256"] == SHA256
        return web.json_response({"ok": False, "description": description}, status=status)

    telegram = TelegramClient(TOKEN, config, UserSettings(bot_api_mode="local"))
    # Offline maintenance cannot account a successful/failed Telegram request.
    telegram.safety = SimpleNamespace(before=AsyncMock(side_effect=AssertionError()),
                                     observe=AsyncMock(side_effect=AssertionError()))
    async with fake_api(handler) as base:
        telegram.bot.session.api = TelegramAPIServer.from_base(base, is_local=True)
        try:
            with pytest.raises(CacheCleanupError) as caught:
                await telegram.delete_file_cache("private-file-id", "/cache/private/path", 6, "generation-one",
                                                 expected_sha256=SHA256)
        finally:
            await telegram.close()
    error = caught.value
    assert error.code == "CACHE_CLEANUP_UNAVAILABLE"
    assert error.reason == reason and error.http_status == status and error.duration_ms >= 0
    diagnostic = DiagnosticLog(tmp_path)
    diagnostic.emit("cache_cleanup", outcome="retry_wait", error_code=error.code,
                    reason=error.reason, http_status=error.http_status, duration_ms=error.duration_ms)
    event = diagnostic.export()["events"][-1]
    assert event["reason"] == reason and event["http_status"] == status
    serialized = json.dumps(event)
    on_disk = (tmp_path / "logs" / "events.jsonl").read_text(encoding="utf-8")
    for private in (description, "private-file-id", "/cache/private/path", TOKEN, SHA256):
        assert private not in serialized
        assert private not in on_disk


@pytest.mark.parametrize(("status", "body", "reason"), [
    (200, b"not json", "cache_response_invalid"),
    (200, b'{"ok":true,"result":{"generation":"other","status":"deleted"}}', "cache_response_invalid"),
    (200, b"x" * 8193, "cache_response_too_large"),
    (500, b"x" * 8193, "cache_response_too_large"),
    (503, b"private unstructured failure", "cache_http_error"),
    (302, b"", "cache_http_error"),
    (405, b"", "cache_unsupported"),
])
async def test_bounded_protocol_failure_details(config, status, body, reason):
    calls = []

    async def handler(request):
        calls.append(request.path)
        return web.Response(body=body, status=status, headers={"Location": "/never-follow"})

    telegram = TelegramClient(TOKEN, config, UserSettings(bot_api_mode="local"))
    async with fake_api(handler) as base:
        telegram.bot.session.api = TelegramAPIServer.from_base(base, is_local=True)
        try:
            with pytest.raises(CacheCleanupError) as caught:
                await telegram.delete_file_cache("alias", "/cache/file", 6, "generation-one", expected_sha256=SHA256)
        finally:
            await telegram.close()
    assert len(calls) == 1
    assert caught.value.reason == reason and caught.value.http_status == status


@pytest.mark.parametrize(("failure", "reason"), [
    (TimeoutError("private-url-token"), "cache_timeout"),
    (ClientError("private-url-token"), "cache_network_error"),
])
async def test_transport_failure_details_never_carry_raw_exception(config, failure, reason):
    telegram = TelegramClient(TOKEN, config, UserSettings(bot_api_mode="local"))
    telegram._cleanup_session = SimpleNamespace(post=Mock(side_effect=failure), close=AsyncMock())
    try:
        with pytest.raises(CacheCleanupError) as caught:
            await telegram.delete_file_cache("alias", "/cache/file", 6, "generation-one", expected_sha256=SHA256)
        assert caught.value.reason == reason and caught.value.http_status is None
        assert "private-url-token" not in str(caught.value)
    finally:
        await telegram.close()


@pytest.mark.parametrize("invalid", ["", "a" * 63, "a" * 65, "A" * 64, "../secret"])
async def test_cleanup_hash_validation_is_before_transport(config, invalid):
    telegram = TelegramClient(TOKEN, config, UserSettings(bot_api_mode="local"))
    try:
        with pytest.raises(CacheCleanupError) as caught:
            await telegram.delete_file_cache("alias", "/cache/file", 6, "generation-one", expected_sha256=invalid)
        assert caught.value.reason == "cache_invalid_request"
        assert telegram._cleanup_session is None
    finally:
        await telegram.close()


async def test_runtime_preserves_cleanup_ownership_and_forwards_sanitized_evidence(client, monkeypatch):
    state, _, _, job_id, source = await published(client)
    failure = CacheCleanupError("cache_identity_unavailable", time.monotonic() - 0.1, 409)
    transport = ready(state.runtime, monkeypatch, AsyncMock(side_effect=failure))
    assert await state.runtime.cleanup_cache()
    with state.db.sessions() as session:
        row = session.get(CacheCleanup, job_id)
        assert row.attempts == 1 and row.error_code == "CACHE_CLEANUP_UNAVAILABLE"
        assert row.next_run_at > time.time()
        assert session.get(Job, job_id).state == "succeeded"
        assert session.get(Job, job_id).auto_retries == 0
        assert session.scalar(select(RequestSafety)) is None
        transport.delete_file_cache.assert_awaited_once_with("file-1", str(source), 6, "generation-one",
                                                            expected_sha256=row.sha256)
    event = [row for row in state.runtime.diagnostics.export()["events"] if row["event"] == "cache_cleanup"][-1]
    assert event["reason"] == "cache_identity_unavailable"
    assert event["http_status"] == 409 and event["duration_ms"] == failure.duration_ms
    assert source.exists() and not state.runtime.worker.cleanup_paused
    assert not state.runtime.connection_lock.locked()


def test_unknown_cleanup_reason_and_unsafe_numeric_metadata_are_dropped():
    event = project_event({"event": "cache_cleanup", "timestamp": "2026-11-01T00:00:00Z",
                           "reason": "/private/token", "http_status": True, "duration_ms": -1})
    assert "reason" not in event and "http_status" not in event and "duration_ms" not in event


@pytest.mark.parametrize(("status", "description"), [
    (400, "Bad Request: TGD_CACHE_CLEANUP_BUSY"),
    (409, "Conflict: TGD_CACHE_STORAGE_BUSY"),
])
async def test_busy_native_notification_refunds_outbox_attempt_without_telegram_backoff(client, monkeypatch,
                                                                                       status, description):
    state = client.app.state
    runtime = state.runtime
    patch = SettingsPatch(revision=1, bot_token=TOKEN, bot_api_mode="local", allowed_user_ids=[42])
    state.settings.save(patch, state.settings.candidate(patch))
    configured = state.settings.read()[1]
    runtime.client_signature = state.settings.client_signature(configured)
    runtime.bot_id = 7
    monkeypatch.setattr(state.settings, "proxy_status", lambda *_: {"state": "disabled"})
    observed = Mock(wraps=runtime.safety.observe)
    monkeypatch.setattr(runtime.safety, "observe", observed)
    with state.db.transaction() as session:
        notice = Notification(event_key="native-recovery-busy", chat_id=42,
                              payload={"bot_id": 7, "text": "synthetic"}, attempts=4)
        session.add(notice)
        session.flush()
        notice_id = notice.id

    async def handler(request):
        assert request.path.endswith("/sendMessage")
        runtime.stopping = True
        return web.json_response({"ok": False, "error_code": status, "description": description}, status=status)

    telegram = TelegramClient(TOKEN, runtime.config, configured)
    telegram.safety = runtime.safety
    runtime.client = telegram
    async with fake_api(handler) as base:
        telegram.bot.session.api = TelegramAPIServer.from_base(base, is_local=True)
        try:
            await runtime.notify_loop()
        finally:
            runtime.client = None
            await telegram.close()
    observed.assert_not_called()
    with state.db.sessions() as session:
        notice = session.get(Notification, notice_id)
        assert (notice.state, notice.attempts, notice.message_id) == ("pending", 4, None)
        assert notice.next_run_at > time.time()
        protection = session.scalar(select(RequestSafety))
        assert protection is not None
        assert not {"backoff", "cooldown", "auth_paused", "failures"}.intersection(protection.values)
