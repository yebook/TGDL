import time
from datetime import UTC, datetime
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from test_pipeline import message, setup_pipeline

from app.bot.client import TelegramClient
from app.bot.messages import STATUS_LABELS
from app.core.errors import AppError
from app.db.models import Job, MediaItem, MediaRequest, Notification
from app.schemas.contracts import SettingsPatch
from app.services.tasks import project


def pending_request(client: TestClient):
    state, ingest = setup_pipeline(client)
    ingest.message(7, message(), "in")
    state.tasks.freeze_due()
    with state.db.sessions() as session:
        request = session.scalar(select(MediaRequest))
        payload = {"id": "query", "from": {"id": 42}, "message": {"chat": {"id": 42}},
                   "data": f"y:{request.id}:{request.nonce}"}
        return state, ingest, request.id, payload


def test_confirmation_feedback_is_idempotent_and_cancel_is_truthful(client: TestClient) -> None:
    state, ingest, request_id, payload = pending_request(client)
    assert ingest.callback(payload) == "⏳ 已加入下载队列，请等待下载结果"
    assert ingest.callback(payload) == "⏳ 已加入下载队列，请等待下载结果"
    payload["data"] = payload["data"].replace("y:", "n:", 1)
    assert "如需取消，请前往 WebUI 操作" in ingest.callback(payload)
    assert state.tasks.detail(request_id)["status"] == "queued"
    with state.db.sessions() as session:
        assert session.scalar(select(func.count()).select_from(Job)) == 1
    for status, expected in [("fetching", "正在下载"), ("saving", "正在保存"),
                             ("succeeded", "下载成功"), ("failed", "下载失败"),
                             ("already_exists", "文件已保存"), ("partial_success", "部分文件已保存"),
                             ("retry_wait", "自动重试"), ("recovery_blocked", "等待恢复"),
                             ("waiting_existing", "相同文件"), ("cancelling", "正在取消")]:
        with state.db.transaction() as session:
            session.get(MediaRequest, request_id).status = status
        for action in ("y", "n"):
            payload["data"] = action + payload["data"][1:]
            assert expected in ingest.callback(payload)


@pytest.mark.parametrize("command", ["/start", "/help", "/status"])
def test_friendly_commands_preserve_single_reply(client: TestClient, command: str) -> None:
    state, ingest = setup_pipeline(client)
    incoming = {"message_id": 1, "from": {"id": 42}, "chat": {"id": 42, "type": "private"}, "text": command}
    ingest.message(7, incoming, "command")
    ingest.message(7, incoming, "command")
    with state.db.sessions() as session:
        notes = list(session.scalars(select(Notification)))
        assert len(notes) == 1
        text = notes[0].payload["text"]
        assert ("📭 暂无下载任务" if command == "/status" else "📥 发送或转发") in text


def test_status_translates_states_and_keeps_full_ids(client: TestClient) -> None:
    state, ingest = setup_pipeline(client)
    with state.db.transaction() as session:
        for number, status in enumerate(["queued", "saving", "retry_wait", "partial_success", "already_exists"]):
            session.add(MediaRequest(id=f"friendly-{number}", bot_id=7, user_id=42, chat_id=42,
                                     title="🌅" * 80, status=status, collect_until=0))
    ingest.message(7, {"message_id": 1, "from": {"id": 42}, "chat": {"id": 42, "type": "private"},
                       "text": "/status"}, "status")
    with state.db.sessions() as session:
        text = session.scalar(select(Notification)).payload["text"]
    for number, status in enumerate(["queued", "saving", "retry_wait", "partial_success", "already_exists"]):
        assert STATUS_LABELS[status] in text
        assert f"任务编号：friendly-{number}" in text
        assert status not in text
    assert len(text.encode("utf-16-le")) // 2 <= 3900
    assert "\ufffd" not in text


@pytest.mark.parametrize("states,expected", [
    (["succeeded", "failed"], "⚠️ 部分完成"),
    (["failed", "failed"], "❌ 下载失败"),
    (["already_exists", "already_exists"], "✅ 文件已保存，无需重复下载"),
    (["cancelled", "cancelled"], "❌ 已取消"),
    (["succeeded", "succeeded"], "✅ 下载完成"),
])
def test_result_messages_have_friendly_states_and_safe_reasons(client: TestClient, states: list[str], expected: str) -> None:
    state, _, request_id, payload = pending_request(client)
    state.tasks.confirm(request_id, payload["data"].split(":")[2], 42, 42, "confirm")
    with state.db.transaction() as session:
        first = session.scalar(select(Job))
        first.state = states[0]
        second = MediaItem(request_id=request_id, bot_id=7, chat_id=42, message_id=2,
                           file_id="synthetic", media_type="video")
        session.add(second)
        session.flush()
        session.add(Job(request_id=request_id, item_id=second.id, state=states[1],
                        relative_path="day/two.mp4", error_code="CLOUD_FILE_TOO_LARGE",
                        error_message="secret-raw-upstream-error"))
        session.flush()
        project(session, session.get(MediaRequest, request_id))
    with state.db.sessions() as session:
        text = session.scalar(select(Notification).where(Notification.event_key.like("result:%"))).payload["text"]
    assert text.startswith(expected)
    assert text.endswith(f"\n任务编号：{request_id}")
    assert "secret-raw-upstream-error" not in text
    for status in states:
        assert status not in text
    if states[1] == "failed":
        assert "20 MB" in text and "切换本地 API" in text
        assert "已保存的文件会保留" in text


async def test_confirmation_buttons_keep_protocol_and_plain_text() -> None:
    telegram = object.__new__(TelegramClient)
    telegram.safety = None
    telegram.bot = Mock()
    telegram.bot.send_message = AsyncMock(return_value=Mock(message_id=123))
    assert await telegram.send(42, "📥 <标题>", ("task", "nonce")) == 123
    args, kwargs = telegram.bot.send_message.call_args
    assert args == (42, "📥 <标题>")
    assert "parse_mode" not in kwargs
    buttons = kwargs["reply_markup"].inline_keyboard[0]
    assert [(b.text, b.callback_data) for b in buttons] == [
        ("✅ 确认下载", "y:task:nonce"), ("❌ 取消", "n:task:nonce")]


@pytest.mark.parametrize("expired", [False, True])
def test_cancel_and_both_expired_buttons_create_no_jobs(client: TestClient, expired: bool) -> None:
    state, ingest, request_id, payload = pending_request(client)
    if expired:
        with state.db.transaction() as session:
            session.get(MediaRequest, request_id).expires_at = time.time() - 1
    else:
        payload["data"] = "n" + payload["data"][1:]
    expected = "确认已过期" if expired else "该请求已取消"
    for action in (payload["data"][0], "n", "y"):
        payload["data"] = action + payload["data"][1:]
        assert expected in ingest.callback(payload)
    assert state.tasks.detail(request_id)["status"] == ("expired" if expired else "cancelled")
    with state.db.sessions() as session:
        assert session.scalar(select(func.count()).select_from(Job)) == 0


def test_invalid_or_unauthorized_buttons_never_mutate_request(client: TestClient) -> None:
    state, ingest, request_id, payload = pending_request(client)
    original = payload["data"]
    for invalid in ("", "y::", "oops", "z:a:b"):
        with pytest.raises(AppError, match="按钮已失效"):
            ingest.callback({**payload, "data": invalid})
    for change in ({"from": {"id": 43}}, {"message": {"chat": {"id": 43}}},
                   {"data": original + "forged"}, {"data": "y:unknown:nonce"}):
        with pytest.raises(AppError, match="获授权的账号"):
            ingest.callback({**payload, **change})
    with pytest.raises(AppError, match="按钮已失效"):
        ingest.callback({**payload, "from": {"id": "invalid"}})
    assert state.tasks.detail(request_id)["status"] == "awaiting_confirmation"
    with state.db.sessions() as session:
        assert session.scalar(select(func.count()).select_from(Job)) == 0


async def test_callback_transport_uses_visible_alert() -> None:
    telegram = object.__new__(TelegramClient)
    telegram.safety = None
    telegram.bot = Mock()
    telegram.bot.answer_callback_query = AsyncMock()
    await telegram.answer("query", "确认已过期，请重新发送媒体")
    telegram.bot.answer_callback_query.assert_awaited_once_with(
        "query", text="确认已过期，请重新发送媒体", show_alert=True, request_timeout=15)


@pytest.mark.parametrize("data", ["expired", "malformed", "unauthorized"])
async def test_poll_loop_acknowledges_expired_and_rejected_clicks(client: TestClient, data: str) -> None:
    state, ingest, request_id, payload = pending_request(client)
    with state.db.transaction() as session:
        session.get(MediaRequest, request_id).expires_at = time.time() - 1
    if data == "malformed":
        payload["data"] = "invalid"
    elif data == "unauthorized":
        payload["from"] = {"id": 43}
    patch = SettingsPatch(revision=2, bot_token="777777:synthetic-feedback-test")
    state.settings.save(patch, state.settings.candidate(patch))
    runtime = state.runtime
    runtime.ensure_client = AsyncMock()
    runtime.client = Mock()
    runtime.client.answer = AsyncMock()
    runtime.bot_id = 7
    runtime.client_signature = state.settings.client_signature(state.settings.read()[1])
    ingest.persist_batch(7, [{"update_id": 1, "callback_query": payload}])

    async def finish(offset):
        runtime.stopping = True
        return []

    runtime.client.updates = AsyncMock(side_effect=finish)
    await runtime.poll_loop()
    runtime.client.answer.assert_awaited_once()
    query_id, text = runtime.client.answer.call_args.args
    assert query_id == "query"
    assert {"expired": "确认已过期", "malformed": "按钮已失效", "unauthorized": "获授权的账号"}[data] in text
    assert ingest.pending(7) == []


@pytest.mark.parametrize(("kind", "media"), [
    ("single", "video"), ("single", "document"), ("group", "video")])
def test_daily_destination_uses_creation_time_and_timezone(client: TestClient, kind: str, media: str) -> None:
    state, ingest = setup_pipeline(client)
    item = message(group="g" if kind == "group" else None, kind=media)
    if media == "document":
        item[media]["file_name"] = "picture.png"
    ingest.message(7, item, "in")
    if kind == "group":
        other = message(2, "g", "document")
        other["document"]["file_name"] = "picture.png"
        ingest.message(7, other, "in2")
    # At this instant UTC is still the previous month; Shanghai has reached November.
    created = datetime(2026, 10, 31, 18, 30, tzinfo=UTC).timestamp()
    with state.db.transaction() as session:
        request = session.scalar(select(MediaRequest))
        request.created_at, request.collect_until = created, 0
        request_id, nonce = request.id, request.nonce
    state.tasks.freeze_due()
    destination = state.tasks.detail(request_id)["effective_destination"]
    assert destination == ("2026-11-01/movie1.mp4"
                           if kind == "group" else "2026-11-01")
    patch = SettingsPatch(revision=2, timezone="UTC")
    state.settings.save(patch, state.settings.candidate(patch))
    assert state.tasks.confirm(request_id, nonce, 42, 42, "confirm") == "queued"
    assert state.tasks.detail(request_id)["effective_destination"] == destination
    assert all(item["relative_path"].startswith(destination + "/")
               for item in state.tasks.detail(request_id)["items"])


def test_frozen_daily_path_survives_confirmation_and_retry(client: TestClient) -> None:
    state, _, request_id, payload = pending_request(client)
    with state.db.transaction() as session:
        session.get(MediaRequest, request_id).destination = "2026-10-21"
    nonce = payload["data"].split(":")[2]
    state.tasks.confirm(request_id, nonce, 42, 42, "confirm")
    path = state.tasks.detail(request_id)["items"][0]["relative_path"]
    with state.db.transaction() as session:
        job = session.scalar(select(Job))
        job.state = "failed"
        session.get(MediaRequest, request_id).status = "failed"
    state.tasks.retry(request_id, None)
    assert state.tasks.detail(request_id)["items"][0]["relative_path"] == path
    assert path.startswith("2026-10-21/")
