"""Attachment contracts across intake, consent, storage, and HTTP projection."""
import asyncio
from pathlib import Path

import pytest
from aiogram.client.telegram import TelegramAPIServer
from aiohttp import web
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.bot.client import TelegramClient
from app.core.config import Config
from app.core.file_types import default_extension, document_type
from app.db.models import Job, MediaItem, MediaRequest, Notification, PublishJournal, StoredFile
from app.schemas.contracts import SettingsPatch, UserSettings
from app.storage.files import contained, file_hash
from app.storage.naming import planned_name, unique_name
from tests.test_cloud_api import TOKEN, cloud_pipeline, fake_api
from tests.test_pipeline import FakeClient, confirm_all, message, setup_pipeline


def attachment(index=1, *, kind="document", name="example.pdf", mime="application/pdf", group=None, size=6):
    payload = message(index, group, kind, size)
    payload[kind]["file_name"] = name
    payload[kind]["mime_type"] = mime
    return payload


@pytest.mark.parametrize("name,mime,expected", [
    ("app.APK", "image/jpeg", "document"), ("report.pdf", None, "document"),
    ("data.unknown", "video/mp4", "document"), ("unsafe.bad!", "image/jpeg", "document"),
    ("photo.JpEg", "application/pdf", "image"), ("movie.MKV", None, "video"),
    ("music.FLAC", None, "audio"), ("clip.GIF", "video/mp4", "animation"),
    ("README", "image/jpeg", "document"), ("README", "video/mp4", "document"),
    (None, "image/jpeg", "image"), (None, "audio/mpeg", "audio"),
    (None, "image/gif", "animation"), (None, "application/x-strange", "document"),
])
def test_document_classification(name, mime, expected):
    assert document_type(name, mime) == expected


@pytest.mark.parametrize("kind,mime,extension", [
    ("document", "application/pdf", ".pdf"), ("document", "application/vnd.android.package-archive", ".apk"),
    ("document", "text/plain; charset=utf-8", ".txt"), ("document", "image/../../bad", ".bin"),
    ("audio", "audio/flac", ".flac"), ("audio", None, ".mp3"),
    ("voice", "audio/ogg", ".ogg"), ("voice", "application/pdf", ".ogg"),
    ("animation", "image/gif", ".gif"), ("animation", "video/mp4", ".mp4"),
    ("video_note", "image/jpeg", ".mp4"), ("video", "application/pdf", ".mp4"),
    ("image", "audio/mpeg", ".jpg"),
])
def test_portable_defaults_respect_native_type(kind, mime, extension):
    assert default_extension(kind, mime) == extension


@pytest.mark.parametrize("name,mime,expected", [
    ("中文.APK", "application/pdf", "中文.APK"), ("archive.tar.gz", None, "archive.tar.gz"),
    ("README", "application/pdf", "README"), ("README", "image/jpeg", "README"),
    ("README", "video/mp4", "README"), ("CON", None, "file_CON"),
    ("../a\\b.PDF", None, "__a_b.PDF"), ("file.unsafe!", None, "file.unsafe!"),
    (None, "application/pdf", "document.pdf"), (None, None, "document.bin"),
])
def test_generic_safe_naming(name, mime, expected):
    item = MediaItem(id="abcdef0123", media_type="document", original_name=name, mime_type=mime)
    request = MediaRequest(created_at=0)
    assert planned_name(item, request, "{name}", "UTC", 1) == expected


def test_long_unicode_names_extensionless_collisions_and_templates():
    item = MediaItem(id="abcdef0123", media_type="document", original_name="长" * 200 + ".PDF")
    request = MediaRequest(created_at=0)
    name = planned_name(item, request, "{name}_{index}", "UTC", 42)
    assert name.endswith("_42.PDF") and len(name.encode()) <= 180
    used = set()
    assert [unique_name(value, used) for value in ("README", "readme", "README", "README.pdf")] == [
        "README", "readme_2", "README_3", "README.pdf"]
    item.original_name = "README"
    request.name_override = "指定.标题"
    assert planned_name(item, request, "{name}", "UTC", 1) == "指定.标题"
    item.original_name = None
    item.mime_type = "application/pdf"
    assert planned_name(item, request, "{name}", "UTC", 1) == "指定.标题.pdf"
    assert planned_name(item, request, "{date}_{type}_{name}_{index}", "UTC", 2) == "19700101_document_指定.标题_2.pdf"


@pytest.mark.parametrize("original", ["a." + "x" * 178, "a." + "长" * 80, "report.bad!", "README"])
def test_generic_collisions_keep_unsafe_suffix_as_bounded_stem(original):
    item = MediaItem(id="abcdef0123", media_type="document", original_name=original)
    name = planned_name(item, MediaRequest(created_at=0), "{name}", "UTC", 1)
    used = set()
    assert unique_name(name, used) == name
    second, third = unique_name(name, used), unique_name(name, used)
    assert second.endswith("_2") and third.endswith("_3")
    assert all(len(value.encode()) <= 180 for value in (name, second, third))
    assert len(used) == 3


@pytest.mark.parametrize("original", ["a." + "x" * 178, "a." + "长" * 80, "README"])
def test_generic_late_publication_collision_preserves_bytes_and_bounded_path(client: TestClient, config: Config, original):
    state, ingest = setup_pipeline(client)
    ingest.message(7, attachment(name=original), "collision")
    request_id = confirm_all(state)
    worker = state.runtime.worker
    job_id = worker.claim()
    with state.db.sessions() as session:
        relative = session.get(Job, job_id).relative_path
    final = contained(config.download_root, relative, create_parent=True)
    final.write_bytes(b"external")
    temporary = final.with_name(f".part.{job_id}")
    temporary.write_bytes(b"abcdef")
    worker.journal_copying(job_id, temporary.relative_to(config.download_root).as_posix(), relative)
    worker.journal_ready(job_id, *file_hash(temporary))
    worker.publish(job_id)
    with state.db.sessions() as session:
        changed = session.get(Job, job_id).relative_path
        assert session.get(PublishJournal, job_id).final_path == changed
    replacement = config.download_root / changed
    assert changed != relative and replacement.name.endswith("_2")
    assert len(replacement.name.encode()) <= 180
    assert not replacement.exists()  # Commit the new path before publication.
    worker.recover(False)
    assert state.tasks.detail(request_id)["status"] == "succeeded"
    assert replacement.read_bytes() == b"abcdef" and final.read_bytes() == b"external"


@pytest.mark.parametrize("kind,mime,extension", [
    ("audio", "audio/mpeg", ".mp3"), ("voice", "audio/ogg", ".ogg"),
    ("animation", "video/mp4", ".mp4"), ("video_note", "video/mp4", ".mp4"),
])
async def test_native_attachment_metadata_replay_and_byte_save(authenticated: TestClient, config: Config, kind, mime, extension):
    state, ingest = setup_pipeline(authenticated)
    payload = attachment(kind=kind, name=None, mime=mime)
    payload[kind]["duration"] = 17
    if kind == "animation":
        payload["document"] = dict(payload[kind])
    ingest.message(7, payload, "one")
    ingest.message(7, payload, "duplicate")
    state.tasks.freeze_due()
    with state.db.sessions() as session:
        items = list(session.scalars(select(MediaItem)))
        assert len(items) == 1
        item = items[0]
        assert (item.media_type, item.mime_type, item.duration_seconds) == (kind, mime, 17)
        assert item.planned_filename == kind + extension
        assert session.scalar(select(Job)) is None
    request_id = confirm_all(state)
    source = config.bot_api_root / (kind + extension)
    source.write_bytes(b"abcdef")
    fake = FakeClient(source)
    assert fake.calls == 0
    await state.runtime.worker.run(fake, state.runtime.worker.claim())
    response = authenticated.get(f"/api/v1/tasks/{request_id}")
    assert response.status_code == 200
    detail = response.json()
    assert detail["status"] == "succeeded"
    assert detail["items"][0]["media_type"] == kind
    assert detail["items"][0]["duration_seconds"] == 17
    assert (config.download_root / detail["items"][0]["relative_path"]).read_bytes() == b"abcdef"


@pytest.mark.parametrize("name,mime", [
    ("app.apk", "application/vnd.android.package-archive"), ("report.pdf", "application/pdf"),
    ("archive.zip", "application/zip"), ("document.docx", None), ("notes.txt", "text/plain"),
    ("package.exe", None), ("future.weird", None), ("README", "application/pdf"),
    ("README", "image/jpeg"), ("README", "video/mp4"),
])
@pytest.mark.parametrize("mode", ["local", "cloud"])
async def test_document_consent_byte_save_rest_and_dedup(authenticated: TestClient, config: Config, name, mime, mode):
    state, ingest = cloud_pipeline(authenticated) if mode == "cloud" else setup_pipeline(authenticated)
    content = b"binary\x00\xffattachment\n" + name.encode()
    payload = attachment(name=name, mime=mime, size=len(content))
    calls = []

    async def handler(request):
        calls.append(request.method)
        if request.method == "POST":
            return web.json_response({"ok": True, "result": {
                "file_id": "file-1", "file_unique_id": "unique-1", "file_size": len(content),
                "file_path": "documents/payload"}})
        return web.Response(body=content)

    ingest.message(7, payload, "one")
    ingest.message(7, payload, "duplicate")
    state.tasks.freeze_due()
    with state.db.sessions() as session:
        assert session.scalar(select(Job)) is None
        request = session.scalar(select(MediaRequest))
        assert request.status == "awaiting_confirmation"
        assert session.scalar(select(MediaItem)).planned_filename == name
        assert session.scalar(select(func.count()).select_from(MediaItem)) == 1
        assert session.scalar(select(Notification).where(Notification.event_key.like("confirm:%")))
    assert calls == []
    request_id = confirm_all(state)
    if mode == "cloud":
        telegram = TelegramClient(TOKEN, config, UserSettings(bot_api_mode="cloud"))
        async with fake_api(handler) as base:
            telegram.bot.session.api = TelegramAPIServer.from_base(base)
            try:
                await state.runtime.worker.run(telegram, state.runtime.worker.claim(7, mode))
            finally:
                await telegram.close()
        assert calls == ["POST", "GET"]
    else:
        source = config.bot_api_root / "payload"
        source.write_bytes(content)
        fake = FakeClient(source)
        await state.runtime.worker.run(fake, state.runtime.worker.claim(7, mode))
        assert fake.calls == 1
    response = authenticated.get(f"/api/v1/tasks/{request_id}")
    assert response.status_code == 200
    detail = response.json()
    assert detail["status"] == "succeeded"
    assert detail["items"][0]["media_type"] == "document"
    target = config.download_root / detail["items"][0]["relative_path"]
    assert target.name == name and target.read_bytes() == content
    with state.db.sessions() as session:
        assert session.scalar(select(StoredFile)).size_bytes == len(content)
        assert session.scalar(select(Notification).where(Notification.event_key.like("result:%")))
    payload["message_id"] = 2
    ingest.message(7, payload, "second")
    repeated_id = confirm_all(state)
    assert state.runtime.worker.claim(7, mode) is None
    assert state.tasks.detail(repeated_id)["status"] == "already_exists"
    assert target.read_bytes() == content


@pytest.mark.parametrize("kind", ["document", "audio"])
def test_real_generic_groups_share_confirmation_destination_and_duplicates(client: TestClient, kind):
    state, ingest = setup_pipeline(client)
    for number in (1, 2):
        payload = attachment(number, kind=kind, name=None, mime=None, group="real-group")
        ingest.message(7, payload, str(number))
        ingest.message(7, payload, f"replay{number}")
    request_id = confirm_all(state)
    detail = state.tasks.detail(request_id)
    assert detail["title"] == "文件组（2 个文件）"
    assert detail["item_count"] == 2
    assert len({Path(item["relative_path"]).parent for item in detail["items"]}) == 1
    with state.db.sessions() as session:
        assert session.scalar(select(func.count()).select_from(Job)) == 2
        assert session.scalar(select(func.count()).select_from(Notification).where(
            Notification.event_key.like("confirm:%"))) == 1


@pytest.mark.parametrize("kind", ["document", "audio", "voice", "animation", "video_note", "video"])
@pytest.mark.parametrize("file_id", [None, "", "   ", 123])
def test_missing_file_identifier_never_creates_half_request(client: TestClient, kind, file_id):
    state, ingest = setup_pipeline(client)
    payload = attachment(kind=kind)
    payload[kind]["file_id"] = file_id
    ingest.message(7, payload, "malformed")
    with state.db.sessions() as session:
        assert session.scalar(select(MediaItem)) is None
        assert session.scalar(select(MediaRequest)) is None
        assert "APK、PDF" in session.scalar(select(Notification)).payload["text"]


@pytest.mark.parametrize("priority", ["animation", "video", "photo", "audio", "voice", "video_note", "document"])
def test_native_priority_and_largest_photo(client: TestClient, priority):
    state, ingest = setup_pipeline(client)
    payload = message()
    payload.pop("video")
    order = ["animation", "video", "photo", "audio", "voice", "video_note", "document"]
    for kind in order[order.index(priority):]:
        payload[kind] = attachment(kind=kind)[kind]
    if "photo" in payload:
        payload["photo"] = [dict(payload["photo"], file_id="small", width=5, height=5),
                            dict(payload["photo"], file_id="largest", width=30, height=20)]
    ingest.message(7, payload, "priority")
    with state.db.sessions() as session:
        items = list(session.scalars(select(MediaItem)))
        assert len(items) == 1
        assert items[0].media_type == ("image" if priority == "photo" else priority)
        assert items[0].file_id == ("largest" if priority == "photo" else "file-1")


@pytest.mark.parametrize("kind", ["document", "audio"])
@pytest.mark.parametrize("mode", ["local", "cloud"])
async def test_generic_retry_preserves_frozen_path(authenticated: TestClient, config: Config, kind, mode):
    state, ingest = cloud_pipeline(authenticated) if mode == "cloud" else setup_pipeline(authenticated)
    ingest.message(7, attachment(kind=kind, name="retry.pdf" if kind == "document" else "retry.mp3"), "retry")
    request_id = confirm_all(state)
    worker = state.runtime.worker
    initial_path = state.tasks.detail(request_id)["items"][0]["relative_path"]
    source = config.bot_api_root / "retry-source"
    source.write_bytes(b"bad")

    class RetryClient(FakeClient):
        async def file_chunks(self, path):
            yield source.read_bytes()

    fake = RetryClient(source)
    fake.mode = mode
    job_id = worker.claim(7, mode)
    await worker.run(fake, job_id)
    assert state.tasks.detail(request_id)["status"] == "retry_wait"
    with state.db.transaction() as session:
        session.get(Job, job_id).next_run_at = 0
    source.write_bytes(b"abcdef")
    patch = SettingsPatch(revision=state.settings.read()[0], filename_template="changed_{id}")
    state.settings.save(patch, state.settings.candidate(patch))
    assert worker.claim(7, mode) == job_id
    await worker.run(fake, job_id)
    detail = authenticated.get(f"/api/v1/tasks/{request_id}").json()
    assert detail["status"] == "succeeded"
    assert detail["items"][0]["relative_path"] == initial_path
    assert len(detail["items"][0]["attempts"]) == 2
    assert (config.download_root / initial_path).read_bytes() == b"abcdef"


@pytest.mark.parametrize("kind", ["document", "audio"])
async def test_generic_local_cancel_retains_acquisition_slot(client: TestClient, config: Config, kind):
    state, ingest = setup_pipeline(client)
    ingest.message(7, attachment(kind=kind), "cancel")
    request_id = confirm_all(state)
    source = config.bot_api_root / "cancel-source"
    source.write_bytes(b"abcdef")
    entered, finish = asyncio.Event(), asyncio.Event()

    class SlowClient:
        async def get_file(self, file_id):
            entered.set()
            await finish.wait()
            return str(source)

    job_id = state.runtime.worker.claim()
    task = asyncio.create_task(state.runtime.worker.run(SlowClient(), job_id))
    await asyncio.wait_for(entered.wait(), 5)
    state.tasks.cancel(request_id)
    assert state.runtime.counts()[0] == 1
    assert state.tasks.detail(request_id)["status"] == "cancelling"
    finish.set()
    await asyncio.wait_for(task, 5)
    assert state.runtime.counts()[0] == 0
    assert state.tasks.detail(request_id)["status"] == "cancelled"
    assert not list(config.download_root.rglob(".part.*"))
