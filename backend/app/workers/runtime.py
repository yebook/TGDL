import asyncio
import json
import time
from typing import Any

from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramRetryAfter,
    TelegramUnauthorizedError,
)
from sqlalchemy import func, select

from app.bot.client import TelegramClient
from app.bot.ingest import IngestService
from app.core.config import Config
from app.core.errors import AppError
from app.core.security import digest
from app.db.models import BotIdentity, Job, MediaRequest, Notification, uid
from app.db.session import Database
from app.schemas.contracts import UserSettings
from app.services import connection_waits as waits
from app.services.cache_cleanup import CacheCleanupService
from app.services.diagnostics import DiagnosticLog
from app.services.download_progress import DownloadProgress
from app.services.local_api import LocalApiService
from app.services.request_safety import RequestSafetyService, SafetyDeferred
from app.services.settings import SettingsService
from app.services.tasks import ACTIVE, TaskService
from app.storage.files import StorageManager
from app.workers.download import DownloadWorker, owned_thread


class Runtime:
    def __init__(self, db: Database, config: Config, settings: SettingsService,
                 tasks: TaskService, storage: StorageManager) -> None:
        self.db, self.config, self.settings, self.tasks, self.storage = db, config, settings, tasks, storage
        log_settings = settings.read()[1]
        self.diagnostics = DiagnosticLog(config.data_dir, retention_days=log_settings.diagnostic_log_retention_days,
                                         cleanup_time=log_settings.diagnostic_log_cleanup_time,
                                         timezone=log_settings.timezone)
        self.safety = RequestSafetyService(db)
        self.safety.diagnostics = self.diagnostics
        tasks.diagnostics = self.diagnostics
        self.ingest = IngestService(db, settings, tasks)
        self.worker = DownloadWorker(db, storage, config, uid(), secrets=settings.secrets)
        self.worker.diagnostics = self.diagnostics
        self.client: TelegramClient | None = None
        self.token_hash = ""
        self.client_signature = ""
        self.client_generation = ""
        self.verified_client: TelegramClient | None = None
        self.verified_username = ""
        self.bot_status_signature = ""
        self.bot_status_generation = ""
        self.bot_id: int | None = None
        self.bot_status: dict[str, Any] = {"state": "unconfigured", "username": "", "message": "请配置 Bot Token 和用户白名单"}
        self.running: dict[str, asyncio.Task[None]] = {}
        self.loops: list[asyncio.Task[None]] = []
        self.stopping = False
        self.api_generation = ""
        self.poll_lock = asyncio.Lock()
        self.notify_lock = asyncio.Lock()
        self.local_api = LocalApiService(settings)
        self.local_api.diagnostics = self.diagnostics
        self.download_progress = DownloadProgress(generation=self.progress_generation)
        self.worker.download_progress = self.download_progress
        self.tasks.download_progress = self.download_progress
        self.connection_lock = asyncio.Lock()
        self.cache_cleanup = CacheCleanupService(db, storage, self.diagnostics, settings.secrets)

    def progress_generation(self) -> str:
        applied = self.local_api.applied() if self.config.managed_local_api else {}
        state = self.local_api.read() if applied else {}
        _, configured, _ = self.settings.read()
        generation = applied.get("generation")
        return generation if (applied.get("state") == "running" and isinstance(generation, str)
                              and self.client is not None and configured.bot_api_mode == "local"
                              and self.client_signature == self.settings.client_signature(configured)
                              and generation == self.client_generation and state.get("applied_mode") == "local"
                              and state.get("phase") == "idle" and not state.get("command_id")
                              # applied_generation records the last managed operation, not every
                              # same-command child restart. ensure_client verifies the live generation.
                              and applied.get("proxy_signature") == self.settings.proxy_signature(configured)
                              and state.get("applied_command_id")
                              and state.get("applied_command_id") == applied.get("command_id")
                              and state.get("applied_credential_revision") == applied.get("credential_revision")) else ""

    async def start(self) -> None:
        await asyncio.to_thread(self.maintain_diagnostics)
        if self.config.managed_local_api:
            # Persist the initial desired/applied barrier before any consumer starts.
            await self.local_api.reconcile(self)
        await asyncio.to_thread(self.recover_generation, True)
        self.loops = [asyncio.create_task(self.poll_loop()), asyncio.create_task(self.tick_loop()),
                      asyncio.create_task(self.notify_loop()), asyncio.create_task(self.diagnostic_loop())]

    def maintain_diagnostics(self) -> None:
        _, configured, _ = self.settings.read()
        self.diagnostics.maintain(configured.diagnostic_log_retention_days,
                                  cleanup_time=configured.diagnostic_log_cleanup_time,
                                  timezone=configured.timezone)

    async def diagnostic_loop(self) -> None:
        # Separate from Bot scheduling: migration, outages and missing tokens cannot skip cleanup.
        while not self.stopping:
            maintenance = asyncio.create_task(asyncio.to_thread(self.maintain_diagnostics))
            try:
                await asyncio.shield(maintenance)
            except asyncio.CancelledError:
                # Cancellation cannot stop filesystem work already running in a thread.
                # Join it before shutdown emits/flushing logs or releases instance.lock.
                await maintenance
                raise
            except Exception:
                self.diagnostics.cleanup_error = True
            await asyncio.sleep(60)

    def recover_generation(self, initial: bool = False) -> None:
        generation_path = self.config.bot_api_root / ".tgd-api-generation"
        stored_path = self.config.data_dir / "api-generation.json"
        try:
            current = generation_path.read_text().strip()
        except OSError:
            current = ""
        previous = self.api_generation
        if initial:
            try:
                previous = json.loads(stored_path.read_text())["generation"]
            except (OSError, ValueError, KeyError):
                previous = ""
        restarted = bool(current and previous and current != previous)
        if restarted and not initial and any(not task.done() for task in self.running.values()):
            # A restarted API does not stop local copies or prove an outstanding
            # HTTP request has returned. Reconciliation must not delete their
            # temporary files or requeue their jobs. Keep the old generation
            # until these owners drain, so the next tick still detects restart.
            return
        if initial or restarted:
            self.worker.recover(restarted)
        if current and (initial or current != self.api_generation):
            stored_path.write_text(json.dumps({"generation": current}))
            self.api_generation = current

    async def close(self) -> None:
        self.stopping = True
        for task in self.loops:
            task.cancel()
        await asyncio.gather(*self.loops, return_exceptions=True)
        if self.running:
            _, pending = await asyncio.wait(list(self.running.values()), timeout=10)
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
        if self.client:
            await self.client.close()

    async def ensure_client(self, *, reconnect_operation: str | None = None) -> bool:
        revision, settings, _ = await asyncio.to_thread(self.settings.read)
        transition = await asyncio.to_thread(self.local_api.read)
        reconnecting = (reconnect_operation is not None and transition.get("operation_id") == reconnect_operation
                        and transition.get("action") == "cloud_reconnect" and transition.get("phase") == "verifying")
        if transition.get("phase") != "idle" and not reconnecting:
            return False
        if settings.bot_api_mode == "cloud" and transition.get("cloud_available_at", 0) > time.time():
            return False
        if settings.bot_api_mode == "local":
            proxy = await asyncio.to_thread(self.settings.proxy_status, revision, settings)
            if proxy["state"] != ("active" if settings.proxy.enabled else "disabled"):
                self.bot_status.update(state="connecting", message=proxy["message"])
                return False
        generation = ""
        if self.config.managed_local_api:
            state = await asyncio.to_thread(self.local_api.read)
            if ((state.get("phase") != "idle" and not reconnecting)
                    or settings.bot_api_mode != state.get("applied_mode")):
                return False
            if settings.bot_api_mode == "local":
                applied = await asyncio.to_thread(self.local_api.applied)
                if applied.get("state") != "running":
                    self.bot_status = {"state": "error", "username": "", "message": "本地 API 服务尚未就绪，请查看服务状态"}
                    return False
                generation = applied.get("generation") or ""
        safety = await asyncio.to_thread(self.safety.status, settings.bot_token)
        if safety["state"] != "ready":
            self.bot_status.update(state="error", message=safety["message"])
            return False
        signature = self.settings.client_signature(settings)
        if not settings.bot_token:
            self.bot_status = {"state": "unconfigured", "username": "", "message": "请配置 Bot Token 和用户白名单"}
            if self.client and not any(not task.done() for task in self.running.values()):
                await waits.network(self.client.close(), waits.CLOSE_TIMEOUT)
                self.client, self.bot_id, self.token_hash = None, None, ""
                self.settings.cloud_applied_signature = ""
            return False
        if self.client and signature == self.client_signature and generation == self.client_generation:
            # A retained verified client is eligible to retry; existence alone
            # is not evidence that the failed transport has recovered.
            return True
        if any(not task.done() for task in self.running.values()):
            return False
        if self.client:
            await waits.network(self.client.close(), waits.CLOSE_TIMEOUT)
            self.client = None
            self.settings.cloud_applied_signature = ""
        self.bot_status = {"state": "connecting", "username": "",
                           "message": "正在连接云端 Bot API" if settings.bot_api_mode == "cloud" else "正在连接本地 Bot API"}
        self.bot_status_signature = signature
        self.bot_status_generation = generation
        client = TelegramClient(settings.bot_token, self.config, settings)
        client.safety = self.safety
        try:
            identity = await waits.network(client.identity(), waits.IDENTITY_TIMEOUT)
            if not await asyncio.to_thread(self.publish_client, client, identity, settings, generation, transition):
                await waits.close_candidate(client)
                return False
            return True
        except Exception as exc:
            await asyncio.to_thread(self.candidate_failure, signature, generation, transition, exc.message if isinstance(exc, AppError)
                                    else "Telegram 连接失败，请检查所选 API、凭据、网络或代理")
            await waits.close_candidate(client)
            raise

    def candidate_failure(self, signature: str, generation: str, transition: dict[str, Any], message: str) -> None:
        with self.settings.connection_publication_lock:
            _, values, _ = self.settings.read()
            latest = self.local_api.read()
            if (self.settings.client_signature(values) == signature and self.client is None
                    and latest.get("operation_id") == transition.get("operation_id")
                    and latest.get("phase") == transition.get("phase")
                    and (not self.config.managed_local_api or values.bot_api_mode != "local"
                         or self.local_api.applied().get("generation") == generation)):
                self.bot_status = {"state": "error", "username": "", "message": message}

    def publish_client(self, client: TelegramClient, identity: dict[str, Any], settings: UserSettings,
                       generation: str, transition: dict[str, Any]) -> bool:
        """Only a current identity may become the active client. No network or await."""
        signature = self.settings.client_signature(settings)
        with self.settings.connection_publication_lock:
            _, latest, _ = self.settings.read()
            latest_transition = self.local_api.read()
            if (self.settings.client_signature(latest) != signature
                    or latest_transition.get("operation_id") != transition.get("operation_id")
                    or latest_transition.get("phase") != transition.get("phase")):
                return False
            if settings.bot_api_mode == "local" and self.config.managed_local_api:
                if self.local_api.applied().get("generation") != generation:
                    return False
            self.remember_identity(identity)
            self.worker.credential_hash = digest(settings.bot_token)
            self.bot_id, self.token_hash, self.client = identity["id"], digest(settings.bot_token), client
            self.client_signature = signature
            self.client_generation = generation
            self.verified_client, self.verified_username = client, identity["username"]
            self.bot_status_signature = signature
            self.bot_status_generation = generation
            self.settings.cloud_applied_signature = signature if settings.bot_api_mode == "cloud" else ""
            self.bot_status = {"state": "online", "username": identity["username"], "message": ""}
            return True

    def connection_snapshot(self) -> tuple[UserSettings, dict[str, Any], dict[str, Any], dict[str, Any]]:
        """One saved connection observation; never probe or hold locks over I/O waits."""
        with self.settings.connection_publication_lock:
            revision, values, _ = self.settings.read()
            state = self.local_api.read()
            applied = self.local_api.applied() if self.config.managed_local_api else {}
            api = self.local_api.status(settings=values, state=state, applied=applied)
            proxy = self.settings.proxy_status(revision, values, applied=applied, runtime_state=state)
            bot = dict(self.bot_status)
            current = (self.client is not None and self.client is self.verified_client
                       and self.client_signature == self.settings.client_signature(values)
                       and (not self.config.managed_local_api or api["applied_mode"] == values.bot_api_mode))
            if not self.config.managed_local_api and current and api["phase"] == "idle":
                api["applied_mode"] = values.bot_api_mode
            if values.bot_api_mode == "local" and self.config.managed_local_api:
                current = (current and api["state"] == "running" and api["generation"] == self.client_generation
                           and proxy["state"] == ("active" if values.proxy.enabled else "disabled"))
            if not values.bot_token:
                bot = {"state": "unconfigured", "username": "", "message": "请配置 Bot Token 和用户白名单"}
            elif not current:
                failed_current = (self.client is None and self.bot_status_signature == self.settings.client_signature(values)
                                  and (not self.config.managed_local_api or values.bot_api_mode != "local"
                                       or api["generation"] == self.bot_status_generation))
                if failed_current and bot["state"] == "error":
                    bot["username"] = ""
                else:
                    bot = {"state": "connecting", "username": "", "message": "正在连接已保存的 Bot 配置"}
            else:
                bot["username"] = self.verified_username
            safety = self.safety.status(values.bot_token)
            bot["request_safety"] = safety
            if safety["state"] != "ready":
                bot.update(state="error", message=safety["message"])
            return values, bot, api, proxy

    def poll_result(self, client: TelegramClient | None, *, error: str = "") -> None:
        """A late old-client result cannot replace a newly published connection."""
        with self.settings.connection_publication_lock:
            if client is not self.client or client is None:
                return
            if self.client_signature != self.settings.client_signature(self.settings.read()[1]):
                return
            self.bot_status = {"state": "error" if error else "online",
                               "username": self.verified_username if client is self.verified_client else "",
                               "message": error}

    def remember_identity(self, identity: dict[str, Any]) -> None:
        with self.db.transaction() as session:
            active = session.scalar(select(MediaRequest.id).where(
                MediaRequest.status.in_(ACTIVE | {"collecting", "awaiting_confirmation"}),
                MediaRequest.bot_id != identity["id"]).limit(1))
            if active:
                raise AppError("BOT_BUSY", "存在原 Bot 的活动或待确认请求，不能更换 Bot 身份", 409)
            if not session.get(BotIdentity, identity["id"]):
                session.add(BotIdentity(id=identity["id"], username=identity["username"]))

    async def poll_loop(self) -> None:
        while not self.stopping:
            polling_client = self.client
            try:
                async with self.poll_lock:
                    transition = await asyncio.to_thread(self.local_api.read)
                    if (self.config.managed_local_api or transition.get("action") == "cloud_reconnect") and transition.get("phase") != "idle":
                        await asyncio.sleep(0.2)
                        continue
                    eligible = await self.ensure_client()
                    polling_client = self.client
                    if self.config.managed_local_api and not eligible:
                        await asyncio.sleep(0.5)
                        continue
                    if not self.client or self.bot_id is None:
                        await asyncio.sleep(2)
                        continue
                    _, configured, _ = await asyncio.to_thread(self.settings.read)
                    if not configured.bot_token or self.client_signature != self.settings.client_signature(configured):
                        # Keep an old client only for its running file owners, not new intake.
                        await asyncio.sleep(1)
                        continue
                    for inbox_id, update in await asyncio.to_thread(self.ingest.pending, self.bot_id):
                        if update.get("message"):
                            await asyncio.to_thread(self.ingest.message, self.bot_id, update["message"], inbox_id)
                        elif update.get("callback_query"):
                            callback = update["callback_query"]
                            try:
                                state = await asyncio.to_thread(self.ingest.callback, callback)
                            except AppError as exc:
                                state = exc.message
                            # Business operation is durable even if answering the callback times out.
                            try:
                                await self.client.answer(callback["id"], state)
                            except Exception:
                                pass
                        await asyncio.to_thread(self.ingest.mark_processed, inbox_id)
                    offset = await asyncio.to_thread(self.ingest.offset, self.bot_id)
                    updates = await self.client.updates(offset)
                    await asyncio.to_thread(self.ingest.persist_batch, self.bot_id, updates)
                    await asyncio.to_thread(self.poll_result, polling_client)
            except asyncio.CancelledError:
                raise
            except AppError as exc:
                await asyncio.to_thread(self.poll_result, polling_client, error=exc.message)
                await asyncio.sleep(5)
            except Exception:
                await asyncio.to_thread(self.poll_result, polling_client, error="Telegram 连接失败，请检查所选 API、凭据、网络或代理")
                await asyncio.sleep(5)

    def counts(self) -> tuple[int, int, int]:
        with self.db.sessions() as session:
            active, blocked, pending = session.execute(select(
                func.count().filter(Job.state.in_(["fetching", "saving", "cancelling", "recovery_blocked"])),
                func.count().filter(Job.state == "recovery_blocked"),
                func.count().filter(Job.state.in_(["queued", "retry_wait", "waiting_existing"])),
            ).select_from(Job).where(Job.state.in_(
                ["fetching", "saving", "cancelling", "recovery_blocked", "queued", "retry_wait", "waiting_existing"]))).one()
            return int(active), int(blocked), int(pending)

    async def cleanup_cache(self) -> bool:
        """Return true while draining/cleaning; every failure releases dispatch for backoff."""
        client, bot_id = self.client, self.bot_id
        if client is None or bot_id is None or client.mode != "local":
            return False
        row = await asyncio.to_thread(self.cache_cleanup.due, bot_id)
        if row is None or not self.progress_generation():
            return False
        self.worker.cleanup_paused = True
        try:
            # Set the claim barrier before inspecting durable ownership. A claim already
            # inside its transaction is then visible to the following owner query.
            if (any(not task.done() for task in self.running.values())
                    or await asyncio.to_thread(self.cache_cleanup.local_owners)):
                return True
            generation = self.progress_generation()
            try:
                fingerprint = await owned_thread(self.cache_cleanup.verify_final, row)
                if fingerprint is None:
                    await owned_thread(self.cache_cleanup.retry, row.job_id, "CACHE_FINAL_UNAVAILABLE")
                    return True
            except (OSError, AppError, ValueError):
                await owned_thread(self.cache_cleanup.retry, row.job_id, "CACHE_FINAL_UNAVAILABLE")
                return True
            async with self.connection_lock:
                # Hashing can take time. Do not use an obsolete generation/client or
                # run housekeeping while an API/proxy operation is underway.
                if (not generation or self.progress_generation() != generation or self.client is not client or self.bot_id != bot_id
                        or await asyncio.to_thread(self.cache_cleanup.local_owners)):
                    return True
                try:
                    if not await owned_thread(self.cache_cleanup.final_unchanged, row, fingerprint):
                        await owned_thread(self.cache_cleanup.retry, row.job_id, "CACHE_FINAL_UNAVAILABLE")
                        return True
                except (OSError, AppError, ValueError):
                    await owned_thread(self.cache_cleanup.retry, row.job_id, "CACHE_FINAL_UNAVAILABLE")
                    return True
                try:
                    source = self.cache_cleanup.source_path(row)
                    await client.delete_file_cache(row.file_id, source, row.size_bytes, generation)
                except Exception:
                    await owned_thread(self.cache_cleanup.retry, row.job_id)
                else:
                    await owned_thread(self.cache_cleanup.complete, row.job_id)
            # One item per tick bounds connection-lock/network ownership to one request.
            return True
        finally:
            self.worker.cleanup_paused = False

    async def tick_loop(self) -> None:
        while not self.stopping:
            try:
                async with self.connection_lock:
                    paused = await self.local_api.reconcile(self)
                if paused:
                    await asyncio.sleep(0.5)
                    continue
                await asyncio.to_thread(self.tasks.freeze_due)
                await asyncio.to_thread(self.recover_generation)
                self.running = {key: task for key, task in self.running.items() if not task.done()}
                client, bot_id = self.client, self.bot_id
                # Reconciliation, grouping and generation checks above always run.
                # No verified consumer means there is no dispatch decision to make.
                if client is None or bot_id is None:
                    await asyncio.sleep(0.5)
                    continue
                revision, settings, _ = await asyncio.to_thread(self.settings.read)
                active, blocked, pending = await asyncio.to_thread(self.counts)
                can_dispatch = (pending > 0 and active < settings.download_concurrency and bool(settings.bot_token)
                                and self.client_signature == self.settings.client_signature(settings) and self.bot_status["state"] == "online"
                                and not blocked)
                if can_dispatch:
                    proxy = await asyncio.to_thread(self.settings.proxy_status, revision, settings)
                    safety = await asyncio.to_thread(self.safety.status, settings.bot_token)
                    can_dispatch = (safety["state"] == "ready"
                                    and proxy["state"] == ("active" if settings.proxy.enabled else "disabled"))
                if can_dispatch and self.config.managed_local_api and settings.bot_api_mode == "local":
                    applied = await asyncio.to_thread(self.local_api.applied)
                    can_dispatch = can_dispatch and applied.get("state") == "running" and applied.get("generation") == self.client_generation
                if await self.cleanup_cache():
                    can_dispatch = False
                if can_dispatch:
                    for _ in range(max(0, settings.download_concurrency - active)):
                        job_id = await asyncio.to_thread(self.worker.claim, bot_id, settings.bot_api_mode)
                        if not job_id:
                            break
                        assert client
                        self.running[job_id] = asyncio.create_task(self.worker.run(client, job_id))
            except asyncio.CancelledError:
                raise
            except Exception:
                # No raw exceptions: Telegram exception representations contain credential-bearing URLs.
                self.bot_status["message"] = "调度暂停，请检查存储、配置和系统诊断"
            await asyncio.sleep(0.5)

    def notification_due(self) -> bool:
        # Do not filter by identity here: the authoritative claim also obsoletes
        # records left behind by a previous Bot or a deleted/expired request.
        with self.db.sessions() as session:
            return session.scalar(select(Notification.id).where(
                Notification.state == "pending", Notification.next_run_at <= time.time()).limit(1)) is not None

    def next_notification(self, bot_id: int | None = None) -> tuple[str, int, dict[str, Any], str | None] | None:
        bot_id = self.bot_id if bot_id is None else bot_id
        with self.db.transaction() as session:
            row = session.scalar(select(Notification).where(Notification.state == "pending", Notification.next_run_at <= time.time())
                                 .order_by(Notification.next_run_at).limit(1))
            if not row:
                return None
            if row.request_id:
                request = session.get(MediaRequest, row.request_id)
                if not request or request.deleted_at is not None:
                    row.state = "obsolete"
                    return None
            if row.payload.get("bot_id") not in {None, bot_id}:
                # Completed requests can outlive their Bot configuration. Never
                # deliver an old identity's durable outbox through a new Bot.
                row.state = "obsolete"
                return None
            if row.payload.get("confirmation") and row.request_id:
                request = session.get(MediaRequest, row.request_id)
                if not request or request.status != "awaiting_confirmation" or (request.expires_at or 0) <= time.time():
                    row.state = "obsolete"
                    return None
            _, configured, _ = self.settings.read()
            if self.safety.status(configured.bot_token)["state"] != "ready":
                return None
            row.attempts += 1
            row.next_run_at = time.time() + 60
            return row.id, row.chat_id, row.payload, row.request_id

    def notification_result(self, notification_id: str, message_id: int | None, permanent: bool, delay: float = 0, deferred: bool = False) -> None:
        with self.db.transaction() as session:
            row = session.get(Notification, notification_id)
            assert row
            if deferred:
                row.attempts = max(0, row.attempts - 1)
            # Removal can commit while a Telegram send is already in flight.
            # Keep its acknowledgement for history, but never revive/retry it.
            request = session.get(MediaRequest, row.request_id) if row.request_id else None
            if row.state == "obsolete" or (request and request.deleted_at is not None):
                if message_id is not None:
                    row.message_id = message_id
                row.state = "obsolete"
                return
            if message_id is not None:
                row.message_id, row.state = message_id, "sent"
                if row.payload.get("confirmation") and row.request_id:
                    request = session.get(MediaRequest, row.request_id)
                    if request:
                        request.card_message_id = message_id
                        # Initial conservative expiry is intentionally never extended by a send retry.
            elif permanent or row.attempts >= 5:
                row.state = "failed"
            else:
                row.next_run_at = time.time() + max(delay, min(300, 2 ** row.attempts))

    async def notify_loop(self) -> None:
        while not self.stopping:
            try:
                async with self.notify_lock:
                    transition = await asyncio.to_thread(self.local_api.read)
                    if (self.config.managed_local_api or transition.get("action") == "cloud_reconnect") and transition.get("phase") != "idle":
                        await asyncio.sleep(0.2)
                        continue
                    client, bot_id = self.client, self.bot_id
                    if not client or bot_id is None:
                        await asyncio.sleep(1)
                        continue
                    if not await asyncio.to_thread(self.notification_due):
                        await asyncio.sleep(0.5)
                        continue
                    revision, configured, _ = await asyncio.to_thread(self.settings.read)
                    if configured.bot_api_mode == "local":
                        network = await asyncio.to_thread(self.settings.proxy_status, revision, configured)
                        if network["state"] != ("active" if configured.proxy.enabled else "disabled"):
                            await asyncio.sleep(0.5)
                            continue
                    if self.client_signature != self.settings.client_signature(configured):
                        await asyncio.sleep(0.5)
                        continue
                    if self.config.managed_local_api:
                        managed_state = await asyncio.to_thread(self.local_api.read)
                        applied = await asyncio.to_thread(self.local_api.applied)
                        if (configured.bot_api_mode != managed_state.get("applied_mode") or
                                configured.bot_api_mode == "local" and (applied.get("state") != "running" or
                                applied.get("generation") != self.client_generation)):
                            await asyncio.sleep(0.5)
                            continue
                    pending = await asyncio.to_thread(self.next_notification, bot_id)
                    if not pending:
                        await asyncio.sleep(0.5)
                        continue
                    row_id, chat_id, payload, request_id = pending
                    callback = (request_id, payload["nonce"]) if payload.get("confirmation") and request_id else None
                    try:
                        message_id = await client.send(chat_id, payload["text"], callback)
                        await asyncio.to_thread(self.notification_result, row_id, message_id, False)
                    except SafetyDeferred as exc:
                        delay = max(1, (exc.retry_at or time.time() + 5) - time.time())
                        await asyncio.to_thread(self.notification_result, row_id, None, False, delay, True)
                    except TelegramRetryAfter as exc:
                        await asyncio.to_thread(self.notification_result, row_id, None, False, exc.retry_after, True)
                    except TelegramUnauthorizedError:
                        await asyncio.to_thread(self.notification_result, row_id, None, False, 5, True)
                    except (TelegramForbiddenError, TelegramBadRequest):
                        await asyncio.to_thread(self.notification_result, row_id, None, True)
                    except Exception:
                        await asyncio.to_thread(self.notification_result, row_id, None, False)
            except asyncio.CancelledError:
                raise
            except Exception:
                await asyncio.sleep(2)
