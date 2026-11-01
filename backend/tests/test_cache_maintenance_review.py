import asyncio
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from test_cache_maintenance import operation, ready, snapshot
from test_local_api import heartbeat, save
from test_local_api import managed as managed_fixture
from test_pipeline import confirm_all, message

from app.core.errors import AppError
from app.core.security import digest
from app.db.models import Job, LocalApiRuntime, MediaRequest
from app.services.cache_maintenance import maintenance_blocked, require_no_maintenance

managed = managed_fixture


@pytest.mark.parametrize("owner", ["automatic_cleanup", "worker_tail"])
async def test_clean_status_matches_live_owner_admission_and_recovers_when_owner_finishes(client, monkeypatch, owner):
    state, _, service, telegram, _ = ready(client, monkeypatch)
    runtime = state.runtime
    pending = asyncio.get_running_loop().create_future()
    if owner == "automatic_cleanup":
        runtime.worker.cleanup_paused = True
    else:
        runtime.running["review-worker-tail"] = pending
    try:
        assert not service.status()["maintenance"]["can_clean"]
        with pytest.raises(AppError) as error:
            await service.start(clean=True)
        assert error.value.code == "CACHE_CLEANUP_BLOCKED"
        telegram.clear_cache_storage.assert_not_awaited()
        runtime.worker.cleanup_paused = False
        pending.set_result(None)
        assert service.status()["maintenance"]["can_clean"]
    finally:
        runtime.worker.cleanup_paused = False
        runtime.running.pop("review-worker-tail", None)
        if not pending.done():
            pending.cancel()


async def test_cancelled_admission_joins_late_intent_without_issuing_clear(client, monkeypatch):
    _, _, service, telegram, _ = ready(client, monkeypatch)
    entered, release = threading.Event(), threading.Event()
    admit = service._admit

    def delayed_admit(*args):
        entered.set()
        assert release.wait(3)
        return admit(*args)

    monkeypatch.setattr(service, "_admit", delayed_admit)
    request = asyncio.create_task(service.start(clean=True))
    try:
        assert await asyncio.to_thread(entered.wait, 3)
        request.cancel()
        await asyncio.sleep(0)
    finally:
        release.set()
    with pytest.raises(asyncio.CancelledError):
        await request
    assert service.read()["state"] == "failed"
    assert not service.runtime.connection_lock.locked()
    telegram.clear_cache_storage.assert_not_awaited()


async def test_refresh_does_not_hold_connection_lock_and_discards_replaced_client_reply(client, monkeypatch):
    state, _, service, old, generation = ready(client, monkeypatch)
    entered, release = asyncio.Event(), asyncio.Event()

    async def read(*args, **kwargs):
        entered.set()
        await release.wait()
        return snapshot(used=16384)

    old.cache_storage.side_effect = read
    await state.runtime.connection_lock.acquire()
    refresh = asyncio.create_task(service._refresh(old, "child", digest(old.bot.token)))
    try:
        await asyncio.wait_for(entered.wait(), 3)
        state.runtime.client = SimpleNamespace(mode="local", bot=SimpleNamespace(token=old.bot.token))
        generation[0] = "replacement"
    finally:
        state.runtime.connection_lock.release()
        release.set()
    await refresh
    assert service.status()["used_bytes"] is None
    assert service._usage["used_bytes"] == 8192


async def test_monitor_rechecks_identity_after_connection_lock_wait(client, monkeypatch):
    state, _, service, old, generation = ready(client, monkeypatch)
    operation_id = service._admit("child", digest(old.bot.token))
    entered = asyncio.Event()
    loop = asyncio.get_running_loop()
    identity = service.identity

    def observed_identity():
        value = identity()
        loop.call_soon_threadsafe(entered.set)
        return value

    monkeypatch.setattr(service, "identity", observed_identity)
    await state.runtime.connection_lock.acquire()
    monitor = asyncio.create_task(service._monitor())
    try:
        await asyncio.wait_for(entered.wait(), 3)
        state.runtime.client = SimpleNamespace(mode="local", bot=old.bot, cache_storage=AsyncMock())
        generation[0] = "replacement"
    finally:
        state.runtime.connection_lock.release()
    await asyncio.wait_for(monitor, 3)
    assert service.read()["operation_id"] == operation_id
    assert service.read()["state"] == "failed"
    old.cache_storage.assert_not_awaited()
    state.runtime.client.cache_storage.assert_not_awaited()


@pytest.mark.parametrize("stored", [None, [], {"state": "unexpected"}, {"state": "running"}])
async def test_malformed_persistent_gate_blocks_claim_and_monitor_safely(client, monkeypatch, stored):
    state, ingest, service, telegram, _ = ready(client, monkeypatch)
    ingest.message(7, message(), "in")
    confirm_all(state)
    with state.db.transaction() as session:
        row = session.get(LocalApiRuntime, 1)
        row.values = dict(row.values) | {"cache_maintenance": stored}
    assert service.read()["state"] == "uncertain"
    with state.db.sessions() as session:
        assert maintenance_blocked(session)
        with pytest.raises(AppError):
            require_no_maintenance(session)
    assert state.runtime.worker.claim() is None
    await asyncio.wait_for(service._monitor(), 3)
    telegram.cache_storage.assert_not_awaited()


def test_old_bot_completion_and_late_error_do_not_leak_into_new_bot_status(client, monkeypatch):
    state, _, service, old, _ = ready(client, monkeypatch)
    credential = digest(old.bot.token)
    operation_id = service._admit("child", credential)
    service._snapshot(snapshot(operation=operation(operation_id, "succeeded", after_bytes=0, released_bytes=8192), used=0), "child", credential)
    state.runtime.client = SimpleNamespace(mode="local", bot=SimpleNamespace(token="987654:other-synthetic-token"))
    new_credential = digest(state.runtime.client.bot.token)
    service._snapshot(snapshot(used=16384), "child", new_credential)
    service._unavailable(TimeoutError(), "child", credential)
    status = service.status()
    assert status["used_bytes"] == 16384
    assert status["usage_state"] == "ready"
    assert status["maintenance"]["last_result"] is None


def test_refresh_error_is_scoped_to_snapshot_identity_and_not_worker_invalidation(client, monkeypatch):
    state, _, service, old, generation = ready(client, monkeypatch)
    credential = digest(old.bot.token)
    service._unavailable(TimeoutError("never expose transport details"), "child", credential, refresh=True)
    assert service.status()["refresh_error"]
    service.invalidate()
    assert service.status()["refresh_error"]
    generation[0] = "replacement"
    assert service.status()["refresh_error"] is None
    service._snapshot(snapshot(generation="replacement", scan="running", used=None), "replacement", credential)
    assert service.status()["refresh_error"] is None
    service._unavailable(TimeoutError(), "child", credential, refresh=True)
    assert service.status()["refresh_error"] is None


async def test_invalid_gate_restart_requires_verified_replacement_before_unlock(managed, monkeypatch):
    state, _, service, telegram, generation = ready(managed, monkeypatch)
    local_api = state.runtime.local_api
    local_api.update(phase="idle", applied_mode="local")
    save(managed, bot_token=telegram.bot.token)
    local_api.update(phase="idle", applied_mode="local")
    heartbeat(local_api, state="running", generation="child")
    with state.db.transaction() as session:
        row = session.get(LocalApiRuntime, 1)
        row.values = dict(row.values) | {"cache_maintenance": {"state": "broken"}}
    local_api.action(True)
    baseline = service.read()
    assert baseline["state"] == "uncertain"
    assert baseline["generation"] == "child"
    assert not baseline.get("invalid")
    assert not service.status()["maintenance"]["can_clean"]
    generation[0] = "replacement"
    await asyncio.wait_for(service._monitor(), 3)
    assert service.read()["state"] == "failed"
    assert service.read()["last_result"] is None
    telegram.clear_cache_storage.assert_not_awaited()


@pytest.mark.parametrize("admission_race", [False, True])
async def test_callback_remains_pending_during_maintenance_then_confirms_once(client, monkeypatch, admission_race):
    state, ingest, service, telegram, _ = ready(client, monkeypatch)
    runtime = state.runtime
    save(client, bot_token=telegram.bot.token)
    runtime.client_signature = state.settings.client_signature(state.settings.read()[1])
    ingest.message(7, message(), "media")
    with state.db.transaction() as session:
        session.scalar(select(MediaRequest)).collect_until = 0
    state.tasks.freeze_due()
    with state.db.sessions() as session:
        request = session.scalar(select(MediaRequest))
        request_id = request.id
        callback = {"id": "synthetic-callback", "data": f"y:{request.id}:{request.nonce}",
                    "from": {"id": 42}, "message": {"chat": {"id": 42}}}
    runtime.ingest.persist_batch(7, [{"update_id": 10, "callback_query": callback}])
    monkeypatch.setattr(runtime, "ensure_client", AsyncMock(return_value=True))
    monkeypatch.setattr(runtime, "poll_result", lambda *args, **kwargs: None)
    telegram.answer = AsyncMock()

    async def stop_after_iteration(offset):
        runtime.stopping = True
        return []

    telegram.updates = AsyncMock(side_effect=stop_after_iteration)
    callback_handler = runtime.ingest.callback
    if admission_race:
        def admit_during_callback(payload):
            service._admit("child", digest(telegram.bot.token))
            return callback_handler(payload)

        monkeypatch.setattr(runtime.ingest, "callback", admit_during_callback)
    else:
        service._admit("child", digest(telegram.bot.token))
    try:
        await asyncio.wait_for(runtime.poll_loop(), 3)
        assert len(runtime.ingest.pending(7)) == 1
        telegram.answer.assert_not_awaited()
        with state.db.sessions() as session:
            assert session.get(MediaRequest, request_id).status == "awaiting_confirmation"
            assert session.scalar(select(Job)) is None
        service._write(service.read()["operation_id"], state="failed")
        monkeypatch.setattr(runtime.ingest, "callback", callback_handler)
        runtime.stopping = False
        await asyncio.wait_for(runtime.poll_loop(), 3)
        assert runtime.ingest.pending(7) == []
        telegram.answer.assert_awaited_once()
        with state.db.sessions() as session:
            assert session.get(MediaRequest, request_id).status == "queued"
            assert len(list(session.scalars(select(Job)))) == 1
    finally:
        runtime.stopping = False
