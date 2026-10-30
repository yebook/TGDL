"""One bounded, read-only diagnostic round from the application network namespace."""
import asyncio
import ssl
import time
from collections.abc import Coroutine, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal
from urllib.parse import urlsplit

import certifi
from aiohttp import ClientSession, ClientTimeout, DummyCookieJar, TCPConnector
from aiohttp_socks import ProxyConnector, ProxyType

from app.core.errors import AppError
from app.schemas.contracts import ProxySettings
from app.schemas.responses import LocalApiStatus, NetworkProbeResult, NetworkTestResponse, ProxyStatus
from app.services.local_api import LocalApiService
from app.services.network_errors import classify_network_failure
from app.services.settings import SettingsService

TELEGRAM_URL = "https://api.telegram.org/"
GOOGLE_URL = "https://www.google.com/generate_204"
TARGET_TIMEOUT = 5.0
ROUND_TIMEOUT = 8.0
START_INTERVAL = 10.0


def iso(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, UTC).isoformat()


@dataclass(frozen=True)
class ProbeSnapshot:
    revision: int
    proxy: ProxySettings
    mode: Literal["cloud", "local"]
    local_api: LocalApiStatus | None
    proxy_status: ProxyStatus


class NetworkDiagnosticsService:
    def __init__(self, settings: SettingsService, local_api: LocalApiService) -> None:
        self.settings, self.local_api = settings, local_api
        # Accessed on the application's event loop, before the first await.
        self._running = False
        self._last_start: float | None = None
        self._next_allowed_at = 0.0

    def _snapshot(self) -> ProbeSnapshot:
        revision, values, _ = self.settings.read()
        # No DB transaction or publication lock survives into network I/O.
        state = self.local_api.read()
        applied = self.local_api.applied() if self.settings.config.managed_local_api else {}
        observed = None
        try:
            if values.bot_api_mode == "local":
                observed = LocalApiStatus.model_validate(self.local_api.status(settings=values, state=state, applied=applied))
            proxy_status = ProxyStatus.model_validate(self.settings.proxy_status(
                revision, values, applied=applied, runtime_state=state))
        except (OSError, ValueError, TypeError, OverflowError):
            # Broken process observations cannot prevent a saved-route HTTPS test.
            observed = None
            proxy_status = ProxyStatus(state="unavailable", desired_revision=revision, applied_revision=None,
                                       message="网络配置应用状态暂不可用，请刷新系统状态")
        return ProbeSnapshot(revision, values.proxy.model_copy(deep=True), values.bot_api_mode, observed, proxy_status)

    async def run(self) -> NetworkTestResponse:
        now = time.monotonic()
        if self._running:
            raise AppError("NETWORK_TEST_BUSY", "网络检测正在进行，请等待本次结果", 409,
                           {"retry_at": iso(self._next_allowed_at)})
        if self._last_start is not None and now - self._last_start < START_INTERVAL:
            raise AppError("NETWORK_TEST_RATE_LIMITED", "网络检测间隔至少 10 秒，请稍后再试", 429,
                           {"retry_at": iso(self._next_allowed_at)})
        self._running = True
        self._last_start = now
        self._next_allowed_at = time.time() + START_INTERVAL
        checked_at = iso(time.time())
        try:
            # A request cancellation must not pretend a DB thread has completed.
            snapshot_task = asyncio.create_task(asyncio.to_thread(self._snapshot))
            try:
                snapshot = await asyncio.shield(snapshot_task)
            except asyncio.CancelledError:
                await snapshot_task
                raise
            operations = {
                "telegram": self._probe_https(snapshot.proxy, TELEGRAM_URL),
                "google": self._probe_https(snapshot.proxy, GOOGLE_URL),
            }
            if snapshot.mode == "local":
                operations["local_api_tcp"] = self._probe_tcp()
            results = await self._round(operations)
            return NetworkTestResponse(
                checked_at=checked_at, settings_revision=snapshot.revision,
                route="proxy" if snapshot.proxy.enabled else "direct",
                proxy_scheme=snapshot.proxy.scheme if snapshot.proxy.enabled else None,
                bot_api_mode=snapshot.mode, telegram=results["telegram"], google=results["google"],
                local_api_tcp=results.get("local_api_tcp", NetworkProbeResult(state="not_applicable")),
                local_api=snapshot.local_api, proxy_status=snapshot.proxy_status,
                local_api_upstream="unverified" if snapshot.mode == "local" else "not_applicable",
                next_allowed_at=iso(self._next_allowed_at),
            )
        finally:
            self._running = False

    async def _round(self, operations: Mapping[str, Coroutine[Any, Any, NetworkProbeResult]]) -> dict[str, NetworkProbeResult]:
        started = time.monotonic()
        tasks = {name: asyncio.create_task(operation) for name, operation in operations.items()}
        try:
            _, pending = await asyncio.wait(tasks.values(), timeout=ROUND_TIMEOUT)
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            result = {}
            for name, task in tasks.items():
                if task.cancelled():
                    result[name] = self._failure(TimeoutError(), started)
                elif task.exception() is not None:
                    error = task.exception()
                    assert error is not None
                    result[name] = self._failure(error, started)
                else:
                    result[name] = task.result()
            return result
        finally:
            for task in tasks.values():
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks.values(), return_exceptions=True)

    @staticmethod
    def _failure(error: BaseException, started: float) -> NetworkProbeResult:
        kind, stage = classify_network_failure(error)
        return NetworkProbeResult.model_validate({
            "state": "timeout" if kind == "timeout" else "failed",
            "latency_ms": int(max(0, time.monotonic() - started) * 1000),
            "failure_kind": kind, "failure_stage": stage,
        })

    async def _probe_https(self, proxy: ProxySettings, url: str) -> NetworkProbeResult:
        started = time.monotonic()
        try:
            async with asyncio.timeout(TARGET_TIMEOUT):
                tls = ssl.create_default_context(cafile=certifi.where())
                connector: TCPConnector
                if proxy.enabled:
                    connector = ProxyConnector(
                        host=proxy.host, port=proxy.port,
                        proxy_type=ProxyType.HTTP if proxy.scheme == "http" else ProxyType.SOCKS5,
                        username=proxy.username or None, password=proxy.password or None,
                        rdns=True, ssl=tls, limit=1,
                    )
                else:
                    connector = TCPConnector(ssl=tls, limit=1)
                async with ClientSession(connector=connector, timeout=ClientTimeout(total=TARGET_TIMEOUT),
                                         trust_env=False, cookie_jar=DummyCookieJar(), auto_decompress=False) as session:
                    async with session.get(url, allow_redirects=False) as response:
                        # Any HTTPS HTTP status is reachability, not Bot readiness.
                        # Never read the remote response body, headers or redirect URL.
                        status = response.status
                        response.close()
                        return NetworkProbeResult(state="reachable", http_status=status,
                            latency_ms=int(max(0, time.monotonic() - started) * 1000))
        except Exception as exc:
            return self._failure(exc, started)

    async def _probe_tcp(self) -> NetworkProbeResult:
        started = time.monotonic()
        writer: asyncio.StreamWriter | None = None
        try:
            async with asyncio.timeout(TARGET_TIMEOUT):
                base = urlsplit(self.settings.config.bot_api_base)
                if base.scheme not in {"http", "https"} or not base.hostname:
                    raise ValueError("Invalid configured API")
                port = base.port or (443 if base.scheme == "https" else 80)
                _, writer = await asyncio.open_connection(base.hostname, port)
                writer.close()
                await writer.wait_closed()
                return NetworkProbeResult(state="reachable",
                    latency_ms=int(max(0, time.monotonic() - started) * 1000))
        except Exception as exc:
            return self._failure(exc, started)
        finally:
            if writer is not None:
                writer.close()
                # Closing TCP must remain bounded even when cancelled during close.
                writer.transport.abort()
