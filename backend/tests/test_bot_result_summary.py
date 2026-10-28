import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from test_pipeline import setup_pipeline

from app.db.models import Job, MediaItem, MediaRequest, Notification
from app.services.tasks import project


def build_result(client: TestClient, states: list[str], *, separate_paths: bool = False,
                 title: str = "海边旅行") -> tuple[str, str]:
    state, _ = setup_pipeline(client)
    with state.db.transaction() as session:
        request = MediaRequest(bot_id=7, user_id=42, chat_id=42, title=title,
                               status="queued", collect_until=0)
        session.add(request)
        session.flush()
        request_id = request.id
        for index, outcome in enumerate(states, 1):
            item = MediaItem(request_id=request_id, bot_id=7, chat_id=42, message_id=index,
                             file_id=f"file-{index}", media_type="video")
            session.add(item)
            session.flush()
            directory = f"2026-10-28/旅行{index}" if separate_paths else "2026-10-28/旅行"
            session.add(Job(request_id=request_id, item_id=item.id, state=outcome,
                            relative_path=f"{directory}/video_{index}.mp4",
                            error_code="NO_SPACE", error_message="unsafe-upstream-secret"))
        session.flush()
        project(session, request)
        project(session, request)
        assert request.result_revision == 1
    with state.db.sessions() as session:
        notes = list(session.scalars(select(Notification).where(Notification.event_key.like("result:%"))))
        assert len(notes) == 1
        assert notes[0].event_key == f"result:{request_id}:1"
        return request_id, notes[0].payload["text"]


def test_two_file_success_has_one_overall_result_and_clear_file_list(client: TestClient) -> None:
    request_id, text = build_result(client, ["succeeded", "succeeded"])
    assert text == ("✅ 下载完成：海边旅行\n"
                    "文件：2 个 · 已下载 2 个\n"
                    "📁 保存到：2026-10-28/旅行\n"
                    "文件清单：\n✅ video_1.mp4\n✅ video_2.mp4\n"
                    f"任务编号：{request_id}")
    assert "下载成功" not in text


@pytest.mark.parametrize("states,summary,details", [
    (["already_exists", "already_exists"], "已保存，无需重复下载 2 个", ["📎 已保存 video_1.mp4"]),
    (["succeeded", "already_exists"], "已下载 1 个 · 已保存，无需重复下载 1 个", ["📎 已保存 video_2.mp4"]),
    (["succeeded", "failed"], "已下载 1 个 · 失败 1 个", ["⚠️ 部分完成", "❌ 失败 video_2.mp4", "存储空间不足"]),
    (["succeeded", "cancelled"], "已下载 1 个 · 取消 1 个", ["⚠️ 部分完成", "🚫 已取消 video_2.mp4"]),
    (["failed", "failed"], "失败 2 个", ["❌ 下载失败", "请在 WebUI"]),
    (["cancelled", "cancelled"], "取消 2 个", ["❌ 已取消", "重新发送媒体"]),
])
def test_mixed_and_unsaved_results_are_honest(client: TestClient, states: list[str], summary: str,
                                             details: list[str]) -> None:
    request_id, text = build_result(client, states)
    assert f"文件：2 个 · {summary}" in text
    assert all(detail in text for detail in details)
    assert "unsafe-upstream-secret" not in text
    assert text.endswith(f"\n任务编号：{request_id}")
    if "succeeded" not in states:
        assert "已下载" not in text
    if all(outcome in {"failed", "cancelled"} for outcome in states):
        assert "📁 保存到" not in text
    if "failed" not in states:
        assert "重试失败文件" not in text


def test_result_uses_actual_paths_when_files_saved_in_different_directories(client: TestClient) -> None:
    _, text = build_result(client, ["succeeded", "already_exists"], separate_paths=True)
    assert "✅ 2026-10-28/旅行1/video_1.mp4" in text
    assert "📎 已保存 2026-10-28/旅行2/video_2.mp4" in text
    assert "📁 保存到" not in text


def test_long_result_preserves_full_identifier_without_splitting_emoji(client: TestClient) -> None:
    request_id, text = build_result(client, ["succeeded"], title="🌅" * 2500)
    assert len(text.encode("utf-16-le")) // 2 <= 3900
    assert text.endswith(f"\n任务编号：{request_id}")
    assert "\ufffd" not in text


def test_single_file_summary_has_no_extra_file_status_sentences(client: TestClient) -> None:
    _, text = build_result(client, ["succeeded"])
    assert "文件：1 个 · 已下载 1 个" in text
    assert text.count("下载完成") == 1
    assert text.count("video_1.mp4") == 1


def test_retry_emits_one_new_summary_only_after_remaining_file_finishes(client: TestClient) -> None:
    request_id, original = build_result(client, ["succeeded", "failed"])
    state = client.app.state
    with state.db.transaction() as session:
        request = session.get(MediaRequest, request_id)
        failed = session.scalar(select(Job).where(Job.request_id == request_id, Job.state == "failed"))
        failed.state = "queued"
        project(session, request)
        assert request.result_revision == 1
        session.flush()
        assert len(list(session.scalars(select(Notification)))) == 1
        failed.state = "succeeded"
        project(session, request)
        project(session, request)
        assert request.result_revision == 2
    with state.db.sessions() as session:
        notes = list(session.scalars(select(Notification).order_by(Notification.event_key)))
        assert len(notes) == 2
        assert notes[0].payload["text"] == original
        assert notes[1].event_key == f"result:{request_id}:2"
        assert "已下载 2 个" in notes[1].payload["text"]
