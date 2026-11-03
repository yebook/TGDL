"""Durable cancel-and-delete coordinator. Native uncertainty always keeps its fence."""
import asyncio
import time
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.db.models import (
    CacheAction,
    CancelCleanup,
    FileDeletionOperation,
    FileReservation,
    Job,
    LocalApiRuntime,
    MediaItem,
    MediaRequest,
    PublishJournal,
    RetainedCache,
)
from app.services.cache_maintenance import maintenance_blocked
from app.storage.saved_media import observe

PENDING = {"pending", "running"}
TERMINAL = {"succeeded", "already_exists", "failed", "cancelled"}
LABELS = {"deleted": "已删除", "missing": "已不存在", "retained_shared": "其他任务仍引用，已保留",
          "retained_unverified": "身份无法确认，已保留", "failed": "清理失败，文件保留", "uncertain": "结果待核对",
          "pending": "等待清理"}


def cleanup_blocked(session: Session, request_id: str) -> bool:
    row = session.get(CancelCleanup, request_id)
    return bool(row and row.state != "complete")


class CancelCleanupService:
    def __init__(self, runtime: Any) -> None:
        self.runtime, self.db = runtime, runtime.db

    @staticmethod
    def projection(row: CancelCleanup | None, *, detail: bool = False) -> dict[str, Any] | None:
        if row is None:
            return None
        return {"state": row.state, "message": row.message, "can_retry": row.state == "partial",
                "entries": [{key: entry.get(key) for key in ("kind", "path", "outcome", "message")}
                            for entry in row.entries] if detail else []}

    def update(self, request_id: str, *, state: str | None = None, message: str | None = None,
               entries: list[dict[str, Any]] | None = None, delay: float = 0) -> None:
        with self.db.transaction() as session:
            row = session.get(CancelCleanup, request_id)
            assert row
            if state is not None:
                row.state = state
            if message is not None:
                row.message = message
            if entries is not None:
                row.entries = entries
            row.next_run_at, row.updated_at = time.time() + delay, time.time()

    def saved_missing(self, root_id: str, path: str, size: int) -> bool:
        try:
            return observe(self.runtime.storage.get(root_id), path, size) == "missing"
        except AppError:
            return False  # A removed/unavailable root never proves child absence.

    def allowed_requests(self, session: Session) -> set[str]:
        # Siblings cancelled by the same/all command may share one physical source.
        # An active owner still vetoes deletion, even when it has cleanup intent.
        ids = set(session.scalars(select(CancelCleanup.request_id).where(CancelCleanup.state != "complete")))
        active = set(session.scalars(select(Job.request_id).where(Job.request_id.in_(ids), Job.state.not_in(TERMINAL))))
        return {request_id for request_id in ids - active if self.ready(session, request_id)}

    def ready(self, session: Session, request_id: str) -> bool:
        jobs = list(session.scalars(select(Job).where(Job.request_id == request_id)))
        ids = [job.id for job in jobs]
        runtime = self.runtime
        if (any(job.state not in TERMINAL for job in jobs)
                or any(job_id in runtime.running and not runtime.running[job_id].done() for job_id in ids)
                or session.scalar(select(PublishJournal.job_id).where(PublishJournal.job_id.in_(ids)).limit(1))
                or session.scalar(select(FileReservation.id).where(FileReservation.job_id.in_(ids)).limit(1))):
            return False
        local = session.get(LocalApiRuntime, 1)
        controls = local.values.get("acquisition_controls", {}) if local else {}
        # Raw durable authorities must be retired by the owner reconciler, never here.
        return not any(job_id in controls for job_id in ids)

    async def run_due(self) -> None:
        with self.db.sessions() as session:
            row = session.scalar(select(CancelCleanup).where(CancelCleanup.state.in_(PENDING),
                CancelCleanup.next_run_at <= time.time()).order_by(CancelCleanup.next_run_at).limit(1))
            if row is None:
                return
            request_id = row.request_id
        await self.residuals(request_id)
        with self.db.sessions() as session:
            row = session.get(CancelCleanup, request_id)
            assert row
            if row.state == "partial":
                return
            if not self.ready(session, request_id):
                self.update(request_id, message="等待下载、保存或恢复操作安全停止；尚未删除文件。请在详情检查恢复提示。", delay=2)
                return
            local_jobs = list(session.scalars(select(Job).where(Job.request_id == request_id,
                Job.acquisition_mode == "local", Job.attempt_sequence > 0)))
            identity = self.runtime.retained_cache.identity()
            missing = []
            unlocated = []
            unknown_ids: dict[str, set[str]] = {}
            for job in local_jobs:
                caches = list(session.scalars(select(RetainedCache).where(RetainedCache.job_id == job.id)))
                unknown_ids[job.id] = {cache.id for cache in caches if not cache.path_encrypted
                    and cache.state == "uncertain" and identity and cache.generation == identity[1]}
                unknown = bool(unknown_ids[job.id])
                if not caches:
                    unlocated.append((job.id, job.attempt_sequence))
                elif unknown:
                    missing.append(job.id)
        for job_id, attempt in unlocated:
            # Historical jobs do not retain a generation/locator. A current alias
            # cannot prove an old child left no orphan; preserve that uncertainty.
            self.runtime.retained_cache.capture_unknown(job_id, "", attempt)
        for job_id in missing:
            identity = self.runtime.retained_cache.identity()
            with self.db.sessions() as session:
                request = session.get(MediaRequest, request_id)
                job = session.get(Job, job_id)
                assert request and job
                bot, attempt = request.bot_id, job.attempt_sequence
            if identity and self.runtime.bot_id == bot:
                await self.runtime.retained_cache.capture_native(identity[0], job_id, identity[1], attempt=attempt)
                with self.db.transaction() as session:
                    caches = list(session.scalars(select(RetainedCache).where(RetainedCache.job_id == job_id)))
                    for cache in caches:
                        if cache.id in unknown_ids[job_id] and not cache.path_encrypted and cache.state == "missing":
                            # An absent remote alias cannot prove an old orphan
                            # gone when this request never captured its locator.
                            cache.state, cache.completeness = "uncertain", "unknown"
                    verified = any(cache.state in {"retained", "missing"} and cache.generation == identity[1]
                                   for cache in caches)
                    if verified:
                        for cache in caches:
                            if not cache.path_encrypted and cache.state == "uncertain" and cache.generation == identity[1]:
                                cache.state = "missing"  # Superseded locator; native current identity is represented above.
            else:
                self.runtime.retained_cache.capture_unknown(job_id, self.runtime.acquisition_generation(), attempt)
        self.update(request_id, state="running", message="任务已停止，正在清理关联缓存和已保存文件。")
        await self.caches(request_id)
        with self.db.sessions() as session:
            row = session.get(CancelCleanup, request_id)
            assert row
            if row.state != "running":
                return
        await self.files(request_id)

    async def residuals(self, request_id: str) -> None:
        """Retry the worker's exact owned-part cleanup only after its writer exits."""
        with self.db.sessions() as session:
            jobs = list(session.scalars(select(Job).where(Job.request_id == request_id)))
            if (any(job.state not in TERMINAL for job in jobs)
                    or any(job.id in self.runtime.running and not self.runtime.running[job.id].done() for job in jobs)):
                return
            local = session.get(LocalApiRuntime, 1)
            controls = local.values.get("acquisition_controls", {}) if local else {}
            if any(job.id in controls for job in jobs):
                return
            ids = [job.id for job in jobs]
            residuals = list(session.scalars(select(PublishJournal).where(PublishJournal.job_id.in_(ids),
                PublishJournal.stage == "cleanup_failed")))
        for journal in residuals:
            owner = asyncio.create_task(asyncio.to_thread(self.runtime.worker.failed, journal.job_id,
                "CANCELLED", "任务已取消，正在重试清理目标临时文件", False))
            try:
                await asyncio.shield(owner)
            except asyncio.CancelledError:
                await owner
                raise
        with self.db.transaction() as session:
            remains = list(session.scalars(select(PublishJournal).where(PublishJournal.job_id.in_(ids),
                PublishJournal.stage == "cleanup_failed")))
            if remains:
                row = session.get(CancelCleanup, request_id)
                assert row
                row.state = "partial"
                row.message = "目标临时文件清理失败，缓存和已保存文件暂时保留；请检查下载目录挂载、权限后点击重试清理。"
                row.entries = [{"kind": "temporary", "path": journal.temporary_path, "outcome": "failed",
                    "message": "任务已停止，但目标临时文件无法删除；请检查挂载和权限后重试清理。"} for journal in remains]
                from app.services.tasks import enqueue
                request = session.get(MediaRequest, request_id)
                assert request
                enqueue(session, request, f"cancel-cleanup:{request_id}:{row.revision}", row.message)

    async def caches(self, request_id: str) -> None:
        retained = self.runtime.retained_cache
        with self.db.sessions() as session:
            row = session.get(CancelCleanup, request_id)
            assert row
            previous_files = [dict(entry) for entry in row.entries if entry["kind"] == "saved"]
            entries = [dict(entry) for entry in row.entries if entry["kind"] == "cache"]
            caches = list(session.scalars(select(RetainedCache).join(Job, Job.id == RetainedCache.job_id)
                .where(Job.request_id == request_id).order_by(RetainedCache.id)))
            known = {entry["cache_id"] for entry in entries}
            for cache in caches:
                if cache.id not in known:
                    entries.append({"kind": "cache", "cache_id": cache.id, "path": retained.display(cache),
                                    "outcome": "pending", "message": "等待清理"})
        waiting = False
        for entry in entries:
            if entry["outcome"] not in {"pending", "uncertain"}:
                continue
            with self.db.transaction() as session:
                cache = session.get(RetainedCache, entry["cache_id"])
                assert cache
                action = session.get(CacheAction, entry.get("action_id")) if entry.get("action_id") else None
                if action:
                    if action.state in {"running", "uncertain"}:
                        entry.update(outcome="uncertain", message=action.message or "原生缓存清理结果待核对，请恢复连接或安全重启本地 API。")
                        waiting = True
                        continue
                    if action.state == "succeeded":
                        entry.update(outcome="deleted", message=action.message or "缓存已删除")
                        continue
                    if action.error_code == "CACHE_CLEAN_BUSY":
                        entry.pop("action_id", None)
                    else:
                        entry.update(outcome="failed", message=action.message or "缓存清理失败，请恢复连接后重试清理。")
                        continue
                if cache.state in {"deleted", "missing"}:
                    entry.update(outcome="deleted" if cache.state == "deleted" else "missing", message="缓存已删除或不存在")
                    continue
                if cache.state != "retained":
                    entry.update(outcome="retained_unverified", message="缓存位置尚未可靠确认；请恢复原 Bot 连接后重试清理，或在设置的媒体缓存维护中处理。")
                    continue
                allowed = self.allowed_requests(session)
                shared = session.scalar(select(RetainedCache.id).join(Job, Job.id == RetainedCache.job_id).where(
                    RetainedCache.bot_id == cache.bot_id, RetainedCache.path_digest == cache.path_digest,
                    RetainedCache.id != cache.id, RetainedCache.state == "retained",
                    Job.request_id.not_in(allowed)).limit(1)) if cache.path_digest else None
                same_source = session.scalar(select(Job.id).join(MediaItem, MediaItem.id == Job.item_id).where(
                    MediaItem.bot_id == cache.bot_id, MediaItem.file_id == cache.file_id,
                    Job.request_id.not_in(allowed), Job.state.not_in(TERMINAL)).limit(1))
                if shared or same_source:
                    entry.update(outcome="retained_shared", message="其他任务仍引用此缓存，已保留；相关任务结束或删除引用后可重试清理。")
                    continue
                if retained.source_busy(session, cache) or maintenance_blocked(session) or self.runtime.worker.cleanup_paused:
                    entry.update(outcome="pending", message="缓存维护结果待核对，等待现有操作结束；必要时安全重启本地 API。")
                    waiting = True
                    continue
                identity = retained.identity()
                if (identity is None or self.runtime.bot_id != cache.bot_id
                        or cache.credential and cache.credential != identity[2]):
                    entry.update(outcome="failed", message="原 Bot 连接不可用或归属已改变，缓存保留；恢复原 Bot 连接后点击重试清理。")
                    continue
                try:
                    retained.validate(cache)
                except AppError as exc:
                    entry.update(outcome="missing" if exc.code == "CACHE_MISSING" else "retained_unverified", message=exc.message)
                    continue
                except OSError:
                    entry.update(outcome="retained_unverified", message="缓存路径不可访问，已保留；检查挂载和权限后重试清理。")
                    continue
                operation = uuid4().hex
                job = session.get(Job, cache.job_id)
                assert job
                session.add(CacheAction(id=operation, job_id=job.id, cache_id=cache.id, kind="clean", state="running",
                    attempt=job.attempt_sequence, generation=identity[1], credential=identity[2]))
                entry["action_id"] = operation
                # Persist locator BEFORE mutation, including process exit / lost reply.
                current = session.get(CancelCleanup, request_id)
                assert current
                current.entries = [dict(value) for value in entries] + previous_files
            await retained.clean(operation, identity[0], identity[1])
            with self.db.sessions() as session:
                action = session.get(CacheAction, operation)
                assert action
                if action.state == "succeeded":
                    entry.update(outcome="deleted", message="缓存已删除")
                elif action.state == "uncertain":
                    entry.update(outcome="uncertain", message=action.message)
                    waiting = True
                elif action.error_code == "CACHE_CLEAN_BUSY":
                    entry.update(outcome="pending", message="其他原生操作仍在使用缓存，等待安全空闲后清理。")
                    waiting = True
                else:
                    entry.update(outcome="failed", message=action.message or "缓存清理失败，已保留；可重试清理。")
        self.update(request_id, entries=entries + previous_files, state="pending" if waiting else "running", delay=5 if waiting else 0,
                    message="缓存清理尚未结束，正在等待或核对；最终文件暂未删除。" if waiting else "正在核对已保存文件并删除。")

    async def files(self, request_id: str) -> None:
        saved = self.runtime.tasks.saved_media
        assert saved
        try:
            with self.db.transaction() as session:
                row = session.get(CancelCleanup, request_id)
                assert row
                operation_id = row.file_operation_id
                operation = session.get(FileDeletionOperation, operation_id) if operation_id else None
                if operation is None:
                    if not self.ready(session, request_id):
                        row.state, row.message, row.next_run_at = "pending", "等待任务安全停止后清理已保存文件。", time.time() + 2
                        return
                    operation_id = uuid4().hex
                    saved.prepare(session, operation_id, [request_id], cancellation=True,
                                  cleanup_scope=self.allowed_requests(session))
                    operation = session.get(FileDeletionOperation, operation_id)
                    assert operation
                    completed = {(entry.get("root_id"), entry["path"]) for entry in row.entries
                        if entry["kind"] == "saved" and entry["outcome"] in {"deleted", "missing", "uncertain"}}
                    operation.entries = [entry for entry in operation.entries if (entry["root_id"], entry["relative_path"]) not in completed]
                    row.file_operation_id = operation_id
                    execute = True
                elif operation.state == "running":
                    # This service awaits its only execute owner. A previous process
                    # is recovered by SavedMediaService.recover before runtime starts.
                    return
                else:
                    execute = False
            assert operation_id
            if execute:
                owner = asyncio.create_task(asyncio.to_thread(saved.execute, operation_id))
                try:
                    await asyncio.shield(owner)
                except asyncio.CancelledError:
                    await owner
                    raise
                except Exception:
                    # The exact owned thread has exited, but it may have unlinked
                    # before its result commit failed. Never replay unfinished
                    # destructive entries after that owner is known to have exited.
                    saved.recover(operation_id)
            with self.db.transaction() as session:
                row = session.get(CancelCleanup, request_id)
                operation = session.get(FileDeletionOperation, operation_id)
                assert row and operation and operation.state == "complete"
                entries = [dict(entry) for entry in row.entries if entry["kind"] == "cache" or
                           entry["kind"] == "saved" and entry["outcome"] in {"deleted", "missing", "uncertain"}]
                for entry in entries:
                    if entry["kind"] == "saved" and entry["outcome"] == "uncertain":
                        # A lost unlink/fsync outcome is never replayed. Only absence
                        # under the enrolled root can retire its uncertainty.
                        if self.saved_missing(entry["root_id"], entry["path"], entry["size_bytes"]):
                            entry.update(outcome="missing", message="已核对文件不存在")
                            if entry.get("proof"):
                                saved.retire_verified(session, entry["proof"])
                        else:
                            entry["message"] = "之前删除结果未知，禁止重复删除；请核实该路径并手动处理后重试清理。"
                for entry in operation.entries:
                    outcome = entry["outcome"]
                    # A completed sibling keeps its historical successful Job,
                    # even after it removed this shared physical path. A shared
                    # reference forbids unlink, but cannot make proven absence
                    # an unresolved cleanup forever. Never expand its authority
                    # to a later file restored at the same path.
                    if outcome == "retained_shared" and self.saved_missing(entry["root_id"],
                            entry["relative_path"], entry.get("size_bytes", -1)):
                        outcome = "missing"
                    entries.append({"kind": "saved", "root_id": entry["root_id"], "size_bytes": entry.get("size_bytes", -1),
                        "path": entry["relative_path"], "outcome": outcome, "message": LABELS[outcome], "proof": dict(entry)})
                row.entries = entries
                problems = [entry for entry in entries if entry["outcome"] not in {"deleted", "missing"}]
                row.state = "partial" if problems else "complete"
                row.message = ("任务已取消，部分文件未能清理。请查看逐项原因，处理后点击重试清理。" if problems else
                               "任务已取消，关联缓存和已保存文件已清理；没有文件的任务无需清理。")
                row.updated_at = time.time()
                request = session.get(MediaRequest, request_id)
                assert request
                request.status, request.updated_at = "cancelled", time.time()
                from app.services.tasks import enqueue
                details = "\n".join(f"{entry['path'] or '缓存位置待确认'}：{entry['message']}" for entry in entries)
                enqueue(session, request, f"cancel-cleanup:{request_id}:{row.revision}", row.message + ("\n" + details if details else ""))
        except AppError as exc:
            self.update(request_id, state="pending", message=exc.message + "；任务清理意图已保留，稍后继续。", delay=5)
