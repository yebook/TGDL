import asyncio
import os
import stat
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.client.telegram import TelegramAPIServer
from aiohttp import web
from sqlalchemy import func, select
from test_cloud_api import TOKEN, cloud_pipeline, fake_api
from test_pipeline import confirm_all, message, setup_pipeline

from app.bot.client import TelegramClient
from app.core.config import Config
from app.core.errors import AppError
from app.core.security import Secrets
from app.db.models import CacheCleanup, Job, MediaRequest, PublishJournal
from app.schemas.contracts import UserSettings
from app.services.cache_cleanup import CacheCleanupService
from app.storage import files
from app.storage.files import contained, copy_stream, file_hash, publish_no_replace, regular_file_hash
from app.workers.download import DownloadWorker


async def published(client):
    state, ingest = setup_pipeline(client)
    ingest.message(7, message(), "in")
    request_id = confirm_all(state)
    worker = state.runtime.worker
    job_id = worker.claim(7, "local")
    source = state.runtime.config.bot_api_root / "synthetic.mp4"
    source.write_bytes(b"abcdef")
    await worker.run(SimpleNamespace(mode="local", get_file=AsyncMock(return_value=str(source))), job_id)
    with state.db.sessions() as session:
        job = session.get(Job, job_id)
        assert job.state == "succeeded"
        assert state.settings.secrets.decrypt(session.get(CacheCleanup, job_id).source_path_encrypted) == str(source)
        assert file_hash(state.runtime.config.download_root / job.relative_path) == (6, session.get(CacheCleanup, job_id).sha256)
    return state, ingest, request_id, job_id, source


def ready(runtime, monkeypatch, delete=None):
    client = SimpleNamespace(mode="local", delete_file_cache=delete or AsyncMock(return_value="deleted"))
    runtime.client, runtime.bot_id = client, 7
    monkeypatch.setattr(runtime, "progress_generation", lambda: "generation-one")
    return client


def test_cleanup_hash_checks_opened_descriptor_and_never_reads_fifo(tmp_path, monkeypatch):
    path = tmp_path / "final.mp4"
    path.write_bytes(b"abcdef")
    opened = []
    original_open = os.open

    def capture_open(value, flags):
        opened.append(flags)
        return original_open(value, flags)

    monkeypatch.setattr(files.os, "open", capture_open)
    monkeypatch.setattr(files.os, "fstat", lambda _: SimpleNamespace(st_mode=stat.S_IFIFO))
    monkeypatch.setattr(files, "hash_stream", lambda _: pytest.fail("Nonregular descriptors must never be read"))
    with pytest.raises(AppError, match="普通文件"):
        regular_file_hash(path)
    assert len(opened) == 1
    for option in ("O_NOFOLLOW", "O_NONBLOCK"):
        flag = getattr(os, option, 0)
        assert opened[0] & flag == flag
    fake_path = SimpleNamespace(stat=lambda **_: SimpleNamespace(st_mode=stat.S_IFIFO))
    with pytest.raises(ValueError, match="not regular"):
        CacheCleanupService.fingerprint(fake_path)


def test_cleanup_regular_hash_preserves_existing_hash_result(tmp_path):
    path = tmp_path / "final.mp4"
    path.write_bytes(b"abcdef")
    assert regular_file_hash(path) == file_hash(path)


async def test_success_queue_survives_task_deletion_and_new_worker(authenticated, monkeypatch):
    state, _, request_id, job_id, source = await published(authenticated)
    assert authenticated.delete(f"/api/v1/tasks/{request_id}").status_code == 200
    restarted = DownloadWorker(state.db, state.storage, state.runtime.config, "new-boot")
    restarted.recover(False)
    client = ready(state.runtime, monkeypatch)
    assert await state.runtime.cleanup_cache()
    client.delete_file_cache.assert_awaited_once_with("file-1", str(source), 6, "generation-one")
    with state.db.sessions() as session:
        assert session.get(CacheCleanup, job_id) is None
        assert session.get(Job, job_id).state == "succeeded"
        assert session.get(MediaRequest, request_id).deleted_at is not None
    # The app never unlinks or moves API-owned files, even when transport says deleted.
    assert source.read_bytes() == b"abcdef"
    assert not state.runtime.worker.cleanup_paused
    assert not state.runtime.connection_lock.locked()


async def test_publication_crash_recovers_source_and_atomic_queue(client, monkeypatch):
    state, ingest = setup_pipeline(client)
    ingest.message(7, message(), "in")
    confirm_all(state)
    worker = state.runtime.worker
    job_id = worker.claim(7, "local")
    source = state.runtime.config.bot_api_root / "synthetic.mp4"
    source.write_bytes(b"abcdef")
    _, root, relative, _ = worker.prepare(job_id)
    storage = state.storage.get(root)
    final = contained(storage.root, relative, create_parent=True)
    temporary = final.with_name(f".part.{job_id}")
    worker.journal_copying(job_id, temporary.relative_to(storage.root).as_posix(), relative, str(source))
    size, sha = copy_stream(source, temporary, 6, lambda: False, lambda *_: None)
    worker.journal_ready(job_id, size, sha)
    publish_no_replace(temporary, final)  # Crash after durable publication, before DB completion.
    with state.db.sessions() as session:
        assert session.get(CacheCleanup, job_id) is None
    worker.recover(False)
    with state.db.sessions() as session:
        assert session.get(Job, job_id).state == "succeeded"
        assert session.get(PublishJournal, job_id) is None
        assert state.settings.secrets.decrypt(session.get(CacheCleanup, job_id).source_path_encrypted) == str(source)
    worker.recover(False)
    with state.db.sessions() as session:
        assert session.scalar(select(func.count()).select_from(CacheCleanup)) == 1


async def test_token_bearing_source_is_encrypted_through_crash_recovery(client, monkeypatch):
    state, ingest = setup_pipeline(client)
    ingest.message(7, message(), "in")
    confirm_all(state)
    worker = state.runtime.worker
    job_id = worker.claim(7, "local")
    _, root, relative, _ = worker.prepare(job_id)
    storage = state.storage.get(root)
    final = contained(storage.root, relative, create_parent=True)
    temporary = final.with_name(f".part.{job_id}")
    api_path = f"/var/lib/telegram-bot-api/{TOKEN}/videos/synthetic.mp4"
    worker.journal_copying(job_id, temporary.relative_to(storage.root).as_posix(), relative, api_path)
    with state.db.sessions() as session:
        encrypted = session.get(PublishJournal, job_id).api_source_path_encrypted
        assert TOKEN not in encrypted and api_path != encrypted
    source = state.runtime.config.bot_api_root / "synthetic.mp4"
    source.write_bytes(b"abcdef")
    size, sha = copy_stream(source, temporary, 6, lambda: False, lambda *_: None)
    worker.journal_ready(job_id, size, sha)
    publish_no_replace(temporary, final)
    # New process-equivalent secret/worker instances use the same master key.
    restarted = DownloadWorker(state.db, state.storage, state.runtime.config, "replacement-boot")
    restarted.recover(False)
    state.runtime.cache_cleanup.secrets = Secrets(state.runtime.config.data_dir)
    with state.db.sessions() as session:
        row = session.get(CacheCleanup, job_id)
        assert row.source_path_encrypted == encrypted
        assert state.runtime.cache_cleanup.source_path(row) == api_path
    db_path = Path(state.db.engine.url.database)
    for path in (db_path, db_path.with_name(db_path.name + "-wal")):
        if path.exists():
            assert TOKEN.encode() not in path.read_bytes()
            assert api_path.encode() not in path.read_bytes()
    telegram = ready(state.runtime, monkeypatch)
    assert await state.runtime.cleanup_cache()
    telegram.delete_file_cache.assert_awaited_once_with("file-1", api_path, 6, "generation-one")


async def test_corrupt_encrypted_source_retries_without_deletion(client, monkeypatch):
    state, _, _, job_id, source = await published(client)
    with state.db.transaction() as session:
        session.get(CacheCleanup, job_id).source_path_encrypted = "corrupt"
    telegram = ready(state.runtime, monkeypatch)
    assert await state.runtime.cleanup_cache()
    telegram.delete_file_cache.assert_not_awaited()
    with state.db.sessions() as session:
        assert session.get(CacheCleanup, job_id).error_code == "CACHE_CLEANUP_UNAVAILABLE"
        assert session.get(Job, job_id).state == "succeeded"
    assert source.exists()
    assert not state.runtime.worker.cleanup_paused


@pytest.mark.parametrize("cancel", [False, True])
async def test_failed_or_cancelled_copy_retains_cache_without_cleanup(client, cancel):
    state, ingest = setup_pipeline(client)
    ingest.message(7, message(size=7), "in")
    confirm_all(state)
    job_id = state.runtime.worker.claim(7, "local")
    source = state.runtime.config.bot_api_root / "synthetic.mp4"
    source.write_bytes(b"abcdef")
    if cancel:
        with state.db.transaction() as session:
            session.get(Job, job_id).cancel_requested = True
    await state.runtime.worker.run(SimpleNamespace(mode="local", get_file=AsyncMock(return_value=str(source))), job_id)
    with state.db.sessions() as session:
        assert session.get(CacheCleanup, job_id) is None
        assert session.get(Job, job_id).state == ("cancelled" if cancel else "retry_wait")
    assert source.read_bytes() == b"abcdef"


async def test_cloud_publication_never_enqueues_local_cleanup(client):
    state, ingest = cloud_pipeline(client)
    ingest.message(7, message(), "in")
    confirm_all(state)
    job_id = state.runtime.worker.claim(7, "cloud")

    async def chunks(_):
        yield b"abcdef"

    await state.runtime.worker.run(SimpleNamespace(mode="cloud", get_file=AsyncMock(return_value="videos/x"), file_chunks=chunks), job_id)
    with state.db.sessions() as session:
        assert session.get(Job, job_id).state == "succeeded"
        assert session.get(CacheCleanup, job_id) is None


@pytest.mark.parametrize("changed", ["missing", "modified", "root"])
async def test_changed_final_retains_only_cache_copy(client, monkeypatch, changed):
    state, _, _, job_id, source = await published(client)
    with state.db.sessions() as session:
        final = state.runtime.config.download_root / session.get(Job, job_id).relative_path
    if changed == "missing":
        final.unlink()
    elif changed == "modified":
        final.write_bytes(b"ABCDEF")  # Same size is insufficient for safe removal.
    else:
        root = state.runtime.config.download_root
        root.rename(root.with_name("original-downloads"))
        root.mkdir()
    telegram = ready(state.runtime, monkeypatch)
    assert await state.runtime.cleanup_cache()
    telegram.delete_file_cache.assert_not_awaited()
    with state.db.sessions() as session:
        row = session.get(CacheCleanup, job_id)
        assert row.error_code == "CACHE_FINAL_UNAVAILABLE" and row.attempts == 1
        assert row.next_run_at > time.time()
        assert session.get(Job, job_id).state == "succeeded"
    assert source.exists()
    assert not await state.runtime.cleanup_cache()  # Backoff permits dispatch.


async def test_timeout_retry_does_not_redownload_or_hold_dispatch(client, monkeypatch):
    state, ingest, _, job_id, _ = await published(client)
    telegram = ready(state.runtime, monkeypatch, AsyncMock(side_effect=TimeoutError()))
    assert await state.runtime.cleanup_cache()
    with state.db.sessions() as session:
        row = session.get(CacheCleanup, job_id)
        assert row.attempts == 1 and row.next_run_at > time.time()
        assert row.error_code == "CACHE_CLEANUP_UNAVAILABLE"
        assert session.get(Job, job_id).auto_retries == 0
    assert not await state.runtime.cleanup_cache()
    ingest.message(7, message(2), "next")
    confirm_all(state)
    assert state.runtime.worker.claim(7, "local") is not None
    assert not state.runtime.worker.cleanup_paused
    assert not state.runtime.connection_lock.locked()
    telegram.delete_file_cache.assert_awaited_once()


@pytest.mark.parametrize("owner", ["fetching", "saving", "cancelling", "recovery_blocked"])
async def test_cleanup_drains_all_local_owners_even_aliases(client, monkeypatch, owner):
    state, ingest, _, _, _ = await published(client)
    ingest.message(7, message(2), "next")
    confirm_all(state)
    job_id = state.runtime.worker.claim(7, "local")
    with state.db.transaction() as session:
        row = session.get(Job, job_id)
        row.state, row.phase = owner, "fetching"
    telegram = ready(state.runtime, monkeypatch)
    assert await state.runtime.cleanup_cache()
    telegram.delete_file_cache.assert_not_awaited()


async def test_cleanup_excludes_running_copies_and_other_bot(client, monkeypatch):
    state, _, _, _, _ = await published(client)
    telegram = ready(state.runtime, monkeypatch)
    task = asyncio.create_task(asyncio.Event().wait())
    state.runtime.running["copy-owner"] = task
    try:
        assert await state.runtime.cleanup_cache()
        telegram.delete_file_cache.assert_not_awaited()
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    state.runtime.bot_id = 8
    assert not await state.runtime.cleanup_cache()
    telegram.delete_file_cache.assert_not_awaited()


async def test_generation_change_during_hash_never_deletes_and_lock_remains_available(client, monkeypatch):
    state, _, _, _, _ = await published(client)
    telegram = ready(state.runtime, monkeypatch)
    verify = state.runtime.cache_cleanup.verify_final

    def restart(row):
        assert not state.runtime.connection_lock.locked()
        assert state.runtime.worker.claim(7, "local") is None  # Claim barrier spans the hash.
        monkeypatch.setattr(state.runtime, "progress_generation", lambda: "replacement-generation")
        return verify(row)

    monkeypatch.setattr(state.runtime.cache_cleanup, "verify_final", restart)
    assert await state.runtime.cleanup_cache()
    telegram.delete_file_cache.assert_not_awaited()
    assert not state.runtime.worker.cleanup_paused


async def test_cleanup_can_free_space_below_download_reserve(client, monkeypatch):
    state, _, _, job_id, _ = await published(client)
    storage = state.storage.get("default")
    storage.minimum_free = 2**63
    with pytest.raises(AppError, match="可用空间不足"):
        storage.check()
    telegram = ready(state.runtime, monkeypatch)
    assert await state.runtime.cleanup_cache()
    telegram.delete_file_cache.assert_awaited_once()
    with state.db.sessions() as session:
        assert session.get(CacheCleanup, job_id) is None


async def test_final_replaced_while_hash_finishes_retains_cache(client, monkeypatch):
    state, _, _, job_id, _ = await published(client)
    telegram = ready(state.runtime, monkeypatch)
    verify = state.runtime.cache_cleanup.verify_final

    def replace(row):
        result = verify(row)
        (state.runtime.config.download_root / row.relative_path).write_bytes(b"changed")
        return result

    monkeypatch.setattr(state.runtime.cache_cleanup, "verify_final", replace)
    assert await state.runtime.cleanup_cache()
    telegram.delete_file_cache.assert_not_awaited()
    with state.db.sessions() as session:
        assert session.get(CacheCleanup, job_id).error_code == "CACHE_FINAL_UNAVAILABLE"


async def test_cleanup_cancellation_joins_hash_owner_and_preserves_queue(client, monkeypatch):
    state, _, _, job_id, _ = await published(client)
    telegram = ready(state.runtime, monkeypatch)
    started, release = asyncio.Event(), asyncio.Event()
    original = state.runtime.cache_cleanup.verify_final
    loop = asyncio.get_running_loop()

    def slow_hash(row):
        loop.call_soon_threadsafe(started.set)
        asyncio.run_coroutine_threadsafe(release.wait(), loop).result()
        return original(row)

    monkeypatch.setattr(state.runtime.cache_cleanup, "verify_final", slow_hash)
    operation = asyncio.create_task(state.runtime.cleanup_cache())
    await started.wait()
    operation.cancel()
    await asyncio.sleep(0)
    assert not operation.done() and state.runtime.worker.cleanup_paused
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await operation
    assert not state.runtime.worker.cleanup_paused
    telegram.delete_file_cache.assert_not_awaited()
    with state.db.sessions() as session:
        assert session.get(CacheCleanup, job_id) is not None


@pytest.mark.parametrize("bad", ["generation", "status", "extra", "huge", "redirect", "unsupported"])
async def test_private_cleanup_protocol_rejects_bad_or_unsupported_responses(config: Config, bad):
    calls = []

    async def handler(request):
        calls.append(await request.post())
        payload = {"ok": True, "result": {"generation": "generation-one", "status": "deleted"}}
        if bad == "generation":
            payload["result"]["generation"] = "stale"
        elif bad == "status":
            payload["result"]["status"] = "pending"
        elif bad == "extra":
            payload["result"]["path"] = "must-not-be-returned"
        elif bad == "huge":
            return web.Response(body=b" " * 8193)
        elif bad == "redirect":
            return web.Response(status=302, headers={"Location": "/should-not-follow"})
        elif bad == "unsupported":
            return web.Response(status=404)
        return web.json_response(payload)

    client = TelegramClient(TOKEN, config, UserSettings(bot_api_mode="local"))
    async with fake_api(handler) as base:
        client.bot.session.api = TelegramAPIServer.from_base(base, is_local=True)
        try:
            with pytest.raises(AppError, match="自动重试"):
                await client.delete_file_cache("file-1", "/api-cache/videos/x", 6, "generation-one")
            assert client._cleanup_session.trust_env is False
            assert client._cleanup_session.timeout.total == 2
        finally:
            await client.close()
    assert len(calls) == 1
    assert calls[0] == {"file_id": "file-1", "expected_path": "/api-cache/videos/x", "expected_size": "6", "generation": "generation-one"}


@pytest.mark.parametrize("status", ["deleted", "absent"])
async def test_private_cleanup_success_and_cloud_never_contacts_endpoint(config: Config, status):
    async def handler(request):
        return web.json_response({"ok": True, "result": {"generation": "generation-one", "status": status}})

    client = TelegramClient(TOKEN, config, UserSettings(bot_api_mode="local"))
    client.safety = SimpleNamespace(before=AsyncMock(side_effect=AssertionError()), observe=AsyncMock(side_effect=AssertionError()))
    async with fake_api(handler) as base:
        client.bot.session.api = TelegramAPIServer.from_base(base, is_local=True)
        try:
            assert await client.delete_file_cache("alias", "/cache/a", 6, "generation-one") == status
            client.mode = "cloud"
            with pytest.raises(AppError):
                await client.delete_file_cache("alias", "/cache/a", 6, "generation-one")
        finally:
            await client.close()


async def test_pending_native_cleanup_defers_getfile_without_uncertainty_or_retry_budget(client, config):
    state, ingest = setup_pipeline(client)
    ingest.message(7, message(), "in")
    confirm_all(state)
    job_id = state.runtime.worker.claim(7, "local")

    async def handler(request):
        return web.json_response({"ok": False, "error_code": 400, "description": "Bad Request: TGD_CACHE_CLEANUP_BUSY"}, status=400)

    telegram = TelegramClient(TOKEN, config, UserSettings(bot_api_mode="local"))
    async with fake_api(handler) as base:
        telegram.bot.session.api = TelegramAPIServer.from_base(base, is_local=True)
        try:
            await state.runtime.worker.run(telegram, job_id)
        finally:
            await telegram.close()
    with state.db.sessions() as session:
        job = session.get(Job, job_id)
        assert job.state == "retry_wait" and job.error_code == "CACHE_CLEANUP_BUSY"
        assert job.auto_retries == 0
        assert job.next_run_at > time.time()
