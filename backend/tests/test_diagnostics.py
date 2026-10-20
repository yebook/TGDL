import asyncio
import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from aiogram.client.telegram import TelegramAPIServer
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramConflictError,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
    TelegramUnauthorizedError,
)
from aiogram.methods import GetMe
from aiohttp import web
from aiohttp_socks import ProxyConnectionError, ProxyError, ProxyTimeoutError
from fastapi.testclient import TestClient
from test_cloud_api import TOKEN, fake_api
from test_pipeline import confirm_all, message, setup_pipeline

from app.bot.client import TelegramClient
from app.core.config import Config
from app.core.errors import AppError
from app.schemas.contracts import UserSettings
from app.services.diagnostics import DiagnosticLog, project_event
from app.services.request_safety import SafetyDeferred

SECRET = "123456:DO-NOT-LEAK https://secret.example/password caption/private/video.mp4"


def test_projection_strict_values_and_types(tmp_path: Path) -> None:
    log = DiagnosticLog(tmp_path)
    log.emit("telegram_request", level="error", mode="cloud", method="getMe", outcome="failed",
             error_code=SECRET, task_id=SECRET, request_id="a" * 16,
             timestamp=SECRET, duration_ms=True, http_status=401, token=SECRET, caption=SECRET,
             retry_at=SECRET)
    event = log.export()["events"][0]
    assert event["http_status"] == 401 and event["request_id"] == "a" * 16
    assert not {"token", "caption", "error_code", "task_id", "duration_ms", "retry_at"} & event.keys()
    assert SECRET not in log._path().read_text()
    for invalid in ({"event": []}, {"event": "telegram_request", "timestamp": "2026-13-20T00:00:00Z"}, []):
        assert project_event(invalid) == {}


def test_persistence_repetition_empty_poll_and_recovery(tmp_path: Path) -> None:
    clock = [1000.0]
    log = DiagnosticLog(tmp_path, clock=lambda: clock[0])
    error = TelegramNetworkError(method=GetMe(), message=SECRET)
    for _ in range(40):
        log.request("cloud", "getUpdates", error=error)
    assert len(log._path().read_text().splitlines()) == 1
    log.request("cloud", "getUpdates", empty=True)
    for _ in range(100):
        log.request("cloud", "getUpdates", empty=True)
    events = DiagnosticLog(tmp_path).export()["events"]
    assert [event["event"] for event in events] == ["telegram_request", "event_summary", "telegram_recovered"]
    assert events[1]["suppressed_count"] == 39
    assert SECRET not in json.dumps(events)


@pytest.mark.parametrize("error_type", [ProxyConnectionError, ProxyTimeoutError, ProxyError])
def test_proxy_errors_are_safe_network_classification(tmp_path: Path, error_type) -> None:
    log = DiagnosticLog(tmp_path)
    log.request("cloud", "downloadFile", error=error_type(SECRET))
    events = log.export()["events"]
    assert events[0]["error_code"] == "NETWORK_ERROR"
    assert SECRET not in json.dumps(events)


def test_concurrent_rotation_stays_bounded_and_private(tmp_path: Path) -> None:
    log = DiagnosticLog(tmp_path, max_file_bytes=700)
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda _: log.emit("service_started", level="info"), range(200)))
    files = list(log.directory.iterdir())
    assert len(files) == 4
    for path in files:
        assert path.stat().st_size <= 700
        for line in path.read_text().splitlines():
            assert json.loads(line)["event"] == "service_started"
        if os.name != "nt":
            assert path.stat().st_mode & 0o777 == 0o600


def test_failure_visible_business_emission_safe_and_recoverable(tmp_path: Path, monkeypatch) -> None:
    log = DiagnosticLog(tmp_path)
    original = log._write
    monkeypatch.setattr(log, "_write", lambda _: (_ for _ in ()).throw(OSError(SECRET)))
    log.emit("service_started")
    assert log.status()["state"] == "error" and SECRET not in json.dumps(log.status())
    monkeypatch.setattr(log, "_write", original)
    log.emit("service_stopped")
    assert log.status()["state"] == "ready"


def test_export_sorts_actual_utc_instants_and_tightens_existing_permissions(tmp_path: Path) -> None:
    log = DiagnosticLog(tmp_path)
    log.directory.mkdir()
    log._path().write_text("\n".join(json.dumps({"event": "service_started", "timestamp": timestamp}) for timestamp in (
        "2026-10-20T00:00:00.1+00:00", "2026-10-20T00:00:00Z")) + "\n")
    log._path().chmod(0o666)
    events = log.export()["events"]
    assert events[0]["timestamp"].endswith("00Z")
    if os.name != "nt":
        assert log._path().stat().st_mode & 0o777 == 0o600


def test_export_reprojects_corrupt_oversized_and_latest_window(tmp_path: Path) -> None:
    log = DiagnosticLog(tmp_path)
    log.emit("service_started")
    with log._path().open("a") as stream:
        stream.write("broken\n")
        stream.write(json.dumps({"timestamp": "2026-10-20T00:00:00Z", "event": "telegram_request", "token": SECRET,
                                 "method": SECRET, "error_code": SECRET}) + "\n")
        stream.write("x" * 10000 + "\n")
        for index in range(1010):
            stream.write(json.dumps({"timestamp": "2026-10-20T00:00:00Z", "event": "task_result",
                                     "job_id": f"{index:020x}"}) + "\n")
    data = log.export()
    assert data["truncated"] and len(data["events"]) == 1000
    assert data["events"][0]["job_id"] == f"{10:020x}"
    assert SECRET not in json.dumps(data)
    # A valid-looking suffix of an oversized line must not become an event.
    log = DiagnosticLog(tmp_path / "small", max_file_bytes=1024)
    log.directory.mkdir(parents=True)
    log._path().write_text("x" * 5000 + json.dumps({"event": "service_started", "timestamp": "2026-10-20T00:00:00Z"}) + "\n")
    assert log.export() == {"events": [], "truncated": True}


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink permission fixture")
@pytest.mark.parametrize("target", ["directory", "active", "archive"])
def test_symlinks_never_read_or_write(tmp_path: Path, target: str) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / "secret"
    secret.write_text(SECRET)
    log = DiagnosticLog(tmp_path / "data")
    log.directory.parent.mkdir()
    if target == "directory":
        log.directory.symlink_to(outside, target_is_directory=True)
    else:
        log.directory.mkdir()
        log._path(0 if target == "active" else 1).symlink_to(secret)
    log.emit("service_started")
    assert log.status()["state"] == "error"
    with pytest.raises(AppError, match="诊断日志"):
        log.export()
    assert secret.read_text() == SECRET


def test_export_requires_admin_and_reads_old_logs_on_write_error(client: TestClient, authenticated: TestClient) -> None:
    log = authenticated.app.state.runtime.diagnostics
    log.emit("service_started")
    log.error = True
    response = authenticated.get("/api/v1/system/logs")
    assert response.status_code == 200 and response.json()["events"]
    assert authenticated.get("/api/v1/system/status").json()["diagnostic_log"]["state"] == "error"
    authenticated.post("/api/v1/auth/logout")
    assert client.get("/api/v1/system/logs").status_code == 401


@pytest.mark.parametrize("exception,status,code", [
    (TelegramUnauthorizedError, 401, "UNAUTHORIZED"), (TelegramForbiddenError, 403, "FORBIDDEN"),
    (TelegramConflictError, 409, "CONFLICT"), (TelegramRetryAfter, 429, "RATE_LIMITED"),
    (TelegramBadRequest, 400, "BAD_REQUEST"), (TelegramNetworkError, None, "NETWORK_ERROR"),
])
async def test_sdk_first_error_sanitized_and_recovery(client: TestClient, config: Config, exception, status, code) -> None:
    telegram = TelegramClient(TOKEN, config, UserSettings(bot_api_mode="cloud"))
    telegram.safety = client.app.state.runtime.safety
    error = exception(method=GetMe(), message=SECRET, **({"retry_after": 30} if status == 429 else {}))
    operation = AsyncMock(side_effect=error)
    try:
        with pytest.raises((exception, SafetyDeferred)):
            await telegram.request(operation, method="getMe")
        event = client.app.state.runtime.diagnostics.export()["events"][1]
        assert event["error_code"] == code
        if status:
            assert event["http_status"] == status
        assert SECRET not in json.dumps(event)
        if status == 401:
            with pytest.raises(SafetyDeferred):
                await telegram.request(operation)
            assert operation.await_count == 1
    finally:
        await telegram.close()


@pytest.mark.parametrize("status", [401, 403, 409, 429, 500])
async def test_cloud_file_http_status_and_no_payload_disclosure(client: TestClient, config: Config, status: int) -> None:
    async def handler(request):
        return web.json_response({"parameters": {"retry_after": 30}, "description": SECRET}, status=status)
    telegram = TelegramClient(TOKEN, config, UserSettings(bot_api_mode="cloud"))
    telegram.safety = client.app.state.runtime.safety
    try:
        async with fake_api(handler) as base:
            telegram.bot.session.api = TelegramAPIServer.from_base(base)
            with pytest.raises(AppError):
                async for _ in telegram.file_chunks("videos/file.mp4"):
                    pass
        events = client.app.state.runtime.diagnostics.export()["events"]
        event = next(event for event in events if event.get("method") == "downloadFile")
        assert event["http_status"] == status
        assert event["error_code"] != "BOT_REQUEST_PAUSED"
        assert SECRET not in json.dumps(events)
    finally:
        await telegram.close()


async def test_cancelled_sdk_request_distinct(client: TestClient, config: Config) -> None:
    telegram = TelegramClient(TOKEN, config)
    telegram.safety = client.app.state.runtime.safety
    try:
        with pytest.raises(asyncio.CancelledError):
            await telegram.request(AsyncMock(side_effect=asyncio.CancelledError()), method="getUpdates")
        events = client.app.state.runtime.diagnostics.export()["events"]
        assert events[-1]["outcome"] == "cancelled"
    finally:
        await telegram.close()


async def test_actual_sdk_probe_recovery_and_logging_failure_never_alters_result(client: TestClient, config: Config, monkeypatch) -> None:
    telegram = TelegramClient(TOKEN, config)
    safety = client.app.state.runtime.safety
    telegram.safety = safety
    clock = [1000.0]
    safety.clock = lambda: clock[0]
    try:
        with pytest.raises(SafetyDeferred):
            await telegram.request(AsyncMock(side_effect=TelegramUnauthorizedError(method=GetMe(), message=SECRET)))
        clock[0] += 2
        assert await telegram.request(AsyncMock(return_value="ok"), probe=True) == "ok"
        assert safety.status(TOKEN)["state"] == "ready"
        assert any(event["event"] == "telegram_recovered" for event in safety.diagnostics.export()["events"])
        monkeypatch.setattr(safety.diagnostics, "request", lambda *a, **kw: (_ for _ in ()).throw(OSError(SECRET)))
        clock[0] += 2
        assert await telegram.request(AsyncMock(return_value="ok")) == "ok"
        assert safety.diagnostics.status()["state"] == "error"
    finally:
        await telegram.close()


async def test_cloud_http_recovery_and_empty_poll_do_not_flood(client: TestClient, config: Config) -> None:
    status = [500]

    async def handler(request):
        return web.Response(status=status[0], body=b"data")

    telegram = TelegramClient(TOKEN, config, UserSettings(bot_api_mode="cloud"))
    safety = client.app.state.runtime.safety
    telegram.safety = safety
    clock = [1000.0]
    safety.clock = lambda: clock[0]
    try:
        async with fake_api(handler) as base:
            telegram.bot.session.api = TelegramAPIServer.from_base(base)
            with pytest.raises(AppError):
                async for _ in telegram.file_chunks("videos/file.mp4"):
                    pass
            status[0] = 200
            clock[0] += 200
            assert b"".join([chunk async for chunk in telegram.file_chunks("videos/file.mp4")]) == b"data"
        events = safety.diagnostics.export()["events"]
        assert any(event["event"] == "telegram_recovered" and event["method"] == "downloadFile" for event in events)
        for _ in range(10):
            clock[0] += 2
            await telegram.request(AsyncMock(return_value=[]), method="getUpdates")
        assert len(safety.diagnostics.export()["events"]) == len(events)
    finally:
        await telegram.close()


@pytest.mark.parametrize("error_type", [ProxyConnectionError, ProxyTimeoutError, ProxyError])
async def test_actual_cloud_proxy_failure_starts_shared_backoff_without_second_request(client: TestClient, config: Config, monkeypatch, error_type) -> None:
    telegram = TelegramClient(TOKEN, config, UserSettings(bot_api_mode="cloud"))
    safety = client.app.state.runtime.safety
    telegram.safety = safety
    safety.clock = lambda: 1000.0
    calls = []

    class BrokenProxySession:
        def get(self, *args, **kwargs):
            calls.append(True)
            raise error_type(SECRET)

    monkeypatch.setattr(telegram.bot.session, "create_session", AsyncMock(return_value=BrokenProxySession()))
    try:
        with pytest.raises(error_type):
            async for _ in telegram.file_chunks("videos/file.mp4"):
                pass
        assert safety.status(TOKEN)["state"] == "backoff"
        assert safety.status(TOKEN)["retry_at"] is not None
        with pytest.raises(SafetyDeferred):
            async for _ in telegram.file_chunks("videos/file.mp4"):
                pass
        assert len(calls) == 1
        events = safety.diagnostics.export()["events"]
        assert next(event for event in events if event.get("method") == "downloadFile")["error_code"] == "NETWORK_ERROR"
        assert SECRET not in json.dumps(events)
    finally:
        await telegram.close()


@pytest.mark.parametrize("success", [True, False])
async def test_actual_worker_result_correlates_without_media(client: TestClient, success: bool) -> None:
    state, ingest = setup_pipeline(client)
    ingest.message(7, message(), "diagnostic")
    request_id = confirm_all(state)
    worker = state.runtime.worker
    job_id = worker.claim(7)
    source = state.runtime.config.bot_api_root / "synthetic.mp4"
    source.write_bytes(b"abcdef")

    class FakeClient:
        async def get_file(self, file_id):
            if not success:
                raise AppError("CLOUD_FILE_TOO_LARGE", SECRET)
            return str(source)

    await worker.run(FakeClient(), job_id)
    events = state.runtime.diagnostics.export()["events"]
    event = next(event for event in events if event.get("job_id") == job_id)
    assert event["task_id"] == request_id
    assert event["outcome"] == ("succeeded" if success else "failed")
    assert SECRET not in json.dumps(events)


async def test_log_sink_failure_does_not_change_completed_download(client: TestClient, monkeypatch) -> None:
    state, ingest = setup_pipeline(client)
    ingest.message(7, message(), "diagnostic")
    request_id = confirm_all(state)
    job_id = state.runtime.worker.claim(7)
    source = state.runtime.config.bot_api_root / "synthetic.mp4"
    source.write_bytes(b"abcdef")
    monkeypatch.setattr(state.runtime.diagnostics, "_write", lambda _: (_ for _ in ()).throw(OSError(SECRET)))

    class FakeClient:
        async def get_file(self, file_id):
            return str(source)

    await state.runtime.worker.run(FakeClient(), job_id)
    assert state.tasks.detail(request_id)["status"] == "succeeded"
    assert state.runtime.diagnostics.status()["state"] == "error"
