import asyncio
import json
import math
import re
import time
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import aclosing
from datetime import UTC
from email.utils import parsedate_to_datetime
from typing import Any, cast
from urllib.parse import quote

from aiogram import Bot
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramConflictError,
    TelegramRetryAfter,
    TelegramUnauthorizedError,
)
from aiogram.methods import GetFile, GetUpdates
from aiogram.types import (
    BotCommand,
    BotCommandScopeAllPrivateChats,
    File,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    MenuButtonCommands,
)
from aiohttp import ClientError, ClientSession, ClientTimeout
from aiohttp_socks import ProxyConnectionError, ProxyError, ProxyTimeoutError

from app.bot.commands import COMMANDS
from app.core.config import Config
from app.core.errors import AppError
from app.core.security import digest
from app.schemas.contracts import UserSettings
from app.services.download_progress import decode_progress
from app.services.request_safety import RequestSafetyService, SafetyDeferred

CLOUD_FILE_LIMIT = 20 * 1024 * 1024
LOCAL_GET_FILE_TIMEOUT = ClientTimeout(total=None, connect=20, sock_connect=20, sock_read=None)


class CacheCleanupError(AppError):
    """Fixed housekeeping evidence; never carry upstream descriptions or identities."""

    def __init__(self, reason: str, started: float, http_status: int | None = None) -> None:
        super().__init__("CACHE_CLEANUP_UNAVAILABLE", "媒体缓存待清理，将自动重试")
        self.reason = reason
        self.http_status = http_status
        self.duration_ms = int(max(0, time.monotonic() - started) * 1000)


# Match exact status/description pairs from the private native protocol only.
# Unknown upstream text is deliberately discarded, including path-bearing errors.
_CACHE_CLEANUP_REJECTIONS = {
    (404, "Not Found: cache cleanup unavailable"): "cache_unavailable",
    (404, "Not Found: progress unavailable"): "cache_unavailable",
    (400, "Bad Request: method not found"): "cache_unsupported",
    (400, "Bad Request: invalid cache cleanup request"): "cache_invalid_request",
    (400, "Bad Request: invalid cache path"): "cache_path_invalid",
    (409, "Conflict: cache generation changed"): "cache_generation_changed",
    (409, "Conflict: cache cleanup busy"): "cache_busy",
    (409, "Conflict: cache file busy"): "cache_busy",
    (409, "Conflict: cache identity unavailable"): "cache_identity_unavailable",
    (409, "Conflict: cache identity changed"): "cache_identity_changed",
    (500, "Internal Server Error: cache removal unconfirmed"): "cache_removal_unconfirmed",
    (409, "Conflict: cache recovery busy"): "cache_busy",
    (409, "Conflict: cache recovery identity changed"): "cache_recovery_identity_changed",
    (409, "Conflict: cache recovery content mismatch"): "cache_recovery_content_mismatch",
    (500, "Internal Server Error: cache recovery unavailable"): "cache_recovery_unavailable",
}


def cloud_size_check(size: int | None) -> None:
    if size is not None and size > CLOUD_FILE_LIMIT:
        raise AppError("CLOUD_FILE_TOO_LARGE", "云端 Bot API 仅支持下载不超过 20 MB 的文件，请在设置切换本地 API 后重试")


class TelegramClient:
    def __init__(self, token: str, config: Config, settings: UserSettings | None = None) -> None:
        settings = settings or UserSettings()
        self.safety: RequestSafetyService | None = None
        self.diagnostic_action: str | None = None
        self.diagnostic_operation_id: str | None = None
        self.mode = settings.bot_api_mode
        proxy = None
        if self.mode == "cloud" and settings.proxy.enabled:
            p = settings.proxy
            auth = f"{quote(p.username, safe='')}:{quote(p.password, safe='')}@" if p.username or p.password else ""
            proxy = f"{p.scheme}://{auth}{p.host}:{p.port}"
        api = TelegramAPIServer.from_base(config.bot_api_base, is_local=True) if self.mode == "local" else TelegramAPIServer.from_base("https://api.telegram.org")
        session = AiohttpSession(api=api, proxy=proxy, timeout=config.get_file_timeout)
        self.bot = Bot(token=token, session=session)
        self.timeout = config.get_file_timeout
        self._progress_session: ClientSession | None = None
        self._progress_retry_at = 0.0
        self._progress_failures = 0
        self._cleanup_session: ClientSession | None = None
        self._cache_storage_session: ClientSession | None = None
        self._acquisition_session: ClientSession | None = None

    async def file_acquisition(self, generation: str, *, file_id: str | None = None,
                               acquisition_id: str | None = None, cancel: bool = False,
                               expected_cache_identity: str | None = None) -> dict[str, Any]:
        """Bounded local control only: it neither enters nor clears Telegram protection."""
        if (self.mode != "local" or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", generation)
                or file_id is not None and not 1 <= len(file_id.encode("utf-8")) <= 4096
                or acquisition_id is not None and not re.fullmatch(r"[0-9a-f]{64}", acquisition_id)
                or (file_id is None) == (acquisition_id is None)
                or expected_cache_identity is not None and (file_id is None
                    or not re.fullmatch(r"[0-9a-f]{64}", expected_cache_identity))):
            raise AppError("ACQUISITION_CONTROL_UNAVAILABLE", "获取控制暂不可用")
        data = {"generation": generation}
        if file_id is not None:
            method = "tgdPrepareFileAcquisition"
            data["file_id"] = file_id
            if expected_cache_identity is not None:
                data["expected_cache_identity"] = expected_cache_identity
        else:
            method = "tgdCancelFileAcquisition" if cancel else "tgdGetFileAcquisition"
            data["acquisition_id"] = str(acquisition_id)
        try:
            if self._acquisition_session is None:
                self._acquisition_session = ClientSession(timeout=ClientTimeout(total=2), trust_env=False)
            url = self.bot.session.api.api_url(self.bot.token, method)
            async with self._acquisition_session.post(url, data=data, allow_redirects=False) as response:
                body = bytearray()
                async for block in response.content.iter_chunked(4096):
                    body.extend(block)
                    if len(body) > 8192:
                        raise ValueError("Acquisition response too large")
                payload = json.loads(body)
                if response.status in {404, 405, 501} or (response.status == 400
                        and isinstance(payload, dict) and payload.get("description") == "Bad Request: method not found"):
                    raise AppError("ACQUISITION_CONTROL_UNSUPPORTED", "当前本地 API 不支持获取阶段取消，请更新配套服务")
                if response.status != 200:
                    raise ValueError("Acquisition unavailable")
                if not isinstance(payload, dict) or set(payload) != {"ok", "result"} or payload["ok"] is not True:
                    raise ValueError("Invalid acquisition response")
                result = payload["result"]
                if (not isinstance(result, dict) or set(result) != {"generation", "acquisition_id", "state", "settled"}
                        or result["generation"] != generation
                        or not isinstance(result["acquisition_id"], str)
                        or not re.fullmatch(r"[0-9a-f]{64}", result["acquisition_id"])
                        or acquisition_id is not None and result["acquisition_id"] != acquisition_id
                        or result["state"] not in {"prepared", "resolving", "downloading", "completed", "cancelling", "cancelled", "failed"}
                        or type(result["settled"]) is not bool
                        or result["settled"] != (result["state"] in {"completed", "cancelled", "failed"})
                        or file_id is not None and result["state"] != "prepared"):
                    raise ValueError("Invalid acquisition identity")
                return dict(result)
        except (ClientError, TimeoutError, ValueError, TypeError, OverflowError, RecursionError) as exc:
            raise AppError("ACQUISITION_CONTROL_UNAVAILABLE", "获取控制暂不可用，仍保留下载槽位") from exc

    async def _cache_storage(self, generation: str, *, refresh: bool = False,
                             operation_id: str | None = None) -> dict[str, Any]:
        from app.services.cache_storage_protocol import decode_cache_storage

        if (self.mode != "local" or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", generation)
                or operation_id is not None and not re.fullmatch(r"[0-9a-f]{32}", operation_id)):
            raise AppError("CACHE_UNAVAILABLE", "缓存维护暂不可用")
        try:
            if self._cache_storage_session is None:
                self._cache_storage_session = ClientSession(timeout=ClientTimeout(total=2), trust_env=False)
            method = "tgdClearCacheStorage" if operation_id else "tgdGetCacheStorage"
            data = {"generation": generation}
            data.update({"operation_id": operation_id} if operation_id else {"refresh": "1" if refresh else "0"})
            url = self.bot.session.api.api_url(self.bot.token, method)
            async with self._cache_storage_session.post(url, data=data, allow_redirects=False) as response:
                if response.status in {404, 405, 501}:
                    raise AppError("CACHE_UNSUPPORTED", "当前本地 API 不支持缓存维护", 409)
                body = bytearray()
                async for block in response.content.iter_chunked(4096):
                    body.extend(block)
                    if len(body) > 16384:
                        raise ValueError("Cache storage response too large")
                payload = json.loads(body)
                if (response.status == 409 and isinstance(payload, dict) and payload.get("ok") is False
                        and payload.get("description") == "Conflict: TGD_CACHE_STORAGE_BUSY"):
                    raise AppError("CACHE_NATIVE_BUSY", "本地 API 正在处理文件，请稍后重试", 409)
                if (response.status == 400 and isinstance(payload, dict) and payload.get("ok") is False
                        and payload.get("description") == "Bad Request: method not found"):
                    raise AppError("CACHE_UNSUPPORTED", "当前本地 API 不支持缓存维护", 409)
                if response.status != 200:
                    raise ValueError("Cache storage unavailable")
                return decode_cache_storage(payload, generation)
        except (ClientError, TimeoutError, ValueError, TypeError, OverflowError, RecursionError) as exc:
            raise AppError("CACHE_UNAVAILABLE", "缓存维护暂不可用，请检查本地 API") from exc

    async def cache_storage(self, generation: str, *, refresh: bool = False) -> dict[str, Any]:
        return await self._cache_storage(generation, refresh=refresh)

    async def clear_cache_storage(self, generation: str, operation_id: str) -> dict[str, Any]:
        return await self._cache_storage(generation, operation_id=operation_id)

    async def delete_file_cache(self, file_id: str, expected_path: str, expected_size: int, generation: str,
                                *, expected_sha256: str) -> str:
        """Offline local TDLib housekeeping. Never call Telegram or alter request protection."""
        started = time.monotonic()
        http_status = None
        if (self.mode != "local" or not 1 <= len(file_id.encode("utf-8")) <= 4096
                or not 1 <= len(expected_path.encode("utf-8")) <= 4096
                or not 1 <= len(generation.encode("utf-8")) <= 128
                or not isinstance(expected_sha256, str) or re.fullmatch(r"[0-9a-f]{64}", expected_sha256) is None
                or type(expected_size) is not int or not 0 <= expected_size <= 2**63 - 1):
            raise CacheCleanupError("cache_invalid_request", started)
        try:
            if self._cleanup_session is None:
                self._cleanup_session = ClientSession(timeout=ClientTimeout(total=2), trust_env=False)
            url = self.bot.session.api.api_url(self.bot.token, "tgdDeleteFileCache")
            async with self._cleanup_session.post(url, data={"file_id": file_id, "expected_path": expected_path,
                    "expected_size": str(expected_size), "expected_sha256": expected_sha256,
                    "generation": generation}, allow_redirects=False) as response:
                http_status = response.status
                body = bytearray()
                async for block in response.content.iter_chunked(4096):
                    body.extend(block)
                    if len(body) > 8192:
                        raise CacheCleanupError("cache_response_too_large", started, http_status)
                try:
                    payload = json.loads(body)
                except (ValueError, TypeError, OverflowError, RecursionError):
                    payload = None
                if http_status != 200:
                    reason = "cache_unsupported" if http_status in {404, 405, 501} else "cache_http_error"
                    if (isinstance(payload, dict) and payload.get("ok") is False
                            and isinstance(payload.get("description"), str)):
                        reason = _CACHE_CLEANUP_REJECTIONS.get((http_status, payload["description"]), reason)
                    raise CacheCleanupError(reason, started, http_status)
                if not isinstance(payload, dict) or set(payload) != {"ok", "result"} or payload["ok"] is not True:
                    raise ValueError("Invalid cleanup response")
                result = payload["result"]
                if (not isinstance(result, dict) or set(result) != {"generation", "status"}
                        or result["generation"] != generation or result["status"] not in ("deleted", "absent")):
                    raise ValueError("Invalid cleanup identity")
                return str(result["status"])
        except TimeoutError:
            raise CacheCleanupError("cache_timeout", started, http_status) from None
        except ClientError:
            raise CacheCleanupError("cache_network_error", started, http_status) from None
        except (ValueError, TypeError, OverflowError, RecursionError):
            raise CacheCleanupError("cache_response_invalid", started, http_status) from None

    async def inspect_file_cache(self, file_id: str, generation: str, *, acquisition_id: str | None = None,
                                 expected_path: str | None = None, expected_size: int | None = None,
                                 expected_identity: str | None = None) -> dict[str, Any]:
        """Bounded offline evidence, including settled orphan partials; never starts a transfer."""
        proof = any(value is not None for value in (expected_path, expected_size, expected_identity))
        if (self.mode != "local" or not 1 <= len(file_id.encode()) <= 4096
                or re.fullmatch(r"[A-Za-z0-9_-]{1,100}", generation) is None
                or acquisition_id is not None and re.fullmatch(r"[0-9a-f]{64}", acquisition_id) is None
                or proof and (expected_path is None or not 1 <= len(expected_path.encode()) <= 4096
                    or not expected_path.startswith("/") or "\0" in expected_path
                    or type(expected_size) is not int or not 0 <= expected_size <= 2**63 - 1
                    or expected_identity is None or re.fullmatch(r"[0-9a-f]{64}", expected_identity) is None)):
            raise AppError("CACHE_INSPECTION_UNAVAILABLE", "缓存检查暂不可用", 409)
        data = {"generation": generation, "file_id": file_id}
        if acquisition_id is not None:
            data["acquisition_id"] = acquisition_id
        if proof:
            data.update(expected_path=str(expected_path), expected_size=str(expected_size),
                        expected_identity=str(expected_identity))
        try:
            if self._cleanup_session is None:
                self._cleanup_session = ClientSession(timeout=ClientTimeout(total=2), trust_env=False)
            url = self.bot.session.api.api_url(self.bot.token, "tgdGetFileCache")
            async with self._cleanup_session.post(url, data=data, allow_redirects=False) as response:
                body = bytearray()
                async for block in response.content.iter_chunked(4096):
                    body.extend(block)
                    if len(body) > 16384:
                        raise ValueError("Cache response too large")
                payload = json.loads(body)
                if response.status in {404, 405, 501} or (response.status == 400
                        and isinstance(payload, dict) and payload.get("description") == "Bad Request: method not found"):
                    raise AppError("CACHE_INSPECTION_UNSUPPORTED", "当前本地 API 不支持缓存检查，请更新配套服务", 409)
                if (response.status == 409 and isinstance(payload, dict)
                        and payload.get("description") in {"Conflict: cache cleanup busy", "Conflict: cache file busy"}):
                    raise AppError("CACHE_NATIVE_BUSY", "文件或缓存操作尚未停止，请稍后重试", 409)
                if (response.status != 200 or not isinstance(payload, dict) or set(payload) != {"ok", "result"}
                        or payload["ok"] is not True):
                    raise ValueError("Cache evidence unavailable")
                result = payload["result"]
                fields = {"generation", "completeness", "path", "size", "allocated_bytes", "identity",
                          "resumable", "reason", "settled"}
                reasons = {"cache_unavailable", "cache_busy", "cache_missing", "cache_identity_changed",
                           "bitmap_unavailable", "cache_complete", "resume_available"}
                if (not isinstance(result, dict) or set(result) != fields or result["generation"] != generation
                        or result["completeness"] not in {"complete", "partial", "absent", "unknown"}
                        or not isinstance(result["path"], str) or len(result["path"].encode()) > 4096
                        or "\0" in result["path"] or not isinstance(result["identity"], str)
                        or type(result["size"]) is not int or not 0 <= result["size"] <= 2**63 - 1
                        or type(result["allocated_bytes"]) is not int or not 0 <= result["allocated_bytes"] <= 2**63 - 1
                        or type(result["settled"]) is not bool or type(result["resumable"]) is not bool
                        or result["reason"] not in reasons):
                    raise ValueError("Invalid cache evidence")
                present = result["completeness"] in {"complete", "partial"}
                if (present and (not result["path"].startswith("/")
                                 or re.fullmatch(r"[0-9a-f]{64}", result["identity"]) is None)
                        or not present and (result["path"] or result["identity"] or result["size"] or result["allocated_bytes"])
                        or result["resumable"] and (result["completeness"] != "partial"
                            or not result["settled"] or result["reason"] != "resume_available")):
                    raise ValueError("Inconsistent cache evidence")
                return dict(result)
        except (ClientError, TimeoutError, ValueError, TypeError, OverflowError, RecursionError):
            raise AppError("CACHE_INSPECTION_UNAVAILABLE", "缓存检查失败，请检查本地 API 后重试", 409) from None

    async def delete_partial_file_cache(self, file_id: str, expected_path: str, expected_size: int,
                                        generation: str, *, expected_identity: str) -> str:
        """Exact TDLib-owned partial deletion; a lost reply remains an unknown mutation."""
        started = time.monotonic()
        http_status = None
        if (self.mode != "local" or not 1 <= len(file_id.encode()) <= 4096
                or not 1 <= len(expected_path.encode()) <= 4096 or not expected_path.startswith("/")
                or "\0" in expected_path or re.fullmatch(r"[A-Za-z0-9_-]{1,100}", generation) is None
                or re.fullmatch(r"[0-9a-f]{64}", expected_identity) is None
                or type(expected_size) is not int or not 0 <= expected_size <= 2**63 - 1):
            raise CacheCleanupError("cache_invalid_request", started)
        try:
            if self._cleanup_session is None:
                self._cleanup_session = ClientSession(timeout=ClientTimeout(total=2), trust_env=False)
            url = self.bot.session.api.api_url(self.bot.token, "tgdDeletePartialFileCache")
            async with self._cleanup_session.post(url, data={"file_id": file_id, "expected_path": expected_path,
                    "expected_size": str(expected_size), "expected_identity": expected_identity,
                    "generation": generation}, allow_redirects=False) as response:
                http_status = response.status
                body = bytearray()
                async for block in response.content.iter_chunked(4096):
                    body.extend(block)
                    if len(body) > 8192:
                        raise CacheCleanupError("cache_response_too_large", started, http_status)
                payload = json.loads(body)
                if http_status != 200:
                    reason = "cache_unsupported" if http_status in {404, 405, 501} else "cache_http_error"
                    if isinstance(payload, dict) and isinstance(payload.get("description"), str):
                        reason = _CACHE_CLEANUP_REJECTIONS.get((http_status, payload["description"]), reason)
                    raise CacheCleanupError(reason, started, http_status)
                result = payload.get("result") if isinstance(payload, dict) else None
                if (not isinstance(payload, dict) or set(payload) != {"ok", "result"} or payload["ok"] is not True
                        or not isinstance(result, dict) or set(result) != {"generation", "status"}
                        or result["generation"] != generation or result["status"] not in {"deleted", "absent"}):
                    raise ValueError("Invalid cleanup proof")
                return str(result["status"])
        except TimeoutError:
            raise CacheCleanupError("cache_timeout", started, http_status) from None
        except ClientError:
            raise CacheCleanupError("cache_network_error", started, http_status) from None
        except (ValueError, TypeError, OverflowError, RecursionError):
            raise CacheCleanupError("cache_response_invalid", started, http_status) from None

    async def file_progress(self, file_ids: list[str]) -> tuple[str, dict[str, dict[str, Any]]] | None:
        """Read our private local snapshot method. Never enter Telegram request/protection paths."""
        if (self.mode != "local" or time.monotonic() < self._progress_retry_at
                or not 1 <= len(file_ids) <= 16
                or any(not isinstance(alias, str) or not 1 <= len(alias.encode("utf-8")) <= 4096 for alias in file_ids)):
            return None
        try:
            if self._progress_session is None:
                self._progress_session = ClientSession(timeout=ClientTimeout(total=1), trust_env=False)
            url = self.bot.session.api.api_url(self.bot.token, "tgdGetFileProgress")
            async with self._progress_session.post(url, data={"file_ids": json.dumps(file_ids, ensure_ascii=False)},
                                                   allow_redirects=False) as response:
                if response.status != 200:
                    # Stock/external APIs should not receive one unsupported-method call per second.
                    self._progress_failures += 1
                    delay = 60 if response.status in {400, 404, 405, 501} else min(30, 2**min(self._progress_failures, 5))
                    self._progress_retry_at = time.monotonic() + delay
                    return None
                body = bytearray()
                async for block in response.content.iter_chunked(16384):
                    body.extend(block)
                    if len(body) > 512 * 1024:
                        raise ValueError("Progress response too large")
                decoded = decode_progress(json.loads(body), file_ids)
            self._progress_failures = 0
            return decoded
        except (ClientError, TimeoutError, ValueError, TypeError, OverflowError, RecursionError):
            self._progress_failures += 1
            self._progress_retry_at = time.monotonic() + min(30, 2**min(self._progress_failures, 5))
            return None

    async def record(self, method: str, error: BaseException | None, started: float,
                     *, empty: bool = False) -> None:
        diagnostic = getattr(self.safety, "diagnostics", None)
        if diagnostic:
            try:
                await asyncio.to_thread(diagnostic.request, self.mode, method, error=error,
                                        duration_ms=int(max(0, time.monotonic() - started) * 1000),
                                        empty=empty, scope=digest(self.bot.token),
                                        http_status=getattr(error, "diagnostic_http_status", None),
                                        action=self.diagnostic_action, operation_id=self.diagnostic_operation_id)
            except Exception:
                diagnostic.error = True

    async def request[T](self, operation: Callable[[], Awaitable[T]], chat_id: int | None = None,
                         *, probe: bool = False, method: str = "getMe") -> T:
        started = time.monotonic()
        try:
            if self.safety:
                await self.safety.before(self.bot.token, chat_id, probe=probe)
        except Exception as exc:
            await self.record(method, exc, started)
            raise
        try:
            result = await operation()
        except asyncio.CancelledError as exc:
            await self.record(method, exc, started)
            raise
        except Exception as exc:
            if isinstance(exc, AppError) and exc.code in {
                    "ACQUISITION_CANCELLED", "ACQUISITION_REJECTED", "NO_SPACE", "CACHE_RESUME_UNAVAILABLE"}:
                # A fixed private control outcome is not a Telegram failure/success.
                await self.record(method, AppError("CANCELLED", "下载已取消")
                                  if exc.code == "ACQUISITION_CANCELLED" else exc, started)
                raise
            if self.mode == "local" and (
                    isinstance(exc, TelegramConflictError)
                    and exc.message in {"TGD_CACHE_STORAGE_BUSY", "Conflict: TGD_CACHE_STORAGE_BUSY"}
                    or isinstance(exc, TelegramBadRequest)
                    and exc.message in {"TGD_CACHE_CLEANUP_BUSY", "Bad Request: TGD_CACHE_CLEANUP_BUSY"}):
                deferred = AppError("CACHE_CLEANUP_BUSY", "媒体缓存正在维护，稍后自动继续")
                await self.record(method, deferred, started)
                raise deferred from exc  # Native guarantees this request was not accepted.
            await self.record(method, exc, started)
            if self.safety and not (isinstance(exc, AppError) and exc.code == "CACHE_CLEANUP_BUSY"):
                await asyncio.to_thread(self.safety.observe, self.bot.token, exc)
                if isinstance(exc, (TelegramRetryAfter, TelegramUnauthorizedError)):
                    raise SafetyDeferred("Telegram 请求已暂停，请等待或检查 Bot 凭据", sent=True,
                                         reason="cooldown" if isinstance(exc, TelegramRetryAfter) else "auth_paused") from exc
            raise
        await self.record(method, None, started, empty=method == "getUpdates" and not result)
        if self.safety:
            await asyncio.to_thread(self.safety.observe, self.bot.token, None, probe=probe)
        return result

    async def identity(self, *, probe: bool = False) -> dict[str, Any]:
        me = await self.request(lambda: self.bot.get_me(request_timeout=20), probe=probe)
        return {"id": me.id, "username": me.username or ""}

    async def register_commands(self) -> None:
        await self.request(lambda: self.bot.set_my_commands(
            commands=[BotCommand(command=name, description=description) for name, description in COMMANDS],
            scope=BotCommandScopeAllPrivateChats(), request_timeout=20), method="setMyCommands")
        await self.request(lambda: self.bot.set_chat_menu_button(
            menu_button=MenuButtonCommands(), request_timeout=20), method="setChatMenuButton")

    async def record_updates(self, error: BaseException | None = None) -> None:
        diagnostic = getattr(self.safety, "diagnostics", None)
        if diagnostic:
            try:
                await asyncio.to_thread(diagnostic.runtime_result, "updates", stage="updates_serialize",
                                        mode=self.mode, method="getUpdates", error=error, scope=digest(self.bot.token))
            except Exception:
                diagnostic.error = True

    async def updates(self, offset: int) -> list[dict[str, Any]]:
        result = await self.request(lambda: self.bot(GetUpdates(offset=offset, timeout=25, allowed_updates=["message", "callback_query"]),
                                request_timeout=35), method="getUpdates")
        try:
            # Omit SDK-only defaults (e.g. LinkPreviewOptions Default sentinels).
            updates = [item.model_dump(mode="json", exclude_none=True, exclude_unset=True) for item in result]
        except Exception as exc:
            await self.record_updates(exc)
            raise
        await self.record_updates()
        return updates

    async def get_file(self, file_id: str, *, generation: str | None = None,
                       acquisition_id: str | None = None) -> str:
        async def acquire() -> File:
            try:
                # Local getFile includes the download itself. aiogram forwards a non-None
                # timeout to aiohttp unchanged, although its annotation only allows int.
                # Keep connection setup bounded without imposing a download/read deadline.
                timeout = cast(Any, LOCAL_GET_FILE_TIMEOUT) if self.mode == "local" else self.timeout
                extra = {"tgd_generation": generation, "tgd_acquisition_id": acquisition_id} if acquisition_id else {}
                return await self.bot(GetFile(file_id=file_id, **extra), request_timeout=timeout)
            except TelegramBadRequest as exc:
                if self.mode == "local" and exc.message in {"TGD_CACHE_NO_SPACE", "Bad Request: TGD_CACHE_NO_SPACE"}:
                    raise AppError("NO_SPACE", "缓存盘空间不足，下载已停止；缓存保留，可到任务详情清理或继续下载") from None
                if self.mode == "local" and exc.message in {
                        "TGD_CACHE_RESUME_UNAVAILABLE", "Bad Request: TGD_CACHE_RESUME_UNAVAILABLE"}:
                    raise AppError("CACHE_RESUME_UNAVAILABLE", "缓存或分片信息已失效，无法继续下载；可清理缓存或确认重新下载") from None
                if self.mode == "local" and exc.message in {"TGD_CACHE_CLEANUP_BUSY", "Bad Request: TGD_CACHE_CLEANUP_BUSY"}:
                    raise AppError("CACHE_CLEANUP_BUSY", "媒体缓存正在清理，稍后自动继续下载") from exc
                if self.mode == "local" and acquisition_id and exc.message in {
                        "TGD_ACQUISITION_CANCELLED", "Bad Request: TGD_ACQUISITION_CANCELLED"}:
                    raise AppError("ACQUISITION_CANCELLED", "正在确认获取已停止") from exc
                if self.mode == "local" and acquisition_id and exc.message in {
                        "TGD_ACQUISITION_INVALID", "Bad Request: TGD_ACQUISITION_INVALID"}:
                    raise AppError("ACQUISITION_REJECTED", "获取控制身份无效，请检查本地 API") from exc
                raise
        result = await self.request(acquire, method="getFile")
        if self.mode == "cloud":
            cloud_size_check(result.file_size)
        return result.file_path or ""

    async def file_chunks(self, path: str) -> AsyncGenerator[bytes]:
        started = time.monotonic()
        try:
            async with aclosing(self._file_chunks(path)) as stream:
                async for chunk in stream:
                    yield chunk
        except (asyncio.CancelledError, GeneratorExit) as exc:
            await self.record("downloadFile", exc, started)
            raise
        except Exception as exc:
            await self.record("downloadFile", exc, started)
            raise
        else:
            await self.record("downloadFile", None, started)

    async def _file_chunks(self, path: str) -> AsyncGenerator[bytes]:
        # Only official relative file paths; reject encoded separators, URL syntax and traversal.
        if not re.fullmatch(r"[A-Za-z0-9_-]+(?:/[A-Za-z0-9_.-]+)+", path) or any(
                segment in {".", ".."} for segment in path.split("/")):
            raise AppError("INVALID_API_PATH", "云端 Bot API 返回的文件路径无效")
        session = await self.bot.session.create_session()  # type: ignore[attr-defined]
        url = self.bot.session.api.file_url(self.bot.token, path)
        if self.safety:
            await self.safety.before(self.bot.token)
        try:
            async with session.get(url, timeout=self.timeout, allow_redirects=False) as response:
                if self.safety and response.status in {429, 401}:
                    wait = 60.0
                    if response.status == 429:
                        # Telegram normally supplies JSON parameters.retry_after;
                        # accept the longest explicit wait, without a one-day cap.
                        waits = []
                        try:
                            waits.append(float(response.headers.get("Retry-After", "0")))
                        except (ValueError, TypeError, OverflowError):
                            try:
                                date = parsedate_to_datetime(response.headers.get("Retry-After", ""))
                                if date.tzinfo is None:
                                    date = date.replace(tzinfo=UTC)
                                waits.append(date.timestamp() - self.safety.clock())
                            except (ValueError, TypeError, OverflowError):
                                pass
                        try:
                            try:
                                body = await response.content.readexactly(4097)
                            except asyncio.IncompleteReadError as incomplete:
                                body = incomplete.partial
                            if len(body) <= 4096:
                                payload = json.loads(body)
                                waits.append(float(payload["parameters"]["retry_after"]))
                        except (ValueError, TypeError, KeyError, OverflowError, ClientError, TimeoutError):
                            pass
                        waits = [value for value in waits if not math.isnan(value) and value > 0]
                        if waits:
                            wait = max(waits)
                    await asyncio.to_thread(self.safety.observe, self.bot.token, None,
                                            retry_after=wait if response.status == 429 else None,
                                            unauthorized=response.status == 401)
                    paused = SafetyDeferred("Telegram 已暂停文件请求，请等待或检查 Bot 凭据", sent=True,
                                            reason="cooldown" if response.status == 429 else "auth_paused")
                    paused.diagnostic_http_status = response.status  # type: ignore[attr-defined]
                    raise paused
                if response.status != 200:
                    transient = response.status == 429 or response.status >= 500
                    if self.safety and transient:
                        await asyncio.to_thread(self.safety.observe, self.bot.token, None, transient=True)
                    rejected = AppError("CLOUD_DOWNLOAD_FAILED" if transient else "CLOUD_DOWNLOAD_REJECTED",
                                        "云端文件下载失败，请检查网络、代理或稍后重试")
                    rejected.diagnostic_http_status = response.status  # type: ignore[attr-defined]
                    raise rejected
                cloud_size_check(response.content_length)
                async for chunk in response.content.iter_chunked(64 * 1024):
                    yield chunk
            if self.safety:
                await asyncio.to_thread(self.safety.observe, self.bot.token, None)
        except (ClientError, TimeoutError, ProxyConnectionError, ProxyTimeoutError, ProxyError) as exc:
            if self.safety:
                await asyncio.to_thread(self.safety.observe, self.bot.token, exc)
            raise

    async def send(self, chat_id: int, text: str, callback: tuple[str, str] | None = None) -> int:
        markup = None
        if callback:
            request_id, nonce = callback
            markup = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="✅ 确认下载", callback_data=f"y:{request_id}:{nonce}"),
                InlineKeyboardButton(text="❌ 取消", callback_data=f"n:{request_id}:{nonce}")]])
        result = await self.request(lambda: self.bot.send_message(chat_id, text, reply_markup=markup, request_timeout=30), chat_id, method="sendMessage")
        return result.message_id

    async def answer(self, query_id: str, text: str) -> None:
        await self.request(lambda: self.bot.answer_callback_query(query_id, text=text[:150], show_alert=True, request_timeout=15), method="answerCallbackQuery")

    async def close(self) -> None:
        if self._acquisition_session:
            await self._acquisition_session.close()
        if self._cache_storage_session:
            await self._cache_storage_session.close()
        if self._cleanup_session:
            await self._cleanup_session.close()
        if self._progress_session:
            await self._progress_session.close()
        await self.bot.session.close()

    async def logout(self) -> bool:
        return bool(await self.request(lambda: self.bot.log_out(request_timeout=30), method="logOut"))
