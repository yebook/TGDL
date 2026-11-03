"""Bot warnings and worker admission use the same real device assessment."""
import shutil
from collections import namedtuple

import pytest
from sqlalchemy import select
from test_pipeline import confirm_all, message, setup_pipeline

from app.bot.messages import utf16_units
from app.db.models import Job, Notification
from app.schemas.contracts import SettingsPatch


@pytest.mark.parametrize("automatic", [False, True])
def test_one_copy_space_warns_and_fails_without_waiting(client, config, monkeypatch, automatic):
    state, ingest = setup_pipeline(client)
    patch = SettingsPatch(revision=state.settings.read()[0], auto_download=automatic)
    state.settings.save(patch, state.settings.candidate(patch))
    usage = namedtuple("Usage", "total used free")
    available = [config.minimum_free_bytes + 6]
    monkeypatch.setattr(shutil, "disk_usage", lambda _: usage(100000, 100000 - available[0], available[0]))
    ingest.message(7, message(size=6), "one-copy")
    if automatic:
        state.tasks.freeze_due()
    else:
        confirm_all(state)
    with state.db.sessions() as session:
        notes = list(session.scalars(select(Notification)))
        queued = next(note.payload["text"] for note in notes if note.event_key.startswith("queued:"))
        assert "缓存与保存盘（同盘）" in queued
        assert "本次需要 12 B" in queued
        assert "尚缺 6 B" in queued
        if not automatic:
            confirmation = next(note.payload["text"] for note in notes if note.event_key.startswith("confirm:"))
            assert "本次需要 12 B" in confirmation and "尚缺 6 B" in confirmation
    assert state.runtime.worker.claim(7, "local") is None
    with state.db.sessions() as session:
        job = session.scalar(select(Job))
        assert job.state == "failed" and job.error_code == "NO_SPACE"
        assert job.storage_failure["volumes"][0]["missing_bytes"] == 6
        request_id = job.request_id
        result = session.scalar(select(Notification).where(Notification.event_key.like("result:%"))).payload["text"]
        assert "尚缺 6 B" in result and "手动重试" in result
    available[0] = 100000
    assert state.runtime.worker.claim(7, "local") is None
    state.tasks.retry(request_id, None)
    assert state.runtime.worker.claim(7, "local") is not None


def test_long_custom_confirmation_keeps_space_warning(client, config, monkeypatch):
    state, ingest = setup_pipeline(client)
    patch = SettingsPatch(revision=state.settings.read()[0], confirmation_template="自定义" + "🌅" * 1980 + "\n{files}")
    state.settings.save(patch, state.settings.candidate(patch))
    usage = namedtuple("Usage", "total used free")
    monkeypatch.setattr(shutil, "disk_usage", lambda _: usage(100000, 100000, 0))
    ingest.message(7, message(size=6), "long-warning")
    confirm_all(state)
    with state.db.sessions() as session:
        for note in session.scalars(select(Notification)):
            assert utf16_units(note.payload["text"]) <= 3900
            assert "空间提醒" in note.payload["text"]
            assert "本次需要 12 B" in note.payload["text"]
            assert "手动重试" in note.payload["text"]
