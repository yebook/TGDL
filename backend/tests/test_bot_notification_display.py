import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from test_pipeline import setup_pipeline

from app.core.templates import render_confirmation
from app.db.models import Job, MediaItem, MediaRequest, Notification
from app.schemas.contracts import SettingsPatch


@pytest.mark.parametrize("automatic", [False, True])
@pytest.mark.parametrize("mixed", [False, True])
def test_queued_notice_has_frozen_names_each_size_and_accurate_types(
    client: TestClient, automatic: bool, mixed: bool,
) -> None:
    state, _ = setup_pipeline(client)
    patch = SettingsPatch(revision=state.settings.read()[0], auto_download=automatic)
    state.settings.save(patch, state.settings.candidate(patch))
    types = ["video", "image", "audio", "voice", "animation", "video_note", "document"] if mixed else ["document"]
    labels = ["视频", "图片", "音频", "语音", "动画", "视频留言", "文件"] if mixed else ["文件"]
    with state.db.transaction() as session:
        request = MediaRequest(bot_id=7, user_id=42, chat_id=42, collect_until=0,
                               title="海边旅行", destination="2026-11-02/旅行", kind="group" if mixed else "single")
        session.add(request)
        session.flush()
        request_id, nonce = request.id, request.nonce
        for index, kind in enumerate(types):
            session.add(MediaItem(request_id=request_id, bot_id=7, chat_id=42, message_id=index + 1,
                                  file_id=f"file-{index}", original_name=f"旅行{index}.dat", media_type=kind,
                                  size_bytes=None if mixed and index == 6 else index * 1024))
    state.tasks.freeze_due()
    with state.db.sessions() as session:
        items = list(session.scalars(select(MediaItem).order_by(MediaItem.message_id)))
        names = [item.planned_filename for item in items]
        if not automatic:
            card = session.scalar(select(Notification).where(Notification.event_key.like("confirm:%")))
            settings = state.settings.read()[1]
            body = render_confirmation(settings.confirmation_template, {
                "title": "海边旅行", "destination": "2026-11-02/旅行", "file_count": str(len(types)),
                "total_size": "未知" if mixed else "0 B", "expires_in_minutes": "30",
                "files": "\n\n".join(f"文件名：{name}\n文件大小：{size}" for name, size in zip(
                    names, ["0 B", "1.0 KB", "2.0 KB", "3.0 KB", "4.0 KB", "5.0 KB", "未知"] if mixed else ["0 B"], strict=True)),
            })
            assert card.payload["text"] == f"📋 等待确认\n任务编号：{request_id}\n\n{body}"
            assert card.payload["confirmation"] is True and card.payload["nonce"] == nonce
    if not automatic:
        patch = SettingsPatch(revision=state.settings.read()[0], filename_template="changed_{name}")
        state.settings.save(patch, state.settings.candidate(patch))
        state.tasks.confirm(request_id, nonce, 42, 42, "confirm")
        state.tasks.confirm(request_id, nonce, 42, 42, "confirm")
    state.tasks.freeze_due()
    with state.db.sessions() as session:
        notifications = list(session.scalars(select(Notification)))
        assert len(notifications) == (1 if automatic else 2)
        queued = next(note for note in notifications if note.event_key == f"queued:{request_id}")
        text = queued.payload["text"]
        assert text.startswith(f"⏳ 已加入下载队列\n任务编号：{request_id}\n\n海边旅行\n")
        assert f"{len(types)} 个文件 · 共 {'未知' if mixed else '0 B'}" in text
        for label in labels:
            assert f"{label} × 1" in text
        sizes = ["0 B", "1.0 KB", "2.0 KB", "3.0 KB", "4.0 KB", "5.0 KB", "未知"] if mixed else ["0 B"]
        for index, (name, size) in enumerate(zip(names, sizes, strict=True), 1):
            assert f"{index}. {name} · {size}" in text
        assert "保存目录：2026-11-02/旅行" in text
        assert text.endswith("系统将按并发设置处理，完成后通知你。")
        assert "正在下载" not in text and "确认" not in text
        assert not queued.payload.get("confirmation")
        planned = {item.id: item.planned_filename for item in items}
        assert all(job.relative_path == f"2026-11-02/旅行/{planned[job.item_id]}"
                   for job in session.scalars(select(Job)))


def test_queued_long_emoji_list_preserves_summary_footer_and_omitted_count(client: TestClient) -> None:
    state, _ = setup_pipeline(client)
    with state.db.transaction() as session:
        request = MediaRequest(bot_id=7, user_id=42, chat_id=42, collect_until=0,
                               status="awaiting_confirmation", expires_at=time.time() + 100,
                               title="🌅" * 80, destination="2026-11-02/海边旅行")
        session.add(request)
        session.flush()
        request_id, nonce = request.id, request.nonce
        for index in range(100):
            session.add(MediaItem(request_id=request_id, bot_id=7, chat_id=42, message_id=index + 1,
                                  file_id=f"file-{index}", media_type="document", size_bytes=1024,
                                  planned_filename=f"{index}_" + "🌅" * 35 + ".pdf"))
    state.tasks.confirm(request_id, nonce, 42, 42, "confirm")
    with state.db.sessions() as session:
        text = session.scalar(select(Notification)).payload["text"]
    assert text.startswith(f"⏳ 已加入下载队列\n任务编号：{request_id}\n\n")
    assert "100 个文件 · 共 100.0 KB" in text and "文件 × 100" in text
    shown = sum(line.endswith(".pdf · 1.0 KB") for line in text.splitlines())
    assert 0 < shown < 100
    assert f"另有 {100 - shown} 个文件未展开" in text
    assert text.endswith("系统将按并发设置处理，完成后通知你。")
    assert len(text.encode("utf-16-le")) // 2 <= 3900
    assert "\ufffd" not in text
