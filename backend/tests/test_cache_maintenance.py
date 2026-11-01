import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from aiogram.client.telegram import TelegramAPIServer
from aiohttp import web
from sqlalchemy import select
from test_cache_cleanup import published
from test_cloud_api import TOKEN, fake_api
from test_pipeline import confirm_all, message, setup_pipeline

from app.bot.client import TelegramClient
from app.core.errors import AppError
from app.core.security import digest
from app.db.models import CacheCleanup, Job, MediaRequest, Notification
from app.schemas.contracts import SettingsPatch, UserSettings
from app.services.cache_maintenance import CacheMaintenanceService
from app.services.cache_storage_protocol import decode_cache_storage


def snapshot(*, operation=None, scan="ready", used=8192, generation="child"):
    return {"generation": generation, "scope": "current_bot", "scan_state": scan,
            "usage": None if used is None else {"used_bytes": used, "file_count": 2}, "operation": operation}


def operation(opid, state="running", **extra):
    return {"operation_id": opid, "state": state, "before_bytes": 8192, "after_bytes": None,
            "released_bytes": None, "error_code": None, **extra}


async def eventually(predicate):
    async with asyncio.timeout(3):
        while not predicate():
            await asyncio.sleep(.01)


def pending_notice(state):
    with state.db.transaction() as session:
        for row in session.scalars(select(Notification)):
            row.state = "obsolete"
        session.add(Notification(event_key="independent-test-notice", chat_id=42, payload={"bot_id": 7}))


def ready(client, monkeypatch):
    state, ingest = setup_pipeline(client)
    runtime = state.runtime
    generation = ["child"]
    monkeypatch.setattr(runtime, "progress_generation", lambda: generation[0])
    telegram = SimpleNamespace(mode="local", bot=SimpleNamespace(token=TOKEN),
                              cache_storage=AsyncMock(return_value=snapshot()), clear_cache_storage=AsyncMock())
    runtime.client, runtime.bot_id = telegram, 7
    service = runtime.cache_maintenance
    service._snapshot(snapshot(), "child", digest(TOKEN))
    return state, ingest, service, telegram, generation


@pytest.mark.parametrize("change", [
    {"generation": "old"}, {"scope": "api_root"}, {"scan_state": []},
    {"usage": {"used_bytes": True, "file_count": 2}}, {"usage": {"used_bytes": -1, "file_count": 2}},
    {"operation": operation("a" * 32, after_bytes=-1)}, {"operation": operation("not-opaque")},
    {"operation": operation("a" * 32, error_code="private-path")}, {"private_path": "secret"},
    {"operation": operation("a" * 32, "succeeded")},
    {"operation": operation("a" * 32, "succeeded", after_bytes=4096, released_bytes=8192)},
    {"operation": operation("a" * 32, "succeeded", after_bytes=0, released_bytes=8192, error_code="CACHE_CLEANUP_FAILED")},
    {"operation": operation("a" * 32, after_bytes=0, released_bytes=8192)},
])
def test_cache_decoder_rejects_malformed_identity_and_fields(change):
    with pytest.raises((ValueError, TypeError)):
        decode_cache_storage({"ok": True, "result": snapshot() | change}, "child")


@pytest.mark.parametrize("response", ["valid", "large", "redirect", "wronggen", "busy", "unknown409", "unsupported"])
async def test_private_transport_is_bounded_and_separate_from_protection(config, monkeypatch, response):
    calls = []

    async def handler(request):
        calls.append(request.path)
        assert dict(await request.post()) == {"generation": "child", "refresh": "1"}
        if response == "large":
            return web.Response(body=b"x" * 16385)
        if response == "redirect":
            return web.Response(status=302, headers={"Location": "/other"})
        if response in {"busy", "unknown409"}:
            return web.json_response({"ok": False, "description": "Conflict: TGD_CACHE_STORAGE_BUSY" if response == "busy" else "private"}, status=409)
        if response == "unsupported":
            return web.json_response({"ok": False, "description": "Bad Request: method not found"}, status=400)
        return web.json_response({"ok": True, "result": snapshot(generation="old" if response == "wronggen" else "child")})

    telegram = TelegramClient(TOKEN, config, UserSettings(bot_api_mode="local"))
    telegram.safety = AsyncMock()
    telegram.safety.diagnostics = None
    monkeypatch.setenv("HTTP_PROXY", "http://invalid.invalid:9")
    async with fake_api(handler) as base:
        telegram.bot.session.api = TelegramAPIServer.from_base(base, is_local=True)
        try:
            if response == "valid":
                assert await telegram.cache_storage("child", refresh=True) == snapshot()
            else:
                with pytest.raises(AppError) as exc:
                    await telegram.cache_storage("child", refresh=True)
                assert exc.value.code == {"busy": "CACHE_NATIVE_BUSY", "unsupported": "CACHE_UNSUPPORTED"}.get(response, "CACHE_UNAVAILABLE")
            assert len(calls) == 1
            assert telegram._cache_storage_session.trust_env is False
            telegram.safety.before.assert_not_called()
            telegram.safety.observe.assert_not_called()
        finally:
            await telegram.close()


def test_actions_require_auth_csrf_and_no_input(client, authenticated, monkeypatch):
    # authenticated fixture has established the login on this same client.
    service = client.app.state.runtime.cache_maintenance
    start = AsyncMock()
    monkeypatch.setattr(service, "start", start)
    for action in ("refresh", "clean"):
        path = "/api/v1/system/cache/" + action
        csrf = client.headers.pop("X-CSRF-Token")
        assert client.post(path).status_code == 403
        client.headers["X-CSRF-Token"] = csrf
        assert client.post(path, json={}).status_code == 422
        assert client.post(path + "?anything=1").status_code == 422
        assert client.post(path, headers={"Origin": "http://evil.invalid"}).status_code == 403
        assert client.post(path).status_code == 202
    assert start.await_count == 2
    client.cookies.clear()
    assert client.post("/api/v1/system/cache/clean").status_code == 401


async def test_status_reads_snapshot_and_refresh_is_single_flight(client, monkeypatch):
    state, _, service, telegram, _ = ready(client, monkeypatch)
    entered, release = asyncio.Event(), asyncio.Event()

    async def refresh(*args, **kwargs):
        entered.set()
        await release.wait()
        return snapshot(used=4096)

    telegram.cache_storage.side_effect = refresh
    await service.start(clean=False)
    await entered.wait()
    try:
        assert service.status()["usage_state"] == "refreshing"
        for _ in range(10):
            service.status()
        assert telegram.cache_storage.await_count == 1
        with pytest.raises(AppError):
            await service.start(clean=True)
    finally:
        release.set()
        await service._task
    assert service.status()["used_bytes"] == 4096
    assert service.status()["maintenance"]["can_clean"]


@pytest.mark.parametrize("blocked", ["fetching", "saving", "cancelling", "recovery_blocked", "queued", "retry_wait", "waiting_existing"])
async def test_clean_refuses_every_local_owner_or_pending_job(client, monkeypatch, blocked):
    state, ingest, service, telegram, _ = ready(client, monkeypatch)
    ingest.message(7, message(), "in")
    confirm_all(state)
    with state.db.transaction() as session:
        job = session.scalar(select(Job))
        job.state, job.acquisition_mode = blocked, "local"
    with pytest.raises(AppError) as error:
        await service.start(clean=True)
    assert error.value.code == "CACHE_CLEANUP_BLOCKED"
    telegram.clear_cache_storage.assert_not_awaited()
    assert not service.read()


async def test_missing_final_pending_cleanup_preserves_only_cache_copy(client, monkeypatch):
    state, _, _, job_id, source = await published(client)
    runtime = state.runtime
    telegram = SimpleNamespace(mode="local", bot=SimpleNamespace(token=TOKEN), clear_cache_storage=AsyncMock(),
                              cache_storage=AsyncMock(return_value=snapshot()))
    runtime.client, runtime.bot_id = telegram, 7
    monkeypatch.setattr(runtime, "progress_generation", lambda: "child")
    service = runtime.cache_maintenance
    service._snapshot(snapshot(), "child", digest(TOKEN))
    with state.db.sessions() as session:
        final = runtime.config.download_root / session.get(Job, job_id).relative_path
    final.unlink()
    assert service.status()["maintenance"]["can_clean"]
    await service.start(clean=False)  # Read-only statistics still work when final output is missing.
    await service._task
    await service.start(clean=True)
    await service._task
    assert service.read()["state"] == "failed"
    assert service.read()["blocked_reason"] == "pending_verification"
    assert service.status()["maintenance"]["message"]
    telegram.clear_cache_storage.assert_not_awaited()
    assert source.exists()
    with state.db.sessions() as session:
        assert session.get(CacheCleanup, job_id)


async def test_known_completion_releases_gate_and_records_freed_bytes(client, monkeypatch):
    state, _, service, telegram, _ = ready(client, monkeypatch)

    async def clear(generation, opid):
        assert not state.runtime.worker.claim()
        return snapshot(operation=operation(opid, "succeeded", after_bytes=4096, released_bytes=4096), used=4096)

    telegram.clear_cache_storage.side_effect = clear
    await service.start(clean=True)
    await service._task
    status = service.status()
    assert status["maintenance"]["state"] == "succeeded"
    assert status["maintenance"]["last_result"] == {"before_bytes": 8192, "after_bytes": 4096, "released_bytes": 4096}
    assert status["maintenance"]["can_clean"]
    assert status["used_bytes"] == 4096
    telegram.clear_cache_storage.assert_awaited_once()


async def test_lost_mutation_retains_gate_restarts_poll_only_and_rejects_obsolete_result(client, monkeypatch):
    state, _, service, telegram, generation = ready(client, monkeypatch)
    telegram.clear_cache_storage.side_effect = TimeoutError()
    telegram.cache_storage.return_value = snapshot(operation=operation("f" * 32, "succeeded", after_bytes=0, released_bytes=8192))
    await service.start(clean=True)
    await eventually(lambda: service.read().get("state") == "uncertain")
    opid = service.read()["operation_id"]
    try:
        assert not service.status()["maintenance"]["can_clean"]
        with pytest.raises(AppError):
            await service.start(clean=True)
        assert await state.runtime.cleanup_cache()  # Auto cleanup cannot open this gate.
        await service.close()
        restarted = CacheMaintenanceService(state.runtime)
        state.runtime.cache_maintenance = restarted
        await restarted.resume()
        await eventually(lambda: telegram.cache_storage.await_count >= 2)
        assert restarted.read()["state"] == "uncertain"
        telegram.clear_cache_storage.assert_awaited_once()  # Never replay lost mutation.
        telegram.cache_storage.return_value = snapshot(operation=operation(opid, "succeeded", after_bytes=0, released_bytes=8192), used=0)
        await eventually(lambda: restarted.read().get("state") == "succeeded")
        await restarted.close()
    finally:
        await state.runtime.cache_maintenance.close()


async def test_new_verified_generation_releases_unknown_without_success_claim(client, monkeypatch):
    state, _, service, telegram, generation = ready(client, monkeypatch)
    telegram.clear_cache_storage.side_effect = TimeoutError()
    telegram.cache_storage.side_effect = TimeoutError()
    await service.start(clean=True)
    await eventually(lambda: service.read().get("state") == "uncertain")
    generation[0] = "replacement"
    await eventually(lambda: service.read().get("state") == "failed")
    assert service.read()["last_result"] is None
    assert service.status()["used_bytes"] is None
    await service.close()


async def test_gc_running_error_remains_unknown_and_fixed_native_busy_is_unsent(client, monkeypatch):
    _, _, service, telegram, _ = ready(client, monkeypatch)
    telegram.clear_cache_storage.side_effect = lambda generation, opid: snapshot(
        operation=operation(opid, error_code="CACHE_CLEANUP_FAILED"))
    await service.start(clean=True)
    await eventually(lambda: service.read().get("state") == "uncertain")
    await service.close()
    service._write(service.read()["operation_id"], state="failed")
    telegram.clear_cache_storage.side_effect = AppError("CACHE_NATIVE_BUSY", "fixed", 409)
    await service.start(clean=True)
    await service._task
    assert service.read()["state"] == "failed"
    assert service.status()["maintenance"]["can_clean"]


async def test_gate_serializes_confirmation_retry_claim_and_connection_settings(client, monkeypatch):
    state, ingest, service, telegram, _ = ready(client, monkeypatch)
    ingest.message(7, message(), "in")
    request_id = confirm_all(state)
    with state.db.transaction() as session:
        job = session.scalar(select(Job))
        job.state = "failed"
        job_id = job.id
        request = session.get(MediaRequest, request_id)
        request.status = "failed"
    telegram.clear_cache_storage.side_effect = TimeoutError()
    await service.start(clean=True)
    await eventually(lambda: service.read().get("state") == "uncertain")
    try:
        with pytest.raises(AppError, match="缓存维护"):
            state.tasks.retry(request_id, None)
        with state.db.transaction() as session:
            session.get(Job, job_id).state = "queued"
        assert state.runtime.worker.claim() is None
        patch = SettingsPatch(revision=state.settings.read()[0], bot_token="987654:other-synthetic-token")
        with pytest.raises(AppError, match="缓存维护"):
            state.settings.save(patch, state.settings.candidate(patch))
        # A newly received confirmation cannot publish jobs after the admission snapshot.
        ingest.message(7, message(2), "second")
        with state.db.transaction() as session:
            request = session.scalar(select(MediaRequest).where(MediaRequest.id != request_id))
            request.collect_until = 0
        state.tasks.freeze_due()
        with state.db.sessions() as session:
            request = session.scalar(select(MediaRequest).where(MediaRequest.id != request_id))
            second, nonce = request.id, request.nonce
        with pytest.raises(AppError, match="缓存维护"):
            state.tasks.confirm(second, nonce, 42, 42, "confirm")
        assert state.tasks.cancel(second)["status"] == "cancelled"
    finally:
        await service.close()


async def test_accepted_running_result_stays_running_until_exact_terminal_poll(client, monkeypatch):
    _, _, service, telegram, _ = ready(client, monkeypatch)
    entered, release = asyncio.Event(), asyncio.Event()

    async def poll(generation, **kwargs):
        entered.set()
        await release.wait()
        return snapshot(operation=operation(service.read()["operation_id"], "succeeded", after_bytes=0, released_bytes=8192), used=0)

    telegram.clear_cache_storage.side_effect = lambda generation, opid: snapshot(operation=operation(opid))
    telegram.cache_storage.side_effect = poll
    await service.start(clean=True)
    await entered.wait()
    try:
        assert service.read()["state"] == "running"
        await service.start(clean=False)  # Reconcile existing operation only.
        telegram.clear_cache_storage.assert_awaited_once()
    finally:
        release.set()
        await service._task
    assert service.read()["state"] == "succeeded"
    assert "完成" in service.status()["maintenance"]["message"]


async def test_lost_scan_response_keeps_last_usage_and_allows_claim_notification_and_retry(client, monkeypatch):
    state, ingest, service, telegram, _ = ready(client, monkeypatch)
    telegram.cache_storage.side_effect = TimeoutError()
    await service.start(clean=False)
    await service._task
    ingest.message(7, message(), "in")
    confirm_all(state)
    pending_notice(state)
    assert state.runtime.worker.claim()
    assert state.runtime.next_notification()
    assert not service.read()
    assert service.status()["used_bytes"] == 8192
    assert service.status()["refresh_error"]
    telegram.cache_storage.side_effect = None
    telegram.cache_storage.return_value = snapshot(used=4096)
    await service.start(clean=False)
    await service._task
    assert service.status()["used_bytes"] == 4096
    assert service.status()["refresh_error"] is None


async def test_failed_scan_without_usage_cannot_report_clean_available(client, monkeypatch):
    _, _, service, telegram, _ = ready(client, monkeypatch)
    service._usage = None
    telegram.cache_storage.return_value = snapshot(scan="failed", used=None)
    await service.start(clean=False)
    await service._task
    assert not service.status()["maintenance"]["can_clean"]
    assert service.status()["refresh_error"]


def test_refreshed_usage_stays_ready_during_existing_download_and_invalidation_only_on_new_activity(client, monkeypatch):
    state, ingest, service, _, _ = ready(client, monkeypatch)
    measured = service.status()["measured_at"]
    service._snapshot(snapshot(scan="running"), "child", digest(TOKEN))
    assert service.status()["measured_at"] == measured
    service._snapshot(snapshot(), "child", digest(TOKEN))
    ingest.message(7, message(), "in")
    confirm_all(state)
    with state.db.transaction() as session:
        job = session.scalar(select(Job))
        job.state, job.acquisition_mode = "fetching", "local"
    assert service.status()["usage_state"] == "ready"
    service.invalidate()  # A new claim or cache deletion invalidates the snapshot.
    assert service.status()["usage_state"] == "stale"
    assert service.status()["refresh_error"] is None
    service._snapshot(snapshot(used=16384), "child", digest(TOKEN))
    assert service.status()["usage_state"] == "ready"
    with state.db.transaction() as session:
        session.scalar(select(Job)).state = "failed"
    assert service.status()["usage_state"] == "ready"


@pytest.mark.parametrize("mode,status,description,expected", [
    ("local", 409, "Conflict: TGD_CACHE_STORAGE_BUSY", "CACHE_CLEANUP_BUSY"),
    ("local", 400, "Bad Request: TGD_CACHE_CLEANUP_BUSY", "CACHE_CLEANUP_BUSY"),
    ("local", 400, "TGD_CACHE_CLEANUP_BUSY", "CACHE_CLEANUP_BUSY"),
    ("local", 409, "Conflict: arbitrary", None),
    ("local", 400, "Bad Request: arbitrary", None),
    ("cloud", 400, "Bad Request: TGD_CACHE_CLEANUP_BUSY", None),
])
async def test_native_notification_busy_does_not_consume_telegram_backoff(config, mode, status, description, expected):
    from aiogram.exceptions import TelegramBadRequest, TelegramConflictError
    from aiogram.methods import SendMessage

    telegram = TelegramClient(TOKEN, config, UserSettings(bot_api_mode=mode))
    telegram.safety = AsyncMock()
    telegram.safety.diagnostics = None
    telegram.safety.observe = Mock()

    exception = TelegramConflictError if status == 409 else TelegramBadRequest

    async def send():
        raise exception(method=SendMessage(chat_id=42, text="synthetic"), message=description)

    try:
        with pytest.raises(AppError if expected else exception) as error:
            await telegram.request(send, method="sendMessage")
        if expected:
            assert error.value.code == expected
            telegram.safety.observe.assert_not_called()
        else:
            telegram.safety.observe.assert_called_once()
    finally:
        await telegram.close()


async def test_read_only_refresh_allows_new_claims_notifications_and_connection_lock(client, monkeypatch):
    state, ingest, service, telegram, _ = ready(client, monkeypatch)
    ingest.message(7, message(), "in")
    confirm_all(state)
    entered, release = asyncio.Event(), asyncio.Event()

    async def scan(*args, **kwargs):
        entered.set()
        await release.wait()
        return snapshot()

    telegram.cache_storage.side_effect = scan
    await service.start(clean=False)
    await entered.wait()
    try:
        assert state.runtime.worker.claim()
        pending_notice(state)
        assert state.runtime.next_notification()
        assert not state.runtime.connection_lock.locked()
        assert not state.runtime.worker.cleanup_paused
        assert not service.read()
    finally:
        release.set()
        await service._task


async def test_new_confirmation_without_local_mode_snapshot_still_blocks_clean(client, monkeypatch):
    state, ingest, service, telegram, _ = ready(client, monkeypatch)
    ingest.message(7, message(), "in")
    confirm_all(state)
    assert service.status()["maintenance"]["blocked_reason"] == "queued_downloads"
    with pytest.raises(AppError):
        await service.start(clean=True)
    telegram.clear_cache_storage.assert_not_awaited()


@pytest.mark.parametrize("owner", ["fetching", "saving", "cancelling", "recovery_blocked"])
async def test_refresh_during_local_ownership_never_uses_cleanup_admission(client, monkeypatch, owner):
    state, ingest, service, telegram, _ = ready(client, monkeypatch)
    ingest.message(7, message(), "in")
    confirm_all(state)
    with state.db.transaction() as session:
        job = session.scalar(select(Job))
        job.state, job.acquisition_mode = owner, "local"
    telegram.cache_storage.return_value = snapshot(used=16384)
    await service.start(clean=False)
    await service._task
    assert service.status()["used_bytes"] == 16384
    assert service.status()["usage_state"] == "ready"
    assert not service.status()["maintenance"]["can_clean"]
    assert not service.read()
    telegram.cache_storage.assert_awaited_once_with("child", refresh=True)
    telegram.clear_cache_storage.assert_not_awaited()


@pytest.mark.parametrize("output", ["valid", "corrupt", "changed_after_hash", "record_changed_after_hash", "other_bot_missing"])
async def test_manual_clean_validates_pending_outputs_instead_of_disabling_for_pending_rows(client, monkeypatch, output):
    state, _, _, job_id, source = await published(client)
    runtime = state.runtime
    telegram = SimpleNamespace(mode="local", bot=SimpleNamespace(token=TOKEN), clear_cache_storage=AsyncMock(),
                              cache_storage=AsyncMock(return_value=snapshot()))
    runtime.client, runtime.bot_id = telegram, 7
    monkeypatch.setattr(runtime, "progress_generation", lambda: "child")
    service = runtime.cache_maintenance
    service._snapshot(snapshot(), "child", digest(TOKEN))
    with state.db.transaction() as session:
        row = session.get(CacheCleanup, job_id)
        final = runtime.config.download_root / row.relative_path
        row.attempts, row.error_code = 5, "CACHE_CLEANUP_UNAVAILABLE"
        if output == "other_bot_missing":
            row.bot_id = 8
            final.unlink()
        elif output == "corrupt":
            final.write_bytes(b"badbad")
    original = runtime.cache_cleanup.verify_final

    def verify(row):
        assert service.read()["state"] == "running"
        assert not runtime.connection_lock.locked()
        assert runtime.worker.claim() is None
        fingerprint = original(row)
        if output == "changed_after_hash":
            final.write_bytes(b"change")
        elif output == "record_changed_after_hash":
            with state.db.transaction() as session:
                session.get(CacheCleanup, job_id).sha256 = "a" * 64
        return fingerprint

    monkeypatch.setattr(runtime.cache_cleanup, "verify_final", verify)

    async def clear(generation, opid):
        return snapshot(operation=operation(opid, "succeeded", after_bytes=0, released_bytes=8192), used=0)

    telegram.clear_cache_storage.side_effect = clear
    assert service.status()["maintenance"]["can_clean"]
    await service.start(clean=True)
    await service._task
    if output in {"valid", "other_bot_missing"}:
        telegram.clear_cache_storage.assert_awaited_once()
        assert service.read()["state"] == "succeeded"
    else:
        telegram.clear_cache_storage.assert_not_awaited()
        assert service.read()["state"] == "failed"
        assert service.read()["blocked_reason"] == "pending_verification"
    assert source.exists()  # The application never mutates the read-only API mount.
