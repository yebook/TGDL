"""Forwarding comments are silent, one-shot names, never an implicit media group."""
import time
from pathlib import PurePosixPath

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.bot.ingest import IngestService
from app.db.models import MediaItem, MediaRequest, Notification
from tests.test_pipeline import confirm_all, message, setup_pipeline


def comment(index: int = 1, text: str = "海边旅行") -> dict:
    return {"message_id": index, "from": {"id": 42},
            "chat": {"id": 42, "type": "private"}, "text": text}


@pytest.mark.parametrize("group", [None, "trip"])
def test_comment_overrides_original_and_caption_once(client: TestClient, group: str | None) -> None:
    state, ingest = setup_pipeline(client)
    ingest.message(7, comment(), "name")
    with state.db.sessions() as session:
        assert list(session.scalars(select(Notification))) == []
    # A new ingest instance demonstrates durable association across intake restarts.
    ingest = IngestService(state.db, state.settings, state.tasks)
    for index in ([2, 3] if group else [2]):
        incoming = message(index, group)
        incoming["caption"] = "原始频道文字"
        ingest.message(7, incoming, f"media{index}")
    request_id = confirm_all(state)
    detail = state.tasks.detail(request_id)
    assert detail["title"] == "海边旅行"
    with state.db.sessions() as session:
        items = list(session.scalars(select(MediaItem).where(
            MediaItem.request_id == request_id).order_by(MediaItem.message_id)))
        assert [item.planned_filename for item in items] == (
            ["海边旅行.mp4", "海边旅行_2.mp4"] if group else ["海边旅行.mp4"])
        assert items[0].original_name == "movie2.mp4"
        assert items[0].caption_snippet == "原始频道文字"
        card = session.scalar(select(Notification).where(Notification.event_key.like("confirm:%")))
        assert "确认下载：海边旅行" in card.payload["text"]
        request = session.get(MediaRequest, request_id)
        if group:
            assert PurePosixPath(request.destination).name == "海边旅行"
    # Replayed comment cannot resurrect the consumed name, even with a different inbox ID.
    ingest.message(7, comment(), "name-replayed")
    ingest.message(7, message(4), "next")
    state.tasks.freeze_due()
    with state.db.sessions() as session:
        latest = session.scalar(select(MediaRequest).where(MediaRequest.id != request_id))
        assert latest.name_override is None
        assert latest.title == "movie4.mp4"


@pytest.mark.parametrize("interruption", ["/start", "/help", "/status", "https://t.me/channel/1"])
def test_commands_and_links_clear_names_and_still_reply(client: TestClient, interruption: str) -> None:
    state, ingest = setup_pipeline(client)
    ingest.message(7, comment(), "name")
    ingest.message(7, comment(2, interruption), "command")
    ingest.message(7, message(3), "media")
    state.tasks.freeze_due()
    with state.db.sessions() as session:
        request = session.scalar(select(MediaRequest))
        assert request.name_override is None
        assert request.title == "movie3.mp4"
        assert session.scalar(select(Notification).where(Notification.event_key == "reply:command")) is not None


def test_latest_text_wins_and_other_scopes_cannot_consume(client: TestClient) -> None:
    state, ingest = setup_pipeline(client)
    ingest.message(7, comment(), "first")
    ingest.message(7, comment(2, "新名称"), "second")
    ingest.message(7, comment(), "late-replay")
    unauthorized = comment(3, "未经授权")
    unauthorized["from"]["id"] = 43
    ingest.message(7, unauthorized, "unauthorized")
    ingest.message(8, message(4), "other-bot")
    other_chat = message(5)
    other_chat["chat"]["id"] = 99
    ingest.message(7, other_chat, "other-chat")
    ingest.message(7, message(6), "own-media")
    with state.db.sessions() as session:
        rows = list(session.scalars(select(MediaRequest).order_by(MediaRequest.created_at)))
        assert [row.name_override for row in rows] == [None, None, "新名称"]


def test_forwarded_text_is_not_a_name_but_old_plain_text_has_no_expiry(client: TestClient) -> None:
    state, ingest = setup_pipeline(client)
    forwarded = comment()
    forwarded["forward_origin"] = {"type": "channel"}
    ingest.message(7, forwarded, "forwarded")
    ingest.message(7, message(2), "first-media")
    old = comment(3)
    old["date"] = int(time.time()) - 180
    ingest.message(7, old, "old-name")
    ingest.message(7, old, "old-name-replayed")
    ingest.message(7, message(4), "second-media")
    with state.db.sessions() as session:
        rows = list(session.scalars(select(MediaRequest).order_by(MediaRequest.created_at)))
        assert [row.name_override for row in rows] == [None, "海边旅行"]
        assert session.scalar(select(Notification).where(Notification.event_key == "reply:old-name")) is None


def test_name_for_next_group_does_not_rename_group_being_collected(client: TestClient) -> None:
    state, ingest = setup_pipeline(client)
    ingest.message(7, message(1, "first"), "first-media")
    ingest.message(7, comment(2, "下一组"), "name")
    ingest.message(7, message(3, "first"), "first-sibling")
    ingest.message(7, message(4, "second"), "second-media")
    with state.db.sessions() as session:
        rows = list(session.scalars(select(MediaRequest).order_by(MediaRequest.created_at)))
        assert [(row.group_key, row.name_override) for row in rows] == [("first", None), ("second", "下一组")]


def test_durable_inbox_orders_comment_before_media_and_sanitizes_paths(client: TestClient) -> None:
    state, ingest = setup_pipeline(client)
    ingest.persist_batch(7, [{"update_id": 102, "message": message(2)},
                             {"update_id": 101, "message": comment(1, "../旅行/片段")}])
    for inbox_id, update in ingest.pending(7):
        ingest.message(7, update["message"], inbox_id)
        ingest.mark_processed(inbox_id)
    request_id = confirm_all(state)
    with state.db.sessions() as session:
        request = session.get(MediaRequest, request_id)
        item = session.scalar(select(MediaItem))
        assert request.title == "../旅行/片段"
        assert "旅行" in item.planned_filename
        assert "/" not in item.planned_filename and "\\" not in item.planned_filename
        assert item.planned_filename.endswith(".mp4")
