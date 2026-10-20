import asyncio
import json
import math
import re
from collections.abc import AsyncGenerator, Awaitable, Callable
from datetime import UTC
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import quote

from aiogram import Bot
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
from aiogram.exceptions import TelegramRetryAfter, TelegramUnauthorizedError
from aiogram.methods import GetFile, GetUpdates
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiohttp import ClientError

from app.core.config import Config
from app.core.errors import AppError
from app.schemas.contracts import UserSettings
from app.services.request_safety import RequestSafetyService, SafetyDeferred

CLOUD_FILE_LIMIT = 20 * 1024 * 1024


def cloud_size_check(size: int | None) -> None:
    if size is not None and size > CLOUD_FILE_LIMIT:
        raise AppError("CLOUD_FILE_TOO_LARGE", "云端 Bot API 仅支持下载不超过 20 MB 的文件，请在设置切换本地 API 后重试")


class TelegramClient:
    def __init__(self, token: str, config: Config, settings: UserSettings | None = None) -> None:
        settings = settings or UserSettings()
        self.safety: RequestSafetyService | None = None
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

    async def request[T](self, operation: Callable[[], Awaitable[T]], chat_id: int | None = None,
                         *, probe: bool = False) -> T:
        if self.safety:
            await self.safety.before(self.bot.token, chat_id, probe=probe)
        try:
            result = await operation()
        except Exception as exc:
            if self.safety:
                await asyncio.to_thread(self.safety.observe, self.bot.token, exc)
                if isinstance(exc, (TelegramRetryAfter, TelegramUnauthorizedError)):
                    raise SafetyDeferred("Telegram 请求已暂停，请等待或检查 Bot 凭据", sent=True) from exc
            raise
        if self.safety:
            await asyncio.to_thread(self.safety.observe, self.bot.token, None, probe=probe)
        return result

    async def identity(self, *, probe: bool = False) -> dict[str, Any]:
        me = await self.request(lambda: self.bot.get_me(request_timeout=20), probe=probe)
        return {"id": me.id, "username": me.username or ""}

    async def updates(self, offset: int) -> list[dict[str, Any]]:
        result = await self.request(lambda: self.bot(GetUpdates(offset=offset, timeout=25, allowed_updates=["message", "callback_query"]),
                                request_timeout=35))
        return [item.model_dump(mode="json", exclude_none=True) for item in result]

    async def get_file(self, file_id: str) -> str:
        result = await self.request(lambda: self.bot(GetFile(file_id=file_id), request_timeout=self.timeout))
        if self.mode == "cloud":
            cloud_size_check(result.file_size)
        return result.file_path or ""

    async def file_chunks(self, path: str) -> AsyncGenerator[bytes]:
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
                    raise SafetyDeferred("Telegram 已暂停文件请求，请等待或检查 Bot 凭据", sent=True)
                if response.status != 200:
                    transient = response.status == 429 or response.status >= 500
                    if self.safety and transient:
                        await asyncio.to_thread(self.safety.observe, self.bot.token, None, transient=True)
                    raise AppError("CLOUD_DOWNLOAD_FAILED" if transient else "CLOUD_DOWNLOAD_REJECTED",
                                   "云端文件下载失败，请检查网络、代理或稍后重试")
                cloud_size_check(response.content_length)
                async for chunk in response.content.iter_chunked(64 * 1024):
                    yield chunk
            if self.safety:
                await asyncio.to_thread(self.safety.observe, self.bot.token, None)
        except (ClientError, TimeoutError) as exc:
            if self.safety:
                await asyncio.to_thread(self.safety.observe, self.bot.token, exc)
            raise

    async def send(self, chat_id: int, text: str, callback: tuple[str, str] | None = None) -> int:
        markup = None
        if callback:
            request_id, nonce = callback
            markup = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="确认下载", callback_data=f"y:{request_id}:{nonce}"),
                InlineKeyboardButton(text="取消", callback_data=f"n:{request_id}:{nonce}")]])
        result = await self.request(lambda: self.bot.send_message(chat_id, text, reply_markup=markup, request_timeout=30), chat_id)
        return result.message_id

    async def answer(self, query_id: str, text: str) -> None:
        await self.request(lambda: self.bot.answer_callback_query(query_id, text=text[:150], show_alert=True, request_timeout=15))

    async def close(self) -> None:
        await self.bot.session.close()

    async def logout(self) -> bool:
        return bool(await self.request(lambda: self.bot.log_out(request_timeout=30)))
