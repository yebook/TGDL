"""Local getFile is a download: real SDK/HTTP regressions without real Telegram."""
import asyncio
import json
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.client.telegram import TelegramAPIServer
from aiogram.exceptions import TelegramNetworkError
from aiohttp import ClientTimeout, web
from fastapi.testclient import TestClient
from sqlalchemy import select
from test_cloud_api import TOKEN, fake_api
from test_local_download_progress import sample
from test_pipeline import confirm_all, message, setup_pipeline

import app.bot.client as client_module
from app.bot.client import LOCAL_GET_FILE_TIMEOUT, TelegramClient
from app.core.config import Config
from app.db.models import FileReservation, Job
from app.schemas.contracts import UserSettings


def file_result(path="/cache/file.mp4"):
    return {"ok": True, "result": {"file_id": "file-1", "file_unique_id": "unique-1",
                                   "file_size": 6, "file_path": path}}


@pytest.mark.parametrize("stage", ["headers", "body"])
async def test_real_sdk_local_getfile_ignores_total_and_read_budgets(config: Config, stage):
    config.get_file_timeout = .03
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []

    async def handler(request):
        calls.append(request.path)
        body = json.dumps(file_result()).encode()
        if stage == "body":
            response = web.StreamResponse(headers={"Content-Type": "application/json"})
            await response.prepare(request)
            await response.write(body[:10])
            entered.set()
            await release.wait()
            await response.write(body[10:])
            return response
        entered.set()
        await release.wait()
        return web.Response(body=body, content_type="application/json")

    telegram = TelegramClient(TOKEN, config, UserSettings(bot_api_mode="local"))
    observed = []
    telegram.safety = SimpleNamespace(before=AsyncMock(), observe=lambda *args, **kwargs: observed.append(args[1]))
    telegram.record = AsyncMock()
    async with fake_api(handler) as base:
        telegram.bot.session.api = TelegramAPIServer.from_base(base, is_local=True)
        # Both the SDK fallback and aiohttp's defaults would expire before the response.
        http = await telegram.bot.session.create_session()
        http._timeout = ClientTimeout(total=.03, sock_read=.03)
        task = asyncio.create_task(telegram.get_file("file-1"))
        try:
            await asyncio.wait_for(entered.wait(), 2)
            await asyncio.sleep(.12)
            assert not task.done()
            release.set()
            assert await asyncio.wait_for(task, 2) == "/cache/file.mp4"
            assert len(calls) == 1
            telegram.safety.before.assert_awaited_once()
            telegram.record.assert_awaited_once()
            assert observed == [None]
        finally:
            release.set()
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await telegram.close()


async def test_local_connection_admission_remains_bounded(config: Config, monkeypatch):
    assert LOCAL_GET_FILE_TIMEOUT.total is None and LOCAL_GET_FILE_TIMEOUT.sock_read is None
    assert LOCAL_GET_FILE_TIMEOUT.connect == LOCAL_GET_FILE_TIMEOUT.sock_connect == 20
    monkeypatch.setattr(client_module, "LOCAL_GET_FILE_TIMEOUT",
                        ClientTimeout(total=None, connect=.03, sock_connect=.03, sock_read=None))
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []

    async def handler(request):
        calls.append(request.path)
        entered.set()
        await release.wait()
        return web.json_response(file_result())

    telegram = TelegramClient(TOKEN, config, UserSettings(bot_api_mode="local"))
    # aiohttp connect covers pool admission as well as establishing a socket. Exhaust a
    # one-connection pool to prove that boundary without depending on external networking.
    telegram.bot.session._connector_init["limit"] = 1
    async with fake_api(handler) as base:
        telegram.bot.session.api = TelegramAPIServer.from_base(base, is_local=True)
        first = asyncio.create_task(telegram.get_file("file-1"))
        try:
            await asyncio.wait_for(entered.wait(), 2)
            with pytest.raises(TelegramNetworkError) as error:
                await asyncio.wait_for(telegram.get_file("different-file"), 1)
            assert isinstance(error.value.__cause__, TimeoutError)
            assert len(calls) == 1 and not first.done()
            release.set()
            assert await asyncio.wait_for(first, 2) == "/cache/file.mp4"
        finally:
            release.set()
            if not first.done():
                first.cancel()
            await asyncio.gather(first, return_exceptions=True)
            await telegram.close()


async def test_other_sdk_methods_keep_their_own_budgets(config: Config):
    telegram = TelegramClient(TOKEN, config, UserSettings(bot_api_mode="local"))
    calls = []

    async def make_request(bot, method, timeout=None):
        calls.append((method.__api_method__, timeout))
        if method.__api_method__ == "getMe":
            return SimpleNamespace(id=7, username="synthetic_bot")
        return []

    telegram.bot.session.make_request = make_request
    try:
        assert await telegram.identity() == {"id": 7, "username": "synthetic_bot"}
        assert await telegram.updates(0) == []
        assert calls == [("getMe", 20), ("getUpdates", 35)]
    finally:
        await telegram.close()


async def test_cloud_getfile_retains_total_budget(config: Config):
    config.get_file_timeout = .03
    release = asyncio.Event()
    calls = []

    async def handler(request):
        calls.append(request.path)
        await release.wait()
        return web.json_response(file_result("videos/file.mp4"))

    telegram = TelegramClient(TOKEN, config, UserSettings(bot_api_mode="cloud"))
    async with fake_api(handler) as base:
        telegram.bot.session.api = TelegramAPIServer.from_base(base)
        try:
            with pytest.raises(TelegramNetworkError):
                await asyncio.wait_for(telegram.get_file("file-1"), 1)
            assert len(calls) == 1
        finally:
            release.set()
            await telegram.close()


@pytest.mark.parametrize("telemetry", ["growing", "unchanged", "unavailable", "wrong_generation", "disconnect"])
async def test_worker_real_progress_and_unavailable_reads_never_end_download(client: TestClient, telemetry):
    state, ingest = setup_pipeline(client)
    ingest.message(7, message(), "in")
    request_id = confirm_all(state)
    worker = state.runtime.worker
    job_id = worker.claim(7)
    registry = worker.download_progress
    registry.generation = lambda: "child"
    source = worker.config.bot_api_root / "synthetic.mp4"
    source.write_bytes(b"abcdef")
    worker.config.get_file_timeout = .03
    entered, release, observed = asyncio.Event(), asyncio.Event(), asyncio.Event()
    calls, samples = [], []

    async def handler(request):
        method = request.path.rsplit("/", 1)[-1]
        calls.append(method)
        if method == "getFile":
            entered.set()
            await release.wait()
            return web.json_response(file_result(str(source)))
        assert method == "tgdGetFileProgress"
        aliases = json.loads((await request.post())["file_ids"])
        assert aliases == ["file-1"]
        samples.append(len(samples) + 1)
        if telemetry in {"unavailable", "wrong_generation", "disconnect"} or len(samples) >= 2:
            observed.set()
        if telemetry == "unavailable":
            return web.Response(status=404)
        if telemetry == "disconnect":
            request.transport.close()
            return web.Response()
        count = min(6, len(samples) * 2) if telemetry == "growing" else 6
        return web.json_response({"ok": True, "result": {
            "generation": "other-child" if telemetry == "wrong_generation" else "child",
            "files": [{"file_id": "file-1", "available": True, **sample(count, 6, completed=count == 6)}]}})

    telegram = TelegramClient(TOKEN, worker.config, UserSettings(bot_api_mode="local"))
    async with fake_api(handler) as base:
        telegram.bot.session.api = TelegramAPIServer.from_base(base, is_local=True)
        task = asyncio.create_task(worker.run(telegram, job_id))
        try:
            await asyncio.wait_for(entered.wait(), 2)
            await asyncio.wait_for(observed.wait(), 3)
            await asyncio.sleep(.12)  # Much longer than the former configured timeout.
            detail = state.tasks.detail(request_id)
            assert not task.done() and detail["status"] == "fetching"
            progress = detail["items"][0]["download_progress"]
            if telemetry == "growing":
                assert progress["downloaded_bytes"] == 4 and progress["speed_bps"] > 0
            elif telemetry == "unchanged":
                assert progress["downloaded_bytes"] == 6 and progress["completed"] is True
                assert progress["speed_bps"] == 0
            else:
                assert progress is None
            assert detail["items"][0]["completed_bytes"] == 0
            release.set()
            await asyncio.wait_for(task, 3)
            assert state.tasks.detail(request_id)["status"] == "succeeded"
            assert calls.count("getFile") == 1
            assert registry._entries == {}
        finally:
            release.set()
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await telegram.close()


@pytest.mark.parametrize("failure", ["network", "shutdown", "user_cancel", "session_close"])
async def test_real_sdk_worker_errors_and_shutdown_keep_uncertain_slot(client: TestClient, failure):
    state, ingest = setup_pipeline(client)
    ingest.message(7, message(), "in")
    request_id = confirm_all(state)
    worker = state.runtime.worker
    job_id = worker.claim(7)
    registry = worker.download_progress
    registry.generation = lambda: "child"
    worker.config.get_file_timeout = .03
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []

    async def handler(request):
        if request.path.endswith("tgdGetFileProgress"):
            return web.Response(status=404)
        calls.append(request.path)
        entered.set()
        await release.wait()
        if request.transport is not None:
            request.transport.close()
        return web.Response()

    telegram = TelegramClient(TOKEN, worker.config, UserSettings(bot_api_mode="local"))
    telegram.record = AsyncMock()
    async with fake_api(handler) as base:
        telegram.bot.session.api = TelegramAPIServer.from_base(base, is_local=True)
        task = asyncio.create_task(worker.run(telegram, job_id))
        try:
            await asyncio.wait_for(entered.wait(), 2)
            connector = (await telegram.bot.session.create_session()).connector
            if failure == "user_cancel":
                state.tasks.cancel(request_id)
                await asyncio.sleep(.12)
                assert not task.done()  # User cancel does not prove upstream stopped.
                assert state.tasks.detail(request_id)["items"][0]["download_progress"] is None
            if failure == "network":
                release.set()
                await asyncio.wait_for(task, 3)
            elif failure == "session_close":
                await telegram.close()
                await asyncio.wait_for(task, 3)
            else:
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            assert len(calls) == 1
            telegram.record.assert_awaited_once()
            assert not connector._acquired  # SDK cancellation/error has released transport.
            with state.db.sessions() as session:
                row = session.get(Job, job_id)
                assert row.state == "recovery_blocked"
                assert row.error_code == ("NETWORK_ERROR" if failure in {"network", "session_close"} else "ACQUISITION_UNCERTAIN")
                assert session.scalar(select(FileReservation).where(FileReservation.job_id == job_id)) is not None
            assert registry._entries == {}
            assert worker.claim(7) is None
        finally:
            release.set()
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await telegram.close()


async def test_cancelled_registration_joins_and_clears_late_owner(client: TestClient, monkeypatch):
    state, ingest = setup_pipeline(client)
    ingest.message(7, message(), "in")
    confirm_all(state)
    worker = state.runtime.worker
    job_id = worker.claim(7)
    registry = worker.download_progress
    registry.generation = lambda: "child"
    entered, release = threading.Event(), threading.Event()
    begin = registry.begin

    def delayed_begin(*args, **kwargs):
        entered.set()
        assert release.wait(3)
        return begin(*args, **kwargs)

    monkeypatch.setattr(registry, "begin", delayed_begin)
    task = asyncio.create_task(worker.begin_progress(job_id))
    try:
        assert await asyncio.to_thread(entered.wait, 3)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()  # Registration owns a state-changing thread until it completes.
    finally:
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 3)
    assert registry._entries == {}
