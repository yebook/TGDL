import asyncio
import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import TelegramNetworkError
from aiogram.methods import GetMe
from fastapi.testclient import TestClient

from app.core.errors import AppError
from app.schemas.contracts import SettingsPatch
from app.services.diagnostics import DiagnosticLog


def record(timestamp: float) -> str:
    return json.dumps({"event": "service_started", "timestamp": datetime.fromtimestamp(timestamp, UTC).isoformat()}) + "\n"


def test_age_filters_events_not_mtime_and_preserves_export_overflow(tmp_path: Path) -> None:
    now = 2_000_000.0
    log = DiagnosticLog(tmp_path, clock=lambda: now)
    log._check()
    cutoff = now - 7 * 86400
    for index in range(4):
        log._path(index).write_text(record(cutoff - 1) + record(cutoff) + record(now) * 300)
        os.utime(log._path(index), (1, 1))
    log.maintain(7)
    lines = [line for index in range(4) for line in log._path(index).read_text().splitlines()]
    assert len(lines) == 1204  # Retention must not use the 1000-event export window.
    assert all(datetime.fromisoformat(json.loads(line)["timestamp"]).timestamp() >= cutoff for line in lines)
    assert log.export()["truncated"]
    assert log.status()["last_cleanup_at"] is not None


def test_zero_disables_age_only_and_schedule_uses_monotonic(tmp_path: Path) -> None:
    wall, steady = [2_000_000.0], [0.0]
    log = DiagnosticLog(tmp_path, clock=lambda: wall[0], monotonic=lambda: steady[0], max_file_bytes=250)
    log.emit("service_started")
    log.maintain(0)
    wall[0] += 86400 * 100
    log.maintain(0, force=True)
    assert len(log.export()["events"]) == 1
    assert log.status()["next_cleanup_at"] is None
    for _ in range(30):
        log.emit("service_started")
    assert len(list(log.directory.iterdir())) == 4
    log.maintain(7)
    last = log.status()["last_cleanup_at"]
    wall[0] -= 86400
    steady[0] += 3599
    log.maintain(7)
    assert log.status()["last_cleanup_at"] == last
    steady[0] += 1
    log.maintain(7)
    assert log.status()["last_cleanup_at"] != last


def test_clear_resets_summaries_only_fixed_paths_and_continues(tmp_path: Path) -> None:
    log = DiagnosticLog(tmp_path)
    for _ in range(5):
        log.emit("telegram_request", dedupe=True, mode="cloud", method="getMe", outcome="failed")
    log.failures[("secret", "cloud", "getMe")] = log.clock()
    sentinel = log.directory / "unrelated.log"
    sentinel.write_text("keep")
    status = log.clear()
    assert status["state"] == "ready"
    assert not log.repeated and not log.failures
    log.flush()
    assert [event["event"] for event in log.export()["events"]] == ["logs_cleared"]
    log.emit("service_started")
    assert len(log.export()["events"]) == 2
    assert sentinel.read_text() == "keep"


def test_cleanup_discards_old_pending_and_reprojects_corrupt_records(tmp_path: Path) -> None:
    clock = [2_000_000.0]
    log = DiagnosticLog(tmp_path, clock=lambda: clock[0])
    for _ in range(4):
        log.emit("service_started", dedupe=True)
    clock[0] += 8 * 86400
    with log._path().open("a") as stream:
        stream.write("x" * 9000 + "\nnot json\n")
        stream.write(json.dumps({"event": "service_started", "timestamp": datetime.fromtimestamp(clock[0], UTC).isoformat(), "secret": "NO"}) + "\n")
    log.maintain(7)
    events = log.export()["events"]
    assert len(events) == 1 and events[0]["event"] == "service_started"
    assert "NO" not in log._path().read_text()


def test_failure_visible_until_successful_maintenance(tmp_path: Path, monkeypatch) -> None:
    log = DiagnosticLog(tmp_path)
    log.emit("service_started")
    original = log._prune_file
    monkeypatch.setattr(log, "_prune_file", lambda *_: (_ for _ in ()).throw(OSError("private")))
    log.maintain(7)
    log.emit("service_started")
    assert log.status()["state"] == "error"
    assert "private" not in json.dumps(log.status())
    monkeypatch.setattr(log, "_prune_file", original)
    log.maintain(7, force=True)
    assert log.status()["state"] == "ready"
    monkeypatch.setattr(log, "_write", lambda *_: (_ for _ in ()).throw(OSError("private")))
    with pytest.raises(AppError, match="未能全部清理"):
        log.clear()


@pytest.mark.skipif(os.name == "nt", reason="POSIX link semantics")
@pytest.mark.parametrize("kind", ["active", "archive", "temporary", "directory", "hardlink"])
def test_unsafe_targets_never_modify_external_files(tmp_path: Path, kind: str) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "events.jsonl"
    sentinel.write_text("private")
    log = DiagnosticLog(tmp_path)
    if kind == "directory":
        log.directory.symlink_to(outside, target_is_directory=True)
    else:
        log._check()
        target = log._path(1) if kind == "archive" else log.directory / "events.jsonl.cleanup" if kind == "temporary" else log._path()
        if kind == "hardlink":
            os.link(sentinel, target)
        else:
            target.symlink_to(sentinel)
    log.maintain(7)
    with pytest.raises(AppError):
        log.clear()
    assert log.status()["state"] == "error"
    assert sentinel.read_text() == "private"


def test_concurrent_rotation_export_clear_and_maintenance(tmp_path: Path) -> None:
    log = DiagnosticLog(tmp_path, max_file_bytes=700)
    def action(index: int) -> None:
        if index % 11 == 0:
            log.clear()
        elif index % 7 == 0:
            log.maintain(7, force=True)
        elif index % 3 == 0:
            log.export()
        else:
            log.emit("service_started")
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(action, range(200)))
    for path in log.directory.iterdir():
        assert path.stat().st_size <= 700
        for line in path.read_text().splitlines():
            assert json.loads(line)["event"] in {"service_started", "logs_cleared"}


def test_clear_requires_auth_csrf_and_origin(client: TestClient, authenticated: TestClient) -> None:
    # Both fixtures intentionally share this application's cookie jar.
    assert authenticated.delete("/api/v1/system/logs", headers={"Origin": "https://other.example"}).status_code == 403
    assert authenticated.delete("/api/v1/system/logs", headers={"X-CSRF-Token": "wrong"}).status_code == 403
    response = authenticated.delete("/api/v1/system/logs")
    assert response.status_code == 200 and response.json()["retention_days"] == 7
    authenticated.cookies.clear()
    assert client.delete("/api/v1/system/logs").status_code == 401


@pytest.mark.parametrize("value", [-1, 3651, True, 1.5, "7"])
def test_invalid_retention_rejected(authenticated: TestClient, value) -> None:
    saved = authenticated.get("/api/v1/settings").json()
    assert authenticated.patch("/api/v1/settings", json={"revision": saved["revision"], "diagnostic_log_retention_days": value}).status_code == 422


def test_policy_save_offline_revision_environment_and_paused_idle(authenticated: TestClient, monkeypatch) -> None:
    state = authenticated.app.state
    monkeypatch.setattr("app.bot.client.TelegramClient.identity", AsyncMock(side_effect=AssertionError("no Telegram")))
    before = authenticated.get("/api/v1/settings").json()
    assert before["values"]["diagnostic_log_retention_days"] == 7
    response = authenticated.patch("/api/v1/settings", json={"revision": before["revision"], "diagnostic_log_retention_days": 0})
    assert response.status_code == 200
    assert authenticated.get("/api/v1/system/status").json()["diagnostic_log"]["retention_days"] == 0
    assert authenticated.patch("/api/v1/settings", json={"revision": before["revision"], "diagnostic_log_retention_days": 7}).status_code == 409
    monkeypatch.setenv("TGD_DIAGNOSTIC_LOG_RETENTION_DAYS", "3")
    policy = authenticated.get("/api/v1/settings").json()
    assert policy["values"]["diagnostic_log_retention_days"] == 3 and not policy["editable"]["diagnostic_log_retention_days"]
    assert authenticated.patch("/api/v1/settings", json={"revision": policy["revision"], "diagnostic_log_retention_days": 2}).status_code == 400
    log = state.runtime.diagnostics
    log._path().write_text(record(1))
    state.runtime.local_api.reconcile = AsyncMock(side_effect=AssertionError("migration independent"))
    async def idle() -> None:
        state.runtime.stopping = False
        async def stop(_: float) -> None:
            state.runtime.stopping = True
        monkeypatch.setattr("app.workers.runtime.asyncio.sleep", stop)
        await state.runtime.diagnostic_loop()
    asyncio.run(idle())
    assert log.retention_days == 3 and log.export()["events"] == []


def test_sweep_preserves_recent_recovery_and_oversize_is_bounded(tmp_path: Path) -> None:
    now = 2_000_000.0
    log = DiagnosticLog(tmp_path, clock=lambda: now, max_file_bytes=1000)
    log.request("cloud", "getMe", error=TelegramNetworkError(method=GetMe(), message="private"))
    log.maintain(7)
    log.request("cloud", "getMe")
    assert log.export()["events"][-1]["event"] == "telegram_recovered"
    log._path().write_text("x" * 10000 + "\n" + record(now) * 3)
    log.maintain(7, force=True)
    assert log._path().stat().st_size <= 1000
    assert len(log.export()["events"]) == 3


def test_expired_pending_summary_cannot_be_retimestamped(tmp_path: Path) -> None:
    clock = [2_000_000.0]
    log = DiagnosticLog(tmp_path, clock=lambda: clock[0])
    for _ in range(3):
        log.emit("service_started", dedupe=True)
    clock[0] += 8 * 86400
    log.emit("service_started", dedupe=True)
    assert all(event["event"] != "event_summary" for event in log.export()["events"])


@pytest.mark.asyncio
async def test_shutdown_joins_inflight_maintenance(authenticated: TestClient, monkeypatch) -> None:
    runtime = authenticated.app.state.runtime
    started, release = threading.Event(), threading.Event()
    def blocked() -> None:
        started.set()
        release.wait(5)
    monkeypatch.setattr(runtime, "maintain_diagnostics", blocked)
    task = asyncio.create_task(runtime.diagnostic_loop())
    await asyncio.to_thread(started.wait, 5)
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task


def test_retention_save_preserves_local_proxy_handoff(authenticated: TestClient) -> None:
    state = authenticated.app.state
    state.runtime.config.proxy_capable = True
    before = state.settings.public()
    patch = SettingsPatch(revision=before["revision"], bot_api_mode="local",
                          proxy={"enabled": True, "host": "proxy.lan"})
    saved = state.settings.save(patch, state.settings.candidate(patch))
    state.settings.write_proxy_desired()
    desired = state.runtime.config.network_dir / "desired.json"
    original = desired.read_bytes()
    network_revision = json.loads(original)["revision"]
    (desired.parent / "applied.json").write_text(json.dumps({
        "desired_revision": network_revision, "applied_revision": network_revision,
        "state": "applied", "message": "proxy_live_check_passed"}))
    assert authenticated.patch("/api/v1/settings", json={
        "revision": saved["revision"], "diagnostic_log_retention_days": 2}).status_code == 200
    state.settings.write_proxy_desired()
    assert desired.read_bytes() == original
    assert state.settings.public()["proxy_status"]["state"] == "active"
    assert state.settings.public()["proxy_status"]["desired_revision"] == network_revision
    changed = SettingsPatch(revision=state.settings.public()["revision"],
                            proxy={"enabled": True, "host": "other.lan"})
    state.settings.save(changed, state.settings.candidate(changed))
    assert state.settings.public()["proxy_status"]["state"] == "pending"
    state.settings.write_proxy_desired()
    assert json.loads(desired.read_bytes())["revision"] > network_revision
