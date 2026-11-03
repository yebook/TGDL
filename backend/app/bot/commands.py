"""Shared menu/help definitions and exact, non-destructive command parsing."""
import re
from dataclasses import dataclass

COMMANDS = (
    ("help", "查看使用说明"),
    ("status", "查看最近任务和任务编号"),
    ("cancel", "取消指定任务：/cancel 任务编号"),
    ("cancel_all", "取消我的全部未完成任务"),
)
CACHE_NOTICE = "安全停止后将删除该任务的缓存及已保存文件（含组内已保存项），任务记录保留；清理结果和未删除原因可在 WebUI 详情查看。"


@dataclass(frozen=True)
class Command:
    name: str
    arguments: tuple[str, ...]


def parse_command(text: str, username: str = "") -> Command | None:
    parts = text.strip().split()
    if not parts:
        return None
    match = re.fullmatch(r"/([a-z_]+)(?:@([A-Za-z0-9_]+))?", parts[0])
    if not match or match[2] and (not username or match[2].lower() != username.lower()):
        return None
    if match[1] not in {name for name, _ in COMMANDS} | {"start"}:
        return None
    return Command(match[1], tuple(parts[1:]))


def help_text(auto_download: bool) -> str:
    workflow = ("我会先整理文件信息，然后自动加入下载队列，无需手动确认。同组文件会一起整理。\n"
                if auto_download else
                "我会先整理文件信息，等你确认后再下载。同组文件共用确认提示。\n"
                "✅ 点击「确认下载」开始，点击「取消」放弃本次请求。\n")
    commands = "\n".join(f"/{name}：{description}" for name, description in COMMANDS)
    return ("📥 发送或转发文件（如 APK、PDF）、视频、图片、音频、语音、动画或视频留言，"
            + workflow + "📋 菜单命令：\n" + commands + "\n"
            "指定取消请使用 /status 中的完整任务编号，例如 /cancel 任务编号。\n"
            "仅取消你在当前 Bot 私聊中的任务；正在下载或保存时需要等待安全停止。"
            + CACHE_NOTICE + "\n"
            "✏️ 转发时可先附带一条文字，作为下一条文件或文件组的名称，仅使用一次。\n"
            "暂不支持消息链接和贴纸。")
