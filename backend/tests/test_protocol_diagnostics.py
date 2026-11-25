import asyncio
import json
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.schemas.contracts import SettingsPatch
from app.schemas.responses import NetworkProbeResult, ProxyStatus, TelegramProtocolResult
from app.services import network_diagnostics as diagnostics
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
    state = {"command_id": "command-1", "credential_revision": 1, "phase": "idle", "applied_mode": "local"}
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
    result = (await task).result
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
    result = (await task).result
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
    result = (await task).result
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
    result = (await task).result
    assert result.state == "reachable" and result.applied_route == "different" and result.route == "direct"


async def test_missing_helper_bounded_and_cancel_removes_handoff(
        client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    service, snapshot = prepare(client, tmp_path, monkeypatch)
    monkeypatch.setattr(ipc, "REQUEST_LIFETIME", 0.1)
    started = time.monotonic()
    result = (await service._probe_protocol(snapshot)).result
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
    result = (await task).result
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
    result = (await task).result
    assert result.state == "unavailable" and result.reason == "stale_observation"
    assert result.applied_route == "unavailable" and len(result.targets) == 4
    assert "fixture-private-error" not in result.model_dump_json()


async def test_protocol_monitor_replaces_healthy_result_when_upstream_times_out(
        client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    service, _ = prepare(client, tmp_path, monkeypatch)
    monkeypatch.setattr(diagnostics, "PROTOCOL_INTERVAL", 0.01)
    snapshot = service._snapshot()
    assert snapshot.monitors_protocol() and snapshot.local_api is not None
    api, proxy = snapshot.local_api.model_dump(), snapshot.proxy_status.model_dump()
    command_id = snapshot.applied["command_id"]
    def read() -> str | None:
        return service.protocol_status(api, proxy, command_id)

    async def wait_for_status(expected: str) -> None:
        while read() != expected:
            await asyncio.sleep(0.001)

    assert read() == "unavailable"
    monitor = asyncio.create_task(service.monitor_protocol())
    try:
        async with asyncio.timeout(3):
            await reply(service)
            await wait_for_status("reachable")
            await reply(service, states=("timeout",) * 4)
            await wait_for_status("timeout")
            assert service.protocol_status(api | {"generation": "new"}, proxy, command_id) == "unavailable"
            assert service.protocol_status(api, proxy | {"applied_revision": 99, "desired_revision": 99}, command_id) == "unavailable"
            assert service.protocol_status(api | {"state": "stopped"}, proxy, command_id) is None
            await reply(service)
            await wait_for_status("reachable")
        observation = service._protocol_observation
        assert observation is not None
        service._protocol_observation = replace(observation, expires=time.monotonic())
        assert read() == "unavailable"
    finally:
        monitor.cancel()
        with pytest.raises(asyncio.CancelledError):
            await monitor
    assert not (tmp_path / "control" / wire.REQUEST_FILE).exists()


async def test_concurrent_protocol_consumers_share_handoff_and_cancel_independently(
        client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    service, _ = prepare(client, tmp_path, monkeypatch)
    snapshot = service._snapshot()
    owner = asyncio.create_task(service._probe_protocol(snapshot))
    other = asyncio.create_task(service._probe_protocol(snapshot))
    third = asyncio.create_task(service._probe_protocol(snapshot))
    try:
        async with asyncio.timeout(3):
            await asyncio.sleep(0)
            changed = await service._probe_protocol(replace(snapshot, revision=snapshot.revision + 1))
            assert changed.result.state == "unavailable"
            owner.cancel()
            with pytest.raises(asyncio.CancelledError):
                await owner
            await reply(service, states=("timeout",) * 4)
            result = await other
            assert result.result.state == "timeout" and result.observation is not None
            assert await third is result
    finally:
        for task in (owner, other, third):
            task.cancel()
        await asyncio.gather(owner, other, third, return_exceptions=True)
    assert not (tmp_path / "control" / wire.REQUEST_FILE).exists()
    assert not (tmp_path / "control" / wire.RESULT_FILE).exists()


async def test_manual_protocol_refresh_publishes_result_and_status_get_does_not_probe(
        authenticated: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    service, _ = prepare(authenticated, tmp_path, monkeypatch)
    path = "/api/v1/system/protocol-refresh"
    directory = service.settings.config.local_api_control_dir
    csrf = authenticated.headers.pop("X-CSRF-Token")
    assert authenticated.post(path).status_code == 403
    authenticated.headers["X-CSRF-Token"] = csrf
    assert authenticated.post(path, headers={"Origin": "http://other.invalid"}).status_code == 403
    assert authenticated.post(path, json={"url": "http://private.invalid"}).status_code == 422
    assert authenticated.post(path + "?target=custom").status_code == 422
    first = authenticated.get("/api/v1/system/status").json()["local_api"]
    assert first["state"] == "running" and first["telegram_protocol"] == "unavailable"
    assert not (directory / wire.REQUEST_FILE).exists()

    request = asyncio.create_task(asyncio.to_thread(authenticated.post, path))
    async with asyncio.timeout(3):
        await reply(service, states=("timeout",) * 4)
        response = await request
        assert response.status_code == 200 and response.json() == {"outcome": "updated", "reason": None}
    second = authenticated.get("/api/v1/system/status").json()["local_api"]
    assert second == (first | {"telegram_protocol": "timeout"})
    assert authenticated.post(path).json() == {"outcome": "reused", "reason": None}
    assert not (directory / wire.REQUEST_FILE).exists()
    authenticated.cookies.clear()
    assert authenticated.post(path).status_code == 401


async def test_full_test_publishes_recovery_and_same_generation_command_invalidates_it(
        authenticated: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    service, _ = prepare(authenticated, tmp_path, monkeypatch)

    async def reachable(*args: Any) -> NetworkProbeResult:
        return NetworkProbeResult(state="reachable")

    monkeypatch.setattr(service, "_probe_https", reachable)
    monkeypatch.setattr(service, "_probe_tcp", reachable)
    status = "/api/v1/system/status"
    async with asyncio.timeout(3):
        request = asyncio.create_task(asyncio.to_thread(authenticated.post, "/api/v1/system/protocol-refresh"))
        await reply(service, states=("reachable", "timeout", "failed", "reachable"))
        assert (await request).is_success
        assert authenticated.get(status).json()["local_api"]["telegram_protocol"] == "partial"
        request = asyncio.create_task(asyncio.to_thread(authenticated.post, "/api/v1/system/network-test"))
        await reply(service)
        result = (await request).json()
        assert result["telegram_protocol"]["state"] == "reachable"
        assert result["status_protocol_updated"] is True
    assert authenticated.get(status).json()["local_api"]["telegram_protocol"] == "reachable"
    applied, state = service.local_api.applied(), service.local_api.read()
    monkeypatch.setattr(service.local_api, "applied", lambda: applied | {"command_id": "command-2"})
    monkeypatch.setattr(service.local_api, "read", lambda: state | {"command_id": "command-2"})
    assert authenticated.get(status).json()["local_api"]["telegram_protocol"] == "unavailable"


async def test_rejected_manual_refresh_does_not_cool_down_new_command(
        client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    service, _ = prepare(client, tmp_path, monkeypatch)
    applied, state = service.local_api.applied(), service.local_api.read()
    calls = []
    started, release = asyncio.Event(), asyncio.Event()

    async def protocol(_settings, _local_api, snapshot):
        command = snapshot.applied["command_id"]
        calls.append(command)
        started.set()
        await release.wait()
        return TelegramProtocolResult(state="reachable", route="direct", settings_revision=snapshot.revision,
                                      applied_route="matched", observed_generation=snapshot.applied["generation"],
                                      observed_command_id=command), command == "command-2"

    monkeypatch.setattr(diagnostics, "protocol_round", protocol)
    old = asyncio.create_task(service.refresh_protocol())
    try:
        async with asyncio.timeout(3):
            await started.wait()
            monkeypatch.setattr(service.local_api, "applied", lambda: applied | {"command_id": "command-2"})
            monkeypatch.setattr(service.local_api, "read", lambda: state | {"command_id": "command-2"})
            blocked = await service.refresh_protocol(manual=True)
            assert (blocked.outcome, blocked.reason) == ("not_updated", "another_round")
            release.set()
            assert (await old).outcome == "not_updated"
            assert (await service.refresh_protocol(manual=True)).outcome == "updated"
            observation = service._protocol_observation
            assert observation is not None
            assert (await service.refresh_protocol(manual=True)).outcome == "reused"
            assert service._protocol_observation is observation  # No expiry extension.
            service._protocol_observation = replace(observation, expires=time.monotonic())
            assert (await service.refresh_protocol(manual=True)).outcome == "not_updated"
            assert calls == ["command-1", "command-2"]
    finally:
        old.cancel()
        await asyncio.gather(old, return_exceptions=True)


@pytest.mark.parametrize("changed", ["phase", "route"])
async def test_full_diagnostic_keeps_reachable_targets_without_publishing_ineligible_evidence(
        client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, changed: str) -> None:
    service, _ = prepare(client, tmp_path, monkeypatch)
    applied, state = service.local_api.applied(), service.local_api.read()

    async def reachable(*args: Any) -> NetworkProbeResult:
        return NetworkProbeResult(state="reachable")

    monkeypatch.setattr(service, "_probe_https", reachable)
    monkeypatch.setattr(service, "_probe_tcp", reachable)
    if changed == "route":
        monkeypatch.setattr(service.local_api, "applied", lambda: applied | {"proxy_signature": "different-route"})
    task = asyncio.create_task(service.run())
    try:
        async with asyncio.timeout(3):
            if changed == "phase":
                while not (tmp_path / "control" / wire.REQUEST_FILE).exists():
                    await asyncio.sleep(0.001)
                monkeypatch.setattr(service.local_api, "read", lambda: state | {"phase": "starting"})
            await reply(service)
            result = await task
        assert result.telegram_protocol.state == "reachable"
        assert result.telegram_protocol.applied_route == ("matched" if changed == "phase" else "different")
        assert result.status_protocol_updated is False
        assert service._protocol_observation is None
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
