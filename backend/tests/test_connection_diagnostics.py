import asyncio
import errno
import json
from unittest.mock import AsyncMock, Mock

import pytest
from aiogram.client.default import Default
from aiogram.exceptions import TelegramNetworkError
from aiogram.methods import AnswerCallbackQuery
from aiogram.types import Update
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from test_connection_status import configure
from test_pipeline import message
from test_telegram_updates import TOKEN as BOT_TOKEN
from test_telegram_updates import link_message

from app.bot.client import TelegramClient
from app.core.errors import AppError
from app.db.models import IngestUpdate, MediaItem, Notification

SECRET = "sensitive-token https://private.invalid/message private-file.pdf"


async def ready(client, monkeypatch):
    runtime = client.app.state.runtime
    configure(client.app.state)
    monkeypatch.setattr(TelegramClient, "identity", AsyncMock(return_value={"id": 7, "username": "fixture_bot"}))
    assert await runtime.ensure_client()
    return runtime


@pytest.mark.parametrize("stage,target,error,code", [
    ("pending_read", "pending", OperationalError("private SQL", {}, RuntimeError(SECRET)), "DATABASE_ERROR"),
    ("message_process", "message", RuntimeError(SECRET), "PROCESSING_ERROR"),
    ("inbox_mark", "mark_processed", OperationalError("private SQL", {}, RuntimeError(SECRET)), "DATABASE_ERROR"),
    ("cursor_read", "offset", ValueError(SECRET), "PROCESSING_ERROR"),
    ("updates_persist", "persist_batch", TimeoutError(SECRET), "PROCESSING_ERROR"),
    ("poll_status", "poll_result", KeyError(SECRET), "PROCESSING_ERROR"),
])
async def test_poll_failure_stage_retry_recovery_and_durable_replay(client, monkeypatch, stage, target, error, code):
    runtime = await ready(client, monkeypatch)
    telegram = runtime.client
    runtime.ingest.persist_batch(7, [{"update_id": 100, "message": message()}])
    owner = runtime if target == "poll_result" else runtime.ingest
    original = getattr(owner, target)
    failed = False

    def fail_once(*args, **kwargs):
        nonlocal failed
        if not failed and not kwargs.get("error"):
            failed = True
            raise error
        return original(*args, **kwargs)

    monkeypatch.setattr(owner, target, fail_once)
    original_result = runtime.poll_result

    def result(*args, **kwargs):
        original_result(*args, **kwargs)
        if not kwargs.get("error"):
            runtime.stopping = True

    monkeypatch.setattr(runtime, "poll_result", result)
    monkeypatch.setattr(telegram, "updates", AsyncMock(return_value=[]))
    waits = []

    async def retry(delay):
        waits.append(delay)
        assert len(waits) == 1

    monkeypatch.setattr("app.workers.runtime.asyncio.sleep", retry)
    try:
        await runtime.poll_loop()
        assert waits == [5] and failed
        assert runtime.bot_status["state"] == "online"
        assert runtime.ingest.offset(7) == 101 and runtime.ingest.pending(7) == []
        with runtime.db.sessions() as session:
            assert len(list(session.scalars(select(MediaItem)))) == 1
        events = runtime.diagnostics.export()["events"]
        relevant = [e for e in events if e.get("component") == "polling"]
        assert [e["event"] for e in relevant] == ["runtime_failure", "runtime_recovered"]
        assert all(e["stage"] == stage for e in relevant)
        assert relevant[0]["error_code"] == code and relevant[0]["exception_type"]
        assert "retry_at" in relevant[0]
        assert SECRET not in json.dumps(events) and SECRET not in runtime.diagnostics._path().read_text()
    finally:
        runtime.stopping = True
        await telegram.close()


async def test_actual_sdk_serialization_failure_is_logged_after_transport_success(client, monkeypatch):
    runtime = await ready(client, monkeypatch)
    telegram = runtime.client
    bad = Update.model_validate({"update_id": 100, "message": link_message({"is_disabled": Default(SECRET)})})
    good = Update.model_validate({"update_id": 100, "message": link_message({"url": "https://example.org"})})
    calls, waits = [], []
    clock = [1793611952.0]
    runtime.safety.clock = lambda: clock[0]

    async def transport(bot, method, timeout=None):
        calls.append(method.offset)
        clock[0] += 10
        if len(calls) <= 2:
            return [bad]
        if len(calls) == 3:
            return [good]
        runtime.stopping = True
        return []

    async def retry(delay):
        waits.append(delay)
        assert len(waits) <= 2
        assert runtime.ingest.offset(7) == 0  # Failed serialization cannot acknowledge the update.

    monkeypatch.setattr(telegram.bot.session, "make_request", transport)
    monkeypatch.setattr("app.workers.runtime.asyncio.sleep", retry)
    try:
        await runtime.poll_loop()
        assert waits == [5, 5] and calls == [0, 0, 0, 101]
        assert runtime.bot_status["state"] == "online" and runtime.ingest.offset(7) == 101
        events = runtime.diagnostics.export()["events"]
        failure = next(e for e in events if e.get("component") == "updates" and e["event"] == "runtime_failure")
        assert failure["stage"] == "updates_serialize"
        assert failure["error_code"] == "SERIALIZATION_ERROR"
        assert failure["exception_type"] == "pydantic_serialization"
        assert events.index(failure) > next(i for i, e in enumerate(events) if e["event"] == "telegram_request")
        assert any(e["event"] == "event_summary" and e.get("component") == "updates"
                   and e["suppressed_count"] == 1 for e in events)
        assert any(e["event"] == "runtime_recovered" and e.get("stage") == "updates_serialize" for e in events)
        assert runtime.ingest.pending(7) == []
        assert SECRET not in json.dumps(events) and SECRET not in runtime.diagnostics._path().read_text()
    finally:
        runtime.stopping = True
        await telegram.close()


@pytest.mark.parametrize("failure_stage", ["client_create", "identity_request", "identity_publish"])
async def test_identity_stages_include_outer_deadline_and_recovery(client, monkeypatch, failure_stage):
    runtime = client.app.state.runtime
    configure(client.app.state)
    identity = AsyncMock(return_value={"id": 7, "username": "fixture_bot"})
    monkeypatch.setattr(TelegramClient, "identity", identity)
    with monkeypatch.context() as fault:
        if failure_stage == "client_create":
            def reject(*args, **kwargs):
                raise ValueError(SECRET)
            fault.setattr("app.workers.runtime.TelegramClient", reject)
        elif failure_stage == "identity_request":
            async def hang(*args, **kwargs):
                await asyncio.Event().wait()
            fault.setattr(TelegramClient, "identity", hang)
            fault.setattr("app.services.connection_waits.IDENTITY_TIMEOUT", 0.01)
        else:
            def reject_publish(*args):
                raise OperationalError("private SQL", {}, RuntimeError(SECRET))
            fault.setattr(runtime, "publish_client", reject_publish)
        with pytest.raises((ValueError, AppError, OperationalError)):
            await runtime.ensure_client()
    assert await runtime.ensure_client()
    try:
        events = runtime.diagnostics.export()["events"]
        relevant = [e for e in events if e.get("component") == "connection"]
        assert [e["event"] for e in relevant] == ["runtime_failure", "runtime_recovered"]
        assert all(e["stage"] == failure_stage for e in relevant)
        if failure_stage == "identity_request":
            assert relevant[0]["error_code"] == "NETWORK_ERROR" and relevant[0]["reason"] == "timeout"
        if failure_stage == "identity_publish":
            assert relevant[0]["error_code"] == "DATABASE_ERROR"
        assert SECRET not in json.dumps(events)
    finally:
        await runtime.client.close()


async def test_callback_answer_failure_is_visible_without_replaying_business_operation(client, monkeypatch):
    runtime = await ready(client, monkeypatch)
    telegram = runtime.client
    callback = {"id": "fixture", "data": "expired", "from": {"id": 42}}
    runtime.ingest.persist_batch(7, [
        {"update_id": 100, "callback_query": callback},
        {"update_id": 101, "callback_query": callback | {"id": "fixture-next"}},
    ])
    error = TelegramNetworkError(method=AnswerCallbackQuery(callback_query_id="fixture"), message=SECRET)
    answer = AsyncMock(side_effect=[error, None])
    monkeypatch.setattr(telegram, "answer", answer)

    async def updates(offset):
        runtime.stopping = True
        return []

    monkeypatch.setattr(telegram, "updates", updates)
    try:
        await runtime.poll_loop()
        assert answer.await_count == 2 and runtime.ingest.pending(7) == []
        events = runtime.diagnostics.export()["events"]
        relevant = [e for e in events if e.get("stage") == "callback_answer"]
        assert [e["event"] for e in relevant] == ["runtime_failure", "runtime_recovered"]
        assert relevant[0]["method"] == "answerCallbackQuery"
        assert not any(e.get("stage") == "callback_process" for e in events)
        assert SECRET not in json.dumps(events)
    finally:
        await telegram.close()


async def test_maintenance_skipped_callback_does_not_report_recovery(client, monkeypatch):
    runtime = await ready(client, monkeypatch)
    telegram = runtime.client
    runtime.ingest.persist_batch(7, [{"update_id": 100, "callback_query": {
        "id": "fixture", "data": "expired", "from": {"id": 42},
    }}])
    await runtime.record_runtime("polling", "callback_process", client=telegram, error=RuntimeError(SECRET))

    async def updates(offset):
        runtime.stopping = True
        return []

    monkeypatch.setattr(telegram, "updates", updates)
    monkeypatch.setattr(telegram, "answer", AsyncMock())
    try:
        with monkeypatch.context() as maintenance:
            maintenance.setattr(runtime.cache_maintenance, "read", lambda: {"state": "running"})
            await runtime.poll_loop()
        assert runtime.ingest.pending(7)
        events = runtime.diagnostics.export()["events"]
        assert not any(e["event"] == "runtime_recovered" and e.get("component") == "polling" for e in events)
        runtime.stopping = False
        await runtime.poll_loop()
        assert runtime.ingest.pending(7) == []
        assert any(e["event"] == "runtime_recovered" and e.get("stage") == "callback_process"
                   for e in runtime.diagnostics.export()["events"])
    finally:
        await telegram.close()


async def test_paused_dispatch_keeps_failure_until_dispatch_is_attempted(client, monkeypatch):
    runtime = await ready(client, monkeypatch)
    telegram = runtime.client
    claim = Mock(side_effect=[RuntimeError(SECRET), None])
    monkeypatch.setattr(runtime.worker, "claim", claim)
    monkeypatch.setattr(runtime, "counts", lambda: (0, 0, 1))
    monkeypatch.setattr(runtime, "cleanup_cache", AsyncMock(side_effect=[False, True, False]))
    loops = 0

    async def tick(delay):
        nonlocal loops
        loops += 1
        events = [e for e in runtime.diagnostics.export()["events"] if e.get("component") == "dispatch"]
        if loops <= 2:
            assert claim.call_count == 1
            assert [e["event"] for e in events] == ["runtime_failure"]
        else:
            assert claim.call_count == 2
            assert [e["event"] for e in events] == ["runtime_failure", "runtime_recovered"]
            assert events[-1]["stage"] == "dispatch_run"
            runtime.stopping = True

    monkeypatch.setattr("app.workers.runtime.asyncio.sleep", tick)
    try:
        await runtime.tick_loop()
        assert loops == 3
    finally:
        await telegram.close()


@pytest.mark.parametrize("stage", ["notification_prepare", "notification_claim"])
async def test_empty_notification_phase_can_recover_without_sending(client, monkeypatch, stage):
    runtime = await ready(client, monkeypatch)
    telegram = runtime.client
    if stage == "notification_prepare":
        monkeypatch.setattr(runtime, "notification_due", Mock(side_effect=[RuntimeError(SECRET), False]))
    else:
        monkeypatch.setattr(runtime, "notification_due", lambda: True)
        monkeypatch.setattr(runtime, "next_notification", Mock(side_effect=[RuntimeError(SECRET), None]))
    send = AsyncMock()
    monkeypatch.setattr(telegram, "send", send)
    waits = []

    async def tick(delay):
        waits.append(delay)
        if len(waits) == 2:
            runtime.stopping = True

    monkeypatch.setattr("app.workers.runtime.asyncio.sleep", tick)
    try:
        await runtime.notify_loop()
        send.assert_not_awaited()
        assert waits == [2, 0.5]
        events = [e for e in runtime.diagnostics.export()["events"] if e.get("component") == "notification"]
        assert [e["event"] for e in events] == ["runtime_failure", "runtime_recovered"]
        assert all(e["stage"] == stage for e in events)
    finally:
        await telegram.close()


async def test_notification_failure_records_stage_and_keeps_existing_retry_result(client, monkeypatch):
    runtime = await ready(client, monkeypatch)
    telegram = runtime.client
    with runtime.db.transaction() as session:
        row = Notification(event_key="fixture", chat_id=42, payload={"text": SECRET, "bot_id": 7})
        session.add(row)
        session.flush()
        row_id = row.id
    monkeypatch.setattr(telegram, "send", AsyncMock(side_effect=TypeError(SECRET)))
    original = runtime.notification_result

    def result(*args):
        original(*args)
        runtime.stopping = True

    monkeypatch.setattr(runtime, "notification_result", result)
    try:
        await runtime.notify_loop()
        with runtime.db.sessions() as session:
            row = session.get(Notification, row_id)
            assert row.state == "pending" and row.attempts == 1
        events = runtime.diagnostics.export()["events"]
        failure = next(e for e in events if e.get("component") == "notification")
        assert failure["stage"] == "notification_send" and failure["method"] == "sendMessage"
        assert failure["error_code"] == "PROCESSING_ERROR" and failure["exception_type"] == "python_type"
        assert SECRET not in json.dumps(events)
    finally:
        await telegram.close()


async def test_notification_failure_stays_with_original_connection_scope(client, monkeypatch):
    runtime = await ready(client, monkeypatch)
    telegram = runtime.client
    original_scope = runtime.client_signature
    with runtime.db.transaction() as session:
        session.add(Notification(event_key="scope-fixture", chat_id=42, payload={"text": SECRET, "bot_id": 7}))

    async def send(*args):
        runtime.client_signature = "replacement-connection"
        raise TypeError(SECRET)

    monkeypatch.setattr(telegram, "send", send)
    original_result = runtime.notification_result

    def result(*args):
        original_result(*args)
        runtime.stopping = True

    monkeypatch.setattr(runtime, "notification_result", result)
    try:
        await runtime.notify_loop()
        assert ("notification", "cloud", original_scope) in runtime.diagnostics.runtime_failures
        await runtime.record_runtime("notification", "notification_commit", client=telegram)
        assert ("notification", "cloud", original_scope) in runtime.diagnostics.runtime_failures
        assert not any(e["event"] == "runtime_recovered" and e.get("component") == "notification"
                       for e in runtime.diagnostics.export()["events"])
    finally:
        await telegram.close()


async def test_logging_exception_does_not_change_intake_result(client, monkeypatch):
    runtime = await ready(client, monkeypatch)
    telegram = runtime.client

    def unavailable(*args, **kwargs):
        raise OSError(SECRET)

    monkeypatch.setattr(runtime.diagnostics, "runtime_result", unavailable)

    async def transport(bot, method, timeout=None):
        runtime.stopping = True
        return [Update.model_validate({"update_id": 100, "message": link_message({})})]

    monkeypatch.setattr(telegram.bot.session, "make_request", transport)
    try:
        await runtime.poll_loop()
        assert runtime.bot_status["state"] == "online" and runtime.ingest.offset(7) == 101
        with runtime.db.sessions() as session:
            assert len(list(session.scalars(select(IngestUpdate)))) == 1
        assert runtime.diagnostics.error
    finally:
        await telegram.close()


def test_status_request_failure_and_recovery_survive_export(authenticated, monkeypatch):
    runtime = authenticated.app.state.runtime
    original = runtime.connection_snapshot
    calls = 0

    def snapshot():
        nonlocal calls
        calls += 1
        if calls <= 2:
            raise OperationalError("private SQL", {}, RuntimeError(SECRET))
        return original()

    monkeypatch.setattr(runtime, "connection_snapshot", snapshot)
    for _ in range(2):
        with pytest.raises(OperationalError):
            authenticated.get("/api/v1/system/status")
    assert authenticated.get("/api/v1/system/status").status_code == 200
    response = authenticated.get("/api/v1/system/logs")
    assert response.status_code == 200
    events = [e for e in response.json()["events"] if e.get("component") == "status"]
    assert [e["event"] for e in events] == ["runtime_failure", "event_summary", "runtime_recovered"]
    assert events[0]["stage"] == "status_snapshot" and events[0]["error_code"] == "DATABASE_ERROR"
    assert events[1]["suppressed_count"] == 1
    assert SECRET not in response.text


def test_manual_validation_outer_timeout_is_recorded_without_saving(authenticated, monkeypatch):
    state = authenticated.app.state
    configure(state)
    revision = state.settings.read()[0]

    async def hang(*args, **kwargs):
        await asyncio.Event().wait()

    monkeypatch.setattr(TelegramClient, "identity", hang)
    monkeypatch.setattr("app.services.connection_waits.IDENTITY_TIMEOUT", 0.01)
    response = authenticated.post("/api/v1/settings/validate", json={"revision": revision})
    assert response.status_code == 422 and response.json()["error"]["code"] == "LOCAL_API_OPERATION_TIMEOUT"
    monkeypatch.setattr(TelegramClient, "identity", AsyncMock(return_value={"id": 7, "username": "fixture_bot"}))
    assert authenticated.post("/api/v1/settings/validate", json={"revision": revision}).status_code == 200
    assert state.settings.read()[0] == revision
    events = [e for e in state.runtime.diagnostics.export()["events"] if e.get("component") == "connection"]
    assert [e["event"] for e in events] == ["runtime_failure", "runtime_recovered"]
    assert events[0]["stage"] == "identity_request" and events[0]["reason"] == "timeout"


def test_manual_recovery_constructor_failure_is_recorded_and_does_not_clear_auth_pause(authenticated, monkeypatch):
    state = authenticated.app.state
    configure(state)
    state.runtime.safety.observe(BOT_TOKEN, None, unauthorized=True)

    def reject(*args, **kwargs):
        raise ValueError(SECRET)

    monkeypatch.setattr("app.main.TelegramClient", reject)
    with pytest.raises(ValueError):
        authenticated.post("/api/v1/system/bot/retry")
    assert state.runtime.safety.status(BOT_TOKEN)["state"] == "auth_paused"
    events = state.runtime.diagnostics.export()["events"]
    failure = next(e for e in events if e.get("component") == "connection")
    assert failure["stage"] == "client_create" and failure["exception_type"] == "python_value"
    assert any(e["event"] == "bot_recovery" and e.get("outcome") == "failed" for e in events)
    assert SECRET not in json.dumps(events)


def test_local_api_transition_retains_exception_type_alongside_existing_network_evidence(client):
    runtime = client.app.state.runtime
    runtime.local_api.update(phase="verifying", target_mode="local")
    error = TelegramNetworkError(method=AnswerCallbackQuery(callback_query_id="fixture"), message=SECRET)
    error.__cause__ = ConnectionRefusedError(errno.ECONNREFUSED, SECRET)
    runtime.local_api.update(phase="failed", diagnostic_error=error, error_code="LOCAL_API_CONNECTION_FAILED")
    failure = runtime.diagnostics.export()["events"][-1]
    assert failure["event"] == "local_api_transition" and failure["stage"] == "verifying"
    assert failure["error_code"] == "NETWORK_ERROR" and failure["reason"] == "tcp"
    assert failure["exception_type"] == "telegram_network"
    assert SECRET not in json.dumps(failure)
