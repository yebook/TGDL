from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from threading import Barrier

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db.models import Job, MediaItem, MediaRequest, Notification
from app.services.tasks import project, task_message
from app.storage.naming import fit_name, text_summary
from tests.test_pipeline import confirm_all, message, setup_pipeline


@pytest.mark.parametrize("caption,original,expected", [
    ("  春游\n\t海边\u202e\x00记录 ", "movie.mp4", "春游 海边 记录"),
    (None, "movie.mp4", "movie.mp4"), (" \n", None, "视频"),
    ("景" * 100, "movie.mp4", "景" * 79 + "…"),
    ("保留原文 #abcdef01", "movie.mp4", "保留原文 #abcdef01"),
])
def test_single_title_and_all_notifications_share_snapshot(client: TestClient, caption, original, expected) -> None:
    state, ingest = setup_pipeline(client)
    media = message()
    media["caption"], media["video"]["file_name"] = caption, original
    ingest.message(7, media, "in")
    request_id = confirm_all(state)
    detail = state.tasks.detail(request_id)
    title = expected
    assert detail["title"] == title
    assert state.tasks.list_tasks(None, None, 1, 20)["items"][0]["title"] == title
    assert len(Path(detail["items"][0]["relative_path"]).parts) == 2
    with state.db.transaction() as session:
        request = session.get(MediaRequest, request_id)
        assert session.scalar(select(MediaItem)).caption_snippet == (text_summary(caption) or None)
        for job in session.scalars(select(Job)):
            job.state = "succeeded"
        project(session, request)
    with state.db.sessions() as session:
        notifications = list(session.scalars(select(Notification)))
        assert len(notifications) == 3
        assert all(title in note.payload["text"] for note in notifications)
        assert all(note.payload["text"].splitlines().count(f"任务编号：{request_id}") == 1
                   for note in notifications)
    ingest.message(7, {"from": {"id": 42}, "chat": {"id": 42, "type": "private"}, "message_id": 20, "text": "/status"}, "status")
    with state.db.sessions() as session:
        assert f"任务编号：{request_id}" in session.scalar(
            select(Notification).where(Notification.event_key == "reply:status")).payload["text"]


def test_group_caption_order_survives_restart_and_freeze_is_stable(client: TestClient) -> None:
    state, ingest = setup_pipeline(client)
    for index, caption in [(3, "到达较早"), (1, "\n"), (2, "海边 / 日落")]:
        media = message(index, "g")
        media["caption"] = caption
        ingest.message(7, media, f"in{index}")
    state.db.engine.dispose()
    request_id = confirm_all(state)
    detail = state.tasks.detail(request_id)
    assert detail["title"] == "海边 / 日落"
    destination = detail["effective_destination"]
    assert destination.split("/")[1] == "海边 _ 日落"
    assert all(str(Path(item["relative_path"]).parent).replace("\\", "/") == destination for item in detail["items"])
    paths = [item["relative_path"] for item in detail["items"]]
    with state.db.transaction() as session:
        for job in session.scalars(select(Job)):
            job.state = "failed"
    state.tasks.retry(request_id, None)
    assert [item["relative_path"] for item in state.tasks.detail(request_id)["items"]] == paths
    with state.db.transaction() as session:
        request = session.get(MediaRequest, request_id)
        request.status, request.collect_until = "collecting", 0
        request.nonce = "new-card"
        for item in session.scalars(select(MediaItem)):
            item.caption_snippet = "不应改名"
    state.tasks.freeze_due()
    assert state.tasks.detail(request_id)["title"] == detail["title"]
    assert state.tasks.detail(request_id)["effective_destination"] == destination


@pytest.mark.parametrize("summary", ["CON", "../a\\b:*?", "图" * 80, ". "])
def test_group_directory_safety(summary: str) -> None:
    name = fit_name(summary, "")
    assert len(name.encode("utf-8")) <= 180
    assert not any(char in name for char in '<>:"/\\|?*')
    assert ".." not in name and name.split(".")[0] != "CON"


def test_same_caption_ids_are_distinct(client: TestClient) -> None:
    state, ingest = setup_pipeline(client)
    for index in (1, 2):
        media = message(index)
        media["caption"] = "相同名称"
        ingest.message(7, media, f"in{index}")
    confirm_all(state)
    rows = state.tasks.list_tasks(None, None, 1, 20)["items"]
    assert [row["title"] for row in rows] == ["相同名称", "相同名称"]
    assert len({row["id"] for row in rows}) == 2


def test_group_names_reserve_transaction_deleted_records_and_existing_files(client: TestClient) -> None:
    state, ingest = setup_pipeline(client)
    for index in (1, 2):
        media = message(index, f"group{index}")
        media["caption"] = "同名"
        ingest.message(7, media, f"in{index}")
    confirm_all(state)
    with state.db.transaction() as session:
        rows = list(session.scalars(select(MediaRequest).order_by(MediaRequest.created_at)))
        destinations = [row.destination for row in rows]
        assert [Path(path).name for path in destinations] == ["同名", "同名 (2)"]
        for row in rows:
            row.deleted_at = 1
    day = Path(destinations[0]).parent
    root = state.runtime.config.download_root
    preserved = root / destinations[0] / "keep.mp4"
    preserved.parent.mkdir(parents=True)
    preserved.write_bytes(b"retained")
    # An ordinary pre-existing file also occupies a potential directory name.
    (root / day / "同名 (3)").write_bytes(b"existing")
    media = message(3, "group3")
    media["caption"] = "同名"
    ingest.message(7, media, "in3")
    request_id = confirm_all(state)
    assert Path(state.tasks.detail(request_id)["effective_destination"]).name == "同名 (4)"
    assert preserved.read_bytes() == b"retained"
    assert (root / day / "同名 (3)").read_bytes() == b"existing"
    with state.db.sessions() as session:
        assert [session.get(MediaRequest, row.id).destination for row in rows] == destinations


def test_group_folder_respects_unconfirmed_single_and_root_day_scope(client: TestClient) -> None:
    state, ingest = setup_pipeline(client)
    ingest.message(7, message(), "single")
    with state.db.transaction() as session:
        single = session.scalar(select(MediaRequest))
        single.created_at = datetime(2026, 10, 20, 12, tzinfo=UTC).timestamp()
        single.collect_until = 0
    state.tasks.freeze_due()
    with state.db.sessions() as session:
        planned = session.scalar(select(MediaItem)).planned_filename
        assert session.scalar(select(MediaRequest)).status == "awaiting_confirmation"
        assert session.scalar(select(Job)) is None
    for index in (2, 3):
        media = message(index, f"g{index}")
        media["caption"] = planned
        ingest.message(7, media, f"in{index}")
    with state.db.transaction() as session:
        groups = list(session.scalars(select(MediaRequest).where(MediaRequest.kind == "group")))
        groups[0].created_at = single.created_at
        groups[1].created_at = single.created_at + 86400
        for request in groups:
            request.collect_until = 0
        # A different storage root must not reserve names in the selected root.
        session.add(MediaRequest(bot_id=7, chat_id=42, user_id=42, kind="group", collect_until=0,
                                 status="succeeded", root_id="other", destination=f"2026-10-21/{planned}"))
    state.tasks.freeze_due()
    with state.db.sessions() as session:
        assert session.get(MediaRequest, groups[0].id).destination == f"2026-10-20/{planned} (2)"
        assert session.get(MediaRequest, groups[1].id).destination == f"2026-10-21/{planned}"


def test_concurrent_freezing_reserves_distinct_folders_and_one_card_per_request(client: TestClient) -> None:
    state, ingest = setup_pipeline(client)
    for index in (1, 2):
        media = message(index, f"g{index}")
        media["caption"] = "shared"
        ingest.message(7, media, f"in{index}")
    with state.db.transaction() as session:
        for request in session.scalars(select(MediaRequest)):
            request.collect_until = 0
    ready = Barrier(2)

    def freeze() -> None:
        ready.wait(timeout=5)
        state.tasks.freeze_due()

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(freeze) for _ in range(2)]
        for future in futures:
            future.result(timeout=15)
    with state.db.sessions() as session:
        requests = list(session.scalars(select(MediaRequest)))
        assert {Path(request.destination).name for request in requests} == {"shared", "shared (2)"}
        assert all(request.status == "awaiting_confirmation" for request in requests)
        assert len(list(session.scalars(select(Notification)))) == 2


def test_task_message_reserves_identifier_after_long_astral_content() -> None:
    request_id = "0123456789abcdef" * 2
    text = task_message("😀" * 4000, request_id)
    assert len(text.encode("utf-16-le")) // 2 <= 3900
    assert text.endswith(f"…\n任务编号：{request_id}")


def test_status_keeps_all_five_full_identifiers_and_frozen_title(client: TestClient) -> None:
    state, ingest = setup_pipeline(client)
    for index in range(1, 6):
        ingest.message(7, message(index), f"in{index}")
    confirm_all(state)
    with state.db.transaction() as session:
        rows = list(session.scalars(select(MediaRequest)))
        ids = [row.id for row in rows]
        rows[0].title = "春日海边记录"
        for row in rows[1:]:
            row.title = "😀" * 1000
        snapshots = [(row.id, row.title, row.destination) for row in rows]
        cards = [note.payload for note in session.scalars(select(Notification))]
        rows[0].status, rows[0].collect_until = "collecting", 0
    state.tasks.freeze_due()
    with state.db.sessions() as session:
        assert [(row.id, row.title, row.destination) for row in session.scalars(select(MediaRequest))] == snapshots
        assert [note.payload for note in session.scalars(select(Notification))][:len(cards)] == cards
    ingest.message(7, {"from": {"id": 42}, "chat": {"id": 42, "type": "private"}, "message_id": 20, "text": "/status"}, "status")
    with state.db.sessions() as session:
        text = session.scalar(select(Notification).where(Notification.event_key == "reply:status")).payload["text"]
        assert len(text.encode("utf-16-le")) // 2 <= 3900
        assert all(text.splitlines().count(f"任务编号：{request_id}") == 1 for request_id in ids)

