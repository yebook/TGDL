"""Retained native media identities and explicit, bounded cache recovery actions.

Only the API/TDLib owns deletion. A lost mutation response keeps its durable fence.
Browser callers select database IDs, never filesystem paths or expected hashes.
"""
import asyncio
import re
import stat
import time
from pathlib import Path
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.bot.client import CacheCleanupError
from app.core.errors import AppError
from app.core.security import digest
from app.db.models import (
    CacheAction,
    CacheCleanup,
    Job,
    MediaItem,
    MediaRequest,
    RetainedCache,
)
from app.services.cache_maintenance import maintenance_blocked, require_no_maintenance
from app.storage.files import regular_file_hash
from app.storage.space import assess_space

TERMINAL = {"failed", "cancelled"}
BUSY = {"queued", "running", "uncertain"}
RECOVERY_GUIDANCE = "请在设置中重试 Bot 连接；无法确认旧操作已停止时，请安全重启本地 Bot API，然后刷新详情。"
RESUME_REASONS = {
    "generation_changed": "Bot API 已重启，无法继续使用原下载进度。可清理旧缓存或从头重新下载。",
    "absent": "缓存已不存在，无法继续下载；可以从头重新下载。",
    "changed": "缓存身份已改变，无法安全复用原进度；可以从头重新下载。",
    "unsupported": "当前本地 API 不支持缓存续传检查，请更新配套服务后重试。",
    "unavailable": "暂时无法确认是否可继续下载，请恢复 Bot 连接后重试。",
    "not_resumable": "原下载的分片信息不可复用；可清理缓存或从头重新下载。",
    "active": "文件仍在使用或停止尚未确认，暂不能操作缓存。",
}
NATIVE_REASONS = {"cache_busy": "active", "cache_missing": "absent", "cache_identity_changed": "changed",
                  "cache_unavailable": "unavailable", "bitmap_unavailable": "not_resumable"}


def cache_fingerprint(path: Path) -> dict[str, int]:
    value = path.lstat()
    if not stat.S_ISREG(value.st_mode):
        raise AppError("CACHE_CHANGED", "缓存不是普通文件，已拒绝操作", 409)
    return {"device": value.st_dev, "inode": value.st_ino, "size": value.st_size,
            "mtime_ns": value.st_mtime_ns, "ctime_ns": value.st_ctime_ns}


class RetainedCacheService:
    def __init__(self, runtime: Any) -> None:
        self.runtime = runtime
        self.db, self.config, self.secrets = runtime.db, runtime.config, runtime.settings.secrets
        self.cleaning: set[str] = set()
        with self.db.transaction() as session:
            for action in session.scalars(select(CacheAction).where(CacheAction.kind == "clean", CacheAction.state == "running")):
                action.state, action.message = "uncertain", "应用已重启，缓存清理结果需要核对。"

    def identity(self, *, allow_saving: bool = False) -> tuple[Any, str, str] | None:
        client, bot_id = self.runtime.client, self.runtime.bot_id
        generation = self.runtime.acquisition_generation()
        _, configured, _ = self.runtime.settings.read()
        token = getattr(getattr(client, "bot", None), "token", "")
        local = self.runtime.local_api.read()
        if (client is None or client.mode != "local" or bot_id is None or not generation or not token
                or configured.bot_api_mode != "local"
                or self.runtime.client_signature != self.runtime.settings.client_signature(configured)
                or not allow_saving and self.runtime.bot_status.get("state") != "online"
                or local.get("phase", "idle") != "idle"):
            return None
        return client, generation, digest(token)

    def path(self, raw: str) -> Path:
        root = self.config.bot_api_root
        path = Path(raw)
        if not path.is_absolute() or not path.is_relative_to(root.absolute()) or root.is_symlink():
            raise AppError("CACHE_CHANGED", "缓存路径不属于已配置的共享目录", 409)
        current = root.absolute()
        for part in path.relative_to(root.absolute()).parts:
            current /= part
            if current.is_symlink():
                raise AppError("CACHE_CHANGED", "缓存路径含符号链接，已拒绝操作", 409)
        if not path.resolve(strict=True).is_relative_to(root.resolve(strict=True)):
            raise AppError("CACHE_CHANGED", "缓存路径身份已改变", 409)
        return path

    def display(self, row: RetainedCache) -> str | None:
        if not row.path_encrypted:
            return None
        try:
            path = Path(self.secrets.decrypt(row.path_encrypted))
            relative = path.relative_to(self.config.bot_api_root.absolute()).as_posix()
            # Redact complete token components, including historical Bot credentials.
            relative = re.sub(r"(?:bot)?[0-9]{1,20}:[A-Za-z0-9_-]+", "[Bot]", relative)
            return "[Bot API 缓存]/" + relative
        except (ValueError, OSError):
            return "[Bot API 缓存]/[路径不可用]"

    def capture(self, job_id: str, raw: str | None, *, completeness: str,
                generation: str = "", acquisition_id: str | None = None,
                credential: str = "", native_identity: str | None = None,
                resumable: bool = False, reason: str | None = None,
                attempt: int | None = None, only_if_missing: bool = False) -> str | None:
        fingerprint = None
        allocated = None
        if raw:
            path = self.path(raw)
            fingerprint = cache_fingerprint(path)
            metadata = path.stat()
            allocated = getattr(metadata, "st_blocks", (metadata.st_size + 511) // 512) * 512
        with self.db.transaction() as session:
            job = session.get(Job, job_id)
            item = session.get(MediaItem, job.item_id) if job else None
            if not job or not item or attempt is not None and job.attempt_sequence != attempt:
                return None
            if only_if_missing and session.scalar(select(RetainedCache.id).where(
                    RetainedCache.job_id == job_id,
                    RetainedCache.attempt == (attempt if attempt is not None else job.attempt_sequence)).limit(1)):
                return None
            if completeness == "complete" and (not fingerprint or item.size_bytes is not None
                                                and fingerprint["size"] != item.size_bytes):
                raise AppError("SIZE_MISMATCH", "完整缓存的文件大小校验失败，未开始保存", 409)
            key = digest(raw) if raw else None
            # A native acquisition can rotate its temporary path while keeping
            # the same physical owner. Reuse that row instead of creating one
            # record per observation. Path identity remains the strongest key;
            # native identity and acquisition identity are bounded fallbacks.
            identity_match = []
            if native_identity:
                identity_match.append(RetainedCache.native_identity == native_identity)
            if generation:
                identity_match.append((RetainedCache.generation == generation)
                                      & (RetainedCache.acquisition_id == acquisition_id))
            match = [RetainedCache.path_digest == key] if key else []
            if identity_match:
                match.append(or_(*identity_match))
            if not match and not raw:
                match.append(RetainedCache.path_digest.is_(None))
            row = session.scalar(select(RetainedCache).where(RetainedCache.job_id == job.id,
                RetainedCache.attempt == job.attempt_sequence, or_(*match))
                .order_by(RetainedCache.updated_at.desc()).limit(1)) if match else None
            if row is None:
                row = RetainedCache(job_id=job.id, bot_id=item.bot_id, file_id=item.file_id,
                                    attempt=job.attempt_sequence)
                session.add(row)
            row.path_encrypted, row.path_digest = self.secrets.encrypt(raw) if raw else None, key
            row.completeness, row.state = completeness, "retained" if raw else "missing" if completeness == "absent" else "uncertain"
            row.size_bytes, row.allocated_bytes = fingerprint["size"] if fingerprint else None, allocated
            # The complete path is observed again before saving without a native
            # probe. Preserve its native identity only while the file is unchanged.
            if native_identity is not None or row.fingerprint != fingerprint:
                row.native_identity = native_identity
            row.fingerprint = fingerprint
            row.generation, row.acquisition_id, row.credential = generation, acquisition_id, credential
            row.resumable, row.resume_reason = resumable and completeness == "partial", reason
            row.attempt, row.updated_at = job.attempt_sequence, time.time()
            session.flush()
            job.cache_id = row.id
            return row.id

    async def capture_native(self, client: Any, job_id: str, generation: str,
                             acquisition_id: str | None = None, *, attempt: int | None = None) -> None:
        if not generation:
            return
        with self.db.sessions() as session:
            job = session.get(Job, job_id)
            item = session.get(MediaItem, job.item_id) if job else None
            if not job or not item:
                return
            expected_attempt, file_id = job.attempt_sequence, item.file_id
        if not hasattr(client, "inspect_file_cache"):
            await asyncio.to_thread(self.capture_unknown, job_id, generation, expected_attempt)
            return
        try:
            async with asyncio.timeout(3):
                value = await client.inspect_file_cache(file_id, generation, acquisition_id=acquisition_id)
            raw = value.get("path") or None
            if not raw and value["completeness"] == "unknown":
                await asyncio.to_thread(self.capture_unknown, job_id, generation, expected_attempt)
                return  # A busy read cannot erase an earlier authoritative cache locator.
            await asyncio.to_thread(self.capture, job_id, raw, completeness=value["completeness"],
                generation=generation, acquisition_id=acquisition_id,
                credential=digest(client.bot.token), native_identity=value.get("identity"),
                resumable=value.get("resumable", False) and value.get("settled", False),
                reason=RESUME_REASONS.get(NATIVE_REASONS.get(value.get("reason", ""), "not_resumable")),
                attempt=expected_attempt if attempt is None else attempt)
        except (AppError, OSError, ValueError, TimeoutError):
            # Failure of a read never changes acquisition ownership or erases previous evidence.
            await asyncio.to_thread(self.capture_unknown, job_id, generation, expected_attempt)

    def capture_unknown(self, job_id: str, generation: str, attempt: int) -> None:
        with self.db.sessions() as session:
            if session.scalar(select(RetainedCache.id).where(RetainedCache.job_id == job_id).limit(1)):
                return
        self.capture(job_id, None, completeness="unknown", generation=generation,
                     reason=RESUME_REASONS["unavailable"], attempt=attempt, only_if_missing=True)

    def validate(self, row: RetainedCache) -> Path:
        if not row.path_encrypted or not row.fingerprint:
            raise AppError("CACHE_MISSING", "没有可可靠定位的缓存；请恢复连接后刷新或重新下载", 409)
        try:
            path = self.path(self.secrets.decrypt(row.path_encrypted))
            if cache_fingerprint(path) != row.fingerprint:
                raise AppError("CACHE_CHANGED", "缓存文件已改变，已拒绝使用原记录", 409)
            return path
        except FileNotFoundError as exc:
            raise AppError("CACHE_MISSING", "缓存文件已不存在，可以从头重新下载", 409) from exc

    def source(self, job_id: str) -> str | None:
        with self.db.sessions() as session:
            job = session.get(Job, job_id)
            row = session.get(RetainedCache, job.cache_id) if job and job.cache_id else None
            if job and job.execution_mode == "save":
                if not row or row.completeness != "complete":
                    raise AppError("CACHE_MISSING", "完整缓存不可用，请检查详情或重新下载", 409)
                return str(self.validate(row))
            return None

    def source_unchanged(self, job_id: str) -> None:
        with self.db.sessions() as session:
            job = session.get(Job, job_id)
            row = session.get(RetainedCache, job.cache_id) if job and job.cache_id else None
            if row and row.completeness == "complete":
                self.validate(row)

    async def refresh(self, request_id: str) -> None:
        """Bounded detail refresh can recover a transient failed capability check.

        This never queues work or calls getFile. Persist only capability changes.
        """
        identity = self.identity()
        if not identity:
            return
        client, generation, credential = identity
        with self.db.sessions() as session:
            rows = list(session.scalars(select(RetainedCache).join(Job, Job.id == RetainedCache.job_id).where(
                Job.request_id == request_id, Job.state.in_(TERMINAL), RetainedCache.bot_id == self.runtime.bot_id,
                RetainedCache.completeness == "partial", RetainedCache.state == "retained",
                RetainedCache.generation == generation).limit(16)))
        async def inspect(row: RetainedCache) -> None:
            try:
                async with asyncio.timeout(3):
                    native = await client.inspect_file_cache(row.file_id, generation)
                reusable = bool(native.get("settled") and native.get("resumable") and native.get("identity") == row.native_identity)
                reason = None if reusable else RESUME_REASONS["not_resumable"]
            except (AppError, AttributeError, TimeoutError):
                reusable, reason = False, RESUME_REASONS["unavailable"]
            with self.db.transaction() as session:
                current = session.get(RetainedCache, row.id)
                job = session.get(Job, row.job_id)
                if (current and job and job.state in TERMINAL and current.attempt == row.attempt
                        and current.generation == generation and current.credential in {"", credential}
                        and current.native_identity == row.native_identity and self.identity() == identity
                        and (current.resumable, current.resume_reason) != (reusable, reason)):
                    current.resumable, current.resume_reason = reusable, reason
        await asyncio.gather(*(inspect(row) for row in rows))

    @staticmethod
    def action(session: Session, job: Job) -> CacheAction | None:
        return session.scalar(select(CacheAction).where(CacheAction.job_id == job.id,
            CacheAction.attempt.in_([job.attempt_sequence, job.attempt_sequence + 1]))
            .order_by(CacheAction.created_at.desc()).limit(1))

    @staticmethod
    def finish_action(session: Session, job: Job) -> None:
        action = RetainedCacheService.action(session, job)
        if action and action.state in {"queued", "running"} and job.state in {"failed", "cancelled", "succeeded", "already_exists"}:
            action.state = "succeeded" if job.state in {"succeeded", "already_exists"} else job.state
            action.error_code, action.message = job.error_code, job.error_message

    def blocker(self, session: Session, job: Job, row: RetainedCache | None, *, clean: bool = False,
                allow_saving: bool = False) -> str | None:
        from app.db.models import CancelCleanup
        cleanup = session.get(CancelCleanup, job.request_id)
        if cleanup:
            return ("任务已取消并完成文件清理；如需重新下载，请重新发送文件创建新任务。" if cleanup.state == "complete" else
                    "任务已请求取消并清理文件，请等待清理完成；部分失败时请使用任务的重试清理。")
        if job.state not in (TERMINAL | {"succeeded", "already_exists"} if clean else TERMINAL):
            return RESUME_REASONS["active"]
        if maintenance_blocked(session, bot_id=row.bot_id if row else None):
            return "缓存维护或删除结果尚未确认，请先完成恢复。"
        if row and self.source_busy(session, row):
            return "此缓存的自动清理结果尚未确认，请恢复连接或安全重启本地 API 后核对。"
        if self.runtime.worker.cleanup_paused:
            return "自动缓存清理正在处理，请等待完成后重试。"
        busy = session.scalar(select(CacheAction.id).where(CacheAction.job_id == job.id,
            CacheAction.state.in_(BUSY)).limit(1))
        if busy:
            return "此文件的缓存操作尚未完成，请等待结果。"
        identity = self.identity(allow_saving=allow_saving)
        if not identity or self.runtime.bot_id != (row.bot_id if row else None):
            return "当前 Bot 连接不可用或归属不匹配，请恢复原 Bot 连接后重试。"
        if row and row.credential and row.credential != identity[2]:
            return "Bot 凭据已改变，请连接原 Bot 后重试。"
        if clean:
            if session.scalar(select(Job.id).where(Job.acquisition_mode == "local",
                    Job.state.in_(["fetching", "saving", "cancelling", "recovery_blocked"])).limit(1)):
                return "请等待其他下载和保存停止后清理缓存。"
            if row and row.path_digest:
                shared = session.scalar(select(RetainedCache.id).join(Job, Job.id == RetainedCache.job_id).where(
                    RetainedCache.path_digest == row.path_digest, RetainedCache.id != row.id,
                    RetainedCache.state == "retained", Job.state.not_in(["succeeded", "already_exists"])).limit(1))
                if shared:
                    return "其他任务仍保留并使用此缓存，暂不能清理。"
            same_source = session.scalar(select(Job.id).join(MediaItem, MediaItem.id == Job.item_id).where(
                Job.id != job.id, MediaItem.bot_id == row.bot_id, MediaItem.file_id == row.file_id,
                Job.state.in_(["queued", "retry_wait", "waiting_existing"])).limit(1)) if row else None
            if same_source:
                return "同一文件仍有排队任务，请先完成或取消相关任务。"
        return None

    def project(self, session: Session, job: Job) -> list[dict[str, Any]]:
        rows = list(session.scalars(select(RetainedCache).where(RetainedCache.job_id == job.id)
                                    .order_by(RetainedCache.updated_at.desc(), RetainedCache.id)))
        result = []
        identity = self.identity()
        for row in rows:
            state = row.state
            if state == "retained":
                try:
                    self.validate(row)
                except AppError as exc:
                    state = "missing" if exc.code == "CACHE_MISSING" else "changed"
                except OSError:
                    state = "uncertain"
            blocker = self.blocker(session, job, row)
            save_blocker = self.blocker(session, job, row, allow_saving=True) if row.completeness == "complete" else blocker
            resume_reason = None
            if identity and row.generation != identity[1]:
                resume_reason = RESUME_REASONS["generation_changed"]
            elif state in {"missing", "deleted"}:
                resume_reason = RESUME_REASONS["absent"]
            elif state == "changed":
                resume_reason = RESUME_REASONS["changed"]
            elif not row.resumable:
                resume_reason = row.resume_reason or RESUME_REASONS["not_resumable"]
            action = session.scalar(select(CacheAction).where(CacheAction.job_id == job.id,
                CacheAction.cache_id == row.id).order_by(CacheAction.created_at.desc()).limit(1))
            clean_blocker = self.blocker(session, job, row, clean=True)
            completed = job.state in {"succeeded", "already_exists"}
            public_blocker = clean_blocker if completed else (save_blocker if row.completeness == "complete" else blocker) or clean_blocker
            guidance = ("无法核对旧缓存文件身份，已禁用单文件操作；可从头重新下载。需要清理旧缓存时，请在设置的媒体缓存维护中核对并清理。"
                        if state in {"changed", "uncertain"} else RECOVERY_GUIDANCE if public_blocker and not completed else None)
            result.append({"id": row.id, "state": state, "completeness": row.completeness,
                "path": self.display(row), "size_bytes": row.size_bytes, "allocated_bytes": row.allocated_bytes,
                "can_clean": not clean_blocker and state == "retained",
                "can_save": not save_blocker and state == "retained" and row.completeness == "complete",
                "can_resume_download": not blocker and state == "retained" and row.completeness == "partial" and not resume_reason,
                "can_redownload": not blocker,
                "unavailable_reason": public_blocker,
                "resume_unavailable_reason": blocker or resume_reason,
                "recovery_guidance": guidance,
                "operation": {"id": action.id, "kind": action.kind, "state": action.state,
                    "error_code": action.error_code, "message": action.message} if action else None})
        return result

    def selected(self, session: Session, request_id: str, item_id: str, cache_id: str | None) -> tuple[Job, RetainedCache]:
        request, item = session.get(MediaRequest, request_id), session.get(MediaItem, item_id)
        job = session.scalar(select(Job).where(Job.item_id == item_id))
        if not request or request.deleted_at is not None or not item or item.request_id != request_id or not job:
            raise AppError("NOT_FOUND", "任务文件不存在", 404)
        row = session.get(RetainedCache, cache_id or job.cache_id) if cache_id or job.cache_id else None
        if not row or row.job_id != job.id:
            raise AppError("CACHE_MISSING", "没有可操作的缓存记录", 409)
        return job, row

    async def submit(self, request_id: str, item_id: str, kind: str, operation_id: str,
                     *, cache_id: str | None = None, confirmed: bool = False) -> dict[str, Any]:
        from app.services.tasks import enqueue, project

        if kind in {"clean", "redownload"} and not confirmed:
            raise AppError("CACHE_CONFIRMATION_REQUIRED", "请先确认此缓存操作", 422)
        with self.db.sessions() as session:
            job, row = self.selected(session, request_id, item_id, cache_id)
            previous = session.get(CacheAction, operation_id)
            if previous:
                if (previous.job_id, previous.cache_id, previous.kind) != (job.id, row.id, kind):
                    raise AppError("OPERATION_CONFLICT", "操作编号已用于其他动作", 409)
                return self.runtime.tasks.detail(request_id)
            blocker = self.blocker(session, job, row, clean=kind == "clean", allow_saving=kind == "save")
            if blocker:
                raise AppError("CACHE_BUSY", blocker, 409)
            job_id, row_id, previous_attempt = job.id, row.id, job.attempt_sequence
            identity = self.identity(allow_saving=kind == "save")
            assert identity
            client, generation, credential = identity
            file_id = row.file_id
        # Network capability reads are bounded and outside both transaction and connection lock.
        if kind == "download":
            try:
                async with asyncio.timeout(3):
                    native = await client.inspect_file_cache(file_id, generation)
                if (not native.get("settled") or not native.get("resumable")
                        or native.get("identity") != row.native_identity or row.generation != generation):
                    raise AppError("RESUME_UNAVAILABLE", RESUME_REASONS["generation_changed"] if row.generation != generation
                                   else RESUME_REASONS["not_resumable"], 409)
            except (TimeoutError, AttributeError, AppError) as exc:
                message = exc.message if isinstance(exc, AppError) and exc.code == "RESUME_UNAVAILABLE" else RESUME_REASONS["unavailable"]
                with self.db.transaction() as session:
                    previous = session.get(CacheAction, operation_id)
                    if previous:
                        if (previous.job_id, previous.cache_id, previous.kind) != (job_id, row_id, kind):
                            raise AppError("OPERATION_CONFLICT", "操作编号已用于其他动作", 409) from exc
                        return self.runtime.tasks.detail(request_id)
                    live_job = session.get(Job, job_id)
                    if (not live_job or live_job.attempt_sequence != previous_attempt
                            or live_job.state not in TERMINAL or self.identity() != identity):
                        raise AppError("CACHE_STATE_CHANGED", "文件或连接状态已改变，请刷新详情", 409) from exc
                    session.add(CacheAction(id=operation_id, job_id=job_id, cache_id=row_id, kind=kind,
                        state="failed", attempt=previous_attempt, generation=generation, credential=credential,
                        error_code="RESUME_UNAVAILABLE", message=message))
                    current = session.get(RetainedCache, row_id)
                    if current:
                        current.resumable, current.resume_reason = False, message
                raise AppError("RESUME_UNAVAILABLE", message, 409) from exc
        with self.db.transaction() as session:
            job, row = self.selected(session, request_id, item_id, cache_id)
            previous = session.get(CacheAction, operation_id)
            if previous:
                if (previous.job_id, previous.cache_id, previous.kind) != (job.id, row.id, kind):
                    raise AppError("OPERATION_CONFLICT", "操作编号已用于其他动作", 409)
                return self.runtime.tasks.detail(request_id)
            if job.attempt_sequence != previous_attempt:
                raise AppError("CACHE_STATE_CHANGED", "文件尝试已改变，请刷新详情", 409)
            require_no_maintenance(session, bot_id=row.bot_id)
            blocker = self.blocker(session, job, row, clean=kind == "clean", allow_saving=kind == "save")
            if blocker or self.identity(allow_saving=kind == "save") != identity:
                raise AppError("CACHE_BUSY", blocker or "Bot 连接已改变，请刷新后重试", 409)
            if kind == "save" and row.completeness != "complete":
                raise AppError("CACHE_INCOMPLETE", "缓存未完整下载，不能继续保存", 409)
            if kind != "redownload":
                self.validate(row)
            if kind == "download" and (not row.resumable or row.generation != generation):
                raise AppError("RESUME_UNAVAILABLE", RESUME_REASONS["not_resumable"], 409)
            sequence = job.attempt_sequence + (kind != "clean")
            session.add(CacheAction(id=operation_id, job_id=job.id, cache_id=row.id, kind=kind,
                state="running" if kind == "clean" else "queued", attempt=sequence,
                generation=generation, credential=credential))
            if kind != "clean":
                job.execution_mode = {"save": "save", "download": "download_resume", "redownload": "redownload"}[kind]
                job.cache_id, job.state, job.phase = row.id, "queued", "queued"
                job.cancel_requested, job.auto_retries = False, 0
                job.error_code, job.error_message, job.storage_failure = None, None, None
                job.next_run_at, job.finished_at = time.time(), None
                item = session.get(MediaItem, job.item_id)
                assert item
                assessment = assess_space(session, self.runtime.storage, self.config, job.root_id,
                    item.size_bytes, "local", "before_save" if kind == "save" else "before_download", job.id)
                if not assessment.sufficient:
                    from app.bot.messages import storage_warning
                    job.state, job.phase, job.finished_at = "failed", "failed", time.time()
                    try:
                        assessment.require()
                    except AppError as exc:
                        job.error_code = exc.code
                    job.error_message = storage_warning(assessment.to_dict())
                    job.storage_failure = assessment.to_dict()
                    session.flush()
                    self.finish_action(session, job)
                request = session.get(MediaRequest, request_id)
                assert request
                session.flush()
                project(session, request)
                enqueue(session, request, f"cache-action:{operation_id}", (job.error_message or "空间检查失败") if job.state == "failed" else {
                    "save": "已加入继续保存队列，将直接使用完整缓存并重新检查空间。",
                    "download": "已加入继续下载队列，启动前将再次检查分片及可用空间。",
                    "redownload": "已加入重新下载队列，将按完整空间预算重新检查；旧缓存保留。"}[kind])
        if kind == "clean":
            await self.clean(operation_id, client, generation)
        return self.runtime.tasks.detail(request_id)

    def clean_result(self, operation_id: str, state: str, message: str, code: str | None = None) -> None:
        with self.db.transaction() as session:
            action = session.get(CacheAction, operation_id)
            if not action or action.state not in {"running", "uncertain"}:
                return
            action.state, action.error_code, action.message = state, code, message
            row = session.get(RetainedCache, action.cache_id) if action.cache_id else None
            if state == "succeeded" and row:
                for sibling in session.scalars(select(RetainedCache).where(RetainedCache.bot_id == row.bot_id,
                        RetainedCache.path_digest == row.path_digest)):
                    sibling.state, sibling.resumable = "deleted", False

    async def clean(self, operation_id: str, client: Any, generation: str) -> None:
        issued = False
        self.cleaning.add(operation_id)
        try:
            with self.db.sessions() as session:
                action = session.get(CacheAction, operation_id)
                row = session.get(RetainedCache, action.cache_id) if action and action.cache_id else None
                assert action and row
                path = self.validate(row)
            sha = None
            if row.completeness == "complete":
                size, sha = await asyncio.to_thread(regular_file_hash, path)
                if size != row.size_bytes:
                    raise AppError("CACHE_CHANGED", "缓存大小已改变，未执行清理", 409)
            async with self.runtime.connection_lock:
                if self.identity() != (client, generation, action.credential):
                    raise AppError("CACHE_CONNECTION_CHANGED", "Bot 连接已改变，未执行清理", 409)
                await asyncio.to_thread(self.validate, row)
                issued = True
                if row.completeness == "complete":
                    result = await client.delete_file_cache(row.file_id, str(path), row.size_bytes, generation,
                                                          expected_sha256=sha)
                else:
                    result = await client.delete_partial_file_cache(row.file_id, str(path), row.size_bytes,
                                                                   generation, expected_identity=row.native_identity)
                if isinstance(result, dict):
                    result = result.get("status")
                if result not in {"deleted", "absent"}:
                    raise AppError("CACHE_CLEAN_UNCONFIRMED", "缓存清理结果尚未确认", 409)
            self.clean_result(operation_id, "succeeded", "缓存已清理。")
            self.runtime.cache_maintenance.invalidate()
        except asyncio.CancelledError:
            self.clean_result(operation_id, "uncertain" if issued else "failed",
                              "缓存清理结果待核对，请恢复连接或安全重启本地 API。", "CACHE_CLEAN_UNCERTAIN")
            raise
        except Exception as exc:
            rejected = isinstance(exc, CacheCleanupError) and exc.reason in {
                "cache_busy", "cache_generation_changed", "cache_identity_changed", "cache_invalid_request",
                "cache_path_invalid", "cache_unsupported", "cache_identity_unavailable"}
            uncertain = issued and not rejected
            busy = isinstance(exc, CacheCleanupError) and exc.reason == "cache_busy"
            self.clean_result(operation_id, "uncertain" if uncertain else "failed",
                "缓存清理结果待核对，请恢复连接或安全重启本地 API。" if uncertain else
                "其他原生操作仍在使用缓存，等待安全空闲后重试。" if busy else
                exc.message if isinstance(exc, AppError) else "缓存身份校验或原生清理失败，未确认删除；请检查连接后重试。",
                "CACHE_CLEAN_UNCERTAIN" if uncertain else "CACHE_CLEAN_BUSY" if busy else "CACHE_CLEAN_FAILED")
        finally:
            self.cleaning.discard(operation_id)

    async def recover(self) -> None:
        """Read only; never replay a possibly committed deletion."""
        self.reconcile_queued()
        identity = self.identity()
        if not identity:
            return
        client, generation, credential = identity
        with self.db.sessions() as session:
            action = session.scalar(select(CacheAction).where(CacheAction.kind == "clean",
                CacheAction.state.in_(["running", "uncertain"])).order_by(CacheAction.created_at).limit(1))
            row = session.get(RetainedCache, action.cache_id) if action and action.cache_id else None
        if not action or not row or action.id in self.cleaning or action.credential != credential:
            return
        try:
            async with asyncio.timeout(3):
                expected = ({"expected_path": self.secrets.decrypt(row.path_encrypted),
                    "expected_size": row.size_bytes, "expected_identity": row.native_identity}
                    if row.path_encrypted and row.native_identity else {})
                native = await client.inspect_file_cache(row.file_id, generation, **expected)
            if self.identity() != identity:
                return
            # Native inspection's settled bit must include exclusion of in-flight deletion.
            if not native.get("settled"):
                return
            if native.get("completeness") == "absent":
                try:
                    self.validate(row)
                except AppError as exc:
                    if exc.code == "CACHE_MISSING":
                        self.clean_result(action.id, "succeeded", "已核对原缓存不存在，清理完成。")
                    else:
                        self.clean_result(action.id, "failed", "旧清理操作已结束，原缓存身份已改变；请刷新详情。", "CACHE_CHANGED")
                except OSError:
                    self.clean_result(action.id, "failed", "旧清理操作已结束，原缓存路径无法访问；检查挂载和权限后重试。", "CACHE_CHANGED")
                else:
                    self.clean_result(action.id, "failed", "旧清理操作已结束，原缓存仍保留；可以重新清理。", "CACHE_CLEAN_RETRY_REQUIRED")
            elif action.generation != generation:
                self.clean_result(action.id, "failed", "旧 Bot API 已退出，缓存仍保留；可以重新清理。", "CACHE_CLEAN_RETRY_REQUIRED")
            else:
                # Paired-native inspection is excluded while its deletion fence
                # is held. A settled exact snapshot proves the old mutation exited;
                # do not strand a known retained source behind an uncertain action.
                try:
                    original = self.validate(row)
                except AppError as exc:
                    self.clean_result(action.id, "failed", exc.message + "；旧清理操作已结束，请刷新详情。", exc.code)
                except OSError:
                    self.clean_result(action.id, "failed", "旧清理操作已结束，但缓存路径不可访问；检查挂载后重试。", "CACHE_CHANGED")
                else:
                    if (native.get("path") == str(original) and
                            (not row.native_identity or native.get("identity") == row.native_identity)):
                        self.clean_result(action.id, "failed", "已确认旧清理操作结束，原缓存仍保留；可手动重试清理。", "CACHE_CLEAN_RETRY_REQUIRED")
                    else:
                        self.clean_result(action.id, "failed", "旧清理操作已结束，但缓存身份已改变，未重复删除；请核对后手动处理。", "CACHE_CHANGED")
        except (AppError, TimeoutError, AttributeError):
            if action.generation != generation:
                # Verified child replacement is physical stop proof for the old mutation.
                try:
                    self.validate(row)
                except AppError as exc:
                    if exc.code == "CACHE_MISSING":
                        self.clean_result(action.id, "succeeded", "旧 API 已退出，已核对原缓存不存在。")
                    elif exc.code == "CACHE_CHANGED":
                        self.clean_result(action.id, "failed", "旧 API 已退出，缓存身份已改变；请刷新详情。", "CACHE_CHANGED")
                except OSError:
                    pass
                else:
                    self.clean_result(action.id, "failed", "旧 API 已退出，缓存仍保留，可以重新清理。", "CACHE_CLEAN_RETRY_REQUIRED")

    def reconcile_queued(self) -> None:
        """A queued manual action has no writer; unavailable prerequisites must end it."""
        from app.services.tasks import enqueue, project

        identity = self.identity()
        save_identity = self.identity(allow_saving=True)
        with self.db.sessions() as session:
            candidates = list(session.scalars(select(CacheAction).where(CacheAction.state == "queued")))
        def unavailable(action: CacheAction) -> bool:
            current = save_identity if action.kind == "save" else identity
            return bool(current and (current[1] != action.generation or current[2] != action.credential)
                        or not current and time.time() - action.created_at >= 10)
        due = [action.id for action in candidates if unavailable(action)]
        if not due:
            return
        with self.db.transaction() as session:
            for action in session.scalars(select(CacheAction).where(CacheAction.id.in_(due), CacheAction.state == "queued")):
                job = session.get(Job, action.job_id)
                if not job or job.state not in {"queued", "retry_wait", "waiting_existing"}:
                    continue
                if action.attempt != job.attempt_sequence + 1:
                    continue
                current = save_identity if action.kind == "save" else identity
                changed = current and (current[1] != action.generation or current[2] != action.credential)
                if not changed and (current or time.time() - action.created_at < 10):
                    continue
                job.state, job.phase, job.finished_at = "failed", "failed", time.time()
                job.error_code = "RESUME_UNAVAILABLE" if action.kind == "download" else "CACHE_CONNECTION_UNAVAILABLE"
                job.error_message = (RESUME_REASONS["generation_changed"] if changed and action.kind == "download"
                                     else "本次缓存操作所需的 Bot 连接不可用，已结束等待；请恢复连接后手动重试。")
                action.state, action.error_code, action.message = "failed", job.error_code, job.error_message
                request = session.get(MediaRequest, job.request_id)
                if request:
                    session.flush()
                    project(session, request)
                    enqueue(session, request, f"cache-action-unavailable:{action.id}", job.error_message)

    def automatic_allowed(self, cleanup: CacheCleanup) -> bool:
        from sqlalchemy import or_
        path_digest = digest(self.secrets.decrypt(cleanup.source_path_encrypted))
        with self.db.sessions() as session:
            rows = session.scalars(select(RetainedCache).where(RetainedCache.bot_id == cleanup.bot_id,
                or_(RetainedCache.file_id == cleanup.file_id, RetainedCache.path_digest == path_digest),
                RetainedCache.state == "retained"))
            return not any((job := session.get(Job, row.job_id)) and job.state not in {"succeeded", "already_exists"}
                           for row in rows)

    def source_busy(self, session: Session, row: RetainedCache) -> bool:
        for cleanup in session.scalars(select(CacheCleanup).where(CacheCleanup.bot_id == row.bot_id,
                CacheCleanup.error_code.in_(["CACHE_CLEANUP_IN_FLIGHT", "CACHE_CLEANUP_UNAVAILABLE"]))):
            if cleanup.file_id == row.file_id or row.path_digest == digest(self.secrets.decrypt(cleanup.source_path_encrypted)):
                return True
        return False

    def mark_automatic_deleted(self, cleanup: CacheCleanup) -> None:
        path_digest = digest(self.secrets.decrypt(cleanup.source_path_encrypted))
        with self.db.transaction() as session:
            for row in session.scalars(select(RetainedCache).where(RetainedCache.bot_id == cleanup.bot_id,
                    RetainedCache.path_digest == path_digest)):
                row.state, row.resumable = "deleted", False
