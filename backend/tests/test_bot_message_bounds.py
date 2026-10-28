import pytest

from app.services.tasks import task_message


@pytest.mark.parametrize("limit", [750, 3900])
def test_emoji_message_truncation_preserves_complete_identifier(limit: int) -> None:
    request_id = "0123456789abcdef0123"
    suffix = f"\n任务编号：{request_id}"
    text = task_message("📥 下载中\n" + "🌅" * 3000, request_id, limit=limit)

    assert len(text.encode("utf-16-le")) // 2 <= limit
    assert text.endswith("…" + suffix)
    assert text.count(suffix) == 1
    assert "\ufffd" not in text
    # Strict encoding above also rejects an isolated surrogate at the cut.
    assert text.startswith("📥 下载中\n")
