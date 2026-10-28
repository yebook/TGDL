from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.bot.grouping import GROUP_MAX_SECONDS
from app.core.errors import AppError
from app.core.templates import render_confirmation
from app.db.models import Attempt, IngestUpdate, Job, MediaItem, MediaRequest, Notification, uid
from app.db.session import Database
from app.services.diagnostics import DiagnosticLog
from app.services.download_progress import DownloadProgress
from app.services.settings import SettingsService
from app.storage.files import contained
from app.storage.naming import fit_name, planned_name, request_summary, unique_name

ACTIVE = {"queued", "fetching", "saving", "cancelling", "retry_wait", "waiting_existing", "recovery_blocked"}
TERMINAL = {"succeeded", "already_exists", "failed", "cancelled"}
DELETABLE = TERMINAL | {"partial_success", "expired"}


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


def iso(value: float | None) -> str | None:
    return datetime.fromtimestamp(value, UTC).isoformat().replace("+00:00", "Z") if value is not None else None


def task_message(text: str, request_id: str, limit: int = 3900) -> str:
    """Reserve a complete system-owned identifier line outside custom content."""
    suffix = f"\n任务编号：{request_id}"
    budget = limit - len(suffix.encode("utf-16-le")) // 2
    if len(text.encode("utf-16-le")) // 2 > budget:
        text = text.encode("utf-16-le")[:(budget - 1) * 2].decode("utf-16-le", errors="ignore") + "…"
    return text + suffix


def enqueue(session: Session, request: MediaRequest, key: str, text: str,
            payload: dict[str, Any] | None = None) -> None:
    if request.deleted_at is not None:
        return
    if not session.scalar(select(Notification).where(Notification.event_key == key)):
        session.add(Notification(event_key=key, request_id=request.id, chat_id=request.chat_id,
                                 payload={"text": task_message(text, request.id), "bot_id": request.bot_id, **(payload or {})}))


def project(session: Session, request: MediaRequest) -> None:
    if request.deleted_at is not None:
        return
    jobs = list(session.scalars(select(Job).where(Job.request_id == request.id)))
    if not jobs:
        return
    states = [job.state for job in jobs]
    if any(state in ACTIVE for state in states):
        state = next((s for s in ("cancelling", "saving", "fetching", "recovery_blocked", "retry_wait") if s in states), "queued")
    else:
        usable = sum(s in {"succeeded", "already_exists"} for s in states)
        if usable == len(states):
            state = "already_exists" if all(s == "already_exists" for s in states) else "succeeded"
        elif usable:
            state = "partial_success"
        elif all(s == "cancelled" for s in states):
            state = "cancelled"
        else:
            state = "failed"
    if state != request.status:
        request.status, request.updated_at = state, time.time()
        if not any(s in ACTIVE for s in states):
            request.result_revision += 1
            labels = {"succeeded": "下载成功", "already_exists": "文件已保存", "partial_success": "部分完成",
                      "cancelled": "已取消", "failed": "下载失败"}
            lines = [f"{labels[state]}：{request.title}"]
            for job in jobs:
                lines.append(f"{job.state}: {job.relative_path}" + (f"（{job.error_message}）" if job.error_message else ""))
            enqueue(session, request, f"result:{request.id}:{request.result_revision}", "\n".join(lines))


class TaskService:
    def __init__(self, database: Database, settings: SettingsService) -> None:
        self.db, self.settings = database, settings
        self.diagnostics: DiagnosticLog | None = None
        self.download_progress: DownloadProgress | None = None

    def destination(self, session: Session, request: MediaRequest, summary: str, timezone: str) -> str:
        date = datetime.fromtimestamp(request.created_at, ZoneInfo(timezone))
        if request.kind == "group":
            prefix = f"{date:%Y-%m-%d}/"
            # Freeze under the same serialized transaction as other confirmation
            # plans. Deleted task records still reserve folders for retained media.
            reserved = session.scalars(select(MediaRequest.destination).where(
                MediaRequest.root_id == request.root_id, MediaRequest.id != request.id,
                MediaRequest.destination.startswith(prefix)))
            used = {path[len(prefix):].split("/")[0].casefold() for path in reserved}
            used.update(name.casefold() for name in session.scalars(select(MediaItem.planned_filename).join(
                MediaRequest, MediaItem.request_id == MediaRequest.id).where(
                    MediaRequest.root_id == request.root_id, MediaRequest.destination == prefix[:-1],
                    MediaItem.planned_filename.is_not(None))) if name is not None)
            root = self.settings.config.storage_roots[request.root_id]
            try:
                day = contained(root, prefix + ".tgd-folder-check").parent
                used.update(path.name.casefold() for path in day.iterdir())
            except FileNotFoundError:
                # Planning never recreates an unavailable mount; worker checks it.
                pass
            name, sequence = fit_name(summary, ""), 2
            while name.casefold() in used:
                name = fit_name(summary, "", f" ({sequence})")
                sequence += 1
            return prefix + name
        return f"{date:%Y-%m-%d}"

    def freeze_due(self) -> None:
        # Most ticks are idle. Avoid a write reservation and decrypting settings
        # unless there is work; recheck the full conditions in the transaction.
        now = time.time()
        with self.db.sessions() as session:
            due = session.scalar(select(MediaRequest.id).where(
                MediaRequest.deleted_at.is_(None), or_(
                    (MediaRequest.status == "collecting") & (MediaRequest.collect_until <= now),
                    (MediaRequest.status == "awaiting_confirmation") & (MediaRequest.expires_at < now),
                )).limit(1))
        if due is None:
            return
        revision, settings, _ = self.settings.read()
        with self.db.transaction() as session:
            now = time.time()
            pending_bots = set(session.scalars(select(IngestUpdate.bot_id).where(
                IngestUpdate.processed.is_(False)).distinct()))
            requests = session.scalars(select(MediaRequest).where(
                MediaRequest.deleted_at.is_(None), MediaRequest.status == "collecting", MediaRequest.collect_until <= now))
            for request in requests:
                # Already durable members must get a chance to join before the card
                # freezes. Do not let unrelated/stuck inbox work exceed the hard cap.
                if (request.kind == "group" and request.bot_id in pending_bots
                        and now < request.created_at + GROUP_MAX_SECONDS):
                    continue
                items = list(session.scalars(select(MediaItem).where(
                    MediaItem.request_id == request.id).order_by(MediaItem.message_id)))
                if not items:
                    continue
                request.status = "awaiting_confirmation"
                request.config_revision = revision
                request.root_id = settings.download_root_id
                if not request.destination:
                    summary = request_summary(request, items)
                    request.title = summary
                    request.destination = self.destination(session, request, summary, settings.timezone)
                # Conservative upper bound if send succeeds but its response is lost.
                request.expires_at = time.time() + settings.confirmation_timeout_seconds
                used: set[str] = set()
                lines = []
                for index, item in enumerate(items, start=1):
                    item.planned_filename = unique_name(planned_name(
                        item, request, settings.filename_template, settings.timezone, index), used)
                    lines.append(f"{item.planned_filename} · {format_bytes(item.size_bytes)}")
                sizes = [item.size_bytes for item in items]
                text = render_confirmation(settings.confirmation_template, {
                    "title": request.title, "destination": request.destination,
                    "file_count": str(len(items)),
                    "expires_in_minutes": f"{settings.confirmation_timeout_seconds / 60:.2f}".rstrip("0").rstrip("."),
                    "total_size": format_bytes(sum(s for s in sizes if s is not None)
                                               if all(s is not None for s in sizes) else None),
                    "files": "\n".join(lines),
                })
                enqueue(session, request, f"confirm:{request.id}:{request.nonce}", text,
                        {"confirmation": True, "nonce": request.nonce})
            for request in session.scalars(select(MediaRequest).where(
                    MediaRequest.deleted_at.is_(None), MediaRequest.status == "awaiting_confirmation", MediaRequest.expires_at < time.time())):
                request.status, request.updated_at = "expired", time.time()

    def confirm(self, request_id: str, nonce: str, user_id: int, chat_id: int, action: str) -> str:
        revision, settings, _ = self.settings.read()
        with self.db.transaction() as session:
            request = session.get(MediaRequest, request_id)
            if (not request or request.user_id != user_id or request.chat_id != chat_id
                    or user_id not in settings.allowed_user_ids or request.nonce != nonce):
                raise AppError("FORBIDDEN", "无效的确认操作", 403)
            if request.deleted_at is not None:
                return "deleted"
            if request.status != "awaiting_confirmation":
                return request.status
            if request.expires_at is None or request.expires_at <= time.time():
                request.status = "expired"
                return "expired"
            if action == "cancel":
                request.status, request.updated_at = "cancelled", time.time()
                return "cancelled"
            if action != "confirm":
                raise AppError("INVALID_ACTION", "无效操作")
            # Only settings that affect consent invalidate this card. Other edits preserve its deadline.
            if request.root_id != settings.download_root_id:
                request.nonce, request.status = uid()[:10], "collecting"
                request.collect_until = time.time()
                return "configuration_changed"
            request.config_revision = revision
            items = list(session.scalars(select(MediaItem).where(MediaItem.request_id == request.id).order_by(MediaItem.message_id)))
            if not items or any(not item.planned_filename for item in items):
                raise AppError("INVALID_FILE_PLAN", "确认信息中的文件名不完整，请重新发送媒体", 409)
            for item in items:
                name = item.planned_filename
                session.add(Job(item_id=item.id, request_id=request.id, root_id=request.root_id,
                                relative_path=f"{request.destination}/{name}", max_auto_retries=settings.max_auto_retries))
            request.status, request.updated_at = "queued", time.time()
            enqueue(session, request, f"queued:{request.id}", f"已加入下载队列：{request.title}")
            return "queued"

    def _dto(self, session: Session, request: MediaRequest, detail: bool = False, *,
             related: tuple[list[MediaItem], list[Job], list[Notification]] | None = None) -> dict[str, Any]:
        if related is None:
            related = (
                list(session.scalars(select(MediaItem).where(MediaItem.request_id == request.id).order_by(MediaItem.message_id))),
                list(session.scalars(select(Job).where(Job.request_id == request.id))),
                list(session.scalars(select(Notification).where(Notification.request_id == request.id))),
            )
        items, job_rows, notifications = related
        jobs = {j.item_id: j for j in job_rows}
        notification_status = "failed" if any(n.state == "failed" for n in notifications) else (
            "pending" if any(n.state == "pending" for n in notifications) else "sent")
        sizes = [i.size_bytes for i in items]
        result: dict[str, Any] = {
            "id": request.id, "title": request.title, "kind": request.kind, "status": request.status,
            "created_at": iso(request.created_at), "updated_at": iso(request.updated_at),
            "item_count": len(items), "completed_count": sum(j.state in {"succeeded", "already_exists"} for j in jobs.values()),
            "total_bytes": sum(s for s in sizes if s is not None) if all(s is not None for s in sizes) else None,
            "completed_bytes": sum(j.completed_bytes for j in jobs.values()),
            "effective_destination": request.destination, "notification_status": notification_status,
            "can_cancel": request.status in ACTIVE | {"collecting", "awaiting_confirmation"},
            "can_retry": any(j.state == "failed" for j in jobs.values()),
            "can_delete": request.status in DELETABLE and all(j.state in TERMINAL for j in jobs.values()),
        }
        if detail:
            result["expires_at"] = iso(request.expires_at)
            result["items"] = []
            for item in items:
                job = jobs.get(item.id)
                attempts = list(session.scalars(select(Attempt).where(Attempt.job_id == job.id).order_by(Attempt.sequence))) if job else []
                result["items"].append({
                    "id": item.id, "job_id": job.id if job else None, "media_type": item.media_type,
                    "original_name": item.original_name, "size_bytes": item.size_bytes,
                    "duration_seconds": item.duration_seconds, "status": job.state if job else request.status,
                    "phase": job.phase if job else request.status, "completed_bytes": job.completed_bytes if job else 0,
                    "acquisition_mode": job.acquisition_mode if job else None,
                    "download_progress": self.download_progress.project(job, bot_id=item.bot_id, file_id=item.file_id)
                        if job and self.download_progress else None,
                    "total_bytes": item.size_bytes, "speed_bps": job.speed_bps if job else None,
                    "elapsed_seconds": max(0, (job.finished_at or time.time()) - job.started_at) if job and job.started_at else 0,
                    "relative_path": job.relative_path if job else None, "error_code": job.error_code if job else None,
                    "error_message": job.error_message if job else None, "linked_job_id": job.linked_job_id if job else None,
                    "attempts": [{"sequence": a.sequence, "started_at": iso(a.started_at),
                                  "finished_at": iso(a.finished_at), "error_code": a.error_code} for a in attempts]})
        return result

    def list_tasks(self, status: str | None, q: str | None, page: int, page_size: int) -> dict[str, Any]:
        from sqlalchemy import func
        with self.db.sessions() as session:
            query = select(MediaRequest).where(MediaRequest.deleted_at.is_(None))
            if status:
                query = query.where(MediaRequest.status == status)
            if q:
                query = query.where(or_(MediaRequest.title.contains(q, autoescape=True),
                                        MediaRequest.id.contains(q, autoescape=True)))
            total = session.scalar(select(func.count()).select_from(query.subquery())) or 0
            rows = list(session.scalars(query.order_by(MediaRequest.created_at.desc()).offset((page-1)*page_size).limit(page_size)))
            related: dict[str, tuple[list[MediaItem], list[Job], list[Notification]]] = {
                row.id: ([], [], []) for row in rows
            }
            if rows:
                ids = list(related)
                for item in session.scalars(select(MediaItem).where(MediaItem.request_id.in_(ids)).order_by(MediaItem.message_id)):
                    related[item.request_id][0].append(item)
                for job in session.scalars(select(Job).where(Job.request_id.in_(ids))):
                    related[job.request_id][1].append(job)
                for notification in session.scalars(select(Notification).where(Notification.request_id.in_(ids))):
                    assert notification.request_id is not None
                    related[notification.request_id][2].append(notification)
            return {"items": [self._dto(session, row, related=related[row.id]) for row in rows],
                    "total": total, "page": page, "page_size": page_size}

    def detail(self, request_id: str) -> dict[str, Any]:
        with self.db.sessions() as session:
            request = session.get(MediaRequest, request_id)
            if not request or request.deleted_at is not None:
                raise AppError("NOT_FOUND", "任务不存在", 404)
            return self._dto(session, request, True)

    def cancel(self, request_id: str) -> dict[str, Any]:
        with self.db.transaction() as session:
            request = session.get(MediaRequest, request_id)
            if not request or request.deleted_at is not None:
                raise AppError("NOT_FOUND", "任务不存在", 404)
            if request.status in {"collecting", "awaiting_confirmation"}:
                request.status = "cancelled"
            for job in session.scalars(select(Job).where(Job.request_id == request.id)):
                if job.state not in ACTIVE:
                    continue
                job.cancel_requested = True
                if job.state in {"queued", "retry_wait", "waiting_existing"}:
                    job.state, job.finished_at = "cancelled", time.time()
                else:
                    job.state = "cancelling" if job.state != "recovery_blocked" else "recovery_blocked"
            session.flush()
            project(session, request)
            outcome = request.status
        if self.diagnostics:
            self.diagnostics.emit("task_result", level="info", task_id=request_id, outcome=outcome)
        return self.detail(request_id)

    def retry(self, request_id: str, item_ids: list[str] | None) -> dict[str, Any]:
        _, settings, _ = self.settings.read()
        with self.db.transaction() as session:
            request = session.get(MediaRequest, request_id)
            if not request or request.deleted_at is not None:
                raise AppError("NOT_FOUND", "任务不存在", 404)
            changed = False
            for job in session.scalars(select(Job).where(Job.request_id == request.id)):
                if job.state == "failed" and (item_ids is None or job.item_id in item_ids):
                    job.state, job.phase, job.next_run_at = "queued", "queued", time.time()
                    job.auto_retries, job.max_auto_retries = 0, settings.max_auto_retries
                    job.cancel_requested, job.error_code, job.error_message = False, None, None
                    changed = True
            if not changed and request.status not in ACTIVE:
                raise AppError("NOT_RETRYABLE", "没有可重试的失败文件", 409)
            session.flush()
            project(session, request)
        return self.detail(request_id)

    def delete(self, request_ids: list[str]) -> dict[str, list[str]]:
        # BEGIN IMMEDIATE serializes this eligibility check with retry, claim,
        # cancellation and worker completion. Never remove referenced history.
        with self.db.transaction() as session:
            requests = list(session.scalars(select(MediaRequest).where(
                MediaRequest.id.in_(request_ids), MediaRequest.deleted_at.is_(None))))
            if len(requests) != len(request_ids):
                raise AppError("NOT_FOUND", "任务不存在或已删除", 404)
            busy = session.scalar(select(Job.id).where(
                Job.request_id.in_(request_ids), Job.state.not_in(TERMINAL)).limit(1))
            if busy or any(request.status not in DELETABLE for request in requests):
                raise AppError("TASK_DELETE_BUSY", "任务尚未结束，请先取消并等待处理完成", 409)
            now = time.time()
            for request in requests:
                request.deleted_at = now
            for row in session.scalars(select(Notification).where(
                    Notification.request_id.in_(request_ids), Notification.state == "pending")):
                row.state = "obsolete"
        return {"deleted_ids": request_ids}
