"""Plain-text Bot presentation; never use these labels as persisted states."""

STATUS_LABELS = {
    "collecting": "⏳ 正在整理媒体",
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
