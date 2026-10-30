import asyncio
import errno
import socket
import ssl
import threading
from collections.abc import Callable
from datetime import datetime
from types import SimpleNamespace
from typing import Any

import pytest
from aiogram.exceptions import TelegramNetworkError
from aiogram.methods import GetMe
from aiohttp import ClientConnectorError, ClientHttpProxyError, ConnectionTimeoutError, SocketTimeoutError
from aiohttp_socks import ProxyConnectionError, ProxyError, ProxyTimeoutError, ProxyType
from fastapi.testclient import TestClient

from app.core.errors import AppError
from app.schemas.contracts import ProxySettings, SettingsPatch
from app.schemas.responses import NetworkProbeResult
from app.services import network_diagnostics as probes
from app.services.network_diagnostics import NetworkDiagnosticsService
from app.services.network_errors import classify_network_failure


@pytest.mark.parametrize(("error", "expected"), [
    (socket.gaierror(socket.EAI_NONAME, "secret"), ("dns", "dns")),
    (ConnectionRefusedError(errno.ECONNREFUSED, "secret"), ("tcp", "connect")),
    (ssl.SSLCertVerificationError("secret"), ("tls", "tls")),
    (ConnectionTimeoutError("secret"), ("timeout", "connect")),
    (SocketTimeoutError("secret"), ("timeout", "response")),
    (TimeoutError("secret"), ("timeout", "unknown")),
    (ProxyConnectionError("secret"), ("proxy_connection", "proxy")),
    (ProxyTimeoutError("secret"), ("timeout", "proxy")),
    (ProxyError("secret", error_code=407), ("proxy_auth", "proxy")),
    (ProxyError("Username and password authentication failure"), ("network_unknown", "proxy")),
    (ProxyError("secret", error_code=4), ("network_unknown", "proxy")),
    (ClientHttpProxyError(None, (), status=407), ("proxy_auth", "proxy")),
    (Exception("DNS proxy password TLS timeout"), ("network_unknown", "unknown")),
])
def test_type_only_failure_classification(error: BaseException, expected: tuple[str, str]) -> None:
    assert classify_network_failure(error) == expected


def test_sdk_wrapper_and_cycles_never_parse_exception_text() -> None:
    wrapped = TelegramNetworkError(method=GetMe(), message="secret Token and URL")
    wrapped.__cause__ = ClientConnectorError(SimpleNamespace(), socket.gaierror(socket.EAI_NONAME, "secret"))
    assert classify_network_failure(wrapped) == ("dns", "dns")
    unknown = Exception("secret")
    unknown.__cause__ = unknown
    assert classify_network_failure(unknown) == ("network_unknown", "unknown")
    with_suppressed = Exception("secret")
    with_suppressed.__context__ = socket.gaierror()
    with_suppressed.__suppress_context__ = True
    assert classify_network_failure(with_suppressed) == ("network_unknown", "unknown")


def service(client: TestClient) -> NetworkDiagnosticsService:
    return client.app.state.network_diagnostics


def save(diagnostic: NetworkDiagnosticsService, patch: SettingsPatch) -> dict[str, Any]:
    patch.revision = diagnostic.settings.read()[0]
    return diagnostic.settings.save(patch, diagnostic.settings.candidate(patch))


def fake_probes(monkeypatch: pytest.MonkeyPatch, outcomes: dict[str, NetworkProbeResult]) -> list[str]:
    called = []

    async def https(self: NetworkDiagnosticsService, proxy: ProxySettings, url: str) -> NetworkProbeResult:
        called.append(url)
        return outcomes["telegram" if url == probes.TELEGRAM_URL else "google"]

    monkeypatch.setattr(NetworkDiagnosticsService, "_probe_https", https)
    return called


@pytest.mark.parametrize(("telegram", "google"), [
    ("reachable", "reachable"), ("reachable", "failed"), ("failed", "reachable"), ("failed", "failed"),
])
async def test_independent_targets_no_token_and_cloud_not_applicable(
        client: TestClient, monkeypatch: pytest.MonkeyPatch, telegram: str, google: str) -> None:
    results = {name: NetworkProbeResult.model_validate({"state": state, "latency_ms": 7})
               for name, state in (("telegram", telegram), ("google", google))}
    called = fake_probes(monkeypatch, results)
    diagnostic = service(client)
    result = await diagnostic.run()
    assert set(called) == {probes.TELEGRAM_URL, probes.GOOGLE_URL}
    assert result.telegram.state == telegram and result.google.state == google
    assert result.origin == "app" and result.route == "direct" and result.proxy_scheme is None
    assert result.local_api_tcp.state == "not_applicable"
    assert result.local_api is None and result.local_api_upstream == "not_applicable"
    assert result.settings_revision == diagnostic.settings.read()[0]
    assert datetime.fromisoformat(result.checked_at).tzinfo is not None


async def test_local_tcp_and_process_observations_remain_separate(
        client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    diagnostic = service(client)
    save(diagnostic, SettingsPatch(bot_api_mode="local"))
    fake_probes(monkeypatch, {name: NetworkProbeResult(state="reachable", http_status=204)
                             for name in ("telegram", "google")})

    async def tcp() -> NetworkProbeResult:
        return NetworkProbeResult(state="failed", failure_kind="tcp", failure_stage="connect", latency_ms=2)

    monkeypatch.setattr(diagnostic, "_probe_tcp", tcp)
    before_state = diagnostic.local_api.read()
    result = await diagnostic.run()
    assert result.local_api_tcp.state == "failed"
    assert result.telegram.state == "reachable"
    assert result.local_api_upstream == "unverified"
    assert result.local_api is not None and not result.local_api.managed
    assert diagnostic.local_api.read() == before_state


async def test_single_flight_no_queue_start_interval_and_cleanup(
        client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    diagnostic = service(client)
    entered, release = asyncio.Event(), asyncio.Event()
    finished: list[str] = []

    async def hanging(proxy: ProxySettings, url: str) -> NetworkProbeResult:
        entered.set()
        try:
            await release.wait()
            return NetworkProbeResult(state="reachable")
        finally:
            finished.append(url)

    monkeypatch.setattr(diagnostic, "_probe_https", hanging)
    run = asyncio.create_task(diagnostic.run())
    await entered.wait()
    with pytest.raises(AppError) as busy:
        await diagnostic.run()
    assert busy.value.code == "NETWORK_TEST_BUSY" and busy.value.status == 409
    run.cancel()
    with pytest.raises(asyncio.CancelledError):
        await run
    assert len(finished) == 2 and not diagnostic._running
    with pytest.raises(AppError) as interval:
        await diagnostic.run()
    assert interval.value.code == "NETWORK_TEST_RATE_LIMITED" and interval.value.status == 429
    assert datetime.fromisoformat(interval.value.details["retry_at"]).tzinfo is not None
    diagnostic._last_start = probes.time.monotonic() - probes.START_INTERVAL
    release.set()
    assert (await diagnostic.run()).telegram.state == "reachable"


async def test_round_budget_retains_completed_target_and_cancels_pending(
        client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    diagnostic = service(client)
    stopped = asyncio.Event()

    async def probe(proxy: ProxySettings, url: str) -> NetworkProbeResult:
        if url == probes.TELEGRAM_URL:
            return NetworkProbeResult(state="reachable", http_status=302)
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()
        raise AssertionError("unreachable")

    monkeypatch.setattr(probes, "ROUND_TIMEOUT", 0.02)
    monkeypatch.setattr(diagnostic, "_probe_https", probe)
    result = await diagnostic.run()
    assert result.telegram.state == "reachable" and result.telegram.http_status == 302
    assert result.google.state == "timeout" and result.google.failure_stage == "unknown"
    assert stopped.is_set() and not diagnostic._running


async def test_cancellation_waits_for_short_database_snapshot_thread(
        client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    diagnostic = service(client)
    snapshot = diagnostic._snapshot()
    entered, release = threading.Event(), threading.Event()

    def snapshot_read() -> probes.ProbeSnapshot:
        entered.set()
        assert release.wait(5)
        return snapshot

    monkeypatch.setattr(diagnostic, "_snapshot", snapshot_read)
    run = asyncio.create_task(diagnostic.run())
    await asyncio.to_thread(entered.wait)
    run.cancel()
    await asyncio.sleep(0)
    assert diagnostic._running and not run.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await run
    assert not diagnostic._running


async def test_supervisor_observation_failure_does_not_block_saved_route_or_change_protection(
        client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    diagnostic = service(client)
    save(diagnostic, SettingsPatch(bot_api_mode="local"))
    safety = client.app.state.runtime.safety
    safety.observe("123:fixture", None, unauthorized=True)
    protection = safety.status("123:fixture")

    def broken_status(**kwargs: Any) -> None:
        raise ValueError("private-password must not be returned")

    async def tcp() -> NetworkProbeResult:
        return NetworkProbeResult(state="reachable", latency_ms=1)

    monkeypatch.setattr(diagnostic.local_api, "status", broken_status)
    monkeypatch.setattr(diagnostic, "_probe_tcp", tcp)
    fake_probes(monkeypatch, {name: NetworkProbeResult(state="reachable") for name in ("telegram", "google")})
    result = await diagnostic.run()
    assert result.telegram.state == "reachable" and result.local_api is None
    assert result.local_api_upstream == "unverified" and result.proxy_status.state == "unavailable"
    assert "private-password" not in result.model_dump_json()
    assert safety.status("123:fixture") == protection


async def test_saved_snapshot_survives_concurrent_save_without_secrets(
        client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    diagnostic = service(client)
    revision = save(diagnostic, SettingsPatch(proxy=ProxySettings(enabled=True, scheme="http",
        host="proxy.invalid", username="private-user", password="private-password")))["revision"]
    entered, release = asyncio.Event(), asyncio.Event()
    used = []

    async def probe(proxy: ProxySettings, url: str) -> NetworkProbeResult:
        used.append(proxy.model_dump())
        entered.set()
        await release.wait()
        return NetworkProbeResult(state="reachable", http_status=403)

    monkeypatch.setattr(diagnostic, "_probe_https", probe)
    run = asyncio.create_task(diagnostic.run())
    await entered.wait()
    save(diagnostic, SettingsPatch(confirmation_timeout_seconds=300))
    release.set()
    result = await run
    assert result.settings_revision == revision
    assert result.route == "proxy" and result.proxy_scheme == "http"
    assert len(used) == 2 and all(value["password"] == "private-password" for value in used)
    serialized = result.model_dump_json()
    assert all(secret not in serialized for secret in ("proxy.invalid", "private-user", "private-password"))
    assert diagnostic.settings.read()[0] > result.settings_revision


class FakeResponse:
    def __init__(self, status: int = 204, error: BaseException | None = None, hang: bool = False) -> None:
        self.status, self.error, self.hang = status, error, hang
        self.closed = False

    async def __aenter__(self) -> "FakeResponse":
        if self.hang:
            await asyncio.Event().wait()
        if self.error:
            raise self.error
        return self

    async def __aexit__(self, *args: Any) -> None:
        self.close()

    def close(self) -> None:
        self.closed = True

    async def read(self) -> bytes:
        raise AssertionError("The response body must never be read")


def capture_sessions(monkeypatch: pytest.MonkeyPatch, response: Callable[[], FakeResponse]) -> list[dict[str, Any]]:
    sessions: list[dict[str, Any]] = []

    class FakeSession:
        def __init__(self, **kwargs: Any) -> None:
            self.data = kwargs
            sessions.append(kwargs)

        async def __aenter__(self) -> "FakeSession":
            return self

        async def __aexit__(self, *args: Any) -> None:
            self.data["closed"] = True
            await self.data["connector"].close()

        def get(self, url: str, **kwargs: Any) -> FakeResponse:
            self.data["url"], self.data["request"] = url, kwargs
            return response()

    monkeypatch.setattr(probes, "ClientSession", FakeSession)
    return sessions


@pytest.mark.parametrize("scheme", [None, "http", "socks5"])
@pytest.mark.parametrize("status", [204, 302, 403, 503])
async def test_transport_routes_tls_no_environment_no_redirect_or_body(
        client: TestClient, monkeypatch: pytest.MonkeyPatch, scheme: str | None, status: int) -> None:
    monkeypatch.setenv("HTTPS_PROXY", "http://unexpected.invalid:8080")
    response = FakeResponse(status=status)
    sessions = capture_sessions(monkeypatch, lambda: response)
    proxy = ProxySettings.model_validate({"enabled": scheme is not None, "scheme": scheme or "socks5",
        "host": "proxy.invalid", "username": "private-user", "password": "private-password"})
    result = await service(client)._probe_https(proxy, probes.TELEGRAM_URL)
    assert result.state == "reachable" and result.http_status == status and response.closed
    assert sessions[0]["trust_env"] is False and sessions[0]["auto_decompress"] is False
    assert sessions[0]["request"] == {"allow_redirects": False}
    assert sessions[0]["timeout"].total == 5.0 and sessions[0]["closed"]
    connector = sessions[0]["connector"]
    assert connector._ssl.verify_mode == ssl.CERT_REQUIRED and connector._ssl.check_hostname
    if scheme:
        assert connector._rdns is True and connector._proxy_host == "proxy.invalid"
        assert connector._proxy_type == (ProxyType.HTTP if scheme == "http" else ProxyType.SOCKS5)
        assert connector._proxy_password == "private-password"
    else:
        assert not hasattr(connector, "_proxy_host")


@pytest.mark.parametrize("error", [socket.gaierror(), ssl.SSLError("private-password"), ProxyError("secret", 407)])
async def test_transport_failure_safe_and_resources_closed(
        client: TestClient, monkeypatch: pytest.MonkeyPatch, error: BaseException) -> None:
    sessions = capture_sessions(monkeypatch, lambda: FakeResponse(error=error))
    result = await service(client)._probe_https(ProxySettings(), probes.GOOGLE_URL)
    assert result.state == "failed" and sessions[0]["closed"]
    assert "private-password" not in result.model_dump_json() and "secret" not in result.model_dump_json()


async def test_per_target_timeout_closes_session(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    sessions = capture_sessions(monkeypatch, lambda: FakeResponse(hang=True))
    monkeypatch.setattr(probes, "TARGET_TIMEOUT", 0.02)
    result = await service(client)._probe_https(ProxySettings(), probes.GOOGLE_URL)
    assert result.state == "timeout" and result.failure_kind == "timeout"
    assert sessions[0]["closed"]


async def test_transport_cancellation_closes_session(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    sessions = capture_sessions(monkeypatch, lambda: FakeResponse(hang=True))
    run = asyncio.create_task(service(client)._probe_https(ProxySettings(), probes.GOOGLE_URL))
    while not sessions:
        await asyncio.sleep(0)
    run.cancel()
    with pytest.raises(asyncio.CancelledError):
        await run
    assert sessions[0]["closed"]


async def test_tcp_only_configured_host_port_no_data_and_close(
        client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    called = []

    class Writer:
        transport: Any

        def __init__(self) -> None:
            self.transport = SimpleNamespace(abort=lambda: called.append("abort"))

        def close(self) -> None:
            called.append("close")

        async def wait_closed(self) -> None:
            called.append("wait_closed")

    async def connect(host: str, port: int) -> tuple[None, Writer]:
        called.append((host, port))
        return None, Writer()

    monkeypatch.setattr(probes.asyncio, "open_connection", connect)
    diagnostic = service(client)
    diagnostic.settings.config.bot_api_base = "http://internal-api.invalid:9191/private-path"
    result = await diagnostic._probe_tcp()
    assert result.state == "reachable" and result.http_status is None
    assert called == [("internal-api.invalid", 9191), "close", "wait_closed", "close", "abort"]


def test_network_endpoint_auth_csrf_origin_and_rejects_parameters(
        client: TestClient, authenticated: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = fake_probes(monkeypatch, {name: NetworkProbeResult(state="reachable") for name in ("telegram", "google")})
    csrf = authenticated.headers.pop("X-CSRF-Token")
    assert authenticated.post("/api/v1/system/network-test").status_code == 403
    authenticated.headers["X-CSRF-Token"] = csrf
    assert authenticated.post("/api/v1/system/network-test", headers={"Origin": "http://other.invalid"}).status_code == 403
    assert authenticated.post("/api/v1/system/network-test", json={"url": "http://private.invalid"}).status_code == 422
    assert authenticated.post("/api/v1/system/network-test?url=http://private.invalid").status_code == 422
    assert calls == []
    response = authenticated.post("/api/v1/system/network-test")
    assert response.status_code == 200 and response.json()["origin"] == "app"
    assert len(calls) == 2
    assert authenticated.post("/api/v1/system/network-test").status_code == 429
    authenticated.cookies.clear()
    assert client.post("/api/v1/system/network-test").status_code == 401


def test_bot_retry_operation_evidence_is_linked_without_changing_eligibility(
        authenticated: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from unittest.mock import AsyncMock

    from app.bot.client import TelegramClient

    state = authenticated.app.state
    token = "123:fixture"
    revision = state.settings.read()[0]
    assert authenticated.patch("/api/v1/settings", json={"revision": revision, "bot_token": token,
                                                        "allowed_user_ids": [42]}).status_code == 200
    state.runtime.safety.observe(token, None, unauthorized=True)

    async def identity(self: TelegramClient, *, probe: bool = False) -> dict[str, Any]:
        return await self.request(AsyncMock(return_value={"id": 123, "username": "fixture"}), probe=probe)

    monkeypatch.setattr(TelegramClient, "identity", identity)
    assert authenticated.post("/api/v1/system/bot/retry").status_code == 200
    events = state.runtime.diagnostics.export()["events"]
    action_events = [event for event in events if event.get("event") == "bot_recovery"]
    assert [event["outcome"] for event in action_events] == ["queued", "success"]
    operation_id = action_events[0]["operation_id"]
    assert all(event["operation_id"] == operation_id and event["action"] == "bot_retry" for event in action_events)
    assert any(event.get("event") == "telegram_request" and event.get("operation_id") == operation_id for event in events)
    assert authenticated.post("/api/v1/system/bot/retry").status_code == 409
    assert token not in str(state.runtime.diagnostics.export())
