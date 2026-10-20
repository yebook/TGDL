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
from app.services.local_api import LocalApiService
from app.services.request_safety import RequestSafetyService, SafetyDeferred
from app.services.settings import SettingsService
from app.services.tasks import ACTIVE, TaskService
from app.storage.files import StorageManager
from app.workers.download import DownloadWorker


class Runtime:
    def __init__(self, db: Database, config: Config, settings: SettingsService,
                 tasks: TaskService, storage: StorageManager) -> None:
        self.db, self.config, self.settings, self.tasks, self.storage = db, config, settings, tasks, storage
        self.safety = RequestSafetyService(db)
        self.ingest = IngestService(db, settings, tasks)
        self.worker = DownloadWorker(db, storage, config, uid())
        self.client: TelegramClient | None = None
        self.token_hash = ""
        self.client_signature = ""
        self.client_generation = ""
        self.bot_id: int | None = None
        self.bot_status: dict[str, Any] = {"state": "unconfigured", "username": "", "message": "请配置 Bot Token 和用户白名单"}
        self.running: dict[str, asyncio.Task[None]] = {}
        self.loops: list[asyncio.Task[None]] = []
        self.stopping = False
        self.api_generation = ""
        self.network_write_error = False
        self.poll_lock = asyncio.Lock()
        self.notify_lock = asyncio.Lock()
        self.local_api = LocalApiService(settings)
        self.connection_lock = asyncio.Lock()

    async def start(self) -> None:
        if self.config.managed_local_api:
            # Persist the initial desired/applied barrier before any consumer starts.
            await self.local_api.reconcile(self)
        await asyncio.to_thread(self.recover_generation, True)
        active, _, _ = await asyncio.to_thread(self.counts)
        if active == 0:
            try:
                await asyncio.to_thread(self.settings.write_proxy_desired)
            except OSError:
                self.network_write_error = True
        self.loops = [asyncio.create_task(self.poll_loop()), asyncio.create_task(self.tick_loop()),
                      asyncio.create_task(self.notify_loop())]

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
        if current:
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

    async def ensure_client(self) -> None:
        _, settings, _ = await asyncio.to_thread(self.settings.read)
        generation = ""
        if self.config.managed_local_api:
            state = await asyncio.to_thread(self.local_api.read)
            if (state.get("phase") != "idle" or settings.bot_api_mode != state.get("applied_mode")
                    or settings.bot_api_mode == "cloud" and state.get("cloud_available_at", 0) > time.time()):
                return
            if settings.bot_api_mode == "local":
                applied = await asyncio.to_thread(self.local_api.applied)
                if applied.get("state") != "running":
                    self.bot_status = {"state": "error", "username": "", "message": "本地 API 服务尚未就绪，请查看服务状态"}
                    return
                generation = applied.get("generation") or ""
        token_hash = digest(settings.bot_token)
        safety = await asyncio.to_thread(self.safety.status, settings.bot_token)
        if safety["state"] != "ready":
            self.bot_status.update(state="error", message=safety["message"])
            return
        signature = self.settings.client_signature(settings)
        if not settings.bot_token:
            self.bot_status = {"state": "unconfigured", "username": "", "message": "请配置 Bot Token 和用户白名单"}
            if self.client and not any(not task.done() for task in self.running.values()):
                await self.client.close()
                self.client, self.bot_id, self.token_hash = None, None, ""
                self.settings.cloud_applied_signature = ""
            return
        if self.client and signature == self.client_signature and generation == self.client_generation:
            self.bot_status.update(state="online", message="")
            return
        if any(not task.done() for task in self.running.values()):
            return
        if self.client:
            await self.client.close()
            self.client = None
            self.settings.cloud_applied_signature = ""
        self.bot_status = {"state": "connecting", "username": "",
                           "message": "正在连接云端 Bot API" if settings.bot_api_mode == "cloud" else "正在连接本地 Bot API"}
        client = TelegramClient(settings.bot_token, self.config, settings)
        client.safety = self.safety
        try:
            identity = await client.identity()
            await asyncio.to_thread(self.remember_identity, identity)
        except Exception:
            await client.close()
            raise
        self.worker.credential_hash = token_hash
        self.bot_id, self.token_hash, self.client = identity["id"], token_hash, client
        self.client_signature = signature
        self.client_generation = generation
        self.settings.cloud_applied_signature = signature if settings.bot_api_mode == "cloud" else ""
        self.bot_status = {"state": "online", "username": identity["username"], "message": ""}

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
            try:
                async with self.poll_lock:
                    if self.config.managed_local_api and (await asyncio.to_thread(self.local_api.read)).get("phase") != "idle":
                        await asyncio.sleep(0.2)
                        continue
                    await self.ensure_client()
                    if self.config.managed_local_api and self.bot_status["state"] != "online":
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
                    self.bot_status["state"], self.bot_status["message"] = "online", ""
            except asyncio.CancelledError:
                raise
            except AppError as exc:
                self.bot_status = {"state": "error", "username": "", "message": exc.message}
                await asyncio.sleep(5)
            except Exception:
                self.bot_status["state"], self.bot_status["message"] = "error", "Telegram 连接失败，请检查所选 API、凭据、网络或代理"
                await asyncio.sleep(5)

    def counts(self) -> tuple[int, int, int]:
        with self.db.sessions() as session:
            active = session.scalar(select(func.count()).select_from(Job).where(
                Job.state.in_(["fetching", "saving", "cancelling", "recovery_blocked"]))) or 0
            blocked = session.scalar(select(func.count()).select_from(Job).where(Job.state == "recovery_blocked")) or 0
            pending = session.scalar(select(func.count()).select_from(Job).where(
                Job.state.in_(["queued", "retry_wait", "waiting_existing"]))) or 0
            return active, blocked, pending

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
                revision, settings, _ = await asyncio.to_thread(self.settings.read)
                active, blocked, _ = await asyncio.to_thread(self.counts)
                proxy = await asyncio.to_thread(self.settings.proxy_status, revision, settings)
                # Pending proxy updates are handed over only after current acquisition/copy slots drain.
                if active == 0 and self.config.proxy_capable and settings.bot_api_mode == "local":
                    try:
                        await asyncio.to_thread(self.settings.write_proxy_desired)
                        self.network_write_error = False
                    except OSError:
                        self.network_write_error = True
                client, bot_id = self.client, self.bot_id
                can_dispatch = (client is not None and bot_id is not None and bool(settings.bot_token)
                                and self.client_signature == self.settings.client_signature(settings) and self.bot_status["state"] == "online"
                                and self.safety.status(settings.bot_token)["state"] == "ready"
                                and not blocked and (settings.bot_api_mode == "cloud" or not self.network_write_error)
                                and (not settings.proxy.enabled or proxy["state"] == "active"))
                if self.config.managed_local_api and settings.bot_api_mode == "local":
                    applied = await asyncio.to_thread(self.local_api.applied)
                    can_dispatch = can_dispatch and applied.get("state") == "running" and applied.get("generation") == self.client_generation
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
                    if self.config.managed_local_api and (await asyncio.to_thread(self.local_api.read)).get("phase") != "idle":
                        await asyncio.sleep(0.2)
                        continue
                    client, bot_id = self.client, self.bot_id
                    if not client or bot_id is None:
                        await asyncio.sleep(1)
                        continue
                    _, configured, _ = await asyncio.to_thread(self.settings.read)
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
