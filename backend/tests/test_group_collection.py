import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from test_pipeline import message, setup_pipeline

from app.bot.ingest import IngestService
from app.db.models import Job, MediaItem, MediaRequest, Notification


def clock(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    now = [time.time()]
    fake = SimpleNamespace(time=lambda: now[0])
    monkeypatch.setattr("app.bot.ingest.time", fake)
    monkeypatch.setattr("app.services.tasks.time", fake)
    return now


def photo(index: int, group: str) -> dict:
    payload = message(index, group)
    payload["photo"] = [payload.pop("video") | {"width": 100, "height": 100}]
    return payload


def test_mixed_group_across_delayed_polls_freezes_one_five_file_card(
    client: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    state, ingest = setup_pipeline(client)
    now = clock(monkeypatch)
    started = now[0]

    def process(updates: list[dict]) -> None:
        ingest.persist_batch(7, updates)
        for inbox_id, update in ingest.pending(7):
            ingest.message(7, update["message"], inbox_id)
            ingest.mark_processed(inbox_id)

    process([{"update_id": n, "message": photo(n, "mixed")} for n in (1, 2)])
    now[0] = started + 1.11  # Actual split: pictures first, videos in the next poll.
    state.tasks.freeze_due()
    with state.db.sessions() as session:
        assert session.scalar(select(func.count()).select_from(Notification)) == 0
    ingest = IngestService(state.db, state.settings, state.tasks)  # Collection survives restart.
    process([{"update_id": n, "message": message(n, "mixed")} for n in (3, 4, 5)])
    process([{"update_id": 5, "message": message(5, "mixed")}])  # Durable replay.
    now[0] = started + 4.99
    state.tasks.freeze_due()
    with state.db.sessions() as session:
        assert session.scalar(select(func.count()).select_from(Notification)) == 0
    now[0] = started + 5
    state.tasks.freeze_due()
    with state.db.sessions() as session:
        requests = list(session.scalars(select(MediaRequest)))
        assert len(requests) == 1
        request = requests[0]
        request_id, nonce = request.id, request.nonce
        items = list(session.scalars(select(MediaItem)))
        assert [item.media_type for item in items].count("image") == 2
        assert [item.media_type for item in items].count("video") == 3
        assert all(item.planned_filename for item in items)
        notices = list(session.scalars(select(Notification)))
        assert len(notices) == 1 and notices[0].payload["confirmation"]
    assert state.tasks.confirm(request_id, nonce, 42, 42, "confirm") == "queued"
    detail = state.tasks.detail(request_id)
    assert detail["item_count"] == 5
    assert len({item["relative_path"].rsplit("/", 1)[0] for item in detail["items"]}) == 1
    with state.db.sessions() as session:
        assert session.scalar(select(func.count()).select_from(Job)) == 5
    ingest.message(7, message(6, "mixed"), "late")
    assert state.tasks.detail(request_id)["item_count"] == 5  # Frozen consent never grows.


def test_quiet_window_extends_but_total_collection_is_bounded(
    client: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    state, ingest = setup_pipeline(client)
    now = clock(monkeypatch)
    started = now[0]
    ingest.message(7, message(1, "g"), "one")
    for index, elapsed in enumerate((4, 6, 8, 10, 12, 14), start=2):
        now[0] = started + elapsed
        ingest.message(7, message(index, "g"), str(index))
        state.tasks.freeze_due()
        with state.db.sessions() as session:
            request = session.scalar(select(MediaRequest))
            assert request.status == "collecting"
            assert request.collect_until == started + min(15, elapsed + 3)
    now[0] = started + 15
    state.tasks.freeze_due()
    with state.db.sessions() as session:
        assert session.scalar(select(MediaRequest)).status == "awaiting_confirmation"
        assert session.scalar(select(func.count()).select_from(Notification)) == 1


def test_different_groups_chats_bots_and_singles_remain_separate(
    client: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    state, ingest = setup_pipeline(client)
    clock(monkeypatch)
    ingest.message(7, message(1, "one"), "one")
    ingest.message(7, message(2, "two"), "two")
    ingest.message(8, message(3, "one"), "other-bot")
    other = message(4, "one")
    other["chat"]["id"] = 43
    ingest.message(7, other, "other-chat")
    ingest.message(7, message(5), "single")
    ingest.message(7, message(6), "single2")
    state.tasks.freeze_due()
    with state.db.sessions() as session:
        requests = list(session.scalars(select(MediaRequest)))
        assert len(requests) == 6
        assert sum(r.status == "collecting" for r in requests) == 4
        assert sum(r.status == "awaiting_confirmation" for r in requests) == 2


def test_unfrozen_group_accepts_late_processed_member_until_hard_cap(
    client: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    state, ingest = setup_pipeline(client)
    now = clock(monkeypatch)
    ingest.message(7, message(1, "g"), "one")
    now[0] += 6
    ingest.message(7, message(2, "g"), "two")
    with state.db.sessions() as session:
        request = session.scalar(select(MediaRequest))
        assert request.collect_until == now[0] + 3
        assert session.scalar(select(func.count()).select_from(MediaRequest)) == 1
    now[0] += 9
    ingest.message(7, message(3, "g"), "three")
    with state.db.sessions() as session:
        assert session.scalar(select(func.count()).select_from(MediaRequest)) == 2


def test_pending_inbox_defers_only_its_bot_groups_until_hard_cap(
    client: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    state, ingest = setup_pipeline(client)
    now = clock(monkeypatch)
    started = now[0]
    ingest.message(7, message(1, "g"), "first")
    ingest.message(8, message(2, "g"), "other-bot")
    ingest.persist_batch(7, [{"update_id": 30, "message": message(3, "g")}])
    ingest.message(7, message(4), "single")
    state.tasks.freeze_due()
    with state.db.sessions() as session:
        single = session.scalar(select(MediaRequest).where(MediaRequest.kind == "single"))
        assert single.status == "awaiting_confirmation"  # No delay for singles, even with backlog.
    now[0] = started + 5
    state.tasks.freeze_due()
    with state.db.sessions() as session:
        requests = {r.bot_id: r for r in session.scalars(select(MediaRequest).where(MediaRequest.kind == "group"))}
        assert requests[7].status == "collecting"
        assert requests[8].status == "awaiting_confirmation"
    # Pending work may be unrelated or stalled, but cannot prevent consent forever.
    now[0] = started + 15
    state.tasks.freeze_due()
    with state.db.sessions() as session:
        assert all(r.status == "awaiting_confirmation" for r in session.scalars(select(MediaRequest)))


def test_direct_duplicate_does_not_extend_collection_deadline(
    client: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    state, ingest = setup_pipeline(client)
    now = clock(monkeypatch)
    started = now[0]
    ingest.message(7, photo(1, "g"), "first")
    now[0] += 4
    ingest.message(7, photo(1, "g"), "replayed")
    with state.db.sessions() as session:
        assert session.scalar(select(MediaRequest)).collect_until == started + 5
        assert session.scalar(select(func.count()).select_from(MediaItem)) == 1
