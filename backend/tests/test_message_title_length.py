from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db.models import MediaItem, MediaRequest
from app.schemas.contracts import SettingsPatch
from tests.test_pipeline import confirm_all, message, setup_pipeline


@pytest.mark.parametrize("limit", [1, 20, 40, 80])
def test_title_and_nameless_group_files_share_configured_limit(client: TestClient, limit: int) -> None:
    state, ingest = setup_pipeline(client)
    caption = "景" * 100
    for index in (1, 2):
        media = message(index, "group")
        media["caption"] = caption if index == 2 else "\n"
        media["video"]["file_name"] = None
        ingest.message(7, media, f"in{index}")
    # Changing the setting after intake must still use the retained source text.
    patch = SettingsPatch(revision=state.settings.read()[0], message_title_length=limit)
    state.settings.save(patch, state.settings.candidate(patch))
    request_id = confirm_all(state)
    expected = "景" * (limit - 1) + "…"
    with state.db.sessions() as session:
        request = session.get(MediaRequest, request_id)
        items = list(session.scalars(select(MediaItem).order_by(MediaItem.message_id)))
        assert request.title == expected
        # The existing 180-byte safety cap still applies at the upper limit.
        assert Path(request.destination).name == (expected if limit < 80 else "景" * 60)
        stem = expected if limit < 80 else "景" * 58
        assert [item.planned_filename for item in items] == [f"{stem}.mp4", f"{stem}_2.mp4"]
        assert items[1].caption_snippet == "景" * 79 + "…"
        snapshot = (request.title, request.destination, [item.planned_filename for item in items])
    patch = SettingsPatch(revision=state.settings.read()[0], message_title_length=5)
    state.settings.save(patch, state.settings.candidate(patch))
    state.tasks.freeze_due()
    with state.db.sessions() as session:
        request = session.get(MediaRequest, request_id)
        items = list(session.scalars(select(MediaItem).order_by(MediaItem.message_id)))
        assert (request.title, request.destination, [item.planned_filename for item in items]) == snapshot


@pytest.mark.parametrize("source", ["caption", "original", "override"])
def test_original_and_explicit_filenames_keep_existing_rules(client: TestClient, source: str) -> None:
    state, ingest = setup_pipeline(client)
    name = "景" * 30
    if source == "override":
        ingest.message(7, {"message_id": 1, "from": {"id": 42},
                           "chat": {"id": 42, "type": "private"}, "text": name}, "name")
    media = message(2)
    media["video"]["file_name"] = name + ".mp4"
    if source == "caption":
        media["caption"] = name
    ingest.message(7, media, "in")
    request_id = confirm_all(state)
    with state.db.sessions() as session:
        assert session.scalar(select(MediaItem)).planned_filename == name + ".mp4"
        assert session.get(MediaRequest, request_id).title == (name if source == "override" else "景" * 19 + "…")


def test_title_length_settings_persist_and_environment_is_read_only(authenticated: TestClient, monkeypatch) -> None:
    initial = authenticated.get("/api/v1/settings").json()
    assert initial["values"]["message_title_length"] == 20
    saved = authenticated.patch("/api/v1/settings", json={
        "revision": initial["revision"], "message_title_length": 40,
    })
    assert saved.status_code == 200, saved.text
    assert authenticated.get("/api/v1/settings").json()["values"]["message_title_length"] == 40
    monkeypatch.setenv("TGD_MESSAGE_TITLE_LENGTH", "12")
    current = authenticated.get("/api/v1/settings").json()
    assert current["values"]["message_title_length"] == 12
    assert current["sources"]["message_title_length"] == "environment"
    assert current["editable"]["message_title_length"] is False
    denied = authenticated.patch("/api/v1/settings", json={
        "revision": current["revision"], "message_title_length": 30,
    })
    assert denied.status_code == 400
    assert denied.json()["error"]["code"] == "READ_ONLY_SETTING"


@pytest.mark.parametrize("value", [0, 81, 1.5, True, "20"])
def test_invalid_title_length_rejected_without_writing(authenticated: TestClient, value) -> None:
    before = authenticated.get("/api/v1/settings").json()
    response = authenticated.patch("/api/v1/settings", json={
        "revision": before["revision"], "message_title_length": value,
    })
    assert response.status_code == 422
    assert authenticated.get("/api/v1/settings").json() == before


@pytest.mark.parametrize("separator", ["\n", "\r\n", "\r", "\u2028"])
@pytest.mark.parametrize("enabled", [True, False])
def test_newline_switch_applies_at_freeze_to_titles_and_nameless_files(client: TestClient, separator: str, enabled: bool) -> None:
    state, ingest = setup_pipeline(client)
    media = message()
    media["video"]["file_name"] = None
    media["caption"] = f"  第一行{separator}第二行  "
    ingest.message(7, media, "in")
    # Both naming sources survive intake/restart before choosing the switch.
    state.db.engine.dispose()
    patch = SettingsPatch(revision=state.settings.read()[0], message_title_first_line=enabled, message_title_length=6)
    state.settings.save(patch, state.settings.candidate(patch))
    request_id = confirm_all(state)
    expected = "第一行" if enabled else "第一行 第…"
    with state.db.sessions() as session:
        request = session.get(MediaRequest, request_id)
        item = session.scalar(select(MediaItem))
        assert request.title == expected
        assert item.planned_filename == expected + ".mp4"
        assert item.caption_snippet == "第一行 第二行"
        assert item.caption_first_line == "第一行"
        snapshot = request.title, item.planned_filename
    patch = SettingsPatch(revision=state.settings.read()[0], message_title_first_line=not enabled)
    state.settings.save(patch, state.settings.candidate(patch))
    state.tasks.freeze_due()
    with state.db.sessions() as session:
        assert (session.get(MediaRequest, request_id).title, session.scalar(select(MediaItem)).planned_filename) == snapshot


@pytest.mark.parametrize("length", [19, 20, 21, 79, 80, 81])
def test_first_line_length_is_applied_before_discarded_lines(client: TestClient, length: int) -> None:
    state, ingest = setup_pipeline(client)
    media = message()
    media["caption"] = "a" * length + "\nignored"
    ingest.message(7, media, "in")
    patch = SettingsPatch(revision=state.settings.read()[0], message_title_length=80)
    state.settings.save(patch, state.settings.candidate(patch))
    request_id = confirm_all(state)
    assert state.tasks.detail(request_id)["title"] == ("a" * length if length <= 80 else "a" * 79 + "…")


def test_blank_first_line_uses_existing_fallback_and_original_names_stay_intact(client: TestClient) -> None:
    state, ingest = setup_pipeline(client)
    media = message()
    media["caption"] = "\n不要用第二行"
    ingest.message(7, media, "in")
    request_id = confirm_all(state)
    assert state.tasks.detail(request_id)["title"] == "movie1.mp4"
    with state.db.sessions() as session:
        assert session.scalar(select(MediaItem)).planned_filename == "movie1.mp4"


def test_first_line_switch_default_persistence_and_environment(authenticated: TestClient, monkeypatch) -> None:
    initial = authenticated.get("/api/v1/settings").json()
    assert initial["values"]["message_title_first_line"] is True
    response = authenticated.patch("/api/v1/settings", json={
        "revision": initial["revision"], "message_title_first_line": False,
    })
    assert response.status_code == 200, response.text
    assert authenticated.get("/api/v1/settings").json()["values"]["message_title_first_line"] is False
    monkeypatch.setenv("TGD_MESSAGE_TITLE_FIRST_LINE", "true")
    current = authenticated.get("/api/v1/settings").json()
    assert current["values"]["message_title_first_line"] is True
    assert current["editable"]["message_title_first_line"] is False
    denied = authenticated.patch("/api/v1/settings", json={
        "revision": current["revision"], "message_title_first_line": False,
    })
    assert denied.status_code == 400
    assert denied.json()["error"]["code"] == "READ_ONLY_SETTING"


@pytest.mark.parametrize("value", [0, 1, "false", []])
def test_non_boolean_first_line_switch_rejected(authenticated: TestClient, value) -> None:
    initial = authenticated.get("/api/v1/settings").json()
    response = authenticated.patch("/api/v1/settings", json={
        "revision": initial["revision"], "message_title_first_line": value,
    })
    assert response.status_code == 422
    assert authenticated.get("/api/v1/settings").json() == initial
