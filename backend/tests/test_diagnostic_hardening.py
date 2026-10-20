import json
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient
from test_pipeline import confirm_all, message, setup_pipeline
from test_request_safety import TOKEN

from app.bot.client import TelegramClient
from app.core.config import Config
from app.services.diagnostics import DiagnosticLog


def test_export_sorts_equivalent_utc_formats_by_instant(tmp_path: Path) -> None:
    log = DiagnosticLog(tmp_path)
    log.directory.mkdir()
    events = [
        {"event": "service_started", "timestamp": "2030-01-01T00:00:00.1+00:00"},
        {"event": "service_stopped", "timestamp": "2030-01-01T00:00:00Z"},
    ]
    log._path().write_text("".join(json.dumps(event) + "\n" for event in events))
    assert log.export()["events"] == events[::-1]


def test_active_cancel_logs_actual_state_outside_write_transaction(client: TestClient, monkeypatch) -> None:
    state, ingest = setup_pipeline(client)
    ingest.message(7, message(), "diagnostic-review")
    request_id = confirm_all(state)
    job_id = state.runtime.worker.claim(7)
    assert job_id is not None
    original_transaction, original_write = state.db.transaction, state.runtime.diagnostics._write
    depth = 0

    @contextmanager
    def transaction():
        nonlocal depth
        with original_transaction() as session:
            depth += 1
            try:
                yield session
            finally:
                depth -= 1

    def write(event):
        assert depth == 0, "Diagnostic file I/O must follow the business commit"
        original_write(event)

    monkeypatch.setattr(state.db, "transaction", transaction)
    monkeypatch.setattr(state.runtime.diagnostics, "_write", write)
    detail = state.tasks.cancel(request_id)
    assert detail["status"] == "cancelling"
    events = state.runtime.diagnostics.export()["events"]
    event = next(event for event in events if event.get("task_id") == request_id)
    assert event["outcome"] == "cancelling"
    assert state.runtime.diagnostics.status()["state"] == "ready"


async def test_diagnostic_status_read_failure_preserves_successful_api_result(
    client: TestClient, config: Config, monkeypatch,
) -> None:
    safety = client.app.state.runtime.safety

    def unavailable(_):
        raise OSError("synthetic diagnostic read error")

    monkeypatch.setattr(safety, "status", unavailable)
    telegram = TelegramClient(TOKEN, config)
    telegram.safety = safety
    try:
        assert await telegram.request(AsyncMock(return_value=123)) == 123
        assert client.app.state.runtime.diagnostics.status()["state"] == "error"
    finally:
        await telegram.close()
