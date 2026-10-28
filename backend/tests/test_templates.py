import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import select

from app.core.errors import AppError
from app.core.templates import (
    DEFAULT_CONFIRMATION_TEMPLATE,
    render_confirmation,
    render_template,
)
from app.db.models import Job, MediaItem, MediaRequest, Notification
from app.schemas.contracts import SettingsPatch, UserSettings
from app.storage.naming import planned_name, unique_name
from tests.test_pipeline import message, setup_pipeline


def test_current_timeout_and_template_defaults() -> None:
    settings = UserSettings()
    assert settings.confirmation_timeout_seconds == 1800
    assert settings.confirmation_template == DEFAULT_CONFIRMATION_TEMPLATE
    assert settings.bot_api_mode == "cloud"
    assert settings.filename_template == "{name}"


@pytest.mark.parametrize("size,total", [(0, "0 B"), (1024, "2.0 KB"), (None, "未知")])
def test_default_confirmation_summary_and_labelled_files(client: TestClient, size: int | None, total: str) -> None:
    state, ingest = setup_pipeline(client)
    for number in (1, 2):
        payload = message(number, "g", size=1024)
        if size is None and number == 2:
            payload["video"].pop("file_size")
        elif size is not None:
            payload["video"]["file_size"] = size
        ingest.message(7, payload, f"in{number}")
    with state.db.transaction() as session:
        request = session.scalar(select(MediaRequest))
        request.collect_until = 0
        request_id = request.id
    state.tasks.freeze_due()
    with state.db.sessions() as session:
        text = session.scalar(select(Notification).where(Notification.event_key.like("confirm:%"))).payload["text"]
        assert text.startswith(f"任务编号：{request_id}\n======\n📥 确认下载：")
        assert text.count(f"任务编号：{request_id}") == 1
        assert "📄 文件数量：2 个" in text
        assert f"📦 总文件大小：{total}\n" in text
        assert text.count("======") == 2
        assert "\n======\n文件名：movie1.mp4\n文件大小：" in text
        assert "\n\n文件名：movie2.mp4\n文件大小：" in text
        assert text.endswith("文件大小：未知" if size is None else f"文件大小：{'0 B' if size == 0 else '1.0 KB'}")


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
                          confirmation_template="{expires_in_minutes} 分钟")
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
        assert card.payload["text"] == f"任务编号：{new_id}\n1.5 分钟"


@pytest.mark.parametrize("field,value", [
    ("confirmation_template", " "), ("confirmation_template", "x" * 2001),
    ("confirmation_template", "{title.__class__}"), ("confirmation_template", "{title[0]}"),
    ("confirmation_template", "{title!r}"), ("confirmation_template", "{title:}"),
    ("confirmation_template", "{unknown}"), ("confirmation_template", "{expires_in}"), ("confirmation_template", "{"),
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
        "title": "😀" * 4000, "destination": "2026-10-21", "expires_in_minutes": "30",
        "file_count": "500", "total_size": "1.0 GB",
        "files": "文件名：video.mp4\n文件大小：2.0 MB\n\n" * 500,
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


def test_freeze_names_prompt_and_retry_preserve_snapshot(client: TestClient) -> None:
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
        assert card.payload["text"] == (
            f"任务编号：{card.request_id}\n2项 2.0 KB\n文件名：19700101_1.mp4\n文件大小：1.0 KB\n\n"
            "文件名：19700101_2.mp4\n文件大小：1.0 KB")
    patch = SettingsPatch(revision=3, filename_template="new_{name}", timezone="Pacific/Honolulu")
    state.settings.save(patch, state.settings.candidate(patch))
    assert state.tasks.confirm(request_id, nonce, 42, 42, "confirm") == "queued"
    with state.db.sessions() as session:
        jobs = list(session.scalars(select(Job)))
        paths = [job.relative_path for job in jobs]
        assert all(path.endswith(name) for path, name in zip(paths, names, strict=True))
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


def test_group_confirmation_separates_each_size_in_custom_file_list(client: TestClient) -> None:
    state, ingest = setup_pipeline(client)
    patch = SettingsPatch(revision=2, filename_template="{index}", confirmation_template="清单\n{files}")
    state.settings.save(patch, state.settings.candidate(patch))
    ingest.message(7, message(1, "g", size=1536), "in1")
    unknown = message(2, "g")
    unknown["video"].pop("file_size")
    ingest.message(7, unknown, "in2")
    with state.db.transaction() as session:
        session.scalar(select(MediaRequest)).collect_until = 0
    state.tasks.freeze_due()
    with state.db.sessions() as session:
        card = session.scalar(select(Notification).where(Notification.event_key.like("confirm:%")))
        assert card.payload["text"] == (
            f"任务编号：{card.request_id}\n清单\n文件名：1.mp4\n文件大小：1.5 KB\n\n"
            "文件名：2.mp4\n文件大小：未知")
    assert state.settings.read()[1].confirmation_template == "清单\n{files}"


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


def test_confirmation_rejects_incomplete_frozen_file_plan(client: TestClient) -> None:
    state, ingest = setup_pipeline(client)
    for number in (1, 2):
        ingest.message(7, message(number, "g"), f"in{number}")
    with state.db.transaction() as session:
        request = session.scalar(select(MediaRequest))
        request.collect_until = 0
        request_id, nonce = request.id, request.nonce
    state.tasks.freeze_due()
    with state.db.transaction() as session:
        session.scalar(select(MediaItem).order_by(MediaItem.message_id.desc())).planned_filename = None
    with pytest.raises(AppError, match="文件名不完整") as error:
        state.tasks.confirm(request_id, nonce, 42, 42, "confirm")
    assert error.value.code == "INVALID_FILE_PLAN"
    with state.db.sessions() as session:
        assert list(session.scalars(select(Job))) == []
        assert session.get(MediaRequest, request_id).status == "awaiting_confirmation"


@pytest.mark.parametrize("original", [None, "", " \n\t\u200b"])
def test_missing_original_uses_caption_without_treating_title_as_extension(original: str | None) -> None:
    request = MediaRequest(id="r", created_at=0, kind="single")
    item = MediaItem(id="abcdef012345", media_type="image", original_name=original, caption_snippet="旅行 1.2")
    assert planned_name(item, request, "{name}", "UTC", 1) == "旅行 1.2.jpg"
    item.caption_snippet = "测试.mp4"
    item.media_type = "video"
    assert planned_name(item, request, "{name}", "UTC", 1) == "测试.mp4.mp4"
    item.original_name = "real.MKV"
    assert planned_name(item, request, "{name}", "UTC", 1) == "real.MKV"
    item.original_name = None
    item.caption_snippet = " \u200b\n"
    assert planned_name(item, request, "{name}", "UTC", 1) == "video.mp4"


def test_group_caption_index_safety_templates_and_collision() -> None:
    request = MediaRequest(id="r", created_at=0, kind="group")
    item = MediaItem(id="abcdef012345", media_type="video", original_name=None)
    caption = "../CON/\u200b测试" + "视" * 90
    name = planned_name(item, request, "{name}", "UTC", 2, caption=caption)
    assert name.endswith(".mp4") and len(name.encode("utf-8")) <= 180
    assert "/" not in name and ".." not in name and "\u200b" not in name
    assert planned_name(item, request, "{date}_{name}_{index}", "UTC", 2, caption="旅行") == "19700101_旅行_2.mp4"
    explicit = planned_name(item, request, "{name}_{index}", "UTC", 2, caption="视" * 80)
    assert explicit.endswith("_2.mp4") and not explicit.endswith("_2_2.mp4")
    assert len(explicit.encode("utf-8")) <= 180
    assert planned_name(item, request, "{name}_{{index}}", "UTC", 2, caption="旅行") == "旅行_{index}.mp4"
    used: set[str] = set()
    assert unique_name(name, used) == name
    collision = unique_name(name, used)
    assert collision != name and collision.endswith("_2.mp4")
    assert len(collision.encode("utf-8")) <= 180


def test_group_first_caption_shared_in_order_and_frozen_before_consent(client: TestClient) -> None:
    state, ingest = setup_pipeline(client)
    patch = SettingsPatch(revision=2, filename_template="{name}")
    state.settings.save(patch, state.settings.candidate(patch))
    for index in (3, 2, 1):
        payload = message(index, "g")
        if index == 1:
            payload.pop("video")
            payload["photo"] = [{"file_id": "photo1", "file_unique_id": "photo-unique", "file_size": 3}]
            payload["caption"] = "旅行\n合辑"
        elif index == 2:
            payload["video"].pop("file_name")
        # Third item retains its actual filename, which takes priority.
        ingest.message(7, payload, f"in{index}")
    with state.db.transaction() as session:
        request = session.scalar(select(MediaRequest))
        request.collect_until = 0
        request_id, nonce = request.id, request.nonce
    state.tasks.freeze_due()
    with state.db.sessions() as session:
        request = session.get(MediaRequest, request_id)
        assert request.title == "旅行 合辑"
        items = list(session.scalars(select(MediaItem).order_by(MediaItem.message_id)))
        assert [item.planned_filename for item in items] == ["旅行 合辑.jpg", "旅行 合辑.mp4", "movie3.mp4"]
        card = session.scalar(select(Notification).where(Notification.event_key.like("confirm:%")))
        assert all(item.planned_filename in card.payload["text"] for item in items)
    assert state.tasks.confirm(request_id, nonce, 42, 42, "confirm") == "queued"
    with state.db.sessions() as session:
        assert sorted(job.relative_path.rsplit("/", 1)[1] for job in session.scalars(select(Job))) == [
            "movie3.mp4", "旅行 合辑.jpg", "旅行 合辑.mp4"]


def test_default_names_only_number_actual_duplicates(client: TestClient) -> None:
    state, ingest = setup_pipeline(client)
    for index in (4, 2, 3, 1):
        payload = message(index, "g")
        payload["video"].pop("file_name")
        if index == 1:
            payload["caption"] = "旅行"
        if index == 4:
            payload["video"]["file_name"] = "Other.MKV"
        ingest.message(7, payload, f"in{index}")
    with state.db.transaction() as session:
        session.scalar(select(MediaRequest)).collect_until = 0
    state.tasks.freeze_due()
    with state.db.sessions() as session:
        names = [item.planned_filename for item in session.scalars(select(MediaItem).order_by(MediaItem.message_id))]
        assert names == ["旅行.mp4", "旅行_2.mp4", "旅行_3.mp4", "Other.MKV"]
        card = session.scalar(select(Notification).where(Notification.event_key.like("confirm:%")))
        assert all(name in card.payload["text"] for name in names)


def test_single_names_reserve_disk_frozen_cards_and_jobs_without_ids(client: TestClient) -> None:
    state, ingest = setup_pipeline(client)
    # Create the first card to discover the date under the current configured timezone.
    ingest.message(7, message(1), "in1")
    with state.db.transaction() as session:
        session.scalar(select(MediaRequest)).collect_until = 0
    state.tasks.freeze_due()
    with state.db.sessions() as session:
        request = session.scalar(select(MediaRequest))
        first_id, nonce, folder = request.id, request.nonce, request.destination
        assert session.scalar(select(MediaItem)).planned_filename == "movie1.mp4"
    root = state.settings.config.download_root
    day = root / folder
    day.mkdir(parents=True)
    (day / "MOVIE1_2.mp4").write_bytes(b"existing")
    state.tasks.confirm(first_id, nonce, 42, 42, "confirm")
    # The actual job path may diverge from its card following an external race.
    with state.db.transaction() as session:
        session.scalar(select(Job)).relative_path = folder + "/movie1_3.mp4"
    group = message(4, "reserved-group")
    group["caption"] = "movie1_4.mp4"
    ingest.message(7, group, "group")
    with state.db.transaction() as session:
        session.scalar(select(MediaRequest).where(MediaRequest.status == "collecting")).collect_until = 0
    state.tasks.freeze_due()
    for number in (2, 3):
        payload = message(number)
        payload["video"]["file_name"] = "movie1.mp4"
        ingest.message(7, payload, f"in{number}")
    with state.db.transaction() as session:
        for request in session.scalars(select(MediaRequest).where(MediaRequest.status == "collecting")):
            request.collect_until = 0
    state.tasks.freeze_due()
    with state.db.sessions() as session:
        assert [item.planned_filename for item in session.scalars(
            select(MediaItem).where(MediaItem.message_id < 4).order_by(MediaItem.message_id))] == [
            "movie1.mp4", "movie1_5.mp4", "movie1_6.mp4"]
    assert (day / "MOVIE1_2.mp4").read_bytes() == b"existing"
