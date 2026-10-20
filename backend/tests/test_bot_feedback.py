import time
from datetime import UTC, datetime
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from test_pipeline import message, setup_pipeline

from app.bot.client import TelegramClient
from app.core.errors import AppError
from app.db.models import Job, MediaRequest
from app.schemas.contracts import SettingsPatch


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
    assert ingest.callback(payload) == "已加入下载队列，请等待下载结果"
    assert ingest.callback(payload) == "已加入下载队列，请等待下载结果"
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
    assert destination == (f"2026-11-01/movie1.mp4_{request_id[:8]}"
                           if kind == "group" else "2026-11-01")
    patch = SettingsPatch(revision=2, timezone="UTC")
    state.settings.save(patch, state.settings.candidate(patch))
    assert state.tasks.confirm(request_id, nonce, 42, 42, "confirm") == "queued"
    assert state.tasks.detail(request_id)["effective_destination"] == destination
    assert all(item["relative_path"].startswith(destination + "/")
               for item in state.tasks.detail(request_id)["items"])


def test_frozen_legacy_monthly_path_survives_confirmation_and_retry(client: TestClient) -> None:
    state, _, request_id, payload = pending_request(client)
    with state.db.transaction() as session:
        session.get(MediaRequest, request_id).destination = "videos/2026-10"
    nonce = payload["data"].split(":")[2]
    state.tasks.confirm(request_id, nonce, 42, 42, "confirm")
    path = state.tasks.detail(request_id)["items"][0]["relative_path"]
    with state.db.transaction() as session:
        job = session.scalar(select(Job))
        job.state = "failed"
        session.get(MediaRequest, request_id).status = "failed"
    state.tasks.retry(request_id, None)
    assert state.tasks.detail(request_id)["items"][0]["relative_path"] == path
    assert path.startswith("videos/2026-10/")
