"""Bounded local diagnostics. No caller-controlled text crosses this boundary."""
import asyncio
import errno
import json
import os
import re
import stat
import threading
import time
from collections import deque
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramConflictError,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
    TelegramServerError,
    TelegramUnauthorizedError,
)
from aiohttp import ClientError
from aiohttp_socks import ProxyConnectionError, ProxyError, ProxyTimeoutError

from app.core.errors import AppError

EVENTS = {"service_started", "service_stopped", "telegram_request", "telegram_recovered",
          "request_deferred", "task_result", "local_api_transition", "event_summary", "request_protection"}
METHODS = {"getMe", "getUpdates", "getFile", "downloadFile", "sendMessage", "answerCallbackQuery", "logOut"}
OUTCOMES = {"success", "failed", "deferred", "cancelled", "succeeded", "already_exists", "retry_wait",
            "recovery_blocked", "queued", "ready", "throttled", "backoff", "auth_paused",
            "cancelling", "fetching", "saving", "partial_success", "expired", "awaiting_confirmation", "collecting",
            "idle", "draining", "logging_out", "starting", "verifying", "cooldown", "blocked", "logout_uncertain"}
ERRORS = {"UNAUTHORIZED", "FORBIDDEN", "CONFLICT", "RATE_LIMITED", "BAD_REQUEST", "NETWORK_ERROR",
          "SERVER_ERROR", "UNKNOWN_ERROR", "BOT_REQUEST_PAUSED", "CANCELLED", "INTERRUPTED",
          "ACQUISITION_UNCERTAIN", "SIZE_MISMATCH", "CLOUD_FILE_TOO_LARGE", "CLOUD_DOWNLOAD_FAILED",
          "CLOUD_DOWNLOAD_REJECTED", "INVALID_API_PATH", "PUBLISH_COLLISION", "PUBLISH_UNCERTAIN",
          "NO_SPACE", "PERMISSION_DENIED", "STORAGE_ERROR", "RECOVERY_FAILED", "TELEGRAM_REJECTED",
          "BOT_UNAUTHORIZED"}
Scalar = str | int | float | bool | None


def project_event(value: Any) -> dict[str, Scalar]:
    """One strict projection for both writer and exporter, including on-disk tampering."""
    if not isinstance(value, dict) or not isinstance(value.get("event"), str) or value["event"] not in EVENTS:
        return {}
    output: dict[str, Scalar] = {"event": value["event"]}
    for key, allowed in (("level", {"info", "warning", "error"}), ("mode", {"cloud", "local"}),
                         ("method", METHODS), ("outcome", OUTCOMES), ("error_code", ERRORS)):
        if isinstance(value.get(key), str) and value[key] in allowed:
            output[key] = value[key]
    for key in ("timestamp", "retry_at"):
        raw = value.get(key)
        if isinstance(raw, str) and re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,6})?(?:Z|\+00:00)", raw):
            try:
                datetime.fromisoformat(raw.replace("Z", "+00:00"))
                output[key] = raw
            except ValueError:
                pass
    if "timestamp" not in output:
        return {}
    for key in ("task_id", "job_id", "request_id"):
        raw = value.get(key)
        if isinstance(raw, str) and re.fullmatch(r"(?:[0-9a-f]{16}|[0-9a-f]{20}|[0-9a-f]{32})", raw):
            output[key] = raw
    for key, maximum in (("http_status", 599), ("duration_ms", 86_400_000), ("suppressed_count", 1_000_000_000)):
        raw = value.get(key)
        if type(raw) is int and 0 <= raw <= maximum and (key != "http_status" or raw >= 100):
            output[key] = raw
    return output


def classify_error(error: BaseException) -> tuple[str, int | None]:
    if isinstance(error, (asyncio.CancelledError, GeneratorExit)):
        return "CANCELLED", None
    for category, code, status in (
        (TelegramUnauthorizedError, "UNAUTHORIZED", 401), (TelegramForbiddenError, "FORBIDDEN", 403),
        (TelegramConflictError, "CONFLICT", 409), (TelegramRetryAfter, "RATE_LIMITED", 429),
        (TelegramBadRequest, "BAD_REQUEST", 400), (TelegramServerError, "SERVER_ERROR", 500),
        (TelegramNetworkError, "NETWORK_ERROR", None), (ClientError, "NETWORK_ERROR", None),
        (TimeoutError, "NETWORK_ERROR", None),
        (ProxyConnectionError, "NETWORK_ERROR", None), (ProxyTimeoutError, "NETWORK_ERROR", None),
        (ProxyError, "NETWORK_ERROR", None),
    ):
        if isinstance(error, category):
            return code, status
    if isinstance(error, AppError) and error.code in ERRORS:
        return error.code, None
    return "UNKNOWN_ERROR", None


class DiagnosticLog:
    def __init__(self, data_dir: Path, *, max_file_bytes: int = 10 * 1024 * 1024,
                 backup_count: int = 3, clock: Callable[[], float] = time.time) -> None:
        self.directory = data_dir / "logs"
        self.max_file_bytes, self.backup_count, self.clock = max_file_bytes, backup_count, clock
        self.lock = threading.RLock()
        self.failures: set[tuple[str, str, str]] = set()
        self.error = False
        self.repeated: dict[tuple[str, ...], tuple[float, int, dict[str, Scalar]]] = {}

    def request(self, mode: str, method: str, *, error: BaseException | None = None,
                duration_ms: int = 0, empty: bool = False, scope: str = "",
                http_status: int | None = None) -> None:
        key = (scope, mode, method)
        with self.lock:
            if error is not None:
                code, status = classify_error(error)
                status = http_status or status
                if status in {400, 401, 403, 409, 429}:
                    code = {400: "BAD_REQUEST", 401: "UNAUTHORIZED", 403: "FORBIDDEN", 409: "CONFLICT", 429: "RATE_LIMITED"}[status]
                elif status is not None and status >= 500:
                    code = "SERVER_ERROR"
                if len(self.failures) >= 256:
                    self.failures.clear()
                self.failures.add(key)
                self.emit("request_deferred" if code == "BOT_REQUEST_PAUSED" else "telegram_request",
                          dedupe=True, mode=mode, method=method, outcome="deferred" if code == "BOT_REQUEST_PAUSED" else "cancelled" if code == "CANCELLED" else "failed",
                          error_code=code, http_status=status, level="warning", duration_ms=duration_ms)
            else:
                recovered = key in self.failures
                self.failures.discard(key)
                if recovered:
                    self.flush()
                if recovered or not empty:
                    self.emit("telegram_recovered" if recovered else "telegram_request", mode=mode, method=method,
                              outcome="success", level="info", duration_ms=duration_ms)

    def _path(self, index: int = 0) -> Path:
        return self.directory / ("events.jsonl" + (f".{index}" if index else ""))

    def _check(self) -> None:
        # Do not follow attacker-replaced log directories or files, including archives.
        if self.directory.is_symlink():
            raise OSError(errno.EPERM, "Unsafe diagnostic directory")
        self.directory.mkdir(mode=0o700, exist_ok=True)
        if not stat.S_ISDIR(self.directory.lstat().st_mode):
            raise OSError(errno.EPERM, "Unsafe diagnostic directory")
        self.directory.chmod(0o700)
        for index in range(self.backup_count + 1):
            path = self._path(index)
            try:
                info = path.lstat()
            except FileNotFoundError:
                continue
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise OSError(errno.EPERM, "Unsafe diagnostic file")

    def _open(self, path: Path, flags: int) -> int:
        fd = os.open(path, flags | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0), 0o600)
        try:
            info, current = os.fstat(fd), path.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or (info.st_dev, info.st_ino) != (current.st_dev, current.st_ino):
                raise OSError(errno.EPERM, "Unsafe diagnostic file")
            if hasattr(os, "fchmod"):
                os.fchmod(fd, 0o600)
        except BaseException:
            os.close(fd)
            raise
        return fd

    def _write(self, event: dict[str, Scalar]) -> None:
        self._check()
        payload = (json.dumps(event, ensure_ascii=True, separators=(",", ":")) + "\n").encode()
        if len(payload) > self.max_file_bytes:
            raise OSError("Diagnostic event exceeds configured bound")
        path = self._path()
        if path.exists() and path.stat().st_size + len(payload) > self.max_file_bytes:
            self._path(self.backup_count).unlink(missing_ok=True)
            for index in range(self.backup_count - 1, -1, -1):
                if self._path(index).exists():
                    self._path(index).replace(self._path(index + 1))
        fd = self._open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND)
        with os.fdopen(fd, "ab") as stream:
            if hasattr(os, "fchmod"):
                os.fchmod(stream.fileno(), 0o600)
            stream.write(payload)
            stream.flush()
        self.error = False

    def emit(self, event: str, *, dedupe: bool = False, **fields: Any) -> None:
        try:
            payload = project_event(fields | {"event": event, "timestamp": datetime.fromtimestamp(self.clock(), UTC).isoformat()})
            if not payload:
                return
            with self.lock:
                if dedupe:
                    key = tuple(str(payload.get(name, "")) for name in ("event", "mode", "method", "outcome", "error_code", "task_id", "job_id"))
                    prior = self.repeated.get(key)
                    if prior and self.clock() - prior[0] < 60:
                        self.repeated[key] = (prior[0], min(prior[1] + 1, 1_000_000_000), payload)
                        return
                    if prior and prior[1]:
                        self._write(prior[2] | {"event": "event_summary", "suppressed_count": prior[1], "timestamp": payload["timestamp"]})
                    if len(self.repeated) >= 256 and key not in self.repeated:
                        self._flush()
                    self.repeated[key] = (self.clock(), 0, payload)
                self._write(payload)
        except Exception:
            self.error = True

    def _flush(self) -> None:
        for _, count, payload in self.repeated.values():
            if count:
                self._write(payload | {"event": "event_summary", "suppressed_count": count,
                                       "timestamp": datetime.fromtimestamp(self.clock(), UTC).isoformat()})
        self.repeated.clear()

    def flush(self) -> None:
        try:
            with self.lock:
                self._flush()
        except Exception:
            self.error = True

    def status(self) -> dict[str, Any]:
        return {"state": "error" if self.error else "ready", "message": "诊断日志不可用，请检查数据卷权限或空间" if self.error else "诊断日志已启用",
                "max_file_bytes": self.max_file_bytes, "backup_count": self.backup_count}

    def export(self) -> dict[str, Any]:
        self.flush()
        events: deque[dict[str, Scalar]] = deque(maxlen=1000)
        truncated = False
        try:
            with self.lock:
                self._check()
                for index in range(self.backup_count, -1, -1):
                    path = self._path(index)
                    if not path.exists():
                        continue
                    fd = self._open(path, os.O_RDONLY)
                    with os.fdopen(fd, "rb") as stream:
                        size = os.fstat(stream.fileno()).st_size
                        if size > self.max_file_bytes:
                            stream.seek(size - self.max_file_bytes)
                            skipped = 0
                            while skipped <= self.max_file_bytes:
                                fragment = stream.readline(4097)
                                skipped += len(fragment)
                                if not fragment or fragment.endswith(b"\n"):
                                    break
                            truncated = True
                        consumed = 0
                        while consumed <= self.max_file_bytes:
                            line = stream.readline(4097)
                            if not line:
                                break
                            consumed += len(line)
                            if len(line) > 4096 or not line.endswith(b"\n"):
                                truncated = True
                                # Drop remainder of this line without unbounded allocation.
                                while line and not line.endswith(b"\n") and consumed <= self.max_file_bytes:
                                    line = stream.readline(4097)
                                    consumed += len(line)
                                continue
                            try:
                                payload = project_event(json.loads(line))
                            except (ValueError, UnicodeError, TypeError, RecursionError):
                                payload = {}
                            if payload:
                                if len(events) == events.maxlen:
                                    truncated = True
                                events.append(payload)
                            else:
                                truncated = True
            return {"events": sorted(events, key=lambda event: datetime.fromisoformat(str(event["timestamp"]).replace("Z", "+00:00"))), "truncated": truncated}
        except Exception:
            self.error = True
            raise AppError("DIAGNOSTIC_LOG_UNAVAILABLE", "诊断日志暂不可用，请检查数据卷权限或空间", 503) from None
