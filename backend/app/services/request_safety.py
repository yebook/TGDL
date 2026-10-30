"""Bot credentials share durable request starts, server waits and auth pauses.

Only a digest is stored. Transactions reserve starts, never remote response time.
Conservative spacing is an application policy, not a Telegram safety guarantee.
"""
import asyncio
import math
import random
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Literal

from aiogram.exceptions import (
    TelegramConflictError,
    TelegramNetworkError,
    TelegramRetryAfter,
    TelegramServerError,
    TelegramUnauthorizedError,
)
from aiohttp import ClientError
from aiohttp_socks import ProxyConnectionError, ProxyError, ProxyTimeoutError

from app.core.errors import AppError
from app.core.security import digest
from app.db.models import RequestSafety
from app.db.session import Database
from app.services.diagnostics import DiagnosticLog

# Keep persisted deadlines representable on supported platforms and in the UI.
MAX_DEADLINE = 253402214400.0
SafetyDeferralReason = Literal["pacing", "cooldown", "backoff", "auth_paused", "protection_unavailable"]


def checked_values(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("Invalid protection state")
    values = dict(raw)
    for key in ("cooldown", "backoff", "manual_at", "next_start", "failures", "auth_revision", "probe_auth_revision"):
        value = values.get(key, 0)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= MAX_DEADLINE:
            raise ValueError("Invalid protection state")
    if not isinstance(values.get("auth_paused", False), bool):
        raise ValueError("Invalid protection state")
    chats = values.get("chats", {})
    if not isinstance(chats, dict) or any(
        not isinstance(key, str) or isinstance(value, bool) or not isinstance(value, (int, float))
        or not 0 <= value <= MAX_DEADLINE for key, value in chats.items()
    ):
        raise ValueError("Invalid protection state")
    return values


class SafetyDeferred(AppError):
    def __init__(self, message: str, retry_at: float | None = None, *, sent: bool = False,
                 reason: SafetyDeferralReason = "protection_unavailable") -> None:
        super().__init__("BOT_REQUEST_PAUSED", message, 429)
        self.retry_at, self.sent = retry_at, sent
        self.reason = reason


class RequestSafetyService:
    def __init__(self, db: Database, clock: Callable[[], float] = time.time,
                 jitter: Callable[[], float] = random.random) -> None:
        self.db, self.clock, self.jitter = db, clock, jitter
        self.diagnostics: DiagnosticLog | None = None

    @staticmethod
    def blocked(values: dict[str, Any], now: float) -> bool:
        try:
            values = checked_values(values)
            return bool(values.get("auth_paused") or max(values.get("cooldown", 0), values.get("backoff", 0)) > now)
        except (ValueError, TypeError):
            return True

    def status(self, token: str) -> dict[str, Any]:
        now = self.clock()
        try:
            with self.db.sessions() as session:
                row = session.get(RequestSafety, digest(token)) if token else None
                values = checked_values(row.values) if row else {}
        except Exception:
            return {"state": "backoff", "retry_at": None, "message": "请求保护状态不可用，已暂停 Telegram 请求", "can_retry": False}
        cooldown, backoff = values.get("cooldown", 0), values.get("backoff", 0)
        deadline = max(cooldown, backoff, values.get("manual_at", 0) if values.get("auth_paused") else 0)
        state = "auth_paused" if values.get("auth_paused") else "throttled" if cooldown > now else "backoff" if backoff > now else "ready"
        message = {"ready": "", "throttled": "Telegram 要求等待，期间暂停新请求", "backoff": "连接异常，正在延后重试",
                   "auth_paused": "Bot 凭据被拒绝，已暂停自动请求；请检查 Token 后手动重试"}[state]
        return {"state": state, "retry_at": datetime.fromtimestamp(deadline, UTC).isoformat() if deadline > now else None,
                "message": message, "can_retry": bool(token) and state == "auth_paused" and deadline <= now
                    and values.get("manual_at", 0) <= now}

    def reserve(self, token: str, chat_id: int | None = None, *, probe: bool = False) -> float:
        return self._reserve(token, chat_id, probe=probe)[0]

    def _reserve(self, token: str, chat_id: int | None = None, *, probe: bool = False) -> tuple[float, bool]:
        now = self.clock()
        with self.db.transaction() as session:
            row = session.get(RequestSafety, digest(token))
            if not row:
                row = RequestSafety(credential=digest(token), values={})
                session.add(row)
            values = checked_values(row.values)
            deadline = max(values.get("cooldown", 0), values.get("backoff", 0))
            if deadline > now:
                cooldown = values.get("cooldown", 0) > now
                raise SafetyDeferred("Telegram 要求等待，暂缓新请求" if cooldown else "连接请求正在退避等待",
                                     deadline, reason="cooldown" if cooldown else "backoff")
            if values.get("auth_paused") and not probe:
                raise SafetyDeferred("Bot 凭据被拒绝，已暂停自动请求，请检查 Token 后手动重试", reason="auth_paused")
            if probe and values.get("manual_at", 0) > now:
                raise SafetyDeferred("手动重试过于频繁，请稍后再试", values["manual_at"], reason="pacing")
            chats = {key: value for key, value in values.get("chats", {}).items() if value > now}
            start = max(values.get("next_start", 0), chats.get(str(chat_id), 0) if chat_id is not None else 0)
            if start > now:
                return start, False
            values["next_start"] = now + 1.0
            if chat_id is not None:
                chats[str(chat_id)] = now + (3.1 if chat_id < 0 else 1.1)
            values["chats"] = chats
            if probe:
                values["manual_at"] = now + 60
                values["probe_auth_revision"] = values.get("auth_revision", 0)
            row.values = values
        return now, True

    async def before(self, token: str, chat_id: int | None = None, *, probe: bool = False) -> None:
        # Small pacing waits may complete; server waits return immediately so lifecycle
        # and settings locks never remain held for a long RetryAfter/auth pause.
        started = self.clock()
        try:
            while True:
                at, accepted = await asyncio.to_thread(self._reserve, token, chat_id, probe=probe)
                now = self.clock()
                if accepted:
                    return
                if at - started > 1.2:
                    raise SafetyDeferred("正在按聊天频率发送，请稍后重试", at, reason="pacing")
                await asyncio.sleep(max(0.001, at - now))
        except AppError:
            raise
        except Exception as exc:
            raise SafetyDeferred("请求保护状态不可用，已暂停 Telegram 请求", reason="protection_unavailable") from exc

    def observe(self, token: str, error: Exception | None, *, probe: bool = False,
                retry_after: float | None = None, unauthorized: bool = False, transient: bool = False) -> None:
        with self.db.transaction() as session:
            row = session.get(RequestSafety, digest(token))
            if not row:
                row = RequestSafety(credential=digest(token), values={})
                session.add(row)
            values, now = checked_values(row.values), self.clock()
            if retry_after is not None or isinstance(error, TelegramRetryAfter):
                wait = retry_after if retry_after is not None else error.retry_after  # type: ignore[union-attr]
                # Invalid or unbounded upstream waits must never produce NaN state
                # or permit immediate retries. Retain effectively indefinite waits.
                wait = max(1, wait) if -MAX_DEADLINE <= wait <= MAX_DEADLINE and math.isfinite(wait) else MAX_DEADLINE
                values["cooldown"] = max(values.get("cooldown", 0), min(MAX_DEADLINE, now + wait + 1))
            elif unauthorized or isinstance(error, TelegramUnauthorizedError):
                values["auth_paused"] = True
                values["auth_revision"] = values.get("auth_revision", 0) + 1
            elif transient or isinstance(error, (TelegramConflictError, TelegramServerError, TelegramNetworkError, ClientError, TimeoutError,
                                                 ProxyConnectionError, ProxyTimeoutError, ProxyError)):
                failures = min(6, values.get("failures", 0) + 1)
                values.update(failures=failures, backoff=max(values.get("backoff", 0), now + min(120, 5 * 2 ** (failures - 1)) + self.jitter()))
            elif error is None:
                values["failures"] = 0
                # Never shorten a concurrently established server wait/backoff.
                if probe and values.get("probe_auth_revision") == values.get("auth_revision", 0):
                    values["auth_paused"] = False
            row.values = values

        if self.diagnostics:
            try:
                state = self.status(token)
                if state["state"] != "ready":
                    self.diagnostics.emit("request_protection", dedupe=True, level="warning",
                                          outcome=state["state"], retry_at=state["retry_at"])
            except Exception:
                self.diagnostics.error = True
