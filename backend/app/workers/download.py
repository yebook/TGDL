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
from pathlib import Path
from typing import Any

from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter, TelegramUnauthorizedError
from aiohttp import ClientError
from sqlalchemy import delete, select

from app.bot.client import TelegramClient, cloud_size_check
from app.core.config import Config
from app.core.errors import AppError
from app.db.models import (
    Attempt,
    FileReservation,
    Job,
    LocalApiRuntime,
    MediaItem,
    MediaRequest,
    PublishJournal,
    Settings,
    StoredFile,
)
from app.db.session import Database
from app.services.tasks import project
from app.storage.files import (
    StorageManager,
    contained,
    copy_stream,
    file_hash,
    publish_no_replace,
    source_file,
)


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
    def __init__(self, db: Database, storage: StorageManager, config: Config, boot_id: str) -> None:
        self.db, self.storage, self.config, self.boot_id = db, storage, config, boot_id
        self.progress_times: dict[str, float] = {}

    def claim(self, bot_id: int | None = None, mode: str | None = None) -> str | None:
        with self.db.transaction() as session:
            if self.config.managed_local_api:
                local = session.get(LocalApiRuntime, 1)
                if not local or local.values.get("phase") != "idle":
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
            jobs = list(session.scalars(query.order_by(Job.next_run_at, Job.id).limit(50)))
            active_requests = list(session.scalars(select(Job.request_id).where(
                Job.state.in_(["fetching", "saving", "cancelling", "recovery_blocked"]))))
            # One busy media group must not monopolize newly available global slots.
            jobs.sort(key=lambda job: (active_requests.count(job.request_id), job.next_run_at, job.id))
            for job in jobs:
                item = session.get(MediaItem, job.item_id)
                assert item
                if job.cancel_requested:
                    job.state, job.finished_at = "cancelled", time.time()
                    request = session.get(MediaRequest, job.request_id)
                    assert request
                    project(session, request)
                    continue
                try:
                    storage = self.storage.get(job.root_id)
                except AppError as exc:
                    job.state, job.phase, job.finished_at = "failed", "failed", time.time()
                    job.error_code, job.error_message = exc.code, exc.message
                    session.execute(delete(FileReservation).where(FileReservation.job_id == job.id))
                    session.flush()
                    request = session.get(MediaRequest, job.request_id)
                    assert request
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
                        job.state, job.phase = "already_exists", "already_exists"
                        job.linked_job_id, job.relative_path, job.root_id = stored.job_id, stored.relative_path, stored.root_id
                        job.completed_bytes, job.finished_at = stored.size_bytes, time.time()
                        session.flush()
                        request = session.get(MediaRequest, job.request_id)
                        assert request
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
                    # Budget target+cache copies and in-flight known sizes conservatively (also across mounts).
                    outstanding = session.scalars(select(MediaItem.size_bytes).join(Job, Job.item_id == MediaItem.id).where(
                        Job.state.in_(["fetching", "saving", "cancelling", "recovery_blocked"])))
                    budget = sum(value or self.config.minimum_free_bytes for value in outstanding)
                    copies = 2 if current_mode == "local" else 1
                    storage.check(copies * ((item.size_bytes or self.config.minimum_free_bytes) + budget))
                    if current_mode == "local" and self.config.bot_api_root.exists():
                        import shutil
                        if shutil.disk_usage(self.config.bot_api_root).free < self.config.minimum_free_bytes + (item.size_bytes or 0) + budget:
                            raise AppError("NO_SPACE", "Bot API 缓存空间不足")
                except (OSError, AppError) as exc:
                    job.error_code = exc.code if isinstance(exc, AppError) else "STORAGE_UNAVAILABLE"
                    job.error_message = exc.message if isinstance(exc, AppError) else "存储不可用，请检查挂载和权限"
                    job.next_run_at = time.time() + 10
                    continue
                job.state, job.phase, job.boot_id = "fetching", "fetching", self.boot_id
                job.acquisition_mode = current_mode
                job.started_at, job.completed_bytes, job.finished_at = time.time(), 0, None
                job.attempt_sequence += 1
                job.linked_job_id, job.error_code, job.error_message = None, None, None
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

    def journal_copying(self, job_id: str, temporary: str, final: str) -> None:
        with self.db.transaction() as session:
            job = session.get(Job, job_id)
            assert job
            job.phase = "saving"
            if not job.cancel_requested:
                job.state = "saving"
            row = session.get(PublishJournal, job_id)
            if row:
                session.delete(row)
                session.flush()
            session.add(PublishJournal(job_id=job_id, temporary_path=temporary, final_path=final, stage="copying"))
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
                suffix = f"_{job.id}_{job.attempt_sequence}"
                final = final.with_name(final.stem + suffix + final.suffix)
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
        try:
            file_id, root_id, relative, expected = await asyncio.to_thread(self.prepare, job_id)
            if cloud:
                cloud_size_check(expected)
            # Never wrap in wait_for/cancel on user cancellation: request holds slot until it actually returns.
            source = await self.cloud_operation(client.get_file(file_id), job_id) if cloud else await client.get_file(file_id)
            phase = "saving"
            if await asyncio.to_thread(self.cancelled, job_id):
                raise AppError("CANCELLED", "下载已取消")
            storage = self.storage.get(root_id)
            await asyncio.to_thread(storage.check, expected or 0)
            final = await asyncio.to_thread(contained, storage.root, relative, create_parent=True)
            temporary = final.with_name(f".part.{job_id}")
            temp_relative = temporary.relative_to(storage.root.resolve()).as_posix()
            await owned_thread(self.journal_copying, job_id, temp_relative, relative)
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
            uncertain = not cloud and phase == "fetching" and not isinstance(exc, (AppError, TelegramRetryAfter, TelegramBadRequest, TelegramUnauthorizedError))
            code, message, retry, delay = self.classify(exc)
            await asyncio.to_thread(self.failed, job_id, code, message, retry, uncertain, delay)
        finally:
            self.progress_times.pop(job_id, None)

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
            if exc.errno == errno.ENOSPC:
                return "NO_SPACE", "存储空间不足", False, 0
            if exc.errno in {errno.EACCES, errno.EPERM}:
                return "PERMISSION_DENIED", "存储目录权限不足", False, 0
            return "STORAGE_ERROR", "文件保存失败，请检查存储和原子发布能力", False, 0
        return "NETWORK_ERROR", "Telegram 连接异常，请检查网络或代理", True, 0

    def failed(self, job_id: str, code: str, message: str, retry: bool, uncertain: bool = False, delay: float = 0) -> None:
        with self.db.transaction() as session:
            job = session.get(Job, job_id)
            if not job or job.state in {"succeeded", "already_exists"}:
                return
            request = session.get(MediaRequest, job.request_id)
            if request and request.deleted_at is not None:
                return
            journal = session.get(PublishJournal, job_id)
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
                    contained(self.storage.get(job.root_id).root, journal.temporary_path).unlink(missing_ok=True)
                except (OSError, AppError):
                    pass
                session.delete(journal)
            job.error_code, job.error_message = code, message
            attempt = session.scalar(select(Attempt).where(Attempt.job_id == job.id, Attempt.sequence == job.attempt_sequence))
            if attempt:
                attempt.finished_at, attempt.error_code = time.time(), code
            if uncertain:
                job.state, job.phase = "recovery_blocked", "fetching"
                job.error_message = "本地 Bot API 可能仍在获取文件；请重启该服务以安全恢复并发槽"
            elif job.cancel_requested or code == "CANCELLED":
                job.state, job.phase, job.finished_at = "cancelled", "cancelled", time.time()
            elif retry and job.auto_retries < job.max_auto_retries:
                job.auto_retries += 1
                job.state, job.phase = "retry_wait", "retry_wait"
                job.next_run_at = time.time() + max(delay, min(300, 2 ** job.auto_retries) + random.random())
            else:
                job.state, job.phase, job.finished_at = "failed", "failed", time.time()
            if job.state in {"cancelled", "failed"}:
                session.execute(delete(FileReservation).where(FileReservation.job_id == job.id))
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
                storage.check()
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
                        job.error_code, job.error_message = "ACQUISITION_UNCERTAIN", "请重启本地 Bot API 以安全恢复获取槽"
                    else:
                        job.state = "cancelled" if job.cancel_requested else "queued"
                        job.phase, job.next_run_at = job.state, time.time()
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
