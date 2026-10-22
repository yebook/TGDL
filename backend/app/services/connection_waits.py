"""Bound cooperative connection operations, never download ownership or database writes."""
import asyncio
from collections.abc import AsyncIterator, Awaitable
from contextlib import asynccontextmanager
from typing import Any

from app.core.errors import AppError

IDENTITY_TIMEOUT = 25.0
LOGOUT_TIMEOUT = 35.0
CLOSE_TIMEOUT = 5.0
BARRIER_POLL_TIMEOUT = 1.0
BARRIER_TIMEOUT = 45.0
VERIFY_TIMEOUT = 120.0
GENERATION_TIMEOUT = 150.0


async def network[T](operation: Awaitable[T], seconds: float, code: str = "LOCAL_API_OPERATION_TIMEOUT") -> T:
    # asyncio.timeout waits for cooperative cancellation to finish. It never
    # detaches a still-running operation or applies to a to_thread database write.
    try:
        async with asyncio.timeout(seconds):
            return await operation
    except TimeoutError as exc:
        raise AppError(code, "连接操作超时，请查看连接状态后重试", 422) from exc


async def close_candidate(client: Any) -> None:
    # A cleanup timeout cannot overwrite an already persisted uncertain logout.
    try:
        await network(client.close(), CLOSE_TIMEOUT)
    except Exception:
        pass


class ConsumerBusy(Exception):
    pass


@asynccontextmanager
async def consumers(poll: asyncio.Lock, notify: asyncio.Lock) -> AsyncIterator[None]:
    acquired = []
    try:
        for lock in (poll, notify):
            try:
                async with asyncio.timeout(BARRIER_POLL_TIMEOUT):
                    await lock.acquire()
            except TimeoutError as exc:
                raise ConsumerBusy from exc
            acquired.append(lock)
        yield
    finally:
        for lock in reversed(acquired):
            lock.release()
