import asyncio
import hashlib
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest
from aiogram.client.telegram import TelegramAPIServer
from aiohttp import ClientError, web
from aiohttp_socks import ProxyConnectionError
from fastapi.testclient import TestClient
from sqlalchemy import select
from test_pipeline import confirm_all, message, setup_pipeline

from app.bot.client import CLOUD_FILE_LIMIT, TelegramClient
from app.core.config import Config
from app.core.errors import AppError
from app.db.models import Job, PublishJournal, Settings, StoredFile
from app.schemas.contracts import ProxySettings, SettingsPatch, UserSettings
from app.services.settings import SettingsService
from app.workers.download import DownloadWorker

TOKEN = "123456:synthetic-never-real-token"


@asynccontextmanager
async def fake_api(handler):
    app = web.Application()
    app.router.add_route("*", "/{path:.*}", handler)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    base = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"
    try:
        yield base
    finally:
        await runner.cleanup()


def cloud_pipeline(client):
    state, ingest = setup_pipeline(client)
    patch = SettingsPatch(revision=2, bot_api_mode="cloud")
    state.settings.save(patch, state.settings.candidate(patch))
    return state, ingest


async def test_cloud_http_download_journal_hash_and_publish(client: TestClient, config: Config):
    state, ingest = cloud_pipeline(client)
    ingest.message(7, message(), "in")
    request_id = confirm_all(state)
    calls = []

    async def handler(request):
        calls.append(request.path)
        if request.method == "POST":
            return web.json_response({"ok": True, "result": {"file_id": "file-1", "file_unique_id": "unique-1",
                                                           "file_size": 6, "file_path": "videos/file.mp4"}})
        return web.Response(body=b"abcdef")

    telegram = TelegramClient(TOKEN, config, UserSettings(bot_api_mode="cloud"))
    assert calls == []  # Confirmation and queue creation perform no acquisition.
    async with fake_api(handler) as base:
        telegram.bot.session.api = TelegramAPIServer.from_base(base)
        job_id = state.runtime.worker.claim(7, "cloud")
        try:
            await state.runtime.worker.run(telegram, job_id)
        finally:
            await telegram.close()
    detail = state.tasks.detail(request_id)
    assert detail["status"] == "succeeded"
    assert len(calls) == 2
    with state.db.sessions() as session:
        stored = session.scalar(select(StoredFile))
        assert stored.sha256 == hashlib.sha256(b"abcdef").hexdigest()
        assert stored.size_bytes == 6
        assert (config.download_root / stored.relative_path).read_bytes() == b"abcdef"
        assert session.get(PublishJournal, job_id) is None
        assert session.get(Job, job_id).acquisition_mode == "cloud"
    assert not list(config.download_root.rglob(".part.*"))


async def test_known_oversize_never_calls_get_file(client: TestClient):
    state, ingest = cloud_pipeline(client)
    ingest.message(7, message(size=CLOUD_FILE_LIMIT + 1), "in")
    request_id = confirm_all(state)
    telegram = AsyncMock()
    telegram.mode = "cloud"
    await state.runtime.worker.run(telegram, state.runtime.worker.claim())
    telegram.get_file.assert_not_awaited()
    detail = state.tasks.detail(request_id)
    assert detail["status"] == "failed"
    assert detail["items"][0]["error_code"] == "CLOUD_FILE_TOO_LARGE"
    assert "20 MB" in detail["items"][0]["error_message"]


async def test_unknown_size_cumulative_cap_cleans_part(client: TestClient, config: Config):
    state, ingest = cloud_pipeline(client)
    media = message()
    media["video"].pop("file_size")
    ingest.message(7, media, "in")
    request_id = confirm_all(state)

    class ChunkClient:
        mode = "cloud"
        get_file = AsyncMock(return_value="videos/file.mp4")

        async def file_chunks(self, path):
            for _ in range(CLOUD_FILE_LIMIT // 65536 + 1):
                yield b"x" * 65536

    job_id = state.runtime.worker.claim()
    await state.runtime.worker.run(ChunkClient(), job_id)
    assert state.tasks.detail(request_id)["items"][0]["error_code"] == "CLOUD_FILE_TOO_LARGE"
    assert not list(config.download_root.rglob(".part.*"))
    with state.db.sessions() as session:
        assert session.get(PublishJournal, job_id) is None
        assert session.scalar(select(StoredFile)) is None


@pytest.mark.parametrize("stage", ["fetching", "saving"])
async def test_cloud_cancel_joins_transport_without_uncertain_slot(client: TestClient, config: Config, stage):
    state, ingest = cloud_pipeline(client)
    ingest.message(7, message(), "in")
    request_id = confirm_all(state)
    entered, closed = asyncio.Event(), asyncio.Event()

    class StalledClient:
        mode = "cloud"

        async def get_file(self, file_id):
            if stage == "fetching":
                entered.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    closed.set()
            return "videos/file.mp4"

        async def file_chunks(self, path):
            yield b"abc"
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                closed.set()

    job_id = state.runtime.worker.claim()
    task = asyncio.create_task(state.runtime.worker.run(StalledClient(), job_id))
    await asyncio.wait_for(entered.wait(), 5)
    state.tasks.cancel(request_id)
    await asyncio.wait_for(task, 5)
    assert closed.is_set()
    assert state.tasks.detail(request_id)["status"] == "cancelled"
    assert state.runtime.counts()[0] == 0
    assert not list(config.download_root.rglob(".part.*"))


@pytest.mark.parametrize("path", ["../secret", "/videos/file", "https://other/file", "videos/%2e%2e/file",
                                    "videos/../file", "videos/file?x=1", "videos\\file"])
async def test_cloud_file_path_rejects_before_request(config: Config, path):
    telegram = TelegramClient(TOKEN, config, UserSettings(bot_api_mode="cloud"))
    try:
        with pytest.raises(AppError, match="文件路径无效"):
            async for _ in telegram.file_chunks(path):
                pytest.fail("unsafe stream")
    finally:
        await telegram.close()


async def test_cloud_redirect_is_not_followed_or_leaked(config: Config):
    paths = []

    async def handler(request):
        paths.append(request.path)
        raise web.HTTPFound("/credential-trap")

    telegram = TelegramClient(TOKEN, config, UserSettings(bot_api_mode="cloud"))
    async with fake_api(handler) as base:
        telegram.bot.session.api = TelegramAPIServer.from_base(base)
        try:
            with pytest.raises(AppError) as error:
                async for _ in telegram.file_chunks("videos/file.mp4"):
                    pytest.fail("redirect body")
            assert error.value.code == "CLOUD_DOWNLOAD_REJECTED"
            assert TOKEN not in str(error.value)
        finally:
            await telegram.close()
    assert len(paths) == 1
    assert "credential-trap" not in paths[0]


@pytest.mark.parametrize("scheme", ["http", "socks5"])
async def test_cloud_proxy_connectors_fail_closed(config: Config, scheme):
    proxy = ProxySettings(enabled=True, scheme=scheme, host="127.0.0.1", port=1,
                          username="synthetic", password="special:@/password")
    telegram = TelegramClient(TOKEN, config, UserSettings(bot_api_mode="cloud", proxy=proxy))
    session = telegram.bot.session
    assert session._connector_type.__name__ == "ProxyConnector"
    assert session._connector_init["rdns"] is True
    assert session._connector_init["password"] == proxy.password
    contacted = []

    async def handler(request):
        contacted.append(request.path)
        return web.Response(body=b"abcdef")

    async with fake_api(handler) as base:
        session.api = TelegramAPIServer.from_base(base)
        try:
            with pytest.raises((ClientError, OSError, ProxyConnectionError)):
                async for _ in telegram.file_chunks("videos/file.mp4"):
                    pytest.fail("proxy bypassed")
            assert not contacted
        finally:
            await telegram.close()


def test_new_cloud_legacy_local_and_safe_mode_guard(client: TestClient):
    state = client.app.state
    assert state.settings.read()[1].bot_api_mode == "cloud"
    with state.db.transaction() as session:
        session.get(Settings, 1).values = {}
    SettingsService(state.db, state.settings.config, state.settings.secrets)
    assert state.settings.read()[1].bot_api_mode == "local"
    state, ingest = setup_pipeline(client)
    ingest.message(7, message(), "in")
    confirm_all(state)
    job_id = state.runtime.worker.claim(7, "local")
    patch = SettingsPatch(revision=2, bot_api_mode="cloud")
    with pytest.raises(AppError) as error:
        state.settings.save(patch, state.settings.candidate(patch))
    assert error.value.code == "API_MODE_BUSY"
    state.runtime.worker.recover(False)
    with state.db.sessions() as session:
        assert session.get(Job, job_id).state == "recovery_blocked"
    with pytest.raises(AppError):
        state.settings.save(patch, state.settings.candidate(patch))


def test_cloud_restart_uses_attempt_snapshot_not_current_mode(client: TestClient):
    state, ingest = cloud_pipeline(client)
    ingest.message(7, message(), "in")
    confirm_all(state)
    job_id = state.runtime.worker.claim(7, "cloud")
    # Simulate a changed deployment/env selection after a crash.
    with state.db.transaction() as session:
        session.get(Settings, 1).values = {"bot_api_mode": "local"}
    state.runtime.worker.recover(False)
    with state.db.sessions() as session:
        assert session.get(Job, job_id).state == "queued"
    # Mode snapshot is checked in the same transaction that acquires the slot.
    assert state.runtime.worker.claim(7, "cloud") is None


async def test_candidate_validation_mode_proxy_and_signature(authenticated: TestClient, monkeypatch):
    state = authenticated.app.state
    seen = []

    async def identity(self):
        seen.append((self.mode, self.bot.session.proxy))
        return {"id": 7, "username": "synthetic"}

    monkeypatch.setattr(TelegramClient, "identity", identity)
    before = state.settings.public()
    checked = authenticated.post("/api/v1/settings/validate", json={
        "bot_api_mode": "cloud", "bot_token": TOKEN, "allowed_user_ids": [42],
        "proxy": {"enabled": True, "scheme": "http", "host": "proxy.lan", "port": 8080}})
    assert checked.status_code == 200
    assert seen == [("cloud", "http://proxy.lan:8080")]
    assert state.settings.public() == before
    patch = SettingsPatch(revision=1, bot_token=TOKEN, allowed_user_ids=[42], bot_api_mode="cloud")
    state.settings.save(patch, state.settings.candidate(patch))
    await state.runtime.ensure_client()
    original = state.runtime.client
    patch = SettingsPatch(revision=2, proxy=ProxySettings(enabled=True, host="proxy.lan"))
    candidate = state.settings.candidate(patch)
    state.settings.save(patch, candidate)
    assert state.settings.public()["proxy_status"]["state"] == "pending"
    await state.runtime.ensure_client()
    assert state.runtime.client is not original
    assert state.settings.public()["proxy_status"]["state"] == "active"
    await state.runtime.client.close()


@pytest.mark.parametrize("mode", ["cloud", "local"])
def test_cloud_does_not_budget_unused_local_cache(client: TestClient, config: Config, monkeypatch, mode):
    import shutil
    from collections import namedtuple

    state, ingest = cloud_pipeline(client) if mode == "cloud" else setup_pipeline(client)
    ingest.message(7, message(), "in")
    request_id = confirm_all(state)
    actual_usage = shutil.disk_usage
    Usage = namedtuple("Usage", "total used free")

    def usage(path):
        return Usage(100, 100, 0) if path == config.bot_api_root else actual_usage(path)

    monkeypatch.setattr(shutil, "disk_usage", usage)
    claimed = state.runtime.worker.claim(7, mode)
    assert bool(claimed) == (mode == "cloud")
    if mode == "local":
        assert state.tasks.detail(request_id)["items"][0]["error_code"] == "NO_SPACE"


def test_migration_existing_job_ownership_and_schema(client: TestClient):
    from pathlib import Path

    from alembic import command
    from alembic.autogenerate import compare_metadata
    from alembic.config import Config as AlembicConfig
    from alembic.migration import MigrationContext

    from app.db.models import Base

    state, ingest = setup_pipeline(client)
    ingest.message(7, message(), "in")
    request_id = confirm_all(state)
    job_id = state.runtime.worker.claim(7, "local")
    migration = AlembicConfig(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    with state.db.engine.begin() as connection:
        migration.attributes["connection"] = connection
        command.downgrade(migration, "0001")
        command.upgrade(migration, "head")
        assert compare_metadata(MigrationContext.configure(connection), Base.metadata) == []
    with state.db.sessions() as session:
        job = session.get(Job, job_id)
        assert job.request_id == request_id
        assert job.acquisition_mode == "local"
        assert job.state == "fetching"
    state.runtime.worker.recover(False)
    assert state.tasks.detail(request_id)["status"] == "recovery_blocked"


@pytest.mark.parametrize("status,retry", [(429, True), (503, True), (404, False)])
async def test_cloud_http_failures_are_sanitized_and_retry_transient(config: Config, status, retry):
    async def handler(request):
        return web.Response(status=status, text=TOKEN)

    telegram = TelegramClient(TOKEN, config, UserSettings(bot_api_mode="cloud"))
    async with fake_api(handler) as base:
        telegram.bot.session.api = TelegramAPIServer.from_base(base)
        try:
            with pytest.raises(AppError) as error:
                async for _ in telegram.file_chunks("videos/file.mp4"):
                    pytest.fail("error body published")
            result = DownloadWorker.classify(error.value)
            assert result[2] is retry
            assert TOKEN not in str(error.value)
        finally:
            await telegram.close()


async def test_cloud_shutdown_cancels_fetch_owner_and_retries(client: TestClient):
    state, ingest = cloud_pipeline(client)
    ingest.message(7, message(), "in")
    request_id = confirm_all(state)
    entered, closed = asyncio.Event(), asyncio.Event()

    class StalledClient:
        mode = "cloud"

        async def get_file(self, file_id):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                closed.set()

    task = asyncio.create_task(state.runtime.worker.run(StalledClient(), state.runtime.worker.claim()))
    await asyncio.wait_for(entered.wait(), 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert closed.is_set()
    assert state.tasks.detail(request_id)["status"] == "retry_wait"


@pytest.mark.parametrize("boundary", ["metadata", "content_length"])
async def test_cloud_unknown_message_size_checks_api_and_http_limits(client: TestClient, config: Config, boundary):
    state, ingest = cloud_pipeline(client)
    media = message()
    media["video"].pop("file_size")
    ingest.message(7, media, "in")
    request_id = confirm_all(state)
    calls = []

    async def handler(request):
        calls.append(request.path)
        if request.method == "POST":
            result = {"file_id": "file-1", "file_unique_id": "unique-1", "file_path": "videos/file.mp4"}
            if boundary == "metadata":
                result["file_size"] = CLOUD_FILE_LIMIT + 1
            return web.json_response({"ok": True, "result": result})
        response = web.StreamResponse(headers={"Content-Length": str(CLOUD_FILE_LIMIT + 1)})
        await response.prepare(request)
        await response.write_eof()
        return response

    telegram = TelegramClient(TOKEN, config, UserSettings(bot_api_mode="cloud"))
    async with fake_api(handler) as base:
        telegram.bot.session.api = TelegramAPIServer.from_base(base)
        try:
            await state.runtime.worker.run(telegram, state.runtime.worker.claim())
        finally:
            await telegram.close()
    detail = state.tasks.detail(request_id)
    assert detail["status"] == "failed"
    assert detail["items"][0]["error_code"] == "CLOUD_FILE_TOO_LARGE"
    assert len(calls) == (1 if boundary == "metadata" else 2)
    assert not list(config.download_root.rglob(".part.*"))


async def test_cloud_stream_closed_before_oversize_owner_finishes(client: TestClient):
    state, ingest = cloud_pipeline(client)
    media = message()
    media["video"].pop("file_size")
    ingest.message(7, media, "in")
    confirm_all(state)
    closed = asyncio.Event()

    class OversizeClient:
        mode = "cloud"
        get_file = AsyncMock(return_value="videos/file.mp4")

        async def file_chunks(self, path):
            try:
                for _ in range(CLOUD_FILE_LIMIT // 65536 + 1):
                    yield b"x" * 65536
            finally:
                await asyncio.sleep(0.05)
                closed.set()

    await state.runtime.worker.run(OversizeClient(), state.runtime.worker.claim())
    assert closed.is_set()
    assert state.runtime.counts()[0] == 0
