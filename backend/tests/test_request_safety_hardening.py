import asyncio
from unittest.mock import Mock

import pytest
from aiogram.client.telegram import TelegramAPIServer
from aiohttp import ClientError, web

from app.bot.client import TelegramClient
from app.core.errors import AppError
from app.core.security import digest
from app.db.models import RequestSafety
from app.schemas.contracts import UserSettings
from app.services.request_safety import RequestSafetyService, SafetyDeferred
from tests.test_cloud_api import TOKEN, fake_api


@pytest.mark.parametrize("values", [
    {"cooldown": "tomorrow"}, {"backoff": float("inf")}, {"cooldown": float("nan")},
    {"next_start": -1}, {"chats": []}, {"chats": {"42": float("inf")}},
    {"auth_paused": "false"}, {"failures": None},
])
async def test_corrupt_state_is_observable_and_blocks_transport(client, values):
    state = client.app.state
    service = RequestSafetyService(state.db)
    with state.db.transaction() as session:
        session.add(RequestSafety(credential=digest(TOKEN), values=values))
    assert service.status(TOKEN)["state"] == "backoff"
    assert not service.status(TOKEN)["can_retry"]
    assert service.blocked(values, service.clock())
    with pytest.raises(SafetyDeferred):
        await service.before(TOKEN)


def test_failed_manual_probe_displays_its_next_available_time(client):
    service = RequestSafetyService(client.app.state.db, clock=lambda: 100)
    service.observe(TOKEN, None, unauthorized=True)
    service.reserve(TOKEN, probe=True)
    service.observe(TOKEN, None, unauthorized=True)
    status = service.status(TOKEN)
    assert status["state"] == "auth_paused"
    assert status["retry_at"] == "1970-01-01T00:02:40+00:00"
    assert not status["can_retry"]


async def test_thread_delay_cannot_turn_unreserved_start_into_permission(client, monkeypatch):
    now = [100.0]
    service = RequestSafetyService(client.app.state.db, clock=lambda: now[0])
    real_reserve = service._reserve
    service.reserve(TOKEN)

    def stalled_reserve(*args, **kwargs):
        result = real_reserve(*args, **kwargs)
        if not result[1]:
            now[0] = 101.1  # A stall crosses the returned future deadline.
        return result

    reserve = Mock(side_effect=stalled_reserve)
    monkeypatch.setattr(service, "_reserve", reserve)
    await service.before(TOKEN)
    assert reserve.call_count == 2
    # The second call must really persist the start before allowing I/O.
    assert service.reserve(TOKEN) == 102.1


async def test_stream_429_understands_http_date_retry_header(client, config):
    async def handler(request):
        return web.Response(status=429, headers={"Retry-After": "Thu, 01 Jan 1970 00:05:00 GMT"})

    service = RequestSafetyService(client.app.state.db, clock=lambda: 100)
    telegram = TelegramClient(TOKEN, config, UserSettings(bot_api_mode="cloud"))
    telegram.safety = service
    async with fake_api(handler) as base:
        telegram.bot.session.api = TelegramAPIServer.from_base(base)
        try:
            with pytest.raises(SafetyDeferred):
                async for _ in telegram.file_chunks("videos/file.mp4"):
                    pytest.fail("Error body yielded")
            assert service.status(TOKEN)["retry_at"] == "1970-01-01T00:05:01+00:00"
        finally:
            await telegram.close()


async def test_stream_429_preserves_longest_header_and_fragmented_json_wait(client, config):
    async def handler(request):
        response = web.StreamResponse(status=429, headers={"Retry-After": "90000"})
        await response.prepare(request)
        await response.write(b'{"parameters":')
        await asyncio.sleep(0.01)
        await response.write(b'{"retry_after":200000}}')
        await response.write_eof()
        return response

    service = RequestSafetyService(client.app.state.db, clock=lambda: 100)
    telegram = TelegramClient(TOKEN, config, UserSettings(bot_api_mode="cloud"))
    telegram.safety = service
    async with fake_api(handler) as base:
        telegram.bot.session.api = TelegramAPIServer.from_base(base)
        try:
            with pytest.raises(SafetyDeferred):
                async for _ in telegram.file_chunks("videos/file.mp4"):
                    pytest.fail("Error body yielded")
            assert service.status(TOKEN)["retry_at"] == "1970-01-03T07:35:01+00:00"
        finally:
            await telegram.close()


@pytest.mark.parametrize("failure", ["server", "disconnect"])
async def test_stream_errors_establish_shared_backoff(client, config, failure):
    async def handler(request):
        if failure == "server":
            return web.Response(status=503)
        response = web.StreamResponse(headers={"Content-Length": "10"})
        await response.prepare(request)
        await response.write(b"x")
        request.transport.close()
        return response

    telegram = TelegramClient(TOKEN, config, UserSettings(bot_api_mode="cloud"))
    telegram.safety = RequestSafetyService(client.app.state.db)
    async with fake_api(handler) as base:
        telegram.bot.session.api = TelegramAPIServer.from_base(base)
        try:
            with pytest.raises((AppError, ClientError)):
                async for _ in telegram.file_chunks("videos/file.mp4"):
                    pass
            assert telegram.safety.status(TOKEN)["state"] == "backoff"
            with pytest.raises(AppError):
                await telegram.safety.before(TOKEN)
        finally:
            await telegram.close()
