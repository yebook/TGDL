import asyncio
import time
from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import TelegramRetryAfter
from sqlalchemy import event, func, select
from test_pipeline import setup_pipeline

from app.bot.client import TelegramClient
from app.bot.commands import COMMANDS
from app.db.models import BotIdentity, Job, MediaItem, MediaRequest, Notification, PendingMediaName, uid
from app.schemas.contracts import SettingsPatch, UserSettings
from app.services.request_safety import SafetyDeferred

TOKEN = "123456789:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"


def seed(state, status="queued", states=None, *, bot=7, chat=42, user=42, deleted=False):
    request_id = uid()
    with state.db.transaction() as session:
        request = MediaRequest(id=request_id, bot_id=bot, chat_id=chat, user_id=user,
                               status=status, collect_until=0, deleted_at=time.time() if deleted else None)
        session.add(request)
        session.flush()
        for index, job_state in enumerate(states if states is not None else [status]):
            item = MediaItem(request_id=request_id, bot_id=bot, chat_id=chat,
                             message_id=int(request_id[:10], 16) + index, file_id=f"file-{request_id}-{index}",
                             media_type="document", original_name="file.bin", size_bytes=6)
            session.add(item)
            session.flush()
            session.add(Job(request_id=request_id, item_id=item.id, state=job_state,
                            relative_path=f"{request_id}/{index}.bin"))
    return request_id


def send(ingest, text, index=100, *, bot=7, chat=42, user=42, chat_type="private", **extra):
    message = {"message_id": index, "from": {"id": user}, "chat": {"id": chat, "type": chat_type},
               "text": text, **extra}
    ingest.persist_batch(bot, [{"update_id": index, "message": message}])
    inbox_id = next(key for key, payload in ingest.pending(bot) if payload["update_id"] == index)
    ingest.message(bot, message, inbox_id)
    return inbox_id, message


def reply(state, inbox):
    with state.db.sessions() as session:
        row = session.scalar(select(Notification).where(Notification.event_key == f"reply:{inbox}"))
        return row.payload["text"] if row else None


@pytest.mark.parametrize("stage", ["collecting", "awaiting_confirmation", "queued", "retry_wait", "waiting_existing",
                                        "fetching", "saving", "cancelling", "recovery_blocked"])
def test_cancel_uses_shared_transaction_and_preserves_physical_stop(client, config, stage):
    state, ingest = setup_pipeline(client)
    request_id = seed(state, stage, [] if stage in {"collecting", "awaiting_confirmation"} else [stage])
    cache = config.bot_api_root / "retained-cache"
    cache.write_bytes(b"cached")
    saved = config.download_root / "already-saved"
    saved.write_bytes(b"saved")
    inbox, _ = send(ingest, f"/cancel {request_id}")
    stopping = stage in {"fetching", "saving", "cancelling", "recovery_blocked"}
    with state.db.sessions() as session:
        request = session.get(MediaRequest, request_id)
        assert request.status == ("recovery_blocked" if stage == "recovery_blocked" else
                                  "cancelling" if stopping else "cancelled")
        jobs = list(session.scalars(select(Job).where(Job.request_id == request_id)))
        assert all(job.cancel_requested for job in jobs)
        if stopping:
            assert all(job.finished_at is None for job in jobs)
    text = reply(state, inbox)
    assert ("正在取消 1" if stopping else "已取消 1") in text
    assert "缓存" in text and "将删除" in text and "清理尚未确认完成" in text
    assert cache.read_bytes() == b"cached" and saved.read_bytes() == b"saved"
    assert state.runtime.counts()[0] == int(stopping)


@pytest.mark.parametrize("stage", ["succeeded", "already_exists", "failed", "cancelled", "partial_success", "expired"])
def test_terminal_tasks_are_unchanged(client, stage):
    state, ingest = setup_pipeline(client)
    request_id = seed(state, stage, ["succeeded", "failed"] if stage == "partial_success" else
                      [] if stage == "expired" else [stage])
    inbox, _ = send(ingest, f"/cancel {request_id}")
    with state.db.sessions() as session:
        assert session.get(MediaRequest, request_id).status == stage
        assert not any(job.cancel_requested for job in session.scalars(select(Job)))
    assert "无需取消" in reply(state, inbox)


def test_cancel_all_is_scoped_and_duplicate_delivery_cannot_cancel_later_tasks(client):
    state, ingest = setup_pipeline(client)
    target = seed(state)
    group = seed(state, "fetching", ["succeeded", "fetching"])
    excluded = [seed(state, bot=8), seed(state, user=43), seed(state, chat=43), seed(state, deleted=True),
                seed(state, "succeeded"), seed(state, "failed")]
    inbox, payload = send(ingest, "/cancel_all")
    later = seed(state)
    ingest.message(7, payload, inbox)
    with state.db.sessions() as session:
        assert session.get(MediaRequest, target).status == "cancelled"
        assert session.get(MediaRequest, group).status == "cancelling"
        assert session.get(MediaRequest, later).status == "queued"
        assert [session.get(MediaRequest, key).status for key in excluded] == [
            "queued", "queued", "queued", "queued", "succeeded", "failed"]
        group_jobs = list(session.scalars(select(Job).where(Job.request_id == group)))
        assert next(job for job in group_jobs if job.state == "succeeded").cancel_requested is False
        assert session.scalar(select(func.count()).select_from(Notification).where(
            Notification.event_key == f"reply:{inbox}")) == 1
    assert "已取消 1" in reply(state, inbox) and "正在取消 1" in reply(state, inbox)


@pytest.mark.parametrize("scope", [{"bot": 8}, {"user": 43}, {"chat": 43}, {"deleted": True}])
def test_cancel_unknown_and_foreign_look_identical(client, scope):
    state, ingest = setup_pipeline(client)
    request_id = seed(state, **scope)
    first, _ = send(ingest, f"/cancel {request_id}")
    second, _ = send(ingest, "/cancel 00000000000000000000", 101)
    assert reply(state, first) == reply(state, second)
    with state.db.sessions() as session:
        assert session.get(MediaRequest, request_id).status == "queued"


@pytest.mark.parametrize("text", ["/cancel", "/cancel short", "/cancel {id} extra", "/cancel_all extra",
                                      "/cancel_all_extra", "/cancel_all@other_bot", "/cancelled {id}",
                                      "/status_extra", "/status extra"])
def test_strict_command_errors_do_not_execute_or_become_names(client, text):
    state, ingest = setup_pipeline(client)
    request_id = seed(state)
    inbox, _ = send(ingest, text.replace("{id}", request_id))
    assert reply(state, inbox)
    with state.db.sessions() as session:
        assert session.get(MediaRequest, request_id).status == "queued"
        assert session.get(PendingMediaName, (7, 42, 42)).name is None


@pytest.mark.parametrize("extra", [{"forward_origin": {"type": "user"}}, {"forward_date": 1}])
def test_forwarded_cancel_is_not_executed(client, extra):
    state, ingest = setup_pipeline(client)
    request_id = seed(state)
    inbox, _ = send(ingest, "/cancel_all", **extra)
    assert "转发的取消命令不会执行" in reply(state, inbox)
    with state.db.sessions() as session:
        assert session.get(MediaRequest, request_id).status == "queued"


@pytest.mark.parametrize("scope", [{"user": 99}, {"chat_type": "group"}])
def test_unapproved_chat_has_no_command_effect_or_reply(client, scope):
    state, ingest = setup_pipeline(client)
    request_id = seed(state)
    inbox, _ = send(ingest, "/cancel_all", **scope)
    assert reply(state, inbox) is None
    with state.db.sessions() as session:
        assert session.get(MediaRequest, request_id).status == "queued"


def test_current_verified_bot_rename_suffix_and_status_chat_isolation(client):
    state, ingest = setup_pipeline(client)
    runtime = state.runtime
    runtime.remember_identity({"id": 7, "username": "old_bot"})
    runtime.remember_identity({"id": 7, "username": "current_bot"})
    own = seed(state)
    foreign = seed(state, chat=43)
    first, _ = send(ingest, "/status@CURRENT_BOT")
    assert own in reply(state, first) and foreign not in reply(state, first)
    send(ingest, "/cancel_all@old_bot", 101)
    with state.db.sessions() as session:
        assert session.get(MediaRequest, own).status == "queued"
        assert session.get(BotIdentity, 7).username == "current_bot"
    send(ingest, f"/cancel@Current_Bot {own}", 102)
    with state.db.sessions() as session:
        assert session.get(MediaRequest, own).status == "cancelled"
        assert session.get(MediaRequest, foreign).status == "queued"


def test_business_mutation_and_reply_rollback_together(client, monkeypatch):
    state, ingest = setup_pipeline(client)
    first, second = seed(state), seed(state)
    original = state.tasks.cancel_in_session
    count = 0

    def fail(session, request):
        nonlocal count
        result = original(session, request)
        count += 1
        if count == 2:
            raise RuntimeError("injected durable write failure")
        return result

    monkeypatch.setattr(state.tasks, "cancel_in_session", fail)
    with pytest.raises(RuntimeError):
        send(ingest, "/cancel_all")
    with state.db.sessions() as session:
        assert [session.get(MediaRequest, key).status for key in (first, second)] == ["queued", "queued"]
        assert session.scalar(select(func.count()).select_from(Notification)) == 0
    monkeypatch.setattr(state.tasks, "cancel_in_session", original)
    inbox, _ = send(ingest, "/cancel_all")
    assert "已取消 2" in reply(state, inbox)


def test_reply_write_failure_rolls_back_cancellation_and_can_replay(client):
    state, ingest = setup_pipeline(client)
    target = seed(state)

    def fail_reply(mapper, connection, row):
        if row.event_key.startswith("reply:"):
            raise RuntimeError("injected reply write failure")

    event.listen(Notification, "before_insert", fail_reply)
    try:
        with pytest.raises(RuntimeError):
            send(ingest, "/cancel_all")
    finally:
        event.remove(Notification, "before_insert", fail_reply)
    with state.db.sessions() as session:
        assert session.get(MediaRequest, target).status == "queued"
        assert not session.scalar(select(Job.cancel_requested).where(Job.request_id == target))
        assert session.scalar(select(func.count()).select_from(Notification)) == 0
    inbox, _ = send(ingest, "/cancel_all")
    assert "已取消 1" in reply(state, inbox)


async def configured_runtime(client, monkeypatch):
    state, runtime = client.app.state, client.app.state.runtime
    patch = SettingsPatch(revision=state.settings.read()[0], bot_token=TOKEN,
                          bot_api_mode="cloud", allowed_user_ids=[42])
    state.settings.save(patch, state.settings.candidate(patch))
    monkeypatch.setattr(TelegramClient, "identity", AsyncMock(return_value={"id": 7, "username": "fixture_bot"}))
    assert await runtime.ensure_client()
    return state, runtime


async def test_menu_transport_private_scope_and_shared_safety(client, monkeypatch):
    state, runtime = await configured_runtime(client, monkeypatch)
    telegram = runtime.client
    calls = []
    clock = [time.time()]
    runtime.safety.clock = lambda: clock[0]

    async def response(bot, method, timeout=None):
        calls.append(method)
        clock[0] += 10
        return True

    monkeypatch.setattr(telegram.bot.session, "make_request", response)
    runtime.schedule_menu(telegram)
    await runtime.menu_task
    assert runtime.menu_registered
    assert [method.__api_method__ for method in calls] == ["setMyCommands", "setChatMenuButton"]
    assert [(c.command, c.description) for c in calls[0].commands] == list(COMMANDS)
    assert calls[0].scope.type == "all_private_chats" and calls[0].language_code is None
    assert calls[1].menu_button.type == "commands" and calls[1].chat_id is None
    done = runtime.menu_task
    runtime.schedule_menu(telegram)
    assert runtime.menu_task is done
    assert [event["method"] for event in runtime.diagnostics.export()["events"]
            if event["event"] == "telegram_request"] == ["setMyCommands", "setChatMenuButton"]


@pytest.mark.parametrize("failure", ["exception", "cooldown", "auth_paused"])
async def test_menu_failure_keeps_consuming_persisted_commands_and_retries_bounded(client, monkeypatch, failure):
    state, runtime = await configured_runtime(client, monkeypatch)
    target = seed(state)
    runtime.ingest.persist_batch(7, [{"update_id": 100, "message": {
        "message_id": 100, "from": {"id": 42}, "chat": {"id": 42, "type": "private"}, "text": "/cancel_all"}}])
    if failure != "exception":
        runtime.safety.observe(TOKEN, None, retry_after=600 if failure == "cooldown" else None,
                               unauthorized=failure == "auth_paused")
    remote = AsyncMock(side_effect=RuntimeError("menu transport failed"))
    monkeypatch.setattr(runtime.client.bot.session, "make_request", remote)
    updates = AsyncMock(return_value=[])

    async def stop_updates(offset):
        runtime.stopping = True
        return await updates(offset)

    monkeypatch.setattr(runtime.client, "updates", stop_updates)
    await runtime.poll_loop()
    if runtime.menu_task:
        await runtime.menu_task
    assert runtime.ingest.pending(7) == []
    with state.db.sessions() as session:
        assert session.get(MediaRequest, target).status == "cancelled"
    assert not runtime.menu_registered and runtime.menu_retry_at > time.time()
    assert runtime.bot_status["state"] == "online"
    if failure != "exception":
        remote.assert_not_awaited()
    runtime.stopping = False
    task = runtime.menu_task
    runtime.schedule_menu(runtime.client)
    assert runtime.menu_task is task
    updates.assert_awaited_once()


@pytest.mark.parametrize("failed_method", ["setMyCommands", "setChatMenuButton"])
async def test_menu_real_rate_limit_shares_pause_and_recovers_after_backoff(client, monkeypatch, failed_method):
    _, runtime = await configured_runtime(client, monkeypatch)
    telegram = runtime.client
    calls = []
    clock = [time.time()]
    runtime.safety.clock = lambda: clock[0]

    async def response(bot, method, timeout=None):
        calls.append(method.__api_method__)
        clock[0] += 10
        if method.__api_method__ == failed_method:
            raise TelegramRetryAfter(method=method, message="retry", retry_after=600)
        return True

    monkeypatch.setattr(telegram.bot.session, "make_request", response)
    runtime.schedule_menu(telegram)
    await runtime.menu_task
    assert not runtime.menu_registered and runtime.menu_failures == 1
    assert runtime.safety.status(TOKEN)["state"] == "throttled"
    assert runtime.menu_retry_at >= time.time() + 4
    with pytest.raises(SafetyDeferred):
        await telegram.updates(0)
    assert calls == (["setMyCommands"] if failed_method == "setMyCommands" else
                     ["setMyCommands", "setChatMenuButton"])

    async def recovered(bot, method, timeout=None):
        clock[0] += 10
        calls.append(method.__api_method__)
        return True

    monkeypatch.setattr(telegram.bot.session, "make_request", recovered)
    clock[0] += 1000
    runtime.menu_retry_at = 0
    runtime.schedule_menu(telegram)
    await runtime.menu_task
    assert runtime.menu_registered
    assert calls[-2:] == ["setMyCommands", "setChatMenuButton"]


async def test_menu_timeout_is_bounded_and_leaves_retryable_task(client, monkeypatch):
    _, runtime = await configured_runtime(client, monkeypatch)
    monkeypatch.setattr("app.workers.runtime.MENU_TIMEOUT", 0.01)
    waiting = asyncio.Event()

    async def stalled():
        await waiting.wait()

    monkeypatch.setattr(runtime.client, "register_commands", stalled)
    runtime.schedule_menu(runtime.client)
    await runtime.menu_task
    assert runtime.menu_task.done() and not runtime.menu_registered
    assert runtime.menu_failures == 1 and runtime.menu_retry_at > time.time()


async def test_menu_old_result_cannot_register_replacement_and_close_joins_task(client, monkeypatch):
    _, runtime = await configured_runtime(client, monkeypatch)
    old = runtime.client
    entered, release = asyncio.Event(), asyncio.Event()

    async def delayed():
        entered.set()
        await release.wait()

    monkeypatch.setattr(old, "register_commands", delayed)
    runtime.schedule_menu(old)
    await entered.wait()
    replacement = TelegramClient(TOKEN, runtime.config, UserSettings(bot_api_mode="cloud"))
    try:
        runtime.client = runtime.verified_client = replacement
        release.set()
        await runtime.menu_task
        assert not runtime.menu_registered
        operation = AsyncMock(return_value=None)
        monkeypatch.setattr(replacement, "register_commands", operation)
        runtime.schedule_menu(replacement)
        await runtime.menu_task
        assert runtime.menu_registered and runtime.menu_client is replacement
        operation.assert_awaited_once()
        runtime.menu_registered = False
        monkeypatch.setattr(replacement, "register_commands", delayed)
        release.clear()
        runtime.schedule_menu(replacement)
        await asyncio.sleep(0)
        await runtime.close()
        assert runtime.menu_task.done()
    finally:
        await old.close()
        await replacement.close()


@pytest.mark.parametrize("when", ["before_start", "before_completion"])
@pytest.mark.parametrize("change", ["credential", "transition"])
async def test_menu_requires_current_saved_connection_before_send_and_success(client, monkeypatch, when, change):
    state, runtime = await configured_runtime(client, monkeypatch)
    telegram = runtime.client
    entered, release = asyncio.Event(), asyncio.Event()

    async def delayed():
        entered.set()
        await release.wait()

    operation = AsyncMock(side_effect=delayed)
    monkeypatch.setattr(telegram, "register_commands", operation)

    def change_connection():
        if change == "credential":
            patch = SettingsPatch(revision=state.settings.read()[0],
                                  bot_token="987654321:BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB")
            state.settings.save(patch, state.settings.candidate(patch))
        else:
            runtime.local_api.update(phase="draining")

    if when == "before_start":
        change_connection()
    runtime.schedule_menu(telegram)
    if when == "before_completion":
        await entered.wait()
        change_connection()
        release.set()
    await runtime.menu_task
    assert not runtime.menu_registered
    assert runtime.client is telegram  # Still the same object, but its ownership is stale.
    if when == "before_start":
        operation.assert_not_awaited()
    else:
        operation.assert_awaited_once()
