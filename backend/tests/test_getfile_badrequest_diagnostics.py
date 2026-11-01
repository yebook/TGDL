"""Safe local getFile rejection evidence without changing acquisition behavior."""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramConflictError,
    TelegramNetworkError,
    TelegramRetryAfter,
    TelegramUnauthorizedError,
)
from aiogram.methods import GetFile, GetMe

from app.bot.client import TelegramClient
from app.core.errors import AppError
from app.schemas.contracts import UserSettings
from app.services.diagnostics import DiagnosticLog, classify_error, project_event

TOKEN = "123456:synthetic-never-real-token"
FILE_ID = "private-file-id-do-not-export"
SECRET = "https://private.example/bot123456:secret /cache/private/video.mp4 caption sha256-private"
KNOWN = [
    ("file_id not specified", "getfile_file_id_missing"),
    ("invalid file_id", "getfile_file_id_invalid"),
    ("file is too big", "getfile_file_too_big"),
    ("wrong file_id or the file is temporarily unavailable", "getfile_file_id_or_unavailable"),
]


@pytest.mark.parametrize(("message", "reason"), KNOWN)
@pytest.mark.parametrize("prefix", ["", "Bad Request: "])
def test_known_local_getfile_rejections_survive_disk_and_export(tmp_path: Path, message, reason, prefix):
    log = DiagnosticLog(tmp_path)
    error = TelegramBadRequest(method=GetFile(file_id=FILE_ID), message=prefix + message)
    log.request("local", "getFile", error=error, duration_ms=123)
    on_disk = (tmp_path / "logs/events.jsonl").read_text(encoding="utf-8")
    persisted = json.loads(on_disk)
    exported = DiagnosticLog(tmp_path).export()["events"]
    assert exported == [persisted]
    assert {key: persisted[key] for key in ("event", "outcome", "error_code", "http_status", "stage", "reason")} == {
        "event": "telegram_request", "outcome": "failed", "error_code": "BAD_REQUEST",
        "http_status": 400, "stage": "response", "reason": reason,
    }
    assert persisted["duration_ms"] == 123 and persisted["level"] == "warning"
    assert message not in on_disk and FILE_ID not in on_disk
    assert classify_error(error) == ("BAD_REQUEST", 400)


@pytest.mark.parametrize("message", [
    SECRET,
    "Bad Request: " + SECRET,
    "Bad Request: invalid file_id " + SECRET,
    SECRET + " file is too big",
    "Bad Request: file is too big\n" + SECRET,
    "Bad Request: wrong file_id or the file is temporarily unavailable: " + SECRET,
    "Bad Request: Bad Request: invalid file_id",
    "Bad Request: FILE_REFERENCE_EXPIRED",  # Not a verified distinct pinned getFile response.
    "Bad Request: Invalid file_id",
    " Bad Request: invalid file_id",
    "Bad Request: invalid file_id ",
    "Bad Request: invalid file_\u200bid",
    "",
])
def test_unknown_or_decorated_messages_have_fixed_reason_and_no_text(tmp_path: Path, message):
    log = DiagnosticLog(tmp_path)
    log.request("local", "getFile", error=TelegramBadRequest(method=GetFile(file_id=FILE_ID), message=message))
    on_disk = (tmp_path / "logs/events.jsonl").read_text(encoding="utf-8")
    event = log.export()["events"][0]
    assert event["reason"] == "getfile_bad_request_unknown"
    assert event["error_code"] == "BAD_REQUEST" and event["http_status"] == 400
    for private in (SECRET, FILE_ID, TOKEN, "/cache/private", "FILE_REFERENCE_EXPIRED"):
        assert private not in on_disk and private not in json.dumps(event)
    if message:
        assert message not in on_disk


@pytest.mark.parametrize(("mode", "method"), [
    ("cloud", "getFile"), ("local", "getMe"), ("local", "sendMessage"), ("cloud", "getMe"),
])
def test_other_transports_and_methods_retain_existing_classification(tmp_path: Path, mode, method):
    log = DiagnosticLog(tmp_path)
    log.request(mode, method, error=TelegramBadRequest(method=GetMe(), message="Bad Request: invalid file_id"))
    event = log.export()["events"][0]
    assert event["error_code"] == "BAD_REQUEST" and event["http_status"] == 400
    assert event["stage"] == "response" and "reason" not in event


@pytest.mark.parametrize(("error", "code", "status", "reason"), [
    (TelegramConflictError(method=GetFile(file_id=FILE_ID), message=SECRET), "CONFLICT", 409, None),
    (TelegramUnauthorizedError(method=GetFile(file_id=FILE_ID), message=SECRET), "UNAUTHORIZED", 401, "auth_paused"),
    (TelegramRetryAfter(method=GetFile(file_id=FILE_ID), message=SECRET, retry_after=10), "RATE_LIMITED", 429, "cooldown"),
    (TelegramNetworkError(method=GetFile(file_id=FILE_ID), message=SECRET), "NETWORK_ERROR", None, "network_unknown"),
    (AppError("BAD_REQUEST", SECRET), "BAD_REQUEST", None, None),
])
def test_other_error_types_are_unchanged(tmp_path: Path, error, code, status, reason):
    log = DiagnosticLog(tmp_path)
    log.request("local", "getFile", error=error)
    event = log.export()["events"][0]
    assert event["error_code"] == code and event.get("http_status") == status
    assert event.get("reason") == reason
    assert SECRET not in json.dumps(event)


def test_overridden_non400_status_does_not_receive_getfile_badrequest_reason(tmp_path: Path):
    log = DiagnosticLog(tmp_path)
    log.request("local", "getFile", error=TelegramBadRequest(method=GetFile(file_id=FILE_ID),
                message="Bad Request: invalid file_id"), http_status=503)
    event = log.export()["events"][0]
    assert event["error_code"] == "SERVER_ERROR" and event["http_status"] == 503
    assert "reason" not in event


def test_finite_reasons_split_dedup_groups_and_reproject_untrusted_disk(tmp_path: Path):
    log = DiagnosticLog(tmp_path)
    for message in ["invalid file_id", "invalid file_id", "file is too big", "file is too big", SECRET, SECRET]:
        log.request("local", "getFile", error=TelegramBadRequest(method=GetFile(file_id=FILE_ID), message=message))
    # An invalid reason cannot enter through the writer or an edited log line.
    log.emit("telegram_request", reason=SECRET, method="getFile", mode="local")
    assert SECRET not in (tmp_path / "logs/events.jsonl").read_text(encoding="utf-8")
    with (tmp_path / "logs/events.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"event": "telegram_request", "timestamp": "2026-11-01T00:00:00Z",
                                 "reason": SECRET, "message": SECRET, "file_id": FILE_ID}) + "\n")
    events = log.export()["events"]
    direct = [event for event in events if event["event"] == "telegram_request" and event.get("error_code")]
    summaries = [event for event in events if event["event"] == "event_summary"]
    assert {event["reason"] for event in direct} == {
        "getfile_file_id_invalid", "getfile_file_too_big", "getfile_bad_request_unknown",
    }
    assert len(direct) == len(summaries) == 3
    assert all(event["suppressed_count"] == 1 for event in summaries)
    assert {event["reason"] for event in summaries} == {event["reason"] for event in direct}
    assert SECRET not in json.dumps(events) and FILE_ID not in json.dumps(events)
    projected = project_event({"event": "telegram_request", "timestamp": "2026-11-01T00:00:00Z",
                               "reason": "getfile_file_id_invalid/" + SECRET})
    assert "reason" not in projected


@pytest.mark.parametrize("mode", ["local", "cloud"])
@pytest.mark.parametrize(("message", "reason"), [
    ("Bad Request: invalid file_id", "getfile_file_id_invalid"),
    ("Bad Request: wrong file_id or the file is temporarily unavailable", "getfile_file_id_or_unavailable"),
    (SECRET, "getfile_bad_request_unknown"),
])
async def test_getfile_sdk_error_is_logged_and_reraised_without_behavior_change(config, tmp_path, monkeypatch,
                                                                              mode, message, reason):
    telegram = TelegramClient(TOKEN, config, UserSettings(bot_api_mode=mode))
    log = DiagnosticLog(tmp_path)
    before, observe = AsyncMock(), Mock()
    telegram.safety = SimpleNamespace(diagnostics=log, before=before, observe=observe)
    error = TelegramBadRequest(method=GetFile(file_id=FILE_ID), message=message)
    operation = AsyncMock(side_effect=error)
    monkeypatch.setattr(telegram.bot.session, "make_request", operation)
    try:
        with pytest.raises(TelegramBadRequest) as caught:
            await telegram.get_file(FILE_ID)
        assert caught.value is error
        operation.assert_awaited_once()
        before.assert_awaited_once()
        observe.assert_called_once_with(TOKEN, error)
    finally:
        await telegram.close()
    event = log.export()["events"][0]
    assert event["error_code"] == "BAD_REQUEST" and event["http_status"] == 400
    assert event.get("reason") == (reason if mode == "local" else None)
    assert FILE_ID not in json.dumps(event) and TOKEN not in json.dumps(event) and SECRET not in json.dumps(event)


@pytest.mark.parametrize(("message", "expected_code", "logged_code"), [
    ("TGD_CACHE_CLEANUP_BUSY", "CACHE_CLEANUP_BUSY", "CACHE_CLEANUP_BUSY"),
    ("TGD_ACQUISITION_CANCELLED", "ACQUISITION_CANCELLED", "CANCELLED"),
    ("TGD_ACQUISITION_INVALID", "ACQUISITION_REJECTED", "ACQUISITION_REJECTED"),
])
@pytest.mark.parametrize("prefix", ["", "Bad Request: "])
async def test_known_native_getfile_controls_are_converted_before_diagnostics(config, tmp_path, monkeypatch,
                                                                           message, expected_code, logged_code, prefix):
    telegram = TelegramClient(TOKEN, config, UserSettings(bot_api_mode="local"))
    log = DiagnosticLog(tmp_path)
    before, observe = AsyncMock(), Mock()
    telegram.safety = SimpleNamespace(diagnostics=log, before=before, observe=observe)
    error = TelegramBadRequest(method=GetFile(file_id=FILE_ID), message=prefix + message)
    operation = AsyncMock(side_effect=error)
    monkeypatch.setattr(telegram.bot.session, "make_request", operation)
    try:
        with pytest.raises(AppError) as caught:
            await telegram.get_file(FILE_ID, generation="synthetic", acquisition_id="a" * 64)
        assert caught.value.code == expected_code
        operation.assert_awaited_once()
        before.assert_awaited_once()
        observe.assert_not_called()
    finally:
        await telegram.close()
    event = log.export()["events"][0]
    assert event["error_code"] == logged_code
    assert "reason" not in event and "http_status" not in event
    assert message not in (tmp_path / "logs/events.jsonl").read_text(encoding="utf-8")
