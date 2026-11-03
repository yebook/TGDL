import asyncio
import errno
import json
import socket
import sqlite3
from pathlib import Path

import pytest
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError
from aiogram.methods import GetMe
from aiogram.types import Update
from pydantic import ValidationError
from pydantic_core import PydanticSerializationError
from sqlalchemy import exc as database_errors

from app.core.errors import AppError
from app.services.diagnostics import DiagnosticLog, classify_exception_type, project_event
from app.services.request_safety import SafetyDeferred

SECRET = "123456:PRIVATE https://private.example/user caption filename.mp4 /private/path"


def sdk_serialization_error() -> PydanticSerializationError:
    # A real SDK model recreates the URL-preview incident; dict fakes miss it.
    update = Update.model_validate({
        "update_id": 1,
        "message": {"message_id": 1, "date": 1793611950, "chat": {"id": 42, "type": "private"},
                    "text": SECRET, "link_preview_options": {"url": SECRET}},
    })
    with pytest.raises(PydanticSerializationError) as captured:
        update.model_dump(mode="json", exclude_none=True)
    return captured.value


def test_real_sdk_serialization_evidence_survives_disk_and_export(tmp_path: Path) -> None:
    log = DiagnosticLog(tmp_path)
    log.runtime_result("updates", stage="updates_serialize", error=sdk_serialization_error(),
                       mode="local", method="getUpdates", scope=SECRET,
                       retry_at="2026-11-02T10:00:05+00:00")
    events = DiagnosticLog(tmp_path).export()["events"]
    assert len(events) == 1
    event = events[0]
    assert (event["event"], event["component"], event["stage"]) == ("runtime_failure", "updates", "updates_serialize")
    assert (event["exception_type"], event["error_code"]) == ("pydantic_serialization", "SERIALIZATION_ERROR")
    assert (event["method"], event["mode"], event["retry_at"]) == ("getUpdates", "local", "2026-11-02T10:00:05+00:00")
    assert SECRET not in log._path().read_text()
    assert SECRET not in json.dumps(events)


def test_actual_pydantic_validation_and_json_decoder_are_distinct(tmp_path: Path) -> None:
    log = DiagnosticLog(tmp_path)
    with pytest.raises(ValidationError) as validation:
        Update.model_validate({"update_id": SECRET})
    with pytest.raises(json.JSONDecodeError) as decoder:
        json.loads(SECRET)
    for error in (validation.value, decoder.value):
        log.runtime_result("updates", stage="updates_serialize", error=error)
    events = log.export()["events"]
    assert [event["exception_type"] for event in events] == ["pydantic_validation", "json_decode"]
    assert all(event["error_code"] == "VALIDATION_ERROR" for event in events)
    assert SECRET not in log._path().read_text()


@pytest.mark.parametrize("error,kind,code", [
    (sqlite3.OperationalError(SECRET), "database_operational", "DATABASE_ERROR"),
    (sqlite3.IntegrityError(SECRET), "database_integrity", "DATABASE_ERROR"),
    (sqlite3.DatabaseError(SECRET), "database", "DATABASE_ERROR"),
    (database_errors.OperationalError(SECRET, {}, Exception(SECRET)), "database_operational", "DATABASE_ERROR"),
    (database_errors.IntegrityError(SECRET, {}, Exception(SECRET)), "database_integrity", "DATABASE_ERROR"),
    (database_errors.TimeoutError(SECRET), "database_timeout", "DATABASE_ERROR"),
    (database_errors.SQLAlchemyError(SECRET), "database", "DATABASE_ERROR"),
    (TimeoutError(SECRET), "timeout", "PROCESSING_ERROR"),
    (KeyError(SECRET), "python_lookup", "PROCESSING_ERROR"),
    (AttributeError(SECRET), "python_lookup", "PROCESSING_ERROR"),
    (TypeError(SECRET), "python_type", "PROCESSING_ERROR"),
    (ValueError(SECRET), "python_value", "PROCESSING_ERROR"),
    (RuntimeError(SECRET), "python_runtime", "PROCESSING_ERROR"),
    (PermissionError(SECRET), "filesystem_permission", "PERMISSION_DENIED"),
    (FileNotFoundError(SECRET), "filesystem_missing", "STORAGE_ERROR"),
    (OSError(errno.ENOSPC, SECRET), "filesystem", "NO_SPACE"),
])
def test_processing_classification_is_not_a_network_failure(tmp_path: Path, error: Exception, kind: str, code: str) -> None:
    log = DiagnosticLog(tmp_path)
    log.runtime_result("polling", stage="updates_persist", error=error)
    event = log.export()["events"][0]
    assert (event["exception_type"], event["error_code"], event["stage"]) == (kind, code, "updates_persist")
    assert "reason" not in event
    assert SECRET not in log._path().read_text()


@pytest.mark.parametrize("stage", ["identity_request", "updates_receive", "callback_answer", "notification_send"])
def test_transport_stage_timeout_keeps_network_evidence(tmp_path: Path, stage: str) -> None:
    log = DiagnosticLog(tmp_path)
    log.runtime_result("connection", stage=stage, error=TimeoutError(SECRET), mode="cloud")
    event = log.export()["events"][0]
    assert (event["error_code"], event["reason"], event["stage"], event["exception_type"]) == (
        "NETWORK_ERROR", "timeout", stage, "timeout")


def test_identity_deadline_wrapper_and_sdk_cause_are_safe(tmp_path: Path) -> None:
    log = DiagnosticLog(tmp_path)
    deadline = AppError("LOCAL_API_OPERATION_TIMEOUT", SECRET)
    deadline.__cause__ = TimeoutError(SECRET)
    network = TelegramNetworkError(method=GetMe(), message=SECRET)
    network.__cause__ = socket.gaierror(-2, SECRET)
    log.runtime_result("connection", stage="identity_request", error=deadline)
    log.runtime_result("connection", stage="identity_request", error=network)
    events = log.export()["events"]
    assert [event["reason"] for event in events] == ["timeout", "dns"]
    assert [event["exception_type"] for event in events] == ["app_error", "telegram_network"]
    assert all(event["stage"] == "identity_request" and event["error_code"] == "NETWORK_ERROR" for event in events)
    assert SECRET not in log._path().read_text()


def test_structured_socket_errno_is_network_evidence(tmp_path: Path) -> None:
    log = DiagnosticLog(tmp_path)
    log.runtime_result("connection", stage="identity_request", error=OSError(errno.ENETUNREACH, SECRET))
    event = log.export()["events"][0]
    assert (event["error_code"], event["reason"], event["exception_type"]) == ("NETWORK_ERROR", "tcp", "network")


def test_invalid_runtime_component_and_stage_never_enter_state_or_disk(tmp_path: Path) -> None:
    log = DiagnosticLog(tmp_path)
    log.runtime_result(SECRET, stage="identity_request", error=RuntimeError(SECRET))
    log.runtime_result("connection", stage=SECRET, error=RuntimeError(SECRET))
    assert not log.runtime_failures and not log.directory.exists()


def test_existing_request_failures_gain_fixed_exception_types(tmp_path: Path) -> None:
    log = DiagnosticLog(tmp_path)
    log.request("local", "getMe", error=TelegramBadRequest(method=GetMe(), message=SECRET))
    log.request("local", "getMe", error=TelegramNetworkError(method=GetMe(), message=SECRET))
    assert [event["exception_type"] for event in log.export()["events"]] == ["telegram_bad_request", "telegram_network"]
    assert SECRET not in log._path().read_text()


def test_empty_success_has_no_io_and_transport_cannot_clear_processing_failure(tmp_path: Path) -> None:
    log = DiagnosticLog(tmp_path)
    for _ in range(100):
        log.runtime_result("polling", stage="poll_status", mode="local", scope=SECRET)
    assert not log.directory.exists()
    log.runtime_result("polling", stage="updates_persist", error=sqlite3.OperationalError(SECRET), mode="local", scope=SECRET)
    log.request("local", "getUpdates", empty=True, scope=SECRET)
    log.runtime_result("connection", stage="identity_publish", mode="local", scope=SECRET)
    assert [event["event"] for event in log.export()["events"]] == ["runtime_failure"]
    log.runtime_result("polling", stage="poll_status", mode="local", scope=SECRET)
    event = log.export()["events"][-1]
    assert event["event"] == "runtime_recovered" and event["stage"] == "updates_persist"


@pytest.mark.parametrize("component,failed,completed", [
    ("dispatch", "dispatch_run", ("dispatch_prepare",)),
    ("polling", "callback_process", ("pending_read", "updates_receive", "poll_status")),
    ("notification", "notification_send", ("notification_prepare", "notification_claim")),
])
def test_skipped_stage_cannot_recover_an_operation(tmp_path: Path, component: str, failed: str,
                                                 completed: tuple[str, ...]) -> None:
    log = DiagnosticLog(tmp_path)
    log.runtime_result(component, stage=failed, error=RuntimeError(SECRET))
    log.runtime_result(component, stage=completed[-1], completed_stages=completed)
    assert len(log.runtime_failures) == 1
    assert [event["event"] for event in log.export()["events"]] == ["runtime_failure"]
    log.runtime_result(component, stage=failed, completed_stages=(*completed, failed))
    assert not log.runtime_failures
    assert log.export()["events"][-1]["event"] == "runtime_recovered"


def test_first_repeat_interval_and_recovery_retain_last_failed_stage(tmp_path: Path) -> None:
    clock = [1000.0]
    log = DiagnosticLog(tmp_path, clock=lambda: clock[0])
    for _ in range(20):
        log.runtime_result("polling", stage="updates_persist", error=sqlite3.OperationalError(SECRET),
                           method="getUpdates", mode="local", scope=SECRET)
    assert len(log._path().read_text().splitlines()) == 1
    clock[0] += 61
    log.runtime_result("polling", stage="updates_persist", error=sqlite3.OperationalError(SECRET),
                       method="getUpdates", mode="local", scope=SECRET)
    assert len(log._path().read_text().splitlines()) == 3
    log.runtime_result("polling", stage="inbox_mark", error=RuntimeError(SECRET),
                       mode="local", task_id="a" * 16, scope=SECRET)
    log.runtime_result("polling", stage="poll_status", mode="local", scope=SECRET)
    events = log.export()["events"]
    assert [event["event"] for event in events] == [
        "runtime_failure", "event_summary", "runtime_failure", "runtime_failure", "runtime_recovered"]
    assert events[1]["suppressed_count"] == 19
    assert events[-1]["stage"] == "inbox_mark" and events[-1]["task_id"] == "a" * 16
    assert len(log.runtime_failures) == 0


def test_stages_exception_types_scopes_and_modes_keep_distinct_first_evidence(tmp_path: Path) -> None:
    log = DiagnosticLog(tmp_path)
    for stage, error in (("pending_read", RuntimeError(SECRET)), ("message_process", RuntimeError(SECRET)),
                         ("message_process", TypeError(SECRET))):
        log.runtime_result("polling", stage=stage, error=error, scope=SECRET)
    log.runtime_result("polling", stage="message_process", error=TypeError(SECRET), scope="other-private-token")
    log.runtime_result("polling", stage="message_process", error=TypeError(SECRET), scope=SECRET, mode="local")
    assert len(log.export()["events"]) == 5
    log.runtime_result("polling", stage="poll_status", scope=SECRET, mode="cloud")
    assert len(log.export()["events"]) == 5
    log.runtime_result("polling", stage="poll_status", scope=SECRET)
    assert len(log.runtime_failures) == 2
    assert SECRET not in log._path().read_text()
    assert "other-private-token" not in log._path().read_text()


def test_projection_blocks_malicious_class_names_fields_and_tampered_disk(tmp_path: Path) -> None:
    class PrivateException(Exception):
        def __str__(self) -> str:
            raise AssertionError("Logging must not format exceptions")

    PrivateException.__name__ = SECRET
    log = DiagnosticLog(tmp_path)
    error = PrivateException(SECRET)
    assert classify_exception_type(error) == "unknown"
    log.runtime_result("polling", stage="message_process", error=error, task_id=SECRET, scope=SECRET)
    log.emit("runtime_failure", component=SECRET, exception_type=SECRET, stage=SECRET,
             token=SECRET, traceback=SECRET, message=SECRET)
    malicious = {"event": "runtime_failure", "timestamp": "2026-11-02T00:00:00Z", "component": SECRET,
                 "exception_type": SECRET, "stage": SECRET, "scope": SECRET, "message": SECRET}
    assert project_event(malicious) == {"event": "runtime_failure", "timestamp": "2026-11-02T00:00:00Z"}
    assert SECRET not in log._path().read_text()
    with log._path().open("a") as stream:
        stream.write(json.dumps(malicious) + "\n")
    events = log.export()["events"]
    assert SECRET not in json.dumps(events)
    assert next(event for event in events if event.get("stage") == "message_process")["exception_type"] == "unknown"


@pytest.mark.parametrize("error", [asyncio.CancelledError(SECRET), GeneratorExit(SECRET),
                                  SafetyDeferred(SECRET, reason="pacing"), SafetyDeferred(SECRET, reason="backoff"),
                                  SafetyDeferred(SECRET, reason="auth_paused", sent=True)])
def test_cancellation_and_safety_deferrals_do_not_create_or_clear_runtime_failures(tmp_path: Path, error: BaseException) -> None:
    log = DiagnosticLog(tmp_path)
    log.runtime_result("polling", stage="updates_receive", error=error)
    assert not log.directory.exists()
    log.runtime_result("polling", stage="updates_persist", error=RuntimeError(SECRET))
    log.runtime_result("polling", stage="updates_receive", error=error)
    assert len(log.runtime_failures) == 1
    assert len(log.export()["events"]) == 1


def test_sink_failure_is_isolated_and_does_not_format_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    log = DiagnosticLog(tmp_path)

    def unavailable(_event: object) -> None:
        raise OSError(errno.ENOSPC, SECRET)

    monkeypatch.setattr(log, "_write", unavailable)
    log.runtime_result("notification", stage="notification_commit", error=RuntimeError(SECRET))
    assert log.status()["state"] == "error"
    log.runtime_result("notification", stage="notification_commit")
    assert not log.runtime_failures
    assert log.status()["state"] == "error"


def test_runtime_cache_is_bounded_and_clear_discards_all_recovery_state(tmp_path: Path) -> None:
    log = DiagnosticLog(tmp_path)
    for number in range(300):
        log.runtime_result("polling", stage="pending_read", error=RuntimeError(SECRET), scope=str(number))
    assert len(log.runtime_failures) == 256
    assert len(log.repeated) <= 256
    log.request("local", "getMe", error=TimeoutError(SECRET), scope=SECRET)
    log.clear()
    assert not log.runtime_failures and not log.failures and not log.repeated
    log.runtime_result("polling", stage="poll_status", scope="299")
    log.request("local", "getMe", empty=True, scope=SECRET)
    assert [event["event"] for event in log.export()["events"]] == ["logs_cleared"]


def test_retention_expires_runtime_and_transport_state_without_resurrecting_summaries(tmp_path: Path) -> None:
    clock = [1000.0]
    log = DiagnosticLog(tmp_path, clock=lambda: clock[0], retention_days=1)
    for _ in range(2):
        log.runtime_result("polling", stage="pending_read", error=RuntimeError(SECRET))
        log.request("local", "getMe", error=TimeoutError(SECRET))
    clock[0] += 86401
    log.maintain(1, force=True)
    assert not log.runtime_failures and not log.failures and not log.repeated
    log.runtime_result("polling", stage="poll_status")
    log.request("local", "getMe", empty=True)
    assert log.export()["events"] == []


def test_recovery_cannot_resurrect_expired_runtime_evidence_without_maintenance(tmp_path: Path) -> None:
    clock = [1000.0]
    log = DiagnosticLog(tmp_path, clock=lambda: clock[0], retention_days=1)
    log.runtime_result("polling", stage="pending_read", error=RuntimeError(SECRET))
    clock[0] += 86401
    log.runtime_result("polling", stage="poll_status")
    assert [event["event"] for event in log.export()["events"]] == ["runtime_failure"]
