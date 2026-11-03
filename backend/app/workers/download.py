"""Durable file executor. Network operations and copying never hold a DB transaction."""
import asyncio
import errno
import hashlib
import os
import random
import threading
import time
from collections.abc import Callable
from contextlib import aclosing
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from typing import Any

from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter, TelegramUnauthorizedError
from aiohttp import ClientError
from sqlalchemy import delete, select

from app.bot.client import TelegramClient, cloud_size_check
from app.bot.messages import storage_warning
from app.core.config import Config
from app.core.errors import AppError
from app.core.security import Secrets, digest
from app.db.models import (
    Attempt,
    CacheCleanup,
    FileReservation,
    Job,
    LocalApiRuntime,
    MediaItem,
    MediaRequest,
    PublishJournal,
    RequestSafety,
    RetainedCache,
    Settings,
    StoredFile,
)
from app.db.session import Database
from app.services.cache_maintenance import maintenance_blocked
from app.services.diagnostics import DiagnosticLog
from app.services.download_progress import DownloadProgress, ProgressOwner
from app.services.file_acquisition import AcquisitionOwner, AcquisitionStore, uncertain_message
from app.services.request_safety import RequestSafetyService, SafetyDeferralReason, SafetyDeferred
from app.services.retained_cache import RetainedCacheService, cache_fingerprint
from app.services.tasks import project
from app.storage.files import (
    StorageManager,
    contained,
    copy_stream,
    file_hash,
    publish_no_replace,
    regular_file_hash,
    source_file,
)
from app.storage.naming import unique_name
from app.storage.space import assess_space


async def owned_thread[T](operation: Callable[..., T], *args: Any) -> T:
    """A cancelled coroutine must still join its short state-changing filesystem/DB owner."""
    task = asyncio.create_task(asyncio.to_thread(operation, *args))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        try:
            await task
        except Exception:
            pass
        raise


class DownloadWorker:
    def __init__(self, db: Database, storage: StorageManager, config: Config, boot_id: str,
                 *, secrets: Secrets | None = None) -> None:
        self.db, self.storage, self.config, self.boot_id = db, storage, config, boot_id
        self.progress_times: dict[str, float] = {}
        self.credential_hash = ""
        self.diagnostics: DiagnosticLog | None = None
        self.download_progress: DownloadProgress | None = None
        self.cleanup_paused = False
        self.secrets = secrets or Secrets(config.data_dir)
        self.acquisitions = AcquisitionStore(db)
        self.acquisition_generation: Callable[[], str] = lambda: ""
        self.retained_cache: RetainedCacheService | None = None

    def progress_job(self, job_id: str) -> tuple[Job, MediaItem] | None:
        with self.db.sessions() as session:
            job = session.get(Job, job_id)
            item = session.get(MediaItem, job.item_id) if job else None
            return (job, item) if job and item else None

    def progress_owned(self, owner: ProgressOwner) -> bool:
        with self.db.sessions() as session:
            job = session.get(Job, owner.job_id)
            item = session.get(MediaItem, job.item_id) if job else None
            return bool(job and item and DownloadProgress.eligible(job, owner.boot_id)
                        and job.attempt_sequence == owner.attempt
                        and (job.item_id, item.bot_id, item.file_id) == (owner.item_id, owner.bot_id, owner.file_id))

    async def begin_progress(self, job_id: str) -> ProgressOwner | None:
        registry = self.download_progress
        rows = await asyncio.to_thread(self.progress_job, job_id)
        if not rows or not registry:
            return None
        task = asyncio.create_task(asyncio.to_thread(
            registry.begin, rows[0], self.boot_id, bot_id=rows[1].bot_id, file_id=rows[1].file_id))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            # Registration consults supervisor/DB state in a thread. Join it before clearing
            # on shutdown so a late result cannot leave a new observation behind.
            result = await asyncio.gather(task, return_exceptions=True)
            if isinstance(result[0], ProgressOwner):
                registry.clear(result[0])
            raise

    async def observe_download(self, client: TelegramClient, file_id: str, owner: ProgressOwner) -> None:
        registry = self.download_progress
        assert registry
        try:
            while await asyncio.to_thread(self.progress_owned, owner):
                result = await client.file_progress([file_id])
                # Recheck ownership after I/O: cancellation/phase change may have committed while waiting.
                if not await asyncio.to_thread(self.progress_owned, owner):
                    return
                if result and file_id in result[1]:
                    await asyncio.to_thread(registry.update, owner, result[0], result[1][file_id])
                else:
                    registry.unavailable(owner)
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            raise
        except Exception:
            # Telemetry cannot change acquisition, protection or the durable job outcome.
            pass
        finally:
            registry.clear(owner)

    def claim(self, bot_id: int | None = None, mode: str | None = None, *, save_only: bool = False) -> str | None:
        outcomes: list[str] = []
        result = self._claim(bot_id, mode, outcomes, save_only=save_only)
        if self.diagnostics:
            for job_id in outcomes:
                try:
                    self.record_result(job_id)
                except Exception:
                    self.diagnostics.error = True
        return result

    def _claim(self, bot_id: int | None, mode: str | None, outcomes: list[str], *, save_only: bool = False) -> str | None:
        with self.db.transaction() as session:
            if self.cleanup_paused or maintenance_blocked(session):
                return None
            safety = session.get(RequestSafety, self.credential_hash) if self.credential_hash else None
            safety_blocked = bool(safety and RequestSafetyService.blocked(safety.values, time.time()))
            local = session.get(LocalApiRuntime, 1)
            if (self.config.managed_local_api and not local or local
                    and (self.config.managed_local_api or local.values.get("action") == "cloud_reconnect")
                    and local.values.get("phase") != "idle"):
                return None
            setting_row = session.get(Settings, 1)
            current_mode = os.getenv("TGD_BOT_API_MODE", setting_row.values.get("bot_api_mode", "local") if setting_row else "local")
            if self.config.managed_local_api and local and local.values.get("applied_mode") != current_mode:
                return None
            if mode is not None and mode != current_mode:
                return None
            query = select(Job).join(MediaRequest, MediaRequest.id == Job.request_id).where(
                MediaRequest.deleted_at.is_(None),
                Job.state.in_(["queued", "retry_wait", "waiting_existing"]), Job.next_run_at <= time.time())
            if bot_id is not None:
                query = query.where(MediaRequest.bot_id == bot_id)
            if save_only:
                query = query.where(Job.execution_mode == "save")
            jobs = list(session.scalars(query.order_by(Job.next_run_at, Job.id).limit(50)))
            active_requests = list(session.scalars(select(Job.request_id).where(
                Job.state.in_(["fetching", "saving", "cancelling", "recovery_blocked"]))))
            # One busy media group must not monopolize newly available global slots.
            jobs.sort(key=lambda job: (active_requests.count(job.request_id), job.next_run_at, job.id))
            for job in jobs:
                if safety_blocked and job.execution_mode != "save":
                    continue
                item = session.get(MediaItem, job.item_id)
                assert item
                if maintenance_blocked(session, bot_id=item.bot_id):
                    continue
                if job.cancel_requested:
                    outcomes.append(job.id)
                    job.state, job.finished_at = "cancelled", time.time()
                    request = session.get(MediaRequest, job.request_id)
                    assert request
                    RetainedCacheService.finish_action(session, job)
                    project(session, request)
                    continue
                try:
                    self.storage.get(job.root_id)
                except AppError as exc:
                    outcomes.append(job.id)
                    job.state, job.phase, job.finished_at = "failed", "failed", time.time()
                    job.error_code, job.error_message = exc.code, exc.message
                    session.execute(delete(FileReservation).where(FileReservation.job_id == job.id))
                    session.flush()
                    request = session.get(MediaRequest, job.request_id)
                    assert request
                    RetainedCacheService.finish_action(session, job)
                    project(session, request)
                    continue
                if item.file_unique_id:
                    stored = session.scalar(select(StoredFile).where(StoredFile.bot_id == item.bot_id,
                                                                     StoredFile.file_unique_id == item.file_unique_id))
                    try:
                        available = bool(stored and self.storage.get(stored.root_id).existing(stored.relative_path, stored.size_bytes))
                    except AppError:
                        available = False
                    if stored and available:
                        outcomes.append(job.id)
                        job.state, job.phase = "already_exists", "already_exists"
                        job.linked_job_id, job.relative_path, job.root_id = stored.job_id, stored.relative_path, stored.root_id
                        job.completed_bytes, job.finished_at = stored.size_bytes, time.time()
                        session.flush()
                        request = session.get(MediaRequest, job.request_id)
                        assert request
                        RetainedCacheService.finish_action(session, job)
                        project(session, request)
                        continue
                    if stored:
                        session.delete(stored)
                    reservation = session.scalar(select(FileReservation).where(
                        FileReservation.bot_id == item.bot_id, FileReservation.file_unique_id == item.file_unique_id))
                    if reservation and reservation.job_id != job.id:
                        existing = session.get(Job, reservation.job_id)
                        if existing and existing.state not in {"failed", "cancelled", "succeeded", "already_exists"}:
                            job.state, job.linked_job_id = "waiting_existing", existing.id
                            job.next_run_at = time.time() + 1
                            continue
                        session.delete(reservation)
                        session.flush()
                    if not reservation or reservation.job_id != job.id:
                        session.add(FileReservation(bot_id=item.bot_id, file_unique_id=item.file_unique_id, job_id=job.id))
                try:
                    cache = session.get(RetainedCache, job.cache_id) if job.cache_id else None
                    if job.execution_mode == "download" and cache and cache.completeness == "complete" and cache.state == "retained":
                        job.execution_mode = "save"
                    if cache and job.execution_mode == "save" and self.retained_cache and self.retained_cache.source_busy(session, cache):
                        raise AppError("CACHE_MUTATION_UNCERTAIN", "此缓存的清理结果尚未确认，请恢复连接或安全重启本地 API 后重试")
                    assessment = assess_space(session, self.storage, self.config, job.root_id, item.size_bytes,
                        current_mode, "before_save" if job.execution_mode == "save" else "before_download", job.id)
                    assessment.require()
                except (OSError, AppError) as exc:
                    outcomes.append(job.id)
                    job.error_code = exc.code if isinstance(exc, AppError) else "STORAGE_UNAVAILABLE"
                    job.error_message = exc.message if isinstance(exc, AppError) else "存储不可用，请检查挂载和权限"
                    job.storage_failure = exc.details if isinstance(exc, AppError) and isinstance(exc.details, dict) else None
                    if job.storage_failure:
                        job.error_message = storage_warning(job.storage_failure)
                    job.state, job.phase, job.finished_at = "failed", "failed", time.time()
                    session.execute(delete(FileReservation).where(FileReservation.job_id == job.id))
                    session.flush()
                    request = session.get(MediaRequest, job.request_id)
                    assert request
                    RetainedCacheService.finish_action(session, job)
                    project(session, request)
                    continue
                job.state, job.phase, job.boot_id = "fetching", "fetching", self.boot_id
                job.acquisition_mode = current_mode
                job.started_at, job.completed_bytes, job.finished_at = time.time(), 0, None
                job.attempt_sequence += 1
                job.linked_job_id, job.error_code, job.error_message = None, None, None
                job.storage_failure = None
                action = RetainedCacheService.action(session, job)
                if action:
                    action.state = "running"
                session.add(Attempt(job_id=job.id, sequence=job.attempt_sequence))
                session.flush()
                request = session.get(MediaRequest, job.request_id)
                assert request
                project(session, request)
                return job.id
        return None

    def cancelled(self, job_id: str) -> bool:
        with self.db.sessions() as session:
            job = session.get(Job, job_id)
            return not job or job.cancel_requested

    def progress(self, job_id: str, completed: int, speed: float) -> None:
        now = time.monotonic()
        if now - self.progress_times.get(job_id, 0) < 0.5:
            return
        self.progress_times[job_id] = now
        failure = self.download_space_exhausted(job_id, "saving")
        if failure:
            self.record_space_failure(job_id, "saving", failure)
            raise self.observed_error(failure)
        with self.db.transaction() as session:
            job = session.get(Job, job_id)
            if job:
                job.completed_bytes, job.speed_bps = completed, speed

    def prepare(self, job_id: str) -> tuple[str, str, str, int | None]:
        with self.db.transaction() as session:
            job = session.get(Job, job_id)
            assert job
            item = session.get(MediaItem, job.item_id)
            assert item
            return item.file_id, job.root_id, job.relative_path, item.size_bytes

    def journal_copying(self, job_id: str, temporary: str, final: str, api_source: str | None = None) -> None:
        with self.db.transaction() as session:
            job = session.get(Job, job_id)
            assert job
            job.phase = "saving"
            if not job.cancel_requested:
                job.state = "saving"
            row = session.get(PublishJournal, job_id)
            if row:
                if row.stage == "ready":
                    raise AppError("PUBLISH_UNCERTAIN", "发布结果尚待核对，请先恢复原任务", 409)
                try:
                    storage = self.storage.get(job.root_id)
                    storage.check(check_space=False)
                    contained(storage.root, row.temporary_path).unlink(missing_ok=True)
                except (OSError, AppError) as exc:
                    raise AppError("CACHE_TEMP_CLEANUP_FAILED", "目标临时文件仍无法清理，请检查权限后重试；缓存保留", 409) from exc
                session.delete(row)
                session.flush()
            session.add(PublishJournal(job_id=job_id, temporary_path=temporary, final_path=final,
                                       stage="copying", api_source_path_encrypted=self.secrets.encrypt(api_source) if api_source else None))
            request = session.get(MediaRequest, job.request_id)
            assert request
            project(session, request)

    def journal_ready(self, job_id: str, size: int, sha: str) -> None:
        with self.db.transaction() as session:
            row = session.get(PublishJournal, job_id)
            assert row
            row.stage, row.size_bytes, row.sha256 = "ready", size, sha

    def publish(self, job_id: str) -> None:
        # Mutex with REST cancellation is the short database write transaction.
        with self.db.transaction() as session:
            job, journal = session.get(Job, job_id), session.get(PublishJournal, job_id)
            assert job and journal and journal.size_bytes is not None and journal.sha256
            storage = self.storage.get(job.root_id)
            storage.check()
            temporary = contained(storage.root, journal.temporary_path)
            final = contained(storage.root, journal.final_path)
            if job.cancel_requested:
                raise AppError("CANCELLED", "下载已取消")
            try:
                publish_no_replace(temporary, final)
            except FileExistsError:
                # Persist collision path before publishing; never overwrite external files.
                item = session.get(MediaItem, job.item_id)
                assert item and item.planned_filename
                used = {path.name.casefold() for path in final.parent.iterdir()}
                destination = journal.final_path.rpartition("/")[0]
                used.update(name.casefold() for name in session.scalars(
                    select(MediaItem.planned_filename).join(MediaRequest).where(
                        MediaRequest.root_id == job.root_id, MediaRequest.destination == destination,
                        MediaItem.id != job.item_id, MediaItem.planned_filename.is_not(None))) if name)
                prefix = destination + "/"
                used.update(path[len(prefix):].split("/")[0].casefold() for path in session.scalars(
                    select(MediaRequest.destination).where(
                        MediaRequest.root_id == job.root_id, MediaRequest.destination.startswith(prefix))))
                for path in session.scalars(select(Job.relative_path).where(Job.root_id == job.root_id)):
                    parent, _, name = path.rpartition("/")
                    if parent == destination:
                        used.add(name.casefold())
                final = final.with_name(unique_name(item.planned_filename, used))
                journal.final_path = final.relative_to(storage.root.resolve()).as_posix()
                job.relative_path = journal.final_path
                # Journal path must commit before any irreversible second publish.
                return
            self._complete(session, job, journal)

    def _complete(self, session: Any, job: Job, journal: PublishJournal) -> None:
        item = session.get(MediaItem, job.item_id)
        assert item
        if item.file_unique_id:
            old = session.scalar(select(StoredFile).where(StoredFile.bot_id == item.bot_id,
                                                         StoredFile.file_unique_id == item.file_unique_id))
            if old:
                session.delete(old)
                session.flush()
        session.add(StoredFile(bot_id=item.bot_id, file_unique_id=item.file_unique_id, job_id=job.id,
                               root_id=job.root_id, relative_path=journal.final_path,
                               size_bytes=journal.size_bytes, sha256=journal.sha256))
        job.state, job.phase, job.finished_at = "succeeded", "succeeded", time.time()
        job.relative_path, job.completed_bytes = journal.final_path, journal.size_bytes or 0
        job.error_code, job.error_message = None, None
        RetainedCacheService.finish_action(session, job)
        if job.acquisition_mode == "local" and journal.api_source_path_encrypted:
            session.add(CacheCleanup(job_id=job.id, bot_id=item.bot_id, file_id=item.file_id,
                                     source_path_encrypted=journal.api_source_path_encrypted, root_id=job.root_id,
                                     relative_path=journal.final_path, size_bytes=journal.size_bytes,
                                     sha256=journal.sha256))
        session.execute(delete(FileReservation).where(FileReservation.job_id == job.id))
        session.delete(journal)
        attempt = session.scalar(select(Attempt).where(Attempt.job_id == job.id, Attempt.sequence == job.attempt_sequence))
        if attempt:
            attempt.finished_at = time.time()
        session.flush()
        request = session.get(MediaRequest, job.request_id)
        assert request
        project(session, request)

    async def run(self, client: TelegramClient, job_id: str) -> None:
        phase = "fetching"
        cloud = getattr(client, "mode", "local") == "cloud"
        copy_stop = threading.Event()
        copy_task: asyncio.Task[tuple[int, str]] | None = None
        publish_task: asyncio.Task[None] | None = None
        observer: asyncio.Task[None] | None = None
        owner: ProgressOwner | None = None
        deferral: tuple[SafetyDeferralReason, bool] | None = None
        try:
            file_id, root_id, relative, expected = await asyncio.to_thread(self.prepare, job_id)
            cached_source = await asyncio.to_thread(self.retained_cache.source, job_id) if self.retained_cache else None
            if cached_source:
                phase = "saving"
            if cloud:
                cloud_size_check(expected)
            elif not cached_source and hasattr(client, "file_progress"):
                try:
                    owner = await self.begin_progress(job_id)
                except Exception:
                    owner = None
                if owner:
                    observer = asyncio.create_task(self.observe_download(client, file_id, owner))
            source: str
            if cached_source:
                source = cached_source
            elif cloud:
                source = await self.cloud_operation(client.get_file(file_id), job_id)
            else:
                source = await self.local_acquisition(client, file_id, job_id)
            if not cloud and self.retained_cache and not cached_source:
                # Capture BEFORE cancellation and space admission: the completed source survives either.
                await owned_thread(partial(self.retained_cache.capture, job_id, source, completeness="complete",
                    generation=self.acquisition_generation(), credential=digest(client.bot.token)
                    if getattr(client, "bot", None) else ""))
            if observer:
                observer.cancel()
                await asyncio.gather(observer, return_exceptions=True)
                observer = None
            phase = "saving"
            if await asyncio.to_thread(self.cancelled, job_id):
                raise AppError("CANCELLED", "下载已取消")
            storage = self.storage.get(root_id)
            await asyncio.to_thread(self.check_space, job_id, "before_save")
            final = await asyncio.to_thread(contained, storage.root, relative, create_parent=True)
            temporary = final.with_name(f".part.{job_id}")
            temp_relative = temporary.relative_to(storage.root.resolve()).as_posix()
            if not cloud:
                # Validate before recording the exact native API identity, never a user path.
                await asyncio.to_thread(source_file, self.config.bot_api_root, source)
            await owned_thread(self.journal_copying, job_id, temp_relative, relative, None if cloud else source)
            if cloud:
                copy_task = asyncio.create_task(self.cloud_operation(
                    self.cloud_copy(client, source, temporary, expected, job_id), job_id))
            else:
                source_path = await asyncio.to_thread(source_file, self.config.bot_api_root, source)
                copy_task = asyncio.create_task(asyncio.to_thread(
                    copy_stream, source_path, temporary, expected,
                    lambda: copy_stop.is_set() or self.cancelled(job_id),
                    lambda count, speed: self.progress(job_id, count, speed)))
            size, sha = await asyncio.shield(copy_task)
            if not cloud and self.retained_cache:
                await asyncio.to_thread(self.retained_cache.source_unchanged, job_id)
            await owned_thread(self.journal_ready, job_id, size, sha)
            # Collisions are journaled and retried until an exclusive final path is obtained.
            for _ in range(10):
                publish_task = asyncio.create_task(asyncio.to_thread(self.publish, job_id))
                await asyncio.shield(publish_task)
                with self.db.sessions() as session:
                    if session.get(PublishJournal, job_id) is None:
                        break
            else:
                raise AppError("PUBLISH_COLLISION", "文件名持续冲突，请重试")
        except asyncio.CancelledError:
            # to_thread cancellation does not stop its OS thread. Join the owner before deleting
            # partial files or disposing the database; copy checks this flag at each block.
            copy_stop.set()
            if copy_task:
                if cloud:
                    copy_task.cancel()
                try:
                    await copy_task
                except (Exception, asyncio.CancelledError):
                    pass
            if publish_task:
                try:
                    await publish_task
                except Exception:
                    pass
            # Shutdown cannot establish the remote API has stopped; recover conservatively at next boot.
            if phase == "fetching" and not cloud:
                await asyncio.to_thread(self.failed, job_id, "ACQUISITION_UNCERTAIN", "获取状态未知，请重启本地 Bot API 后恢复", False, True)
            else:
                await asyncio.to_thread(self.failed, job_id, "INTERRUPTED", "本地保存已中断，等待重试", True)
            raise
        except Exception as exc:
            if isinstance(exc, (SafetyDeferred, TelegramRetryAfter, TelegramUnauthorizedError)):
                delay = (exc.retry_at or time.time() + 5) - time.time() if isinstance(exc, SafetyDeferred) else float(exc.retry_after) if isinstance(exc, TelegramRetryAfter) else 5
                pacing = isinstance(exc, SafetyDeferred) and exc.is_pacing
                reason: SafetyDeferralReason = exc.reason if isinstance(exc, SafetyDeferred) else "cooldown" if isinstance(exc, TelegramRetryAfter) else "auth_paused"
                deferral = (reason, exc.sent if isinstance(exc, SafetyDeferred) else True)
                message = {"pacing": "等待请求间隔，将自动继续", "cooldown": "Telegram 要求等待，任务将在等待结束后继续",
                           "backoff": "连接异常，任务将在退避等待后重试", "auth_paused": "Bot 凭据被拒绝，请检查 Token 后重试 Bot 连接",
                           "protection_unavailable": "请求保护状态不可用，任务已暂停，请检查系统诊断"}[reason]
                await asyncio.to_thread(self.failed, job_id, "BOT_REQUEST_WAITING" if pacing else "BOT_REQUEST_PAUSED", message, True, False, max(1, delay))
                return
            uncertain = not cloud and phase == "fetching" and (
                isinstance(exc, AppError) and exc.code == "ACQUISITION_UNCERTAIN"
                or not isinstance(exc, (AppError, TelegramRetryAfter, TelegramBadRequest, TelegramUnauthorizedError)))
            code, message, retry, delay = self.classify(exc)
            if code == "NO_SPACE":
                await asyncio.to_thread(self.record_space_failure, job_id,
                    "saving" if phase == "saving" else "downloading", getattr(exc, "details", None))
                message += "；完整缓存已保留，可在详情继续保存或清理" if phase == "saving" and not cloud else ""
            if await asyncio.to_thread(self.manual_recovery_attempt, job_id):
                retry = False
            await asyncio.to_thread(self.failed, job_id, code, message, retry, uncertain, delay)
        finally:
            if observer:
                observer.cancel()
                await asyncio.gather(observer, return_exceptions=True)
            if owner and self.download_progress:
                self.download_progress.clear(owner)
            self.progress_times.pop(job_id, None)
            await owned_thread(self.acquisitions.prune)
            if self.diagnostics:
                try:
                    await asyncio.to_thread(self.record_result, job_id, deferral)
                except Exception:
                    self.diagnostics.error = True

    def manual_recovery_attempt(self, job_id: str) -> bool:
        with self.db.sessions() as session:
            job = session.get(Job, job_id)
            return bool(job and job.execution_mode in {"save", "download_resume", "redownload"})

    def check_space(self, job_id: str, phase: str) -> None:
        with self.db.transaction() as session:
            job = session.get(Job, job_id)
            item = session.get(MediaItem, job.item_id) if job else None
            assert job and item
            assessment = assess_space(session, self.storage, self.config, job.root_id,
                                      item.size_bytes, job.acquisition_mode, phase, job.id)
            assessment.require()

    def record_space_failure(self, job_id: str, phase: str, details: Any = None) -> None:
        with self.db.transaction() as session:
            job = session.get(Job, job_id)
            if job:
                job.storage_failure = details if isinstance(details, dict) else {"phase": phase, "sufficient": False, "volumes": []}

    @staticmethod
    def observed_error(details: dict[str, Any]) -> AppError:
        code = next((volume["error_code"] for volume in details.get("volumes", []) if volume.get("error_code")), "NO_SPACE")
        return AppError(code, storage_warning(details) or "存储空间检查失败，请检查挂载和可用空间后手动重试", details=details)

    def download_space_exhausted(self, job_id: str, phase: str = "downloading") -> dict[str, Any] | None:
        # A budget is admission, not a filesystem reservation. Check actual free while active.
        with self.db.sessions() as session:
            job = session.get(Job, job_id)
            if not job:
                return None
            assessment = assess_space(session, self.storage, self.config, job.root_id, 0,
                                      job.acquisition_mode, phase, job.id)
        value = assessment.observed().to_dict()
        if value["sufficient"]:
            return None
        return value

    def record_result(self, job_id: str, deferral: tuple[SafetyDeferralReason, bool] | None = None) -> None:
        if not self.diagnostics:
            return
        with self.db.sessions() as session:
            job = session.get(Job, job_id)
            if not job:
                return
            fields: dict[str, Any] = {"task_id": job.request_id, "job_id": job.id, "mode": job.acquisition_mode,
                      "outcome": job.state, "error_code": job.error_code}
            if deferral and job.state == "retry_wait":
                fields.update(reason=deferral[0], stage="response" if deferral[1] else "before_send",
                              retry_at=datetime.fromtimestamp(job.next_run_at, UTC).isoformat())
            pending_cleanup = session.get(CacheCleanup, job_id) is not None
        pacing = fields["error_code"] == "BOT_REQUEST_WAITING"
        if pacing:
            fields.update(reason="pacing", stage="before_send")
        self.diagnostics.emit("task_result", level="info" if pacing or fields["outcome"] == "succeeded" else "warning", **fields)
        if pending_cleanup:
            self.diagnostics.emit("cache_cleanup", level="info", mode="local", job_id=job_id, outcome="queued")

    async def cloud_operation[T](self, operation: Any, job_id: str) -> T:
        task = asyncio.create_task(operation)
        try:
            while not task.done():
                await asyncio.wait({task}, timeout=0.2)
                if await asyncio.to_thread(self.cancelled, job_id):
                    raise AppError("CANCELLED", "下载已取消")
            return await task
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def local_acquisition(self, client: TelegramClient, file_id: str, job_id: str) -> str:
        """Only a settled, attempt-bound native control result can stop the HTTP owner early."""
        if await asyncio.to_thread(self.cancelled, job_id):
            raise AppError("CANCELLED", "下载已取消")
        generation = await asyncio.to_thread(self.acquisition_generation)
        if not hasattr(client, "file_acquisition") or (not generation and not self.config.managed_local_api):
            # Legacy/external APIs cannot prove cancellation. Retain old ownership semantics.
            with self.db.sessions() as session:
                current = session.get(Job, job_id)
                if current and current.execution_mode == "download_resume":
                    raise AppError("RESUME_UNAVAILABLE", "当前 API 无法验证原进度，未开始下载；可恢复连接或确认从头重新下载")
            return await client.get_file(file_id)
        if not generation:
            raise AppError("ACQUISITION_CONTROL_UNAVAILABLE", "获取控制暂不可用，请检查本地 API")
        resume_identity = None
        if self.retained_cache:
            with self.db.sessions() as session:
                current = session.get(Job, job_id)
                cache = session.get(RetainedCache, current.cache_id) if current and current.cache_id else None
                if current and current.execution_mode == "download_resume":
                    if not cache or cache.generation != generation or not cache.native_identity:
                        raise AppError("RESUME_UNAVAILABLE", "Bot API 已重启或缓存记录失效；可清理旧缓存或从头重新下载")
                    try:
                        await asyncio.to_thread(self.retained_cache.validate, cache)
                        async with asyncio.timeout(3):
                            native = await client.inspect_file_cache(file_id, generation)
                    except (AppError, AttributeError, TimeoutError, OSError) as exc:
                        raise AppError("RESUME_UNAVAILABLE", "暂时无法确认是否可继续下载，请恢复连接后重试") from exc
                    if not native.get("settled") or not native.get("resumable") or native.get("identity") != cache.native_identity:
                        raise AppError("RESUME_UNAVAILABLE", "缓存分片已失效，未开始下载；可清理缓存或从头重新下载")
                    resume_identity = cache.native_identity
        try:
            prepared = (await client.file_acquisition(generation, file_id=file_id, expected_cache_identity=resume_identity)
                        if resume_identity else await client.file_acquisition(generation, file_id=file_id))
        except AppError as exc:
            if exc.code == "ACQUISITION_CONTROL_UNSUPPORTED" and not self.config.managed_local_api and not resume_identity:
                return await client.get_file(file_id)
            raise
        with self.db.sessions() as session:
            job = session.get(Job, job_id)
            item = session.get(MediaItem, job.item_id) if job else None
            assert job and item and job.boot_id
            owner = AcquisitionOwner(job.id, job.attempt_sequence, job.boot_id, item.bot_id,
                                     digest(client.bot.token), self.secrets.encrypt(client.bot.token),
                                     generation, prepared["acquisition_id"])
        if not await owned_thread(self.acquisitions.persist, owner):
            # No getFile was issued; this idle native registration can only expire.
            raise AppError("CANCELLED", "下载已取消")
        task: asyncio.Task[str] | None = None
        try:
            if not await asyncio.to_thread(self.cancelled, job_id):
                task = asyncio.create_task(client.get_file(file_id, generation=generation,
                                                          acquisition_id=owner.acquisition_id))
            last_control = 0.0
            last_inspect = 0.0
            space_failure: dict[str, Any] | None = None
            while True:
                cancelled = await asyncio.to_thread(self.cancelled, job_id)
                if time.monotonic() - last_inspect >= 1:
                    last_inspect = time.monotonic()
                    if self.retained_cache:
                        await self.retained_cache.capture_native(client, job_id, generation, owner.acquisition_id,
                                                                attempt=owner.attempt)
                    space_failure = space_failure or await asyncio.to_thread(self.download_space_exhausted, job_id)
                    if space_failure:
                        await asyncio.to_thread(self.record_space_failure, job_id, "downloading", space_failure)
                if task and task.done() and not cancelled and not space_failure:
                    try:
                        result = task.result()
                    except Exception as exc:
                        if isinstance(exc, SafetyDeferred) and not exc.sent:
                            # Joined admission never sent getFile; no deferred callback can start it.
                            try:
                                await client.file_acquisition(generation, acquisition_id=owner.acquisition_id,
                                                              cancel=True)
                            except AppError:
                                pass  # An abandoned idle preparation can never start itself.
                            raise
                        rejected = (isinstance(exc, (TelegramBadRequest, TelegramRetryAfter,
                                                     TelegramUnauthorizedError, SafetyDeferred))
                                    or isinstance(exc, AppError) and exc.code in {
                                        "CACHE_CLEANUP_BUSY", "ACQUISITION_REJECTED"})
                        try:
                            state = (await self.stop_acquisition(client, owner)
                                     if rejected
                                     else await client.file_acquisition(generation, acquisition_id=owner.acquisition_id))
                        except AppError:
                            state = {}
                        if not state.get("settled"):
                            if state and isinstance(exc, (AppError, TelegramBadRequest, TelegramRetryAfter,
                                                          TelegramUnauthorizedError, SafetyDeferred)):
                                # A definite native response may precede its stop barrier.
                                await asyncio.sleep(0.5)
                                continue
                            raise AppError("ACQUISITION_UNCERTAIN", "获取状态待确认，请取消任务或重启本地 API") from exc
                        if state["state"] == "cancelled" and not rejected:
                            raise AppError("CANCELLED", "下载已取消") from exc
                        raise
                    try:
                        state = await client.file_acquisition(generation, acquisition_id=owner.acquisition_id)
                    except AppError:
                        state = {}
                    if state.get("settled"):
                        if state["state"] != "completed":
                            raise AppError("CANCELLED" if state["state"] == "cancelled" else "TELEGRAM_REJECTED",
                                           "下载已取消" if state["state"] == "cancelled" else "本地 API 文件获取失败")
                        return result
                    # getFile may return before its native downloader actor teardown.
                    # Keep the slot and permit cancellation while that barrier settles.
                    await asyncio.sleep(0.5)
                    continue
                if (cancelled or space_failure) and time.monotonic() - last_control >= 0.5:
                    last_control = time.monotonic()
                    try:
                        state = await self.stop_acquisition(client, owner)
                        if state["settled"]:
                            if self.retained_cache:
                                await self.retained_cache.capture_native(client, job_id, generation, owner.acquisition_id,
                                                                        attempt=owner.attempt)
                                if task and task.done() and not task.cancelled():
                                    try:
                                        source = task.result()
                                        await owned_thread(partial(self.retained_cache.capture, job_id, source,
                                            completeness="complete", generation=generation,
                                            acquisition_id=owner.acquisition_id, credential=owner.credential,
                                            attempt=owner.attempt))
                                    except Exception:
                                        pass
                            if task and not task.done():
                                task.cancel()
                            if task:
                                await asyncio.gather(task, return_exceptions=True)
                            if space_failure and not cancelled:
                                raise self.observed_error(space_failure)
                            raise AppError("CANCELLED", "下载已取消")
                    except AppError as exc:
                        if exc.code in {"CANCELLED", "NO_SPACE", "STORAGE_CHANGED", "STORAGE_UNAVAILABLE", "ROOT_UNAVAILABLE"}:
                            raise
                        # Hand the durable owner to reconciliation. Keeping this HTTP
                        # coroutine alive would prevent an explicit child restart from
                        # draining, even when both private control routes are unavailable.
                        raise AppError("ACQUISITION_UNCERTAIN", "取消结果待确认，请恢复连接或重启本地 API") from exc
                if task and not task.done():
                    await asyncio.wait({task}, timeout=0.2)
                else:
                    await asyncio.sleep(0.2)
        finally:
            if task and not task.done():
                task.cancel()
            if task:
                await asyncio.gather(task, return_exceptions=True)
            if self.retained_cache:
                await self.retained_cache.capture_native(client, job_id, generation, owner.acquisition_id,
                                                        attempt=owner.attempt)

    async def stop_acquisition(self, client: TelegramClient, owner: AcquisitionOwner) -> dict[str, Any]:
        """A lost stop reply cannot prevent an independent authoritative status check."""
        try:
            state = await client.file_acquisition(owner.generation, acquisition_id=owner.acquisition_id,
                                                  cancel=True)
            if state["settled"]:
                return state
        except AppError:
            pass
        return await client.file_acquisition(owner.generation, acquisition_id=owner.acquisition_id)

    async def cloud_copy(self, client: TelegramClient, source: str, temporary: Path,
                         expected: int | None, job_id: str) -> tuple[int, str]:
        # Chunk owners are joined before closing the descriptor or deleting the journal's part file.
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        # Open synchronously so cancellation cannot lose ownership of a newly returned descriptor.
        fd = os.open(temporary, flags, 0o640)
        stream = os.fdopen(fd, "wb")
        sha, count, started = hashlib.sha256(), 0, time.monotonic()
        try:
            async with aclosing(client.file_chunks(source)) as chunks:
                async for block in chunks:
                    count += len(block)
                    cloud_size_check(count)
                    if await asyncio.to_thread(self.cancelled, job_id):
                        raise AppError("CANCELLED", "下载已取消")
                    await owned_thread(stream.write, block)
                    sha.update(block)
                    await asyncio.to_thread(self.progress, job_id, count, count / max(time.monotonic() - started, 0.001))
            await owned_thread(stream.flush)
            await owned_thread(os.fsync, stream.fileno())
        finally:
            await owned_thread(stream.close)
        if expected is not None and count != expected:
            raise AppError("SIZE_MISMATCH", "文件大小校验失败")
        return count, sha.hexdigest()

    @staticmethod
    def classify(exc: Exception) -> tuple[str, str, bool, float]:
        if isinstance(exc, AppError):
            return exc.code, exc.message, exc.code in {"SIZE_MISMATCH", "CLOUD_DOWNLOAD_FAILED"}, 0
        if isinstance(exc, TelegramRetryAfter):
            return "RATE_LIMITED", "Telegram 请求限流", True, float(exc.retry_after)
        if isinstance(exc, TelegramUnauthorizedError):
            return "BOT_UNAUTHORIZED", "Bot 凭据无效", False, 0
        if isinstance(exc, TelegramBadRequest):
            return "TELEGRAM_REJECTED", "Telegram 拒绝文件请求，请确认文件有效", False, 0
        if isinstance(exc, (ClientError, TimeoutError)):
            return "NETWORK_ERROR", "Telegram 连接异常，请检查网络或代理", True, 0
        if isinstance(exc, OSError):
            if exc.errno in {errno.ENOSPC, getattr(errno, "EDQUOT", -1)}:
                return "NO_SPACE", "存储空间不足", False, 0
            if exc.errno in {errno.EACCES, errno.EPERM}:
                return "PERMISSION_DENIED", "存储目录权限不足", False, 0
            return "STORAGE_ERROR", "文件保存失败，请检查存储和原子发布能力", False, 0
        return "NETWORK_ERROR", "Telegram 连接异常，请检查网络或代理", True, 0

    def failed(self, job_id: str, code: str, message: str, retry: bool, uncertain: bool = False,
               delay: float = 0, owner: AcquisitionOwner | None = None) -> None:
        publication_proof = None
        if code == "NO_SPACE":
            # A potentially large final hash must never hold a write transaction.
            with self.db.sessions() as session:
                saved_job, saved_journal = session.get(Job, job_id), session.get(PublishJournal, job_id)
                snapshot = ((saved_job.attempt_sequence, saved_job.root_id, saved_journal.final_path,
                             saved_journal.size_bytes, saved_journal.sha256)
                            if saved_job and saved_journal and saved_journal.stage == "ready" else None)
            if snapshot:
                try:
                    storage = self.storage.get(snapshot[1])
                    storage.check(check_space=False)
                    final = contained(storage.root, snapshot[2])
                    before = cache_fingerprint(final)
                    if regular_file_hash(final) == (snapshot[3], snapshot[4]) and cache_fingerprint(final) == before:
                        publication_proof = snapshot, before
                except (OSError, AppError):
                    pass
        with self.db.transaction() as session:
            job = session.get(Job, job_id)
            if not job or job.state in {"succeeded", "already_exists"}:
                return
            if owner and (job.id != owner.job_id or job.attempt_sequence != owner.attempt
                          or job.boot_id != owner.boot_id or job.phase != "fetching"
                          or job.state not in {"fetching", "cancelling", "recovery_blocked"}):
                return
            request = session.get(MediaRequest, job.request_id)
            if request and request.deleted_at is not None:
                return
            journal = session.get(PublishJournal, job_id)
            if journal and journal.stage == "ready" and code == "NO_SPACE":
                # Space can disappear after copy/fsync but before the link. Check
                # the journal now, so a pre-publication failure releases its slot.
                try:
                    storage = self.storage.get(job.root_id)
                    storage.check(check_space=False)
                    final = contained(storage.root, journal.final_path)
                    if not final.exists():
                        journal.stage = "copying"
                    elif (publication_proof and publication_proof[0] ==
                          (job.attempt_sequence, job.root_id, journal.final_path, journal.size_bytes, journal.sha256)
                          and cache_fingerprint(final) == publication_proof[1]):
                        temporary = contained(storage.root, journal.temporary_path)
                        try:
                            temporary.unlink(missing_ok=True)
                        except OSError:
                            pass  # The verified final publication already won.
                        self._complete(session, job, journal)
                        return
                except (OSError, AppError):
                    pass  # Publication may have happened: retain recovery ownership.
            if journal and journal.stage == "ready" and code != "CANCELLED":
                # An irreversible publication might have happened; reconciliation owns it.
                job.state, job.phase = "recovery_blocked", "recovery"
                job.error_code, job.error_message = "PUBLISH_UNCERTAIN", "发布结果需要启动恢复核对"
                session.flush()
                request = session.get(MediaRequest, job.request_id)
                assert request
                project(session, request)
                return
            if journal:
                try:
                    storage = self.storage.get(job.root_id)
                    storage.check(check_space=False)
                    contained(storage.root, journal.temporary_path).unlink(missing_ok=True)
                except (OSError, AppError):
                    journal.stage = "cleanup_failed"
                    message += "；目标临时文件清理失败，已保留残留记录，请检查目标目录权限"
                    residual = Path(journal.temporary_path)
                    if not residual.anchor and ".." not in residual.parts:
                        message += f"；残留路径（相对下载目录）：{residual.as_posix()}"
                else:
                    session.delete(journal)
            job.error_code, job.error_message = code, message
            cache = session.get(RetainedCache, job.cache_id) if job.cache_id else None
            if job.cancel_requested:
                job.error_message += "；安全停止后将清理关联缓存及已保存文件，请查看任务的取消清理结果。"
            elif cache and cache.state == "retained":
                job.error_message += ("；完整缓存已保留，可在详情继续保存或清理" if cache.completeness == "complete"
                                      else "；部分缓存已保留，可在详情检查继续下载或手动清理")
            attempt = session.scalar(select(Attempt).where(Attempt.job_id == job.id, Attempt.sequence == job.attempt_sequence))
            if attempt:
                attempt.finished_at, attempt.error_code = time.time(), code
            if uncertain:
                job.state, job.phase = "recovery_blocked", "fetching"
                job.error_message = uncertain_message(session, job)
            elif job.cancel_requested or code == "CANCELLED":
                job.state, job.phase, job.finished_at = "cancelled", "cancelled", time.time()
            elif (code in {"BOT_REQUEST_PAUSED", "BOT_REQUEST_WAITING"} or job.execution_mode == "download" and code in {"CACHE_CLEANUP_BUSY",
                          "ACQUISITION_CONTROL_UNAVAILABLE", "ACQUISITION_CONTROL_BUSY"}):
                job.state, job.phase = "retry_wait", "retry_wait"
                job.next_run_at = time.time() + max(2, delay)
            elif retry and job.auto_retries < job.max_auto_retries:
                job.auto_retries += 1
                job.state, job.phase = "retry_wait", "retry_wait"
                job.next_run_at = time.time() + max(delay, min(300, 2 ** job.auto_retries) + random.random())
            else:
                job.state, job.phase, job.finished_at = "failed", "failed", time.time()
            if job.state in {"cancelled", "failed"}:
                session.execute(delete(FileReservation).where(FileReservation.job_id == job.id))
            RetainedCacheService.finish_action(session, job)
            session.flush()
            request = session.get(MediaRequest, job.request_id)
            assert request
            project(session, request)

    def recover(self, api_restarted: bool) -> None:
        with self.db.sessions() as session:
            ids = list(session.scalars(select(Job.id).where(Job.state.in_(
                ["fetching", "saving", "cancelling", "recovery_blocked"]))))
        for job_id in ids:
            with self.db.sessions() as session:
                job, journal = session.get(Job, job_id), session.get(PublishJournal, job_id)
                assert job
                root_id = job.root_id
                cloud_attempt = job.acquisition_mode == "cloud"
                facts = (journal.temporary_path, journal.final_path, journal.size_bytes, journal.sha256, journal.stage) if journal else None
            try:
                storage = self.storage.get(root_id)
                storage.check(check_space=False)
                if facts and facts[4] == "ready":
                    temporary, final = contained(storage.root, facts[0]), contained(storage.root, facts[1])
                    if final.exists() and file_hash(final) == (facts[2], facts[3]):
                        with self.db.transaction() as session:
                            job, journal = session.get(Job, job_id), session.get(PublishJournal, job_id)
                            assert job and journal
                            self._complete(session, job, journal)
                        temporary.unlink(missing_ok=True)
                        continue
                    if temporary.exists() and file_hash(temporary) == (facts[2], facts[3]):
                        if self.cancelled(job_id):
                            temporary.unlink(missing_ok=True)
                            with self.db.transaction() as session:
                                row = session.get(PublishJournal, job_id)
                                if row:
                                    session.delete(row)
                            self.failed(job_id, "CANCELLED", "下载已取消", False)
                            continue
                        for _ in range(10):
                            self.publish(job_id)
                            with self.db.sessions() as session:
                                if session.get(PublishJournal, job_id) is None:
                                    break
                        continue
                if facts:
                    contained(storage.root, facts[0]).unlink(missing_ok=True)
                    with self.db.transaction() as session:
                        row = session.get(PublishJournal, job_id)
                        if row:
                            session.delete(row)
                with self.db.transaction() as session:
                    job = session.get(Job, job_id)
                    assert job
                    if job.phase == "fetching" and not api_restarted and not cloud_attempt:
                        job.state = "recovery_blocked"
                        job.error_code, job.error_message = "ACQUISITION_UNCERTAIN", uncertain_message(session, job)
                    else:
                        if job.storage_failure and job.phase == "fetching":
                            job.state = "cancelled" if job.cancel_requested else "failed"
                            job.error_code = "NO_SPACE"
                            job.error_message = storage_warning(job.storage_failure) + "；旧获取已停止，缓存保留，请手动操作"
                            job.finished_at = time.time()
                        elif job.execution_mode == "download_resume" and job.phase == "fetching":
                            job.state = "cancelled" if job.cancel_requested else "failed"
                            job.error_code = "RESUME_UNAVAILABLE"
                            job.error_message = "Bot API 已重启，原下载进度无法确认；可清理缓存或从头重新下载"
                            job.finished_at = time.time()
                        else:
                            job.state = "cancelled" if job.cancel_requested else "queued"
                        job.phase, job.next_run_at = job.state, time.time()
                    RetainedCacheService.finish_action(session, job)
                    session.flush()
                    request = session.get(MediaRequest, job.request_id)
                    assert request
                    project(session, request)
            except (OSError, AppError):
                self.failed(job_id, "RECOVERY_FAILED", "恢复失败，请检查原存储挂载和文件系统", False, True)
        with self.db.transaction() as session:
            for reservation in session.scalars(select(FileReservation)):
                job = session.get(Job, reservation.job_id)
                if not job or job.state in {"cancelled", "failed", "succeeded", "already_exists"}:
                    session.delete(reservation)
