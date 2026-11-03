import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from aiohttp import web
from test_cloud_api import TOKEN, fake_api

from app.bot.client import CacheCleanupError, TelegramClient
from app.core.errors import AppError
from app.schemas.contracts import UserSettings


def snapshot():
    return {"generation": "child-one", "completeness": "partial", "path": "/cache/private/temp/1",
            "size": 65536, "allocated_bytes": 65536, "identity": "a" * 64,
            "resumable": True, "reason": "resume_available", "settled": True}


async def test_private_cache_metadata_cleanup_and_resume_prepare(config):
    received = []
    result = snapshot()

    async def handler(request):
        method = request.path.rsplit("/", 1)[-1]
        received.append((method, dict(await request.post())))
        if method == "tgdDeletePartialFileCache":
            value = {"generation": "child-one", "status": "deleted"}
        elif method == "tgdPrepareFileAcquisition":
            value = {"generation": "child-one", "acquisition_id": "b" * 64, "state": "prepared", "settled": False}
        else:
            value = result
        return web.json_response({"ok": True, "result": value})

    async with fake_api(handler) as base:
        config.bot_api_base = base
        api = TelegramClient(TOKEN, config, UserSettings(bot_api_mode="local"))
        api.safety = AsyncMock()
        try:
            assert await api.inspect_file_cache("alias", "child-one", acquisition_id="b" * 64) == result
            proof = dict(expected_path=result["path"], expected_size=result["size"], expected_identity=result["identity"])
            assert await api.inspect_file_cache("alias", "child-one", **proof) == result
            assert await api.delete_partial_file_cache("alias", result["path"], result["size"], "child-one",
                                                       expected_identity=result["identity"]) == "deleted"
            await api.file_acquisition("child-one", file_id="alias", expected_cache_identity="a" * 64)
            assert received[0] == ("tgdGetFileCache", {"generation": "child-one", "file_id": "alias",
                                                     "acquisition_id": "b" * 64})
            assert received[-1][1]["expected_cache_identity"] == "a" * 64
            api.safety.before.assert_not_called()
            api.safety.observe.assert_not_called()
        finally:
            await api.close()


@pytest.mark.parametrize("change", [
    {"generation": "wrong"}, {"size": True}, {"allocated_bytes": -1}, {"identity": "x"},
    {"path": "relative/path"}, {"settled": False}, {"completeness": "complete"},
    {"reason": "upstream secret/path"}, {"completeness": "absent"}, {"extra": True},
])
async def test_rejects_inconsistent_native_cache_metadata(config, change):
    async def handler(request):
        return web.json_response({"ok": True, "result": snapshot() | change})

    async with fake_api(handler) as base:
        config.bot_api_base = base
        api = TelegramClient(TOKEN, config, UserSettings(bot_api_mode="local"))
        try:
            with pytest.raises(AppError) as error:
                await api.inspect_file_cache("alias", "child-one")
            assert error.value.code == "CACHE_INSPECTION_UNAVAILABLE"
            assert TOKEN not in str(error.value) and "/cache/" not in str(error.value)
        finally:
            await api.close()


@pytest.mark.parametrize(("status", "description", "code"), [
    (404, "Not Found: progress unavailable", "CACHE_INSPECTION_UNSUPPORTED"),
    (400, "Bad Request: method not found", "CACHE_INSPECTION_UNSUPPORTED"),
    (409, "Conflict: cache cleanup busy", "CACHE_NATIVE_BUSY"),
    (409, "Conflict: cache generation changed", "CACHE_INSPECTION_UNAVAILABLE"),
])
async def test_cache_metadata_failure_is_bounded_and_fixed(config, status, description, code):
    async def handler(request):
        return web.json_response({"ok": False, "description": description, "error_code": status}, status=status)

    async with fake_api(handler) as base:
        config.bot_api_base = base
        api = TelegramClient(TOKEN, config, UserSettings(bot_api_mode="local"))
        try:
            with pytest.raises(AppError) as error:
                await api.inspect_file_cache("alias", "child-one")
            assert error.value.code == code
            assert "?" not in str(error.value)
        finally:
            await api.close()


async def test_partial_delete_lost_reply_stays_unknown(config):
    entered = asyncio.Event()
    release = asyncio.Event()

    async def handler(request):
        entered.set()
        await release.wait()
        return web.json_response({"ok": True, "result": {"generation": "child-one", "status": "deleted"}})

    async with fake_api(handler) as base:
        config.bot_api_base = base
        api = TelegramClient(TOKEN, config, UserSettings(bot_api_mode="local"))
        try:
            with pytest.raises(CacheCleanupError) as error:
                await api.delete_partial_file_cache("alias", "/cache/temp/one", 1, "child-one",
                                                    expected_identity="a" * 64)
            assert entered.is_set() and error.value.reason == "cache_timeout"
        finally:
            release.set()
            await api.close()


@pytest.mark.parametrize(("marker", "code"), [("TGD_CACHE_NO_SPACE", "NO_SPACE"),
                                             ("TGD_CACHE_RESUME_UNAVAILABLE", "CACHE_RESUME_UNAVAILABLE")])
async def test_getfile_fixed_native_storage_failures(config, marker, code):
    async def handler(request):
        return web.json_response({"ok": False, "error_code": 400, "description": "Bad Request: " + marker}, status=400)

    async with fake_api(handler) as base:
        config.bot_api_base = base
        api = TelegramClient(TOKEN, config, UserSettings(bot_api_mode="local"))
        safety = SimpleNamespace(before=AsyncMock(), observe=Mock(), diagnostics=None)
        api.safety = safety
        try:
            with pytest.raises(AppError) as error:
                await api.get_file("alias", generation="child-one", acquisition_id="b" * 64)
            assert error.value.code == code
            assert "缓存" in str(error.value) and "?" not in str(error.value)
            safety.before.assert_awaited_once()
            safety.observe.assert_not_called()
        finally:
            await api.close()
