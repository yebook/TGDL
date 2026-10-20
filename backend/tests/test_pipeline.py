import asyncio
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.bot.ingest import IngestService
from app.core.config import Config
from app.core.errors import AppError
from app.db.models import IngestUpdate, Job, MediaItem, MediaRequest, Notification, PublishJournal
from app.schemas.contracts import SettingsPatch
from app.storage.files import contained, file_hash, publish_no_replace, safe_name, source_file


def setup_pipeline(client: TestClient):
    state = client.app.state
    candidate = state.settings.candidate(SettingsPatch(allowed_user_ids=[42], bot_api_mode="local"))
    state.settings.save(SettingsPatch(revision=1, allowed_user_ids=[42]), candidate)
    ingest = IngestService(state.db, state.settings, state.tasks)
    return state, ingest


def message(index: int = 1, group: str | None = None, kind: str = "video", size: int = 6) -> dict:
    media = {"file_id": f"file-{index}", "file_unique_id": f"unique-{index}", "file_name": f"movie{index}.mp4", "file_size": size}
    result = {"message_id": index, "from": {"id": 42}, "chat": {"id": 42, "type": "private"}, kind: media}
    if group:
        result["media_group_id"] = group
    return result


def confirm_all(state) -> str:
    with state.db.transaction() as session:
        requests = list(session.scalars(select(MediaRequest)))
        for request in requests:
            request.collect_until = 0
    state.tasks.freeze_due()
    with state.db.sessions() as session:
        request = session.scalar(select(MediaRequest).order_by(MediaRequest.created_at.desc()))
        request_id, nonce = request.id, request.nonce
    assert state.tasks.confirm(request_id, nonce, 42, 42, "confirm") == "queued"
    return request_id


def test_durable_inbox_offset_and_replay(client: TestClient) -> None:
    state, ingest = setup_pipeline(client)
    update = {"update_id": 100, "message": message()}
    ingest.persist_batch(7, [update, update])
    assert ingest.offset(7) == 101
    assert len(ingest.pending(7)) == 1
    inbox_id, payload = ingest.pending(7)[0]
    ingest.message(7, payload["message"], inbox_id)
    ingest.message(7, payload["message"], inbox_id)
    with state.db.sessions() as session:
        assert session.scalar(select(func.count()).select_from(MediaItem)) == 1
        assert session.scalar(select(func.count()).select_from(IngestUpdate)) == 1
    ingest.mark_processed(inbox_id)
    assert ingest.pending(7) == []


def test_mixed_group_confirmation_ownership_expiry_and_late_batch(client: TestClient) -> None:
    state, ingest = setup_pipeline(client)
    ingest.message(7, message(1, "g"), "in1")
    other = message(2, "g", "document")
    other["document"]["file_name"] = "photo.png"
    ingest.message(7, other, "in2")
    request_id = confirm_all(state)
    detail = state.tasks.detail(request_id)
    assert detail["item_count"] == 2
    assert {i["media_type"] for i in detail["items"]} == {"image", "video"}
    assert len({str(Path(i["relative_path"]).parent) for i in detail["items"]}) == 1
    assert len(Path(detail["effective_destination"]).parts) == 2
    with state.db.sessions() as session:
        request = session.get(MediaRequest, request_id)
        nonce = request.nonce
    with pytest.raises(AppError):
        state.tasks.confirm(request_id, nonce, 43, 42, "confirm")
    assert state.tasks.confirm(request_id, nonce, 42, 42, "confirm") == "queued"
    with state.db.sessions() as session:
        assert session.scalar(select(func.count()).select_from(Job)) == 2
    ingest.message(7, message(3, "g"), "in3")
    with state.db.sessions() as session:
        assert session.scalar(select(func.count()).select_from(MediaRequest)) == 2


def test_confirmation_formats_file_sizes_without_changing_dto_bytes(client: TestClient) -> None:
    state, ingest = setup_pipeline(client)
    sizes = [(None, "未知"), (0, "0 B"), (1023, "1023 B"), (1024, "1.0 KB"),
             (1536, "1.5 KB"), (20 * 1024**2, "20.0 MB"),
             (3 * 1024**3 // 2, "1.5 GB"), (1024**4, "1.0 TB")]
    for index, (size, _) in enumerate(sizes, start=1):
        media = message(index, "sizes", size=size or 0)
        if size is None:
            media["video"].pop("file_size")
        ingest.message(7, media, f"in{index}")
    request_id = confirm_all(state)
    with state.db.sessions() as session:
        notification = session.scalar(select(Notification).where(Notification.event_key.like("confirm:%")))
        assert notification is not None
        text = notification.payload["text"]
    with state.db.sessions() as session:
        items = list(session.scalars(select(MediaItem).order_by(MediaItem.message_id)))
        for item, (_, expected) in zip(items, sizes, strict=True):
            assert f"{item.planned_filename} · {expected}" in text
    assert "字节" not in text
    detail = state.tasks.detail(request_id)
    assert [item["size_bytes"] for item in detail["items"]] == [size for size, _ in sizes]
    assert detail["total_bytes"] is None


def test_expiry_uses_card_deadline_not_new_setting(client: TestClient) -> None:
    state, ingest = setup_pipeline(client)
    ingest.message(7, message(), "in")
    state.tasks.freeze_due()
    with state.db.transaction() as session:
        request = session.scalar(select(MediaRequest))
        request.expires_at = time.time() - 1
        request_id, nonce = request.id, request.nonce
    assert state.tasks.confirm(request_id, nonce, 42, 42, "confirm") == "expired"
    with state.db.sessions() as session:
        assert session.scalar(select(func.count()).select_from(Job)) == 0


class FakeClient:
    def __init__(self, path: Path) -> None:
        self.path, self.calls = path, 0

    async def get_file(self, file_id: str) -> str:
        self.calls += 1
        return str(self.path)


@pytest.mark.integration
async def test_large_file_pipeline_dedup_missing_file_and_notification_outbox(client: TestClient, config: Config) -> None:
    state, ingest = setup_pipeline(client)
    size = 21 * 1024 * 1024
    source = config.bot_api_root / "large.mp4"
    with source.open("wb") as stream:
        stream.write(b"large-media-block" * (size // 17))
    size = source.stat().st_size
    ingest.message(7, message(size=size), "in")
    request_id = confirm_all(state)
    worker = state.runtime.worker
    job_id = worker.claim()
    fake = FakeClient(source)
    await worker.run(fake, job_id)
    detail = state.tasks.detail(request_id)
    assert detail["status"] == "succeeded"
    target = config.download_root / detail["items"][0]["relative_path"]
    assert file_hash(target) == file_hash(source)
    with state.db.sessions() as session:
        assert session.scalar(select(Notification).where(Notification.event_key.like("result:%"))) is not None
        assert session.scalar(select(PublishJournal)) is None
    repeated = message(2, size=size)
    repeated["video"]["file_unique_id"] = "unique-1"
    ingest.message(7, repeated, "in2")
    request2 = confirm_all(state)
    assert worker.claim() is None
    assert state.tasks.detail(request2)["status"] == "already_exists"
    assert fake.calls == 1
    target.unlink()
    repeated["message_id"] = 3
    ingest.message(7, repeated, "in3")
    request3 = confirm_all(state)
    job3 = worker.claim()
    assert job3
    await worker.run(fake, job3)
    assert state.tasks.detail(request3)["status"] == "succeeded"
    assert fake.calls == 2


@pytest.mark.integration
async def test_cancel_acquisition_keeps_slot_until_remote_returns(client: TestClient, config: Config) -> None:
    state, ingest = setup_pipeline(client)
    source = config.bot_api_root / "file"
    source.write_bytes(b"abcdef")
    ingest.message(7, message(), "in")
    request_id = confirm_all(state)
    job_id = state.runtime.worker.claim()
    entered, finish = asyncio.Event(), asyncio.Event()

    class SlowClient:
        async def get_file(self, file_id: str) -> str:
            entered.set()
            await finish.wait()
            return str(source)

    task = asyncio.create_task(state.runtime.worker.run(SlowClient(), job_id))
    await entered.wait()
    state.tasks.cancel(request_id)
    assert state.runtime.counts()[0] == 1
    assert state.tasks.detail(request_id)["status"] == "cancelling"
    finish.set()
    await task
    assert state.tasks.detail(request_id)["status"] == "cancelled"
    assert state.runtime.counts()[0] == 0


def test_crash_after_publish_reconciles_without_overwrite(client: TestClient, config: Config) -> None:
    state, ingest = setup_pipeline(client)
    ingest.message(7, message(), "in")
    request_id = confirm_all(state)
    job_id = state.runtime.worker.claim()
    with state.db.sessions() as session:
        job = session.get(Job, job_id)
        final = contained(config.download_root, job.relative_path, create_parent=True)
        relative = job.relative_path
    temporary = final.with_name(f".part.{job_id}")
    temporary.write_bytes(b"abcdef")
    state.runtime.worker.journal_copying(job_id, temporary.relative_to(config.download_root).as_posix(), relative)
    size, sha = file_hash(temporary)
    state.runtime.worker.journal_ready(job_id, size, sha)
    publish_no_replace(temporary, final)  # crash before the database marks success
    state.runtime.worker.recover(False)
    assert state.tasks.detail(request_id)["status"] == "succeeded"
    assert final.read_bytes() == b"abcdef"
    state.runtime.worker.recover(False)
    assert state.tasks.detail(request_id)["status"] == "succeeded"


def test_acquisition_recovery_blocks_until_api_generation_changed(client: TestClient) -> None:
    state, ingest = setup_pipeline(client)
    ingest.message(7, message(), "in")
    request_id = confirm_all(state)
    state.runtime.worker.claim()
    state.runtime.worker.recover(False)
    assert state.tasks.detail(request_id)["status"] == "recovery_blocked"
    assert state.runtime.counts()[0] == 1
    state.runtime.worker.recover(True)
    assert state.tasks.detail(request_id)["status"] == "queued"


def test_links_unauthorized_and_unsupported_documents_create_no_media(client: TestClient) -> None:
    state, ingest = setup_pipeline(client)
    wrong = message()
    wrong["from"]["id"] = 43
    ingest.message(7, wrong, "in1")
    ingest.message(7, {"message_id": 2, "from": {"id": 42}, "chat": {"id": 42, "type": "private"}, "text": "https://t.me/channel/1"}, "in2")
    wrong = message(3, kind="document")
    wrong["document"]["file_name"] = "x.exe"
    ingest.message(7, wrong, "in3")
    with state.db.sessions() as session:
        assert session.scalar(select(func.count()).select_from(MediaItem)) == 0


def test_path_safety_and_exclusive_publication(config: Config) -> None:
    assert "/" not in safe_name("../../evil/name.mp4", "file.mp4")
    assert safe_name("CON", "file") != "CON"
    with pytest.raises(AppError):
        contained(config.download_root, "../outside")
    outside = config.data_dir / "outside"
    outside.write_bytes(b"old")
    with pytest.raises(AppError):
        source_file(config.bot_api_root, str(outside))
    temporary, final = config.download_root / "part", config.download_root / "final"
    temporary.write_bytes(b"new")
    final.write_bytes(b"old")
    with pytest.raises(FileExistsError):
        publish_no_replace(temporary, final)
    assert final.read_bytes() == b"old"


def test_storage_marker_loss_does_not_reinitialize(client: TestClient, config: Config) -> None:
    storage = client.app.state.storage.get("default")
    storage.check()
    (config.download_root / ".tgd-root").unlink()
    with pytest.raises(AppError):
        storage.check()
    assert not (config.download_root / ".tgd-root").exists()
