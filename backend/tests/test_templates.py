from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config as AlembicConfig
from alembic.migration import MigrationContext
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import select

from app.core.templates import (
    DEFAULT_CONFIRMATION_TEMPLATE,
    LEGACY_CONFIRMATION_TEMPLATE,
    render_confirmation,
    render_template,
)
from app.db.models import Base, Job, MediaItem, MediaRequest, Notification, Settings
from app.schemas.contracts import SettingsPatch, UserSettings
from app.services.settings import SettingsService
from app.storage.naming import planned_name, unique_name
from tests.test_pipeline import message, setup_pipeline


@pytest.mark.parametrize("seconds", [None, 30, 31, 90, 300, 1800])
def test_timeout_defaults_and_legacy_template_upgrade(client: TestClient, seconds: int | None) -> None:
    state = client.app.state
    with state.db.transaction() as session:
        row = session.get(Settings, 1)
        row.values = {"confirmation_template": LEGACY_CONFIRMATION_TEMPLATE,
                      **({"confirmation_timeout_seconds": seconds} if seconds is not None else {})}
        revision = row.revision
    service = SettingsService(state.db, state.settings.config, state.settings.secrets)
    upgraded_revision, values, _ = service.read()
    assert upgraded_revision == revision + 1
    assert values.confirmation_timeout_seconds == (seconds if seconds is not None else 1800)
    assert values.confirmation_template == DEFAULT_CONFIRMATION_TEMPLATE
    assert SettingsService(state.db, state.settings.config, state.settings.secrets).read()[0] == upgraded_revision


def test_legacy_custom_template_and_environment_stay_seconds(client: TestClient, monkeypatch) -> None:
    state = client.app.state
    custom = "请在 {expires_in} 秒内确认"
    with state.db.transaction() as session:
        row = session.get(Settings, 1)
        row.values = {"confirmation_template": custom, "confirmation_timeout_seconds": 90}
        revision = row.revision
    service = SettingsService(state.db, state.settings.config, state.settings.secrets)
    assert service.read()[0] == revision
    assert service.read()[1].confirmation_template == custom
    monkeypatch.setenv("TGD_CONFIRMATION_TIMEOUT_SECONDS", "31")
    monkeypatch.setenv("TGD_CONFIRMATION_TEMPLATE", LEGACY_CONFIRMATION_TEMPLATE)
    _, values, sources = service.read()
    assert values.confirmation_timeout_seconds == 31
    assert values.confirmation_template == LEGACY_CONFIRMATION_TEMPLATE
    assert sources["confirmation_timeout_seconds"] == "environment"


def test_default_card_minutes_and_existing_expiry_unchanged(client: TestClient) -> None:
    state, ingest = setup_pipeline(client)
    ingest.message(7, message(), "in1")
    with state.db.transaction() as session:
        row = session.scalar(select(MediaRequest))
        row.collect_until = 0
        request_id = row.id
    state.tasks.freeze_due()
    with state.db.sessions() as session:
        expiry = session.get(MediaRequest, request_id).expires_at
        card = session.scalar(select(Notification).where(Notification.event_key.like("confirm:%")))
        original_text = card.payload["text"]
        assert "确认有效期：30 分钟" in original_text
    patch = SettingsPatch(revision=state.settings.read()[0], confirmation_timeout_seconds=90,
                          confirmation_template="{expires_in_minutes} 分钟 / {expires_in} 秒")
    state.settings.save(patch, state.settings.candidate(patch))
    state.tasks.freeze_due()
    with state.db.sessions() as session:
        assert session.get(MediaRequest, request_id).expires_at == expiry
        assert session.scalar(select(Notification).where(Notification.event_key.like("confirm:%"))).payload["text"] == original_text
    ingest.message(7, message(2), "in2")
    with state.db.transaction() as session:
        row = session.scalar(select(MediaRequest).where(MediaRequest.status == "collecting"))
        row.collect_until = 0
        new_id = row.id
    state.tasks.freeze_due()
    with state.db.sessions() as session:
        card = session.scalar(select(Notification).where(Notification.request_id == new_id))
        assert card.payload["text"] == f"1.5 分钟 / 90 秒\n任务编号：{new_id}"


@pytest.mark.parametrize("field,value", [
    ("confirmation_template", " "), ("confirmation_template", "x" * 2001),
    ("confirmation_template", "{title.__class__}"), ("confirmation_template", "{title[0]}"),
    ("confirmation_template", "{title!r}"), ("confirmation_template", "{title:}"),
    ("confirmation_template", "{unknown}"), ("confirmation_template", "{"),
    ("confirmation_template", "bad\x00text"), ("filename_template", "x" * 161),
    ("filename_template", "../{name}"), ("filename_template", "a\\{id}"),
    ("filename_template", "{index:03d}"), ("filename_template", "{name}\n"),
    ("filename_template", "{name}:{id}"),
])
def test_template_validation(field: str, value: str) -> None:
    for model in (UserSettings, SettingsPatch):
        with pytest.raises(ValidationError):
            model.model_validate({field: value})


def test_plain_render_escapes_and_length() -> None:
    assert render_template("{{title}} {title}", {"title": "{files}"}) == "{title} {files}"
    text = render_confirmation(DEFAULT_CONFIRMATION_TEMPLATE, {
        "title": "😀" * 4000, "destination": "videos/2026-10", "expires_in_minutes": "30",
        "files": "video.mp4 · 2.0 MB\n" * 500,
    })
    assert len(text.encode("utf-16-le")) // 2 <= 3500
    assert "video.mp4" in text and "…" in text
    assert len(render_confirmation("😀" * 2000, {}).encode("utf-16-le")) // 2 <= 3500


def test_template_settings_persistence_and_environment(authenticated: TestClient, monkeypatch) -> None:
    client = authenticated
    initial = client.get("/api/v1/settings").json()
    patch = {"revision": initial["revision"], "confirmation_template": "共{file_count}项 {total_size}\n{files}",
             "filename_template": "{datetime}_{index}_{name}"}
    response = client.patch("/api/v1/settings", json=patch)
    assert response.status_code == 200
    saved = response.json()
    assert saved["values"]["filename_template"] == patch["filename_template"]
    assert saved["sources"]["confirmation_template"] == "database"
    assert client.patch("/api/v1/settings", json=patch).status_code == 409
    assert client.patch("/api/v1/settings", json={"revision": saved["revision"],
                                                "filename_template": "{unknown}"}).status_code == 422
    monkeypatch.setenv("TGD_FILENAME_TEMPLATE", "{id}")
    assert client.get("/api/v1/settings").json()["editable"]["filename_template"] is False
    readonly = client.patch("/api/v1/settings", json={"revision": saved["revision"],
                                                    "filename_template": "{name}"})
    assert readonly.status_code == 400 and readonly.json()["error"]["code"] == "READ_ONLY_SETTING"


def test_freeze_names_prompt_and_legacy_compatibility(client: TestClient) -> None:
    state, ingest = setup_pipeline(client)
    patch = SettingsPatch(revision=2, filename_template="{date}_{index}",
                          confirmation_template="{file_count}项 {total_size}\n{files}")
    state.settings.save(patch, state.settings.candidate(patch))
    for number in (2, 1):
        ingest.message(7, message(number, "g", size=1024), f"in{number}")
    with state.db.transaction() as session:
        request = session.scalar(select(MediaRequest))
        request.created_at, request.collect_until = 0, 0
        request_id, nonce = request.id, request.nonce
    state.tasks.freeze_due()
    with state.db.sessions() as session:
        items = list(session.scalars(select(MediaItem).order_by(MediaItem.message_id)))
        names = [item.planned_filename for item in items]
        assert names == ["19700101_1.mp4", "19700101_2.mp4"]
        card = session.scalar(select(Notification).where(Notification.event_key.like("confirm:%")))
        assert card.payload["text"] == f"2项 2.0 KB\n19700101_1.mp4 · 1.0 KB\n19700101_2.mp4 · 1.0 KB\n任务编号：{card.request_id}"
    patch = SettingsPatch(revision=3, filename_template="new_{name}", timezone="Pacific/Honolulu")
    state.settings.save(patch, state.settings.candidate(patch))
    # Simulate an awaiting card from a pre-0003 database for one item.
    with state.db.transaction() as session:
        first = session.scalar(select(MediaItem).order_by(MediaItem.message_id))
        first.planned_filename = None
        legacy_expected = f"movie1_{first.id[:8]}.mp4"
    assert state.tasks.confirm(request_id, nonce, 42, 42, "confirm") == "queued"
    with state.db.sessions() as session:
        jobs = list(session.scalars(select(Job)))
        paths = [job.relative_path for job in jobs]
        assert paths[0].endswith(legacy_expected) and paths[1].endswith(names[1])
    state.tasks.confirm(request_id, nonce, 42, 42, "confirm")
    with state.db.sessions() as session:
        assert [job.relative_path for job in session.scalars(select(Job))] == paths
    with state.db.transaction() as session:
        for job in session.scalars(select(Job)):
            job.state = "failed"
    state.tasks.retry(request_id, None)
    state.db.engine.dispose()
    with state.db.sessions() as session:
        assert [job.relative_path for job in session.scalars(select(Job))] == paths


def test_name_safety_unicode_extensions_and_collision() -> None:
    request = MediaRequest(id="r", created_at=0)
    item = MediaItem(id="abcdef012345", media_type="video", original_name="../{name}/" + "视" * 150 + ".mkv")
    name = planned_name(item, request, "{name}_{id}", "Asia/Shanghai", 1)
    assert name.endswith(".mkv") and len(name.encode("utf-8")) <= 180
    assert "/" not in name and "\\" not in name
    item.original_name = "{date}.mp4"
    assert planned_name(item, request, "{name}", "Asia/Shanghai", 1) == "{date}.mp4"
    assert planned_name(item, request, "{datetime}_{time}_{type}_{index}_{id}", "Asia/Shanghai", 3) == (
        "19700101_080000_080000_video_3_abcdef01.mp4")
    item.original_name = None
    assert planned_name(item, request, "CON.mp4", "Asia/Shanghai", 1) == "file_CON.mp4"
    used: set[str] = set()
    assert unique_name("A.mp4", used) == "A.mp4"
    assert unique_name("a.mp4", used) == "a_2.mp4"
    long_name = "视" * 58 + ".mp4"
    used = {long_name.casefold()}
    collision = unique_name(long_name, used)
    assert collision.endswith("_2.mp4") and len(collision.encode("utf-8")) <= 180


def test_upgrade_0002_keeps_existing_items(client: TestClient) -> None:
    state, ingest = setup_pipeline(client)
    ingest.message(7, message(), "in1")
    migration = AlembicConfig(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    with state.db.engine.begin() as connection:
        migration.attributes["connection"] = connection
        command.downgrade(migration, "0002")
        assert connection.exec_driver_sql("SELECT original_name FROM media_items").scalar() == "movie1.mp4"
        command.upgrade(migration, "head")
        assert connection.exec_driver_sql("SELECT planned_filename FROM media_items").scalar() is None
        assert compare_metadata(MigrationContext.configure(connection), Base.metadata) == []
