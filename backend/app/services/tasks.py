from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.bot.grouping import GROUP_MAX_SECONDS
from app.bot.messages import (
    STAGE_LABELS,
    failure_hint,
    file_list_body,
    format_bytes,
    shorten,
    storage_warning,
    task_message,
    type_summary,
)
from app.core.errors import AppError
from app.core.security import digest
from app.core.templates import render_confirmation
from app.db.models import (
    Attempt,
    CacheAction,
    CancelCleanup,
    FileDeletionOperation,
    IngestUpdate,
    Job,
    MediaItem,
    MediaRequest,
    Notification,
    RetainedCache,
    uid,
)
from app.db.session import Database
from app.schemas.contracts import UserSettings
from app.services.cache_maintenance import maintenance_blocked, require_no_maintenance
from app.services.cancel_cleanup import CancelCleanupService, cleanup_blocked
from app.services.diagnostics import DiagnosticLog
from app.services.download_progress import DownloadProgress
from app.services.file_acquisition import uncertain_message
from app.services.retained_cache import RetainedCacheService
from app.services.saved_media import SavedMediaService
from app.services.settings import SettingsService
from app.storage.files import StorageManager, contained
from app.storage.naming import fit_name, planned_name, request_caption, request_summary, unique_name
from app.storage.saved_media import observe
from app.storage.space import assess_space

ACTIVE = {"queued", "fetching", "saving", "cancelling", "retry_wait", "waiting_existing", "recovery_blocked"}
TERMINAL = {"succeeded", "already_exists", "failed", "cancelled"}
DELETABLE = TERMINAL | {"partial_success", "expired"}


def iso(value: float | None) -> str | None:
    return datetime.fromtimestamp(value, UTC).isoformat().replace("+00:00", "Z") if value is not None else None


def enqueue(session: Session, request: MediaRequest, key: str, text: str,
            payload: dict[str, Any] | None = None, *, stage: str | None = None) -> None:
    if request.deleted_at is not None:
        return
    if not session.scalar(select(Notification).where(Notification.event_key == key)):
        session.add(Notification(event_key=key, request_id=request.id, chat_id=request.chat_id,
                                 payload={"text": task_message(text, request.id, stage=stage),
                                          "bot_id": request.bot_id, **(payload or {})}))


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
            if cleanup_blocked(session, request.id):
                return  # Cancel cleanup publishes its own truthful, per-file final result.
            stage = STAGE_LABELS[state]
            lines = [shorten(request.title, 300), ""]
            counts = [f"文件：{len(jobs)} 个"]
            for outcome, label in (("succeeded", "已下载"), ("already_exists", "已保存，无需重复下载"),
                                   ("failed", "失败"), ("cancelled", "取消")):
                count = states.count(outcome)
                if count:
                    counts.append(f"{label} {count} 个")
            lines.append(" · ".join(counts))
            saved = [job for job in jobs if job.state in {"succeeded", "already_exists"}]
            if saved:
                size = sum(job.completed_bytes for job in saved) if all(
                    job.finished_at is not None and job.completed_bytes >= 0 for job in saved) else None
                lines.append(f"实际已保存：{len(saved)} / {len(jobs)} 个文件 · 共 {format_bytes(size)}")
            paths = [PurePosixPath(job.relative_path) for job in jobs]
            common_parent = len({path.parent for path in paths}) == 1
            if common_parent and any(s in {"succeeded", "already_exists"} for s in states):
                lines.append(f"📁 实际目录：{shorten(str(paths[0].parent), 800)}")
            lines.extend(["", "保存结果："])
            rows = []
            for job in jobs:
                name = PurePosixPath(job.relative_path).name if common_parent else job.relative_path
                label = {"succeeded": "✅", "already_exists": "📎 已保存", "failed": "❌ 失败",
                         "cancelled": "🚫 已取消"}[job.state]
                line = f"{label} {name}"
                if job.state == "failed":
                    line += f"（{failure_hint(job.error_code)}）"
                    if job.error_code in {"RESUME_UNAVAILABLE", "CACHE_RESUME_UNAVAILABLE"}:
                        line += "\n原下载进度无法复用；请在详情检查原因，可手动清理缓存或确认从头重新下载。"
                if job.storage_failure:
                    line += "\n" + storage_warning(job.storage_failure)
                cache = session.get(RetainedCache, job.cache_id) if job.cache_id else None
                if cache and cache.state == "retained" and job.state not in {"succeeded", "already_exists"}:
                    line += ("\n完整缓存已保留，目标尚未保存；可在详情继续保存或清理。"
                             if cache.completeness == "complete" else
                             "\n下载未完成，部分缓存已保留；可在详情检查继续下载或手动清理。")
                elif cache and cache.state == "retained":
                    line += "\n文件已保存，缓存清理将按现有规则独立处理。"
                rows.append(line)
            footer = ""
            if "failed" in states:
                footer = "请在 WebUI 查看详情并重试失败文件，已保存的文件会保留。"
            elif "cancelled" in states:
                footer = "已保存的文件会保留，缓存可在 WebUI 详情中检查和操作；无缓存时可重新发送文件。"
            elif state == "already_exists":
                footer = "文件已保存，无需重复下载。"
            text = file_list_body(stage, request.id, "\n".join(lines), rows, footer)
            enqueue(session, request, f"result:{request.id}:{request.result_revision}", text, stage=stage)


class TaskService:
    def __init__(self, database: Database, settings: SettingsService) -> None:
        self.db, self.settings = database, settings
        self.diagnostics: DiagnosticLog | None = None
        self.download_progress: DownloadProgress | None = None
        self.saved_media: SavedMediaService | None = None
        self.retained_cache: RetainedCacheService | None = None

    def space_warnings(self, session: Session, request: MediaRequest, items: list[MediaItem], settings: UserSettings) -> str:
        manager = self.saved_media.storage if self.saved_media else StorageManager(
            self.settings.config.storage_roots, self.settings.config.data_dir,
            self.settings.config.minimum_free_bytes, self.settings.config.storage_root_identities)
        warnings = []
        for item in items:
            assessment = assess_space(session, manager, self.settings.config, request.root_id,
                                      item.size_bytes, settings.bot_api_mode, "before_download")
            if not assessment.sufficient:
                warnings.append(f"{item.planned_filename or '文件'}：{storage_warning(assessment.to_dict())}")
        return "\n".join(warnings)

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
                automatic = settings.auto_download and request.user_id in settings.allowed_user_ids
                if automatic and maintenance_blocked(session):
                    continue  # Retry collection after maintenance releases download admission.
                request.status = "awaiting_confirmation"
                request.config_revision = revision
                request.root_id = settings.download_root_id
                if not request.destination:
                    summary = request_summary(request, items)
                    request.title = summary
                    request.destination = self.destination(session, request, summary, settings.timezone)
                # Conservative upper bound if send succeeds but its response is lost.
                request.expires_at = time.time() + settings.confirmation_timeout_seconds
                # Reserve other frozen plans and actual files before showing consent.
                used = {name.casefold() for name in session.scalars(
                    select(MediaItem.planned_filename).join(MediaRequest).where(
                        MediaRequest.root_id == request.root_id, MediaRequest.destination == request.destination,
                        MediaRequest.id != request.id, MediaItem.planned_filename.is_not(None))) if name}
                root = self.settings.config.storage_roots[request.root_id]
                prefix = request.destination + "/"
                used.update(path[len(prefix):].split("/")[0].casefold() for path in session.scalars(
                    select(MediaRequest.destination).where(
                        MediaRequest.root_id == request.root_id, MediaRequest.id != request.id,
                        MediaRequest.destination.startswith(prefix))))
                try:
                    folder = contained(root, request.destination + "/.tgd-name-check").parent
                    used.update(path.name.casefold() for path in folder.iterdir())
                except FileNotFoundError:
                    pass
                for path in session.scalars(select(Job.relative_path).where(Job.root_id == request.root_id)):
                    parent, _, name = path.rpartition("/")
                    if parent == request.destination:
                        used.add(name.casefold())
                lines = []
                caption = request_caption(items)
                for index, item in enumerate(items, start=1):
                    item.planned_filename = unique_name(planned_name(
                        item, request, settings.filename_template, settings.timezone, index, caption=caption), used)
                    lines.append(f"文件名：{item.planned_filename}\n文件大小：{format_bytes(item.size_bytes)}")
                if automatic:
                    request.expires_at = None
                    self._queue(session, request, items, settings)
                    continue
                sizes = [item.size_bytes for item in items]
                text = render_confirmation(settings.confirmation_template, {
                    "title": request.title, "destination": request.destination,
                    "file_count": str(len(items)),
                    "expires_in_minutes": f"{settings.confirmation_timeout_seconds / 60:.2f}".rstrip("0").rstrip("."),
                    "total_size": format_bytes(sum(s for s in sizes if s is not None)
                                               if all(s is not None for s in sizes) else None),
                    "files": "\n\n".join(lines),
                })
                warning = self.space_warnings(session, request, items, settings)
                if warning:
                    text = shorten(text, max(100, 3400 - len(warning))) + "\n\n" + shorten(warning, 2200)
                enqueue(session, request, f"confirm:{request.id}:{request.nonce}", text,
                        {"confirmation": True, "nonce": request.nonce}, stage=STAGE_LABELS["awaiting_confirmation"])
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
                self.cancel_in_session(session, request)
                return "cancelled"
            if action != "confirm":
                raise AppError("INVALID_ACTION", "无效操作")
            require_no_maintenance(session, bot_id=request.bot_id)
            # Only settings that affect consent invalidate this card. Other edits preserve its deadline.
            if request.root_id != settings.download_root_id:
                request.nonce, request.status = uid()[:10], "collecting"
                request.collect_until = time.time()
                return "configuration_changed"
            request.config_revision = revision
            items = list(session.scalars(select(MediaItem).where(MediaItem.request_id == request.id).order_by(MediaItem.message_id)))
            self._queue(session, request, items, settings)
            return "queued"

    def _queue(self, session: Session, request: MediaRequest, items: list[MediaItem], settings: UserSettings) -> None:
        require_no_maintenance(session, bot_id=request.bot_id)
        if not items or any(not item.planned_filename for item in items):
            raise AppError("INVALID_FILE_PLAN", "确认信息中的文件名不完整，请重新发送文件", 409)
        for item in items:
            session.add(Job(item_id=item.id, request_id=request.id, root_id=request.root_id,
                            relative_path=f"{request.destination}/{item.planned_filename}", max_auto_retries=settings.max_auto_retries))
        request.status, request.updated_at = "queued", time.time()
        sizes = [item.size_bytes for item in items]
        total = sum(size for size in sizes if size is not None) if all(size is not None for size in sizes) else None
        summary = (f"{shorten(request.title, 300)}\n\n{len(items)} 个文件 · 共 {format_bytes(total)}\n"
                   f"{type_summary([item.media_type for item in items])}\n"
                   f"保存目录：{shorten(request.destination, 800)}\n\n下载清单")
        rows = [f"{index}. {item.planned_filename} · {format_bytes(item.size_bytes)}"
                for index, item in enumerate(items, 1)]
        stage = STAGE_LABELS["queued"]
        text = file_list_body(stage, request.id, summary, rows, "系统将按并发设置处理，完成后通知你。")
        warning = self.space_warnings(session, request, items, settings)
        if warning:
            text = shorten(text, max(100, 3400 - len(warning))) + "\n\n" + shorten(warning, 2200)
        enqueue(session, request, f"queued:{request.id}", text, stage=stage)

    def _dto(self, session: Session, request: MediaRequest, detail: bool = False, *,
             related: tuple[list[MediaItem], list[Job], list[Notification]] | None = None,
             cache_rows: dict[str, RetainedCache] | None = None,
             cleanup_rows: dict[str, CancelCleanup] | None = None) -> dict[str, Any]:
        if related is None:
            related = (
                list(session.scalars(select(MediaItem).where(MediaItem.request_id == request.id).order_by(MediaItem.message_id))),
                list(session.scalars(select(Job).where(Job.request_id == request.id))),
                list(session.scalars(select(Notification).where(Notification.request_id == request.id))),
            )
        items, job_rows, notifications = related
        jobs = {j.item_id: j for j in job_rows}
        cleanup = cleanup_rows.get(request.id) if cleanup_rows is not None else session.get(CancelCleanup, request.id)
        cleaning = bool(cleanup and cleanup.state != "complete")
        notification_status = "failed" if any(n.state == "failed" for n in notifications) else (
            "pending" if any(n.state == "pending" for n in notifications) else "sent")
        sizes = [i.size_bytes for i in items]
        def retryable(job: Job) -> bool:
            if job.state != "failed":
                return False
            cache = (cache_rows.get(job.cache_id) if cache_rows is not None else session.get(RetainedCache, job.cache_id)) if job.cache_id else None
            return not (cache and cache.state == "retained" and cache.completeness != "complete")
        result: dict[str, Any] = {
            "id": request.id, "title": request.title, "kind": request.kind, "status": request.status,
            "terminal": request.status in DELETABLE,
            "created_at": iso(request.created_at), "updated_at": iso(request.updated_at),
            "item_count": len(items), "completed_count": sum(j.state in {"succeeded", "already_exists"} for j in jobs.values()),
            "total_bytes": sum(s for s in sizes if s is not None) if all(s is not None for s in sizes) else None,
            "completed_bytes": sum(j.completed_bytes for j in jobs.values()),
            "effective_destination": request.destination, "notification_status": notification_status,
            "can_cancel": bool(cleanup and cleanup.state == "partial") or not cleaning and (request.status in {"collecting", "awaiting_confirmation"}
                           or request.status in ACTIVE and any(j.state in ACTIVE and not j.cancel_requested
                                                              for j in jobs.values())),
            "can_retry": cleanup is None and any(retryable(j) for j in jobs.values()),
            "can_delete": not cleaning and request.status in DELETABLE and all(j.state in TERMINAL for j in jobs.values()),
            "cancel_cleanup": CancelCleanupService.projection(cleanup, detail=detail),
        }
        if detail:
            result["expires_at"] = iso(request.expires_at)
            result["items"] = []
            for item in items:
                job = jobs.get(item.id)
                caches = self.retained_cache.project(session, job) if self.retained_cache and job else []
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
                    "storage_failure": job.storage_failure if job else None,
                    "cache": caches[0] if caches else None, "caches": caches,
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
            page_rows = session.execute(query.add_columns(CancelCleanup).outerjoin(
                CancelCleanup, CancelCleanup.request_id == MediaRequest.id).order_by(
                    MediaRequest.created_at.desc()).offset((page-1)*page_size).limit(page_size)).all()
            rows = [row[0] for row in page_rows]
            cleanup_rows = {row[1].request_id: row[1] for row in page_rows if row[1] is not None}
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
            cache_ids = {job.cache_id for _, jobs, _ in related.values() for job in jobs if job.state == "failed" and job.cache_id}
            cache_rows = {cache.id: cache for cache in session.scalars(select(RetainedCache).where(RetainedCache.id.in_(cache_ids)))} if cache_ids else {}
            result: dict[str, Any] = {"items": [self._dto(session, row, related=related[row.id], cache_rows=cache_rows,
                                                       cleanup_rows=cleanup_rows) for row in rows],
                      "total": total, "page": page, "page_size": page_size}
        observations: dict[tuple[str, str, int], str] = {}
        for dto, row in zip(result["items"], rows, strict=True):
            self._local_files(dto, related[row.id][1], observations)
        return result

    def detail(self, request_id: str) -> dict[str, Any]:
        with self.db.sessions() as session:
            request = session.get(MediaRequest, request_id)
            if not request or request.deleted_at is not None:
                raise AppError("NOT_FOUND", "任务不存在", 404)
            result = self._dto(session, request, True)
            jobs = list(session.scalars(select(Job).where(Job.request_id == request.id)))
        self._local_files(result, jobs, {})
        return result

    def _local_files(self, dto: dict[str, Any], jobs: list[Job], observations: dict[tuple[str, str, int], str]) -> None:
        counts = dict.fromkeys(("present", "missing", "changed", "unavailable", "not_saved"), 0)
        states = {}
        for job in jobs:
            state = "not_saved"
            if job.state in {"succeeded", "already_exists"}:
                key = job.root_id, job.relative_path, job.completed_bytes
                if key not in observations:
                    try:
                        observations[key] = observe(self.saved_media.storage.get(job.root_id), job.relative_path,
                                                    job.completed_bytes) if self.saved_media else "unavailable"
                    except AppError:
                        observations[key] = "unavailable"
                state = observations[key]
            states[job.item_id] = state
            counts[state] += 1
        counts["not_saved"] += max(0, dto["item_count"] - len(jobs))
        dto["local_files"] = counts
        for item in dto.get("items", []):
            item["local_file_state"] = states.get(item["id"], "not_saved")

    def cancel(self, request_id: str) -> dict[str, Any]:
        with self.db.transaction() as session:
            request = session.get(MediaRequest, request_id)
            if not request or request.deleted_at is not None:
                raise AppError("NOT_FOUND", "任务不存在", 404)
            self.cancel_in_session(session, request)
            outcome = request.status
        if self.diagnostics:
            self.diagnostics.emit("task_result", level="info", task_id=request_id, outcome=outcome)
        return self.detail(request_id)

    @staticmethod
    def cancel_in_session(session: Session, request: MediaRequest) -> str:
        """Persist intent in the caller's transaction; never perform file/network I/O."""
        cleanup = session.get(CancelCleanup, request.id)
        if cleanup:
            if cleanup.state == "partial":
                cleanup.state, cleanup.file_operation_id, cleanup.next_run_at = "pending", None, time.time()
                cleanup.message, cleanup.revision = "已重新提交文件清理，等待安全核对。", cleanup.revision + 1
                cleanup.entries = [({key: value for key, value in dict(entry, outcome="pending", message="等待重新核对").items()
                                     if key != "action_id"} if entry["kind"] == "cache"
                                    and entry["outcome"] not in {"deleted", "missing"} else entry)
                                   for entry in cleanup.entries if entry["kind"] != "temporary"]
                return "cancelling"
            return "cancelling" if cleanup.state != "complete" else "unchanged"
        changed = request.status in {"collecting", "awaiting_confirmation"}
        if changed:
            request.status = "cancelled"
        stopping = False
        for job in session.scalars(select(Job).where(Job.request_id == request.id)):
            if job.state not in ACTIVE:
                continue
            changed = True
            job.cancel_requested = True
            if job.state in {"queued", "retry_wait", "waiting_existing"}:
                job.state, job.finished_at = "cancelled", time.time()
            else:
                stopping = True
                job.state = "cancelling" if job.state != "recovery_blocked" else "recovery_blocked"
                if job.state == "recovery_blocked":
                    job.error_message = uncertain_message(session, job)
            RetainedCacheService.finish_action(session, job)
        if not changed:
            return "unchanged"
        session.add(CancelCleanup(request_id=request.id))
        for notice in session.scalars(select(Notification).where(Notification.request_id == request.id,
                Notification.state == "pending", Notification.event_key.startswith("result:"))):
            notice.state = "obsolete"
        session.flush()
        project(session, request)
        enqueue(session, request, f"cancel:{request.id}", "已提交取消并清理文件；安全停止后将删除关联缓存和已保存文件，任务记录保留。")
        return "cancelling" if stopping else "cancelled" if changed else "unchanged"

    def retry(self, request_id: str, item_ids: list[str] | None) -> dict[str, Any]:
        _, settings, _ = self.settings.read()
        with self.db.transaction() as session:
            request = session.get(MediaRequest, request_id)
            if not request or request.deleted_at is not None:
                raise AppError("NOT_FOUND", "任务不存在", 404)
            cleanup = session.get(CancelCleanup, request_id)
            if cleanup:
                raise AppError("TASK_CANCELLED" if cleanup.state == "complete" else "CANCEL_CLEANUP_PENDING",
                    "任务已取消并完成文件清理；如需重新下载，请重新发送文件创建新任务" if cleanup.state == "complete" else
                    "取消后的文件清理尚未完成，请等待或在详情重试清理", 409)
            require_no_maintenance(session, bot_id=request.bot_id)
            changed = False
            for job in session.scalars(select(Job).where(Job.request_id == request.id)):
                if job.state == "failed" and (item_ids is None or job.item_id in item_ids):
                    cache = session.get(RetainedCache, job.cache_id) if job.cache_id else None
                    if cache and cache.state == "retained" and cache.completeness != "complete":
                        if item_ids is None:
                            continue
                        raise AppError("CACHE_CHOICE_REQUIRED", "此文件保留了部分缓存，请在详情选择继续下载或确认重新下载", 409)
                    job.execution_mode = "save" if cache and cache.completeness == "complete" and cache.state == "retained" else "download"
                    job.state, job.phase, job.next_run_at = "queued", "queued", time.time()
                    job.auto_retries, job.max_auto_retries = 0, settings.max_auto_retries
                    job.cancel_requested, job.error_code, job.error_message = False, None, None
                    changed = True
            if not changed and request.status not in ACTIVE:
                raise AppError("NOT_RETRYABLE", "没有可重试的失败文件", 409)
            session.flush()
            project(session, request)
        return self.detail(request_id)

    def delete(self, request_ids: list[str], *, delete_files: bool = False,
               operation_id: str | None = None) -> dict[str, Any]:
        # BEGIN IMMEDIATE serializes this eligibility check with retry, claim,
        # cancellation and worker completion. Never remove referenced history.
        caches: list[tuple[str, str, str]] = []
        with self.db.transaction() as session:
            if operation_id and self.saved_media:
                existing = self.saved_media.existing(session, operation_id, request_ids, delete_files)
                if existing:
                    return existing
            if delete_files and (not operation_id or not self.saved_media):
                raise AppError("FILE_DELETE_OPERATION_REQUIRED", "删除文件需要有效的操作编号", 422)
            requests = list(session.scalars(select(MediaRequest).where(
                MediaRequest.id.in_(request_ids), MediaRequest.deleted_at.is_(None))))
            if len(requests) != len(request_ids):
                raise AppError("NOT_FOUND", "任务不存在或已删除", 404)
            if any(cleanup_blocked(session, request_id) for request_id in request_ids):
                raise AppError("CANCEL_CLEANUP_PENDING", "取消后的文件清理尚未完成，暂不能删除任务记录", 409)
            busy = session.scalar(select(Job.id).where(
                Job.request_id.in_(request_ids), Job.state.not_in(TERMINAL)).limit(1))
            if busy or any(request.status not in DELETABLE for request in requests):
                raise AppError("TASK_DELETE_BUSY", "任务尚未结束，请先取消并等待处理完成", 409)
            require_no_maintenance(session)
            if delete_files and self.retained_cache:
                for cache in session.scalars(select(RetainedCache).join(Job, Job.id == RetainedCache.job_id).where(
                        Job.request_id.in_(request_ids), RetainedCache.state == "retained")):
                    job = session.get(Job, cache.job_id)
                    if not job:
                        continue
                    blocker = self.retained_cache.blocker(session, job, cache, clean=True)
                    if blocker:
                        raise AppError("TASK_CACHE_DELETE_BLOCKED", blocker, 409)
                    caches.append((job.request_id, job.item_id, cache.id))
            if not delete_files:
                now = time.time()
                for request in requests:
                    request.deleted_at = now
                for row in session.scalars(select(Notification).where(
                        Notification.request_id.in_(request_ids), Notification.state == "pending")):
                    row.state = "obsolete"
                if operation_id:
                    operation = FileDeletionOperation(id=operation_id, request_ids=request_ids,
                                                      delete_files=False, entries=[], state="complete")
                    session.add(operation)
                    session.flush()
                    return self.saved_media.result(operation) if self.saved_media else {
                        "deleted_ids": request_ids, "operation_id": operation_id,
                        "files": {key: 0 for key in ("deleted", "missing", "retained_shared",
                                                      "retained_unverified", "failed", "uncertain")}}
                return {"deleted_ids": request_ids}
        if delete_files and self.retained_cache:
            # Native cache ownership remains under RetainedCacheService. Each
            # action is durable, so a retry never replays a lost mutation.
            for request_id, item_id, cache_id in caches:
                cache_operation = digest(f"task-delete:{operation_id}:{cache_id}")[:32]
                asyncio.run(self.retained_cache.submit(request_id, item_id, "clean", cache_operation,
                                                       cache_id=cache_id, confirmed=True))
                with self.db.sessions() as session:
                    action = session.get(CacheAction, cache_operation)
                    if not action or action.state != "succeeded":
                        raise AppError("TASK_CACHE_DELETE_BLOCKED",
                                       action.message if action and action.message else
                                       "缂撳瓨娓呯悊缁撴灉灏氭湭纭锛屾殏涓嶈兘鍒犻櫎浠诲姟璁板綍", 409)
        with self.db.transaction() as session:
            requests = list(session.scalars(select(MediaRequest).where(
                MediaRequest.id.in_(request_ids), MediaRequest.deleted_at.is_(None))))
            if len(requests) != len(request_ids):
                raise AppError("NOT_FOUND", "任务不存在或已删除", 404)
            if delete_files:
                assert self.saved_media and operation_id
                self.saved_media.prepare(session, operation_id, request_ids)
            elif operation_id:
                session.add(FileDeletionOperation(id=operation_id, request_ids=request_ids, delete_files=False,
                                                  entries=[], state="complete"))
            now = time.time()
            for request in requests:
                request.deleted_at = now
            for row in session.scalars(select(Notification).where(
                    Notification.request_id.in_(request_ids), Notification.state == "pending")):
                row.state = "obsolete"
        if delete_files:
            assert self.saved_media and operation_id
            return self.saved_media.execute(operation_id)
        if operation_id and self.saved_media:
            with self.db.sessions() as session:
                operation = session.get(FileDeletionOperation, operation_id)
                assert operation
                return self.saved_media.result(operation)
        return {"deleted_ids": request_ids}
