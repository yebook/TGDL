"""Plain-text Bot presentation; never use these labels as persisted states."""

from collections import Counter
from collections.abc import Sequence
from typing import Any

from app.core.file_types import TYPE_LABELS

STATUS_LABELS = {
    "collecting": "⏳ 正在整理文件",
    "awaiting_confirmation": "📥 等待确认",
    "queued": "⏳ 等待下载",
    "fetching": "📥 下载中",
    "saving": "📥 下载/保存中",
    "retry_wait": "⏳ 等待自动重试",
    "waiting_existing": "⏳ 等待相同文件完成",
    "recovery_blocked": "⚠️ 等待恢复",
    "cancelling": "⏳ 正在取消",
    "succeeded": "✅ 下载成功",
    "already_exists": "✅ 文件已保存，无需重复下载",
    "partial_success": "⚠️ 部分完成",
    "failed": "❌ 下载失败",
    "cancelled": "❌ 已取消",
    "expired": "⌛ 确认已过期",
}


def status_label(state: str) -> str:
    return STATUS_LABELS.get(state, "⚠️ 请在 WebUI 查看状态")


STAGE_LABELS = {
    "awaiting_confirmation": "📋 等待确认",
    "queued": "⏳ 已加入下载队列",
    "succeeded": "✅ 下载完成",
    "already_exists": "📁 文件已存在",
    "partial_success": "⚠️ 部分下载完成",
    "failed": "❌ 下载失败",
    "cancelled": "🛑 下载已取消",
}


def utf16_units(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def shorten(text: str, budget: int) -> str:
    if utf16_units(text) <= budget:
        return text
    return text.encode("utf-16-le")[:max(0, budget - 1) * 2].decode("utf-16-le", errors="ignore") + "…"


def format_bytes(value: int | None) -> str:
    if value is None:
        return "未知"
    units = ("B", "KB", "MB", "GB", "TB")
    size = float(value)
    unit = 0
    while size >= 1024 and unit < len(units) - 1:
        size /= 1024
        unit += 1
    return f"{size:.0f} B" if unit == 0 else f"{size:.1f} {units[unit]}"


def storage_warning(assessment: dict[str, Any]) -> str:
    """Render only structured capacity values and fixed location labels."""
    if assessment.get("sufficient") is True:
        return ""
    lines = ["⚠️ 空间提醒：下载或保存前会重新检查；不足时任务将失败，可释放空间后手动重试。"]
    labels = {"target": "保存盘", "cache": "缓存盘", "target+cache": "缓存与保存盘（同盘）"}
    for volume in assessment.get("volumes", []):
        if not isinstance(volume, dict):
            continue
        location = volume.get("location")
        label = labels.get(location, "存储盘") if isinstance(location, str) else "存储盘"
        if volume.get("error_code"):
            lines.append(f"{label}：无法检测空间，请检查挂载和权限。")
            continue
        if not volume.get("missing_bytes"):
            continue
        def capacity(key: str, row: dict[str, Any] = volume) -> str:
            value = row.get(key)
            return format_bytes(value if type(value) is int and value >= 0 else None)
        lines.append(f"{label}：可用 {capacity('free_bytes')}，本次需要 {capacity('required_bytes')}，"
                     f"并发预留 {capacity('concurrent_bytes')}，安全余量 {capacity('reserve_bytes')}，"
                     f"尚缺 {capacity('missing_bytes')}。")
    return "\n".join(lines)


def type_summary(types: Sequence[str]) -> str:
    counts = Counter(types)
    return " · ".join(f"{TYPE_LABELS.get(kind, '其他文件')} × {count}" for kind, count in counts.items())


def message_prefix(stage: str, request_id: str) -> str:
    return f"{stage}\n任务编号：{request_id}\n\n"


def task_message(text: str, request_id: str, limit: int = 3900, *, stage: str | None = None) -> str:
    """Keep system stage and complete ID outside the editable confirmation body."""
    if stage is None:
        # /status already supplies a translated status on its first line.
        first, separator, rest = text.partition("\n")
        if first in STATUS_LABELS.values() or first == status_label(""):
            stage, text = first, rest if separator else ""
        else:
            stage = "📋 任务状态"
    prefix = message_prefix(stage, request_id)
    return prefix + shorten(text, limit - utf16_units(prefix))


def file_list_body(stage: str, request_id: str, summary: str, rows: Sequence[str], footer: str = "") -> str:
    """Spend the remaining budget on complete rows, keeping summary and advice."""
    budget = 3900 - utf16_units(message_prefix(stage, request_id))
    suffix = f"\n\n{footer}" if footer else ""
    selected: list[str] = []
    for index, row in enumerate(rows):
        remaining = len(rows) - index - 1
        omitted = f"\n… 另有 {remaining} 个文件未展开，请在 WebUI 查看完整清单。" if remaining else ""
        candidate = summary + "\n" + "\n".join([*selected, row]) + omitted + suffix
        if utf16_units(candidate) > budget:
            selected.append(f"… 另有 {len(rows) - index} 个文件未展开，请在 WebUI 查看完整清单。")
            break
        selected.append(row)
    return summary + "\n" + "\n".join(selected) + suffix


def failure_hint(code: str | None) -> str:
    """Only fixed, user-actionable reasons; never interpolate upstream errors."""
    return {
        "NO_SPACE": "存储空间不足，请释放对应磁盘空间后重试",
        "PERMISSION_DENIED": "目录权限不足，请检查下载目录的读写权限",
        "NETWORK_ERROR": "连接异常，请检查网络或代理后重试",
        "CLOUD_DOWNLOAD_FAILED": "下载连接异常，请检查网络或代理后重试",
        "CLOUD_FILE_TOO_LARGE": "文件超过云端 20 MB 限制，请切换本地 API 后重试",
        "STORAGE_CHANGED": "下载目录已变化，请检查挂载和目录登记",
        "STORAGE_UNAVAILABLE": "下载目录不可用，请检查磁盘挂载",
        "STORAGE_ERROR": "保存失败，请检查磁盘与目录权限",
        "SIZE_MISMATCH": "文件大小校验失败，请重试",
        "BOT_UNAUTHORIZED": "Bot 凭据无效，请检查 Token 后手动校验",
        "RATE_LIMITED": "Telegram 请求受限，请等待限制结束后重试",
    }.get(code or "", "请在 WebUI 查看详情")
