import pytest

from app.bot.messages import STATUS_LABELS, status_label
from app.services.tasks import task_message


@pytest.mark.parametrize("limit", [750, 3900])
def test_emoji_message_truncation_preserves_complete_identifier(limit: int) -> None:
    request_id = "0123456789abcdef0123"
    prefix = f"📥 下载中\n任务编号：{request_id}\n\n"
    text = task_message("📥 下载中\n" + "🌅" * 3000, request_id, limit=limit)

    assert len(text.encode("utf-16-le")) // 2 <= limit
    assert text.startswith(prefix)
    assert text.endswith("…")
    assert text.count(prefix) == 1
    assert "\ufffd" not in text
    # Strict encoding above also rejects an isolated surrogate at the cut.
    assert text.count("📥 下载中") == 1


def test_explicit_confirmation_stage_preserves_custom_body_exactly() -> None:
    body = "\n自定义 <标题>\n任务编号：自定义文本\n📥 确认下载：旅行\n\n"
    assert task_message(body, "full-id", stage="📋 等待确认") == (
        "📋 等待确认\n任务编号：full-id\n\n" + body)


@pytest.mark.parametrize("state", [*STATUS_LABELS, "unknown"])
def test_status_uses_existing_translated_stage_once(state: str) -> None:
    stage = status_label(state)
    assert task_message(f"{stage}\n海边旅行", "full-id", limit=750) == (
        f"{stage}\n任务编号：full-id\n\n海边旅行")
