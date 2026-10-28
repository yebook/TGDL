import asyncio
import json
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, select
from test_cache_cleanup import published, ready
from test_pipeline import confirm_all, message, setup_pipeline

from app.core.security import private_file
from app.db.models import CacheCleanup, Job, Notification
from app.schemas.contracts import SettingsPatch
from app.services import local_api


def test_idle_group_scheduler_never_reserves_a_write_or_decrypts_settings(client: TestClient, monkeypatch) -> None:
    state = client.app.state
    statements: list[str] = []
    event.listen(state.db.engine, "before_cursor_execute",
                 lambda _c, _u, sql, _p, _x, _m: statements.append(sql))
    monkeypatch.setattr(state.settings, "read", Mock(side_effect=AssertionError("Idle settings read")))
    for _ in range(3):
        state.tasks.freeze_due()
    assert len(statements) == 3
    assert all(sql.lstrip().startswith("SELECT") for sql in statements)


def test_unchanged_generation_is_not_rewritten_but_restart_remains_durable(client: TestClient, monkeypatch) -> None:
    runtime = client.app.state.runtime
    marker = runtime.config.bot_api_root / ".tgd-api-generation"
    marker.write_text("first")
    runtime.recover_generation(initial=True)
    persisted = runtime.config.data_dir / "api-generation.json"
    original = Path.write_text
    writes = []

    def record(path, *args, **kwargs):
        if path == persisted:
            writes.append(path)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", record)
    runtime.worker.recover = Mock()
    for _ in range(3):
        runtime.recover_generation()
    assert not writes
    runtime.worker.recover.assert_not_called()
    marker.write_text("second")
    runtime.recover_generation()
    assert len(writes) == 1
    assert json.loads(persisted.read_text()) == {"generation": "second"}
    runtime.worker.recover.assert_called_once_with(True)


@pytest.mark.asyncio
async def test_idle_tick_skips_dispatch_checks_then_downloads_new_confirmation(client: TestClient, monkeypatch) -> None:
    state = client.app.state
    runtime = state.runtime
    patch = SettingsPatch(revision=1, bot_token="777777:synthetic-idle", allowed_user_ids=[42], bot_api_mode="cloud")
    state.settings.save(patch, state.settings.candidate(patch))
    runtime.client = Mock(mode="cloud")
    runtime.bot_id = 7
    runtime.client_signature = state.settings.client_signature(state.settings.read()[1])
    runtime.bot_status["state"] = "online"
    proxy = Mock(wraps=state.settings.proxy_status)
    claim = Mock(wraps=runtime.worker.claim)
    monkeypatch.setattr(state.settings, "proxy_status", proxy)
    monkeypatch.setattr(runtime.worker, "claim", claim)
    run = AsyncMock()
    monkeypatch.setattr(runtime.worker, "run", run)

    async def stop(delay):
        assert delay == 0.5  # Idle optimization must not slow scheduling.
        runtime.stopping = True

    monkeypatch.setattr("app.workers.runtime.asyncio.sleep", stop)
    statements: list[str] = []
    event.listen(state.db.engine, "before_cursor_execute",
                 lambda _c, _u, sql, _p, _x, _m: statements.append(sql))
    await runtime.tick_loop()
    proxy.assert_not_called()
    claim.assert_not_called()
    assert all(not sql.startswith("BEGIN") for sql in statements)

    runtime.ingest.message(7, message(), "new-media")
    request_id = confirm_all(state)
    runtime.stopping = False
    await runtime.tick_loop()
    await asyncio.gather(*runtime.running.values())
    assert claim.call_count >= 1
    run.assert_awaited_once()
    assert state.tasks.detail(request_id)["status"] == "fetching"


@pytest.mark.asyncio
async def test_empty_cache_cleanup_does_not_probe_generation(client: TestClient, monkeypatch) -> None:
    runtime = client.app.state.runtime
    runtime.client, runtime.bot_id = Mock(mode="local"), 7
    probe = Mock(side_effect=AssertionError("No cache item to validate"))
    monkeypatch.setattr(runtime, "progress_generation", probe)
    assert not await runtime.cleanup_cache()
    probe.assert_not_called()


@pytest.mark.parametrize("age,available", [(0, True), (4.99, True), (5, True), (15, True), (15.01, False)])
def test_less_frequent_heartbeat_keeps_original_stale_detection(config, monkeypatch, age, available) -> None:
    control = config.data_dir / "control"
    control.mkdir()
    config.local_api_control_dir = control
    private_file(control / "applied.json", json.dumps({
        "version": 1, "state": "running", "generation": "child", "heartbeat_at": 1000 - age,
    }).encode())
    monkeypatch.setattr(local_api, "time", SimpleNamespace(time=lambda: 1000))
    assert bool(local_api.supervisor_status(config)) is available


@pytest.mark.asyncio
async def test_generation_recovery_waits_for_owner_then_retries_failed_marker_write(client, monkeypatch) -> None:
    runtime = client.app.state.runtime
    marker = runtime.config.bot_api_root / ".tgd-api-generation"
    marker.write_text("first")
    runtime.recover_generation(initial=True)
    persisted = runtime.config.data_dir / "api-generation.json"
    runtime.worker.recover = Mock()
    owner = asyncio.create_task(asyncio.Event().wait())
    runtime.running["owned"] = owner
    marker.write_text("second")
    runtime.recover_generation()
    runtime.worker.recover.assert_not_called()
    assert runtime.api_generation == "first"
    owner.cancel()
    await asyncio.gather(owner, return_exceptions=True)

    original = Path.write_text

    def fail_marker(path, *args, **kwargs):
        if path == persisted:
            raise OSError("temporary generation marker I/O failure")
        return original(path, *args, **kwargs)

    with monkeypatch.context() as temporary:
        temporary.setattr(Path, "write_text", fail_marker)
        with pytest.raises(OSError):
            runtime.recover_generation()
    assert runtime.api_generation == "first"
    assert json.loads(persisted.read_text())["generation"] == "first"
    runtime.recover_generation()
    assert runtime.api_generation == "second"
    assert json.loads(persisted.read_text())["generation"] == "second"
    runtime.worker.recover.assert_called_with(True)


def test_counts_cover_all_live_states_in_one_query_without_counting_terminal_history(client) -> None:
    state, ingest = setup_pipeline(client)
    states = ["fetching", "saving", "cancelling", "recovery_blocked", "queued", "retry_wait",
              "waiting_existing", "succeeded", "failed", "cancelled", "already_exists"]
    for index in range(1, len(states) + 1):
        ingest.message(7, message(index, "batch"), f"mixed-{index}")
    confirm_all(state)
    with state.db.transaction() as session:
        for job, status in zip(session.scalars(select(Job)), states, strict=True):
            job.state = status
    statements = []
    event.listen(state.db.engine, "before_cursor_execute",
                 lambda _c, _u, sql, _p, _x, _m: statements.append(sql))
    assert state.runtime.counts() == (4, 1, 3)
    assert len(statements) == 1
    assert "WHERE jobs.state IN" in statements[0]


@pytest.mark.asyncio
async def test_tick_cleans_published_cache_even_without_pending_downloads(client, monkeypatch) -> None:
    state, _, _, job_id, _ = await published(client)
    runtime = state.runtime
    transport = ready(runtime, monkeypatch)
    claim = Mock(side_effect=AssertionError("Empty download queue must not claim"))
    monkeypatch.setattr(runtime.worker, "claim", claim)

    async def stop(delay):
        assert delay == 0.5
        runtime.stopping = True

    monkeypatch.setattr("app.workers.runtime.asyncio.sleep", stop)
    assert runtime.counts() == (0, 0, 0)
    await runtime.tick_loop()
    transport.delete_file_cache.assert_awaited_once()
    claim.assert_not_called()
    with state.db.sessions() as session:
        assert session.get(CacheCleanup, job_id) is None
        assert session.get(Job, job_id).state == "succeeded"


@pytest.mark.asyncio
async def test_empty_outbox_avoids_dispatch_reads_and_writes_then_sends_newly_due_message(client, monkeypatch) -> None:
    state = client.app.state
    runtime = state.runtime
    patch = SettingsPatch(revision=1, bot_token="777777:synthetic-notify", allowed_user_ids=[42], bot_api_mode="cloud")
    state.settings.save(patch, state.settings.candidate(patch))
    runtime.client_signature = state.settings.client_signature(state.settings.read()[1])
    runtime.bot_id = 7
    with state.db.transaction() as session:
        note = Notification(event_key="due-later", chat_id=42, payload={"bot_id": 7, "text": "ready"},
                            next_run_at=time.time() + 3600)
        session.add(note)
        session.flush()
        note_id = note.id
    statements = []
    event.listen(state.db.engine, "before_cursor_execute",
                 lambda _c, _u, sql, _p, _x, _m: statements.append(sql))
    read = Mock(wraps=state.settings.read)
    proxy = Mock(wraps=state.settings.proxy_status)
    claim = Mock(wraps=runtime.next_notification)
    monkeypatch.setattr(state.settings, "read", read)
    monkeypatch.setattr(state.settings, "proxy_status", proxy)
    monkeypatch.setattr(runtime, "next_notification", claim)

    async def send(chat_id, text, callback):
        assert (chat_id, text, callback) == (42, "ready", None)
        runtime.stopping = True
        return 123

    runtime.client = Mock(mode="cloud", send=AsyncMock(side_effect=send))

    async def make_due(delay):
        assert delay == 0.5
        read.assert_not_called()
        proxy.assert_not_called()
        claim.assert_not_called()
        assert statements and all(sql.lstrip().startswith("SELECT") for sql in statements)
        with state.db.transaction() as session:
            session.get(Notification, note_id).next_run_at = 0

    monkeypatch.setattr("app.workers.runtime.asyncio.sleep", make_due)
    await runtime.notify_loop()
    runtime.client.send.assert_awaited_once()
    claim.assert_called_once_with(7)
    with state.db.sessions() as session:
        note = session.get(Notification, note_id)
        assert (note.state, note.attempts, note.message_id) == ("sent", 1, 123)


def test_notification_due_probe_does_not_hide_old_bot_work_from_obsoleting(client) -> None:
    runtime = client.app.state.runtime
    with runtime.db.transaction() as session:
        note = Notification(event_key="previous-bot", chat_id=42, payload={"bot_id": 8, "text": "obsolete"})
        session.add(note)
        session.flush()
        note_id = note.id
    assert runtime.notification_due()
    assert runtime.next_notification(7) is None
    assert not runtime.notification_due()
    with runtime.db.sessions() as session:
        assert session.get(Notification, note_id).state == "obsolete"
