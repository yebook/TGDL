import asyncio
import json
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.schemas.contracts import SettingsPatch
from app.schemas.responses import ProxyStatus
from app.services import protocol_diagnostics as ipc
from app.services import protocol_probe as wire
from app.services.network_diagnostics import NetworkDiagnosticsService, ProbeSnapshot


def prepare(client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[NetworkDiagnosticsService, ProbeSnapshot]:
    service = client.app.state.network_diagnostics
    revision = service.settings.read()[0]
    patch = SettingsPatch(revision=revision, bot_api_mode="local")
    service.settings.save(patch, service.settings.candidate(patch))
    service.settings.config.managed_local_api = True
    service.settings.config.local_api_control_dir = tmp_path / "control"
    service.local_api.control_cipher()
    revision, settings, _ = service.settings.read()
    applied = {"state": "running", "generation": "generation-1", "command_id": "command-1",
               "credential_revision": 1, "proxy_signature": service.settings.proxy_signature(settings)}
    state = {"command_id": "command-1", "credential_revision": 1}
    monkeypatch.setattr(service.local_api, "applied", lambda: dict(applied))
    monkeypatch.setattr(service.local_api, "read", lambda: dict(state))
    snapshot = ProbeSnapshot(revision, settings.proxy, "local", None,
        ProxyStatus(state="disabled", desired_revision=revision, applied_revision=revision, message=""), dict(applied), dict(state))
    return service, snapshot


async def reply(service: NetworkDiagnosticsService, *, states: tuple[str, ...] = ("reachable",) * 4,
                tamper: str | None = None) -> dict[str, Any]:
    directory = service.settings.config.local_api_control_dir
    while not (directory / wire.REQUEST_FILE).exists():
        await asyncio.sleep(0.001)
    request = wire.load_request(directory)
    payload = {key: value for key, value in request.items() if key != "proxy"}
    payload["targets"] = [{"dc": dc, "host": host, "port": port, "state": state, "latency_ms": 4,
        "failure_kind": None if state == "reachable" else "timeout" if state == "timeout" else "protocol"}
        for (dc, host, port), state in zip(wire.TARGETS, states, strict=True)]
    if tamper == "nonce":
        payload["request_id"] = "b" * 32
    elif tamper == "revision":
        payload["settings_revision"] += 1
    elif tamper == "signature":
        payload["proxy_signature"] = "c" * 64
    elif tamper == "generation":
        payload["generation"] = "obsolete-generation"
    elif tamper == "coverage":
        payload["targets"][0]["host"] = "127.0.0.1"
    elif tamper == "kind":
        payload["targets"][0]["state"] = "timeout"
        payload["targets"][0]["failure_kind"] = "tcp"
    wire.write_private(directory / wire.RESULT_FILE, wire.cipher_for(directory).encrypt(json.dumps(payload).encode()))
    return request


@pytest.mark.parametrize(("states", "expected"), [(("reachable",) * 4, "reachable"),
    (("timeout",) * 4, "timeout"), (("failed",) * 4, "failed"), (("reachable", "timeout", "failed", "timeout"), "partial")])
async def test_independent_protocol_outcomes_coverage_cleanup_and_no_mutation(
        client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, states: tuple[str, ...], expected: str) -> None:
    service, snapshot = prepare(client, tmp_path, monkeypatch)
    before_settings = service.settings.read()
    before_state = service.local_api.read()
    protection = client.app.state.runtime.safety.status("123:fixture")
    task = asyncio.create_task(service._probe_protocol(snapshot))
    request = await reply(service, states=states)
    assert all(secret not in json.dumps(request) for secret in ("bot_token", "telegram_api_id", "telegram_api_hash"))
    result = await task
    assert result.state == expected and result.applied_route == "matched"
    assert result.observed_generation == "generation-1" and len(result.targets) == 4
    assert not (tmp_path / "control" / wire.REQUEST_FILE).exists()
    assert not (tmp_path / "control" / wire.RESULT_FILE).exists()
    assert service.settings.read() == before_settings and service.local_api.read() == before_state
    assert client.app.state.runtime.safety.status("123:fixture") == protection


@pytest.mark.parametrize("tamper", ["nonce", "revision", "signature", "generation", "coverage", "kind"])
async def test_invalid_stale_cross_request_result_never_passes(
        client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, tamper: str) -> None:
    service, snapshot = prepare(client, tmp_path, monkeypatch)
    monkeypatch.setattr(ipc, "REQUEST_LIFETIME", 0.12)
    task = asyncio.create_task(service._probe_protocol(snapshot))
    await reply(service, tamper=tamper)
    result = await task
    assert result.state == "unavailable" and result.applied_route == "unavailable"


@pytest.mark.parametrize("change", ["generation", "command_id", "credential_revision", "proxy_signature", "state", "heartbeat", "settings"])
async def test_post_io_fresh_observation_and_saved_revision_races(
        client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: str) -> None:
    service, snapshot = prepare(client, tmp_path, monkeypatch)
    task = asyncio.create_task(service._probe_protocol(snapshot))
    await reply(service)
    if change == "settings":
        patch = SettingsPatch(revision=service.settings.read()[0], confirmation_timeout_seconds=300)
        service.settings.save(patch, service.settings.candidate(patch))
    else:
        value = {} if change == "heartbeat" else snapshot.applied | {change: "changed"}
        monkeypatch.setattr(service.local_api, "applied", lambda: value)
    result = await task
    assert result.state == "unavailable" and result.applied_route == "unavailable"
    assert result.reason == ("configuration_changed" if change == "settings" else "stale_observation")


async def test_applied_proxy_route_separate_from_saved_route_probe(
        client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    service, snapshot = prepare(client, tmp_path, monkeypatch)
    applied = snapshot.applied | {"proxy_signature": "old-route"}
    monkeypatch.setattr(service.local_api, "applied", lambda: applied)
    snapshot = replace(snapshot, applied=applied)
    task = asyncio.create_task(service._probe_protocol(snapshot))
    await reply(service)
    result = await task
    assert result.state == "reachable" and result.applied_route == "different" and result.route == "direct"


async def test_missing_helper_bounded_and_cancel_removes_handoff(
        client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    service, snapshot = prepare(client, tmp_path, monkeypatch)
    monkeypatch.setattr(ipc, "REQUEST_LIFETIME", 0.1)
    started = time.monotonic()
    result = await service._probe_protocol(snapshot)
    assert time.monotonic() - started < 1 and result.state == "unavailable"
    task = asyncio.create_task(service._probe_protocol(snapshot))
    while not (tmp_path / "control" / wire.REQUEST_FILE).exists():
        await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not (tmp_path / "control" / wire.REQUEST_FILE).exists()


async def test_dead_supervisor_does_not_prove_applied_child_connectivity(
        client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    service, snapshot = prepare(client, tmp_path, monkeypatch)
    monkeypatch.setattr(service.local_api, "applied", lambda: {})
    snapshot = replace(snapshot, applied={})
    task = asyncio.create_task(service._probe_protocol(snapshot))
    await reply(service)
    result = await task
    assert result.state == "reachable" and result.applied_route == "unavailable"
    assert result.observed_generation is None


async def test_fresh_observation_io_failure_cannot_become_helper_success(
        client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    service, snapshot = prepare(client, tmp_path, monkeypatch)

    async def broken(local_api: Any, settings: Any) -> tuple[Any, ...]:
        raise OSError("fixture-private-error must never be returned")

    monkeypatch.setattr(ipc, "joined_snapshot", broken)
    task = asyncio.create_task(service._probe_protocol(snapshot))
    await reply(service)
    result = await task
    assert result.state == "unavailable" and result.reason == "stale_observation"
    assert result.applied_route == "unavailable" and len(result.targets) == 4
    assert "fixture-private-error" not in result.model_dump_json()
