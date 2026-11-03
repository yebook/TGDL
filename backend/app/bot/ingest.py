import re
import time
from typing import Any

from sqlalchemy import select

from app.bot.commands import CACHE_NOTICE, help_text, parse_command
from app.bot.grouping import GROUP_MAX_SECONDS, GROUP_MIN_SECONDS, GROUP_QUIET_SECONDS
from app.bot.messages import status_label
from app.core.errors import AppError
from app.core.file_types import document_type
from app.db.models import (
    BotIdentity,
    CancelCleanup,
    IngestUpdate,
    MediaItem,
    MediaRequest,
    Notification,
    PendingMediaName,
    PollCursor,
    uid,
)
from app.db.session import Database
from app.services.settings import SettingsService
from app.services.tasks import ACTIVE, TaskService, task_message
from app.storage.naming import text_summary


class IngestService:
    def __init__(self, database: Database, settings: SettingsService, tasks: TaskService) -> None:
        self.db, self.settings, self.tasks = database, settings, tasks

    def offset(self, bot_id: int) -> int:
        with self.db.sessions() as session:
            cursor = session.get(PollCursor, bot_id)
            return cursor.next_offset if cursor else 0

    def persist_batch(self, bot_id: int, updates: list[dict[str, Any]]) -> None:
        with self.db.transaction() as session:
            cursor = session.get(PollCursor, bot_id)
            if not cursor:
                cursor = PollCursor(bot_id=bot_id, next_offset=0)
                session.add(cursor)
            for update in updates:
                if not session.scalar(select(IngestUpdate).where(IngestUpdate.bot_id == bot_id,
                                                                IngestUpdate.update_id == update["update_id"])):
                    session.add(IngestUpdate(bot_id=bot_id, update_id=update["update_id"], payload=update))
                cursor.next_offset = max(cursor.next_offset, int(update["update_id"]) + 1)

    def pending(self, bot_id: int) -> list[tuple[str, dict[str, Any]]]:
        with self.db.sessions() as session:
            return [(u.id, u.payload) for u in session.scalars(select(IngestUpdate).where(
                IngestUpdate.bot_id == bot_id, IngestUpdate.processed.is_(False)).order_by(IngestUpdate.update_id).limit(100))]

    def mark_processed(self, update_id: str) -> None:
        with self.db.transaction() as session:
            row = session.get(IngestUpdate, update_id)
            if row:
                row.processed, row.payload = True, {}  # file identifiers only retained in normalized items

    def message(self, bot_id: int, message: dict[str, Any], inbox_id: str) -> None:
        _, settings, _ = self.settings.read()
        user_id = message.get("from", {}).get("id")
        chat = message.get("chat", {})
        # aiogram dumps alias from_user as from_user unless by_alias=True.
        user_id = user_id or message.get("from_user", {}).get("id")
        if chat.get("type") != "private" or user_id not in settings.allowed_user_ids:
            return
        chat_id = int(chat["id"])
        media: dict[str, Any] | None = None
        kind = "document"
        # Telegram animations also expose document; the first native attachment
        # wins and each message yields at most one normalized item.
        for field in ("animation", "video", "photo", "audio", "voice", "video_note", "document"):
            attachment = message.get(field)
            if not attachment:
                continue
            if field == "photo":
                media = max(attachment, key=lambda p: p.get("width", 0) * p.get("height", 0))
                kind = "image"
            else:
                media = attachment
                kind = document_type(media.get("file_name"), media.get("mime_type")) if field == "document" else field
            break
        if media and (not isinstance(media.get("file_id"), str) or not media["file_id"].strip()):
            media = None
        with self.db.transaction() as session:
            pending = session.get(PendingMediaName, (bot_id, chat_id, user_id))
            if media:
                if session.scalar(select(MediaItem).where(MediaItem.bot_id == bot_id, MediaItem.chat_id == chat_id,
                                                          MediaItem.message_id == message["message_id"])):
                    return
                group_key = str(message["media_group_id"]) if message.get("media_group_id") else None
                request = session.scalar(select(MediaRequest).where(
                    MediaRequest.bot_id == bot_id, MediaRequest.chat_id == chat_id,
                    MediaRequest.group_key == group_key, MediaRequest.status == "collecting",
                    MediaRequest.deleted_at.is_(None)
                ).order_by(MediaRequest.created_at.desc())) if group_key else None
                now = time.time()
                if request and request.created_at + GROUP_MAX_SECONDS <= now:
                    request = None  # Hard cap; displayed cards are never selected above.
                if not request:
                    name_override = None
                    if pending and pending.name and message["message_id"] > pending.message_id:
                        name_override = pending.name
                        pending.name = None  # Consume once, keeping the message watermark for replay safety.
                    request = MediaRequest(id=uid(), bot_id=bot_id, chat_id=chat_id, user_id=user_id,
                                           kind="group" if group_key else "single", group_key=group_key,
                                           name_override=name_override,
                                           created_at=now, updated_at=now,
                                           collect_until=now + (GROUP_MIN_SECONDS if group_key else 0))
                    session.add(request)
                    session.flush()
                elif group_key:
                    request.collect_until = min(request.created_at + GROUP_MAX_SECONDS,
                                                max(request.created_at + GROUP_MIN_SECONDS,
                                                    now + GROUP_QUIET_SECONDS))
                session.add(MediaItem(request_id=request.id, bot_id=bot_id, chat_id=chat_id,
                                      message_id=message["message_id"], file_id=media["file_id"],
                                      file_unique_id=media.get("file_unique_id"), media_type=kind,
                                      mime_type=media.get("mime_type"),
                                      original_name=media.get("file_name"), size_bytes=media.get("file_size"),
                                      caption_snippet=text_summary(message.get("caption") or message.get("text")) or None,
                                      duration_seconds=media.get("duration")))
            else:
                text = str(message.get("text", ""))
                key = f"reply:{inbox_id}"
                if session.scalar(select(Notification).where(Notification.event_key == key)):
                    return
                summary = text_summary(text)
                is_name = bool(summary) and not text.lstrip().startswith(("/", "https://", "http://", "t.me/")) and not any(
                    message.get(field) for field in ("forward_origin", "forward_date", "forward_from", "forward_from_chat"))
                if pending is None:
                    pending = PendingMediaName(bot_id=bot_id, chat_id=chat_id, user_id=user_id,
                                               message_id=message["message_id"], name=None)
                    session.add(pending)
                elif message["message_id"] <= pending.message_id:
                    return
                pending.message_id = message["message_id"]
                pending.name = summary if is_name else None
                if is_name:
                    return  # The media confirmation shows the name; do not send an extra acknowledgement.
                identity = session.get(BotIdentity, bot_id)
                command = parse_command(text, identity.username if identity else "")
                forwarded = any(message.get(field) for field in (
                    "forward_origin", "forward_date", "forward_from", "forward_from_chat"))
                if command and command.name in {"cancel", "cancel_all"}:
                    if forwarded:
                        response = "⚠️ 转发的取消命令不会执行，请亲自发送 /cancel 任务编号 或 /cancel_all。"
                    elif command.name == "cancel" and (len(command.arguments) != 1
                            or not re.fullmatch(r"[0-9a-f]{20}", command.arguments[0])):
                        response = "用法：/cancel 完整任务编号。请先发送 /status 查看任务编号。"
                    elif command.name == "cancel_all" and command.arguments:
                        response = "用法：/cancel_all（无需参数），取消当前私聊中你的全部未完成任务。"
                    else:
                        query = select(MediaRequest).where(
                            MediaRequest.bot_id == bot_id, MediaRequest.user_id == user_id,
                            MediaRequest.chat_id == chat_id, MediaRequest.deleted_at.is_(None))
                        if command.name == "cancel":
                            query = query.where(MediaRequest.id == command.arguments[0])
                        else:
                            query = query.where(MediaRequest.status.in_(ACTIVE | {"collecting", "awaiting_confirmation"}))
                        rows = list(session.scalars(query))
                        outcomes = [self.tasks.cancel_in_session(session, row) for row in rows]
                        cancelled, stopping = outcomes.count("cancelled"), outcomes.count("cancelling")
                        if command.name == "cancel" and not rows:
                            response = "未找到可操作的任务，请用 /status 检查当前私聊的完整任务编号。"
                        elif cancelled or stopping:
                            response = f"🛑 已取消 {cancelled} 个任务；正在取消 {stopping} 个任务（等待安全停止或清理）。文件清理尚未确认完成。" + CACHE_NOTICE
                        elif command.name == "cancel":
                            response = f"任务当前状态：{status_label(rows[0].status)}，无需取消；本次没有删除文件。"
                        else:
                            response = "当前没有需要取消的未完成任务（任务可能已完成或已取消）；本次没有删除文件。"
                elif command and command.arguments:
                    response = f"用法：/{command.name}（无需参数）。/help 查看使用说明。"
                elif command and command.name == "status":
                    rows = list(session.scalars(select(MediaRequest).where(MediaRequest.user_id == user_id,
                                                                            MediaRequest.bot_id == bot_id,
                                                                            MediaRequest.chat_id == chat_id,
                                                                            MediaRequest.deleted_at.is_(None))
                                                .order_by(MediaRequest.created_at.desc()).limit(5)))
                    summaries = []
                    for row in rows:
                        cleanup = session.get(CancelCleanup, row.id)
                        text = f"{status_label(row.status)}\n{row.title}"
                        if cleanup:
                            text += "\n文件清理：" + cleanup.message
                        summaries.append(task_message(text, row.id, limit=750))
                    response = "\n\n".join(summaries) or "📭 暂无下载任务，发送文件即可开始。"
                else:
                    response = help_text(settings.auto_download)
                if not session.scalar(select(Notification).where(Notification.event_key == key)):
                    session.add(Notification(event_key=key, chat_id=chat_id, payload={"text": response, "bot_id": bot_id}))

    def callback(self, payload: dict[str, Any]) -> str:
        data = str(payload.get("data", "")).split(":")
        if len(data) != 3 or data[0] not in {"y", "n"} or not data[1] or not data[2]:
            raise AppError("INVALID_CALLBACK", "⌛ 这个按钮已失效，请重新发送文件获取确认提示")
        user = payload.get("from", payload.get("from_user", {}))
        try:
            user_id = int(user.get("id", 0))
            chat_id = int(payload.get("message", {}).get("chat", {}).get("id", 0))
        except (TypeError, ValueError, AttributeError):
            raise AppError("INVALID_CALLBACK", "⌛ 这个按钮已失效，请重新发送文件获取确认提示") from None
        try:
            state = self.tasks.confirm(data[1], data[2], user_id, chat_id,
                                       "confirm" if data[0] == "y" else "cancel")
        except AppError as exc:
            if exc.code == "FORBIDDEN":
                raise AppError("FORBIDDEN", "⚠️ 无法操作此请求，请使用获授权的账号重新发送文件", 403) from None
            raise
        messages = {
            "deleted": "🗑️ 任务记录已删除，如需下载请重新发送文件",
            "queued": "⏳ 已加入下载队列，请等待下载结果",
            "fetching": "📥 任务正在下载，请等待下载结果",
            "saving": "📥 任务正在下载或正在保存，请稍候",
            "retry_wait": "⏳ 下载暂时失败，正在等待自动重试",
            "waiting_existing": "⏳ 正在等待相同文件的下载结果，无需重复确认",
            "recovery_blocked": "⚠️ 任务正在等待恢复，请在 WebUI 查看状态",
            "cancelling": "⏳ 任务正在取消，请等待处理结果",
            "succeeded": "✅ 文件已下载成功，无需重复确认",
            "already_exists": "✅ 文件已保存，无需重复下载",
            "partial_success": "⚠️ 部分文件已保存，请在 WebUI 查看详情或重试失败项",
            "failed": "❌ 下载失败，请在 WebUI 查看原因并重试",
            "cancelled": "❌ 该请求已取消，如需下载请重新发送文件",
            "expired": "⌛ 确认已过期，未开始下载。请重新发送文件获取新的确认提示",
            "configuration_changed": "🔄 保存配置已变更，正在生成新的确认提示，请使用新提示操作",
            "collecting": "⏳ 正在生成确认提示，请稍候再操作",
            "awaiting_confirmation": "📥 请使用最新的确认提示操作",
        }
        text = messages.get(state, "⚠️ 请在 WebUI 查看任务的最新状态")
        if data[0] == "n" and state in {"queued", "fetching", "saving", "retry_wait", "waiting_existing", "recovery_blocked"}:
            text += "。如需取消，请前往 WebUI 操作"
        return text
