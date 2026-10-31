"""Current-Bot cache snapshots and durable, single-flight offline maintenance."""
import asyncio
import re
import threading
import time
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.core.security import digest
from app.db.models import CacheCleanup, Job, LocalApiRuntime

BUSY = {"running", "uncertain"}
MESSAGES = {
    "unavailable": "本地 Bot 尚未就绪，无法读取缓存",
    "unsupported": "当前本地 API 不支持缓存维护，请更新服务",
    "connection_busy": "连接操作正在进行，请稍后维护缓存",
    "active_downloads": "请等待下载和保存完成后清理缓存",
    "recovery_required": "存在获取状态未知的任务，请先重启本地 API 恢复",
    "queued_downloads": "存在排队或重试任务，请先完成或取消这些任务",
    "pending_verification": "已保存文件的缓存仍待安全校验，请先处理存储或缓存清理问题",
    "maintenance_running": "正在清理缓存，暂不开始新的下载",
    "maintenance_uncertain": "缓存清理结果尚未确认，正在核对；如持续未恢复，请重启本地 API",
}


def maintenance_values(session: Session) -> dict[str, Any]:
    row = session.get(LocalApiRuntime, 1)
    value = row.values.get("cache_maintenance", {}) if row else {}
    if value == {}:
        return {}
    if (not isinstance(value, dict) or not isinstance(value.get("state"), str)
            or value.get("state") not in BUSY | {"idle", "succeeded", "failed"}
            or value.get("state") in BUSY and (
                not isinstance(value.get("operation_id"), str) or not re.fullmatch(r"[0-9a-f]{32}", value["operation_id"])
                or not isinstance(value.get("generation"), str) or not 1 <= len(value["generation"]) <= 100
                or not isinstance(value.get("credential"), str) or not re.fullmatch(r"[0-9a-f]{64}", value["credential"]))):
        return {"state": "uncertain", "operation_id": None, "invalid": True}
    return value


def maintenance_blocked(session: Session) -> bool:
    return maintenance_values(session).get("state") in BUSY


def require_no_maintenance(session: Session) -> None:
    if maintenance_blocked(session):
        raise AppError("CACHE_MAINTENANCE_BUSY", "缓存维护尚未完成，请等待处理完成后重试", 409)


def owners_reason(session: Session) -> str | None:
    states = set(session.scalars(select(Job.state).where(
        Job.acquisition_mode.in_(["local"]) | Job.acquisition_mode.is_(None)
        | Job.state.in_(["queued", "retry_wait", "waiting_existing"]))))
    if "recovery_blocked" in states:
        return "recovery_required"
    if states & {"fetching", "saving", "cancelling"}:
        return "active_downloads"
    if states & {"queued", "retry_wait", "waiting_existing"}:
        return "queued_downloads"
    if session.scalar(select(CacheCleanup.job_id).limit(1)):
        return "pending_verification"
    return None


class CacheMaintenanceService:
    def __init__(self, runtime: Any) -> None:
        self.runtime = runtime
        self._lock = threading.RLock()
        self._usage: dict[str, int] | None = None
        self._identity: tuple[str, str] | None = None
        self._usage_state = "unavailable"
        self._measured_at: str | None = None
        self._task: asyncio.Task[None] | None = None
        # A previous app may have exited after mutation intent but before its response.
        current = self.read()
        if current.get("state") == "running":
            self._write(current["operation_id"], state="uncertain")

    def read(self) -> dict[str, Any]:
        with self.runtime.db.sessions() as session:
            return dict(maintenance_values(session))

    def _write(self, operation_id: str, **updates: Any) -> None:
        with self.runtime.db.transaction() as session:
            row = session.get(LocalApiRuntime, 1)
            assert row
            current = maintenance_values(session)
            if updates.get("state") == "uncertain" and current.get("state") not in BUSY:
                return  # A late cancellation cannot undo an already confirmed terminal result.
            if current.get("operation_id") == operation_id:
                row.values = dict(row.values) | {"cache_maintenance": current | updates}

    def identity(self) -> tuple[Any, str, str] | None:
        runtime = self.runtime
        try:
            generation = runtime.progress_generation()
        except Exception:
            return None
        client = runtime.client
        if not generation or client is None or runtime.bot_id is None or client.mode != "local":
            return None
        token = getattr(getattr(client, "bot", None), "token", None)
        return (client, generation, digest(token)) if isinstance(token, str) and token else None

    def invalidate(self) -> None:
        with self._lock:
            if self._usage_state == "ready":
                self._usage_state = "stale"

    def status(self) -> dict[str, Any]:
        identity = self.identity()
        with self.runtime.db.sessions() as session:
            maintenance = dict(maintenance_values(session))
            reason = owners_reason(session)
        if reason in {"active_downloads", "recovery_required", "pending_verification"}:
            self.invalidate()
        state = maintenance.get("state", "idle")
        if state not in BUSY and identity and maintenance.get("credential") != identity[2]:
            maintenance, state = {}, "idle"
        if state in BUSY:
            reason = "maintenance_running" if state == "running" else "maintenance_uncertain"
        elif not identity:
            reason = "unavailable"
        elif self.runtime.local_api.read().get("phase") != "idle":
            reason = "connection_busy"
        with self._lock:
            matching = identity and self._identity == (identity[1], identity[2])
            usage_state = self._usage_state if matching else "unavailable"
            usage = self._usage if matching else None
            measured = self._measured_at if matching else None
        if usage_state == "unsupported" and state not in BUSY:
            reason = "unsupported"
        elif (usage is None or usage_state in {"unavailable", "refreshing"}) and reason is None:
            reason = "maintenance_running" if usage_state == "refreshing" else "unavailable"
        if self._task and not self._task.done() and reason is None:
            reason = "maintenance_running"
        return {"used_bytes": usage["used_bytes"] if usage else None,
                "file_count": usage["file_count"] if usage else None,
                "usage_state": usage_state, "measured_at": measured, "scope": "current_bot_media",
                "maintenance": {"state": state, "operation_id": maintenance.get("operation_id"),
                    "can_clean": reason is None and not (self._task and not self._task.done()),
                    "blocked_reason": reason, "message": MESSAGES.get(reason or "", {
                        "idle": "缓存占用已更新，可在下载空闲时清理",
                        "succeeded": "缓存清理完成，已更新实际占用",
                        "failed": "缓存清理未完成，请刷新占用后重试；如仍有问题，请重启本地 API",
                    }.get(state, "正在核对缓存维护状态")),
                    "last_result": maintenance.get("last_result") if identity and maintenance.get("credential") == identity[2] else None}}

    def _admit(self, generation: str, credential: str) -> str:
        runtime = self.runtime
        with runtime.settings.connection_publication_lock, runtime.db.transaction() as session:
            require_no_maintenance(session)
            row = session.get(LocalApiRuntime, 1)
            assert row
            reason = owners_reason(session)
            if row.values.get("phase") != "idle":
                reason = "connection_busy"
            if reason:
                raise AppError("CACHE_CLEANUP_BLOCKED", MESSAGES[reason], 409)
            operation_id = uuid4().hex
            row.values = dict(row.values) | {"cache_maintenance": {
                "state": "running", "operation_id": operation_id, "generation": generation,
                "credential": credential, "started_at": time.time(), "last_result": None}}
            return operation_id

    async def start(self, *, clean: bool) -> None:
        runtime = self.runtime
        if not clean and (await asyncio.to_thread(self.read)).get("state") in BUSY:
            await self.resume()
            return  # Explicit refresh reconciles the same durable operation, never reissues clear.
        if self._task and not self._task.done() or runtime.connection_lock.locked():
            raise AppError("CACHE_MAINTENANCE_BUSY", "缓存或连接操作正在进行，请稍后重试", 409)
        async with runtime.connection_lock:
            identity = await asyncio.to_thread(self.identity)
            if not identity:
                raise AppError("CACHE_UNAVAILABLE", MESSAGES["unavailable"], 409)
            client, generation, credential = identity
            current = await asyncio.to_thread(self.read)
            if current.get("state") in BUSY:
                if clean:
                    raise AppError("CACHE_MAINTENANCE_BUSY", MESSAGES["maintenance_uncertain"], 409)
                self._task = asyncio.create_task(self._monitor())
                return
            if clean:
                with self._lock:
                    if self._identity != (generation, credential) or self._usage_state not in {"ready", "stale"} or self._usage is None:
                        raise AppError("CACHE_UNAVAILABLE", "请先刷新缓存占用后再清理", 409)
                if runtime.worker.cleanup_paused or any(not task.done() for task in runtime.running.values()):
                    raise AppError("CACHE_CLEANUP_BLOCKED", MESSAGES["active_downloads"], 409)
                admission = asyncio.create_task(asyncio.to_thread(self._admit, generation, credential))
                try:
                    operation_id = await asyncio.shield(admission)
                except asyncio.CancelledError:
                    result = await asyncio.gather(admission, return_exceptions=True)
                    if isinstance(result[0], str):
                        # No private mutation was issued; reconcile safely without orphan intent.
                        await asyncio.to_thread(self._write, result[0], state="failed")
                    raise
                self._task = asyncio.create_task(self._clear(client, generation, credential, operation_id))
            else:
                runtime.worker.cache_scan_paused = True
                try:
                    def check_scan() -> str | None:
                        # Wait for any claim already in progress before checking its owner.
                        with runtime.db.transaction() as session:
                            require_no_maintenance(session)
                            reason = owners_reason(session)
                            return reason if reason in {"active_downloads", "recovery_required"} else None

                    reason = await asyncio.to_thread(check_scan)
                    if reason or runtime.worker.cleanup_paused or any(not task.done() for task in runtime.running.values()):
                        raise AppError("CACHE_CLEANUP_BLOCKED", MESSAGES[reason or "active_downloads"], 409)
                except BaseException:
                    runtime.worker.cache_scan_paused = False
                    raise
                with self._lock:
                    if self._identity != (generation, credential):
                        self._usage, self._measured_at = None, None
                    self._identity, self._usage_state = (generation, credential), "refreshing"
                self._task = asyncio.create_task(self._refresh(client, generation, credential))

    def _snapshot(self, snapshot: dict[str, Any], generation: str, credential: str) -> None:
        identity = self.identity()
        if not identity or (identity[1], identity[2]) != (generation, credential):
            return
        with self._lock:
            before_state = self._usage_state
            if self._identity != (generation, credential):
                self._usage, self._measured_at = None, None
            self._identity = generation, credential
            usage = snapshot["usage"]
            if usage is not None:
                if snapshot["scan_state"] == "ready" and (usage != self._usage or before_state == "refreshing" or self._measured_at is None):
                    self._measured_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
                self._usage = dict(usage)
            self._usage_state = {"ready": "ready", "running": "refreshing", "failed": "stale",
                                 "idle": "stale" if usage else "unavailable"}[snapshot["scan_state"]]
        current = self.read()
        operation = snapshot["operation"]
        if (current.get("state") in BUSY and current.get("generation") == generation
                and current.get("credential") == credential and operation
                and operation["operation_id"] == current.get("operation_id")):
            state = operation["state"]
            if state == "running" and operation["error_code"]:
                self._write(current["operation_id"], state="uncertain")
            if state != "running":
                self._write(current["operation_id"], state=state, last_result={
                    key: operation[key] for key in ("before_bytes", "after_bytes", "released_bytes")})

    def _unavailable(self, error: Exception, generation: str, credential: str) -> None:
        with self._lock:
            if self._identity != (generation, credential):
                return
            self._usage_state = "unsupported" if isinstance(error, AppError) and error.code == "CACHE_UNSUPPORTED" else "stale" if self._usage else "unavailable"

    async def _clear(self, client: Any, generation: str, credential: str, operation_id: str) -> None:
        try:
            async with self.runtime.connection_lock:
                identity = await asyncio.to_thread(self.identity)
                if not identity or identity != (client, generation, credential):
                    await asyncio.to_thread(self._write, operation_id, state="failed")
                    return  # No mutation was sent.
                try:
                    snapshot = await client.clear_cache_storage(generation, operation_id)
                except AppError as exc:
                    if exc.code == "CACHE_NATIVE_BUSY":
                        await asyncio.to_thread(self._write, operation_id, state="failed")
                        return  # Native guarantees this operation was not accepted.
                    raise
                await asyncio.to_thread(self._snapshot, snapshot, generation, credential)
        except asyncio.CancelledError:
            await asyncio.to_thread(self._write, operation_id, state="uncertain")
            raise
        except Exception as exc:
            self._unavailable(exc, generation, credential)
            await asyncio.to_thread(self._write, operation_id, state="uncertain")
        await self._monitor()

    async def _refresh(self, client: Any, generation: str, credential: str) -> None:
        refresh = True
        try:
            while not self.runtime.stopping:
                try:
                    async with self.runtime.connection_lock:
                        if await asyncio.to_thread(self.identity) != (client, generation, credential):
                            return
                        start_scan, refresh = refresh, False
                        snapshot = await client.cache_storage(generation, refresh=start_scan)
                        await asyncio.to_thread(self._snapshot, snapshot, generation, credential)
                        if (snapshot["scan_state"] != "running"
                                and not (snapshot["operation"] and snapshot["operation"]["state"] == "running")):
                            return
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    self._unavailable(exc, generation, credential)
                    if isinstance(exc, AppError) and exc.code in {"CACHE_NATIVE_BUSY", "CACHE_UNSUPPORTED"}:
                        return  # Known unaccepted scan/capability failure.
                    # A lost scan response cannot prove native work stopped. Poll only;
                    # retain dispatch/notification barriers until it finishes or is replaced.
                await asyncio.sleep(1)
        finally:
            self.runtime.worker.cache_scan_paused = False

    async def _monitor(self) -> None:
        while not self.runtime.stopping:
            current = await asyncio.to_thread(self.read)
            if current.get("state") not in BUSY:
                return
            if current.get("invalid"):
                return  # Fail closed; an explicit managed restart establishes a recovery baseline.
            identity = await asyncio.to_thread(self.identity)
            if identity:
                client, generation, credential = identity
                if credential == current.get("credential") and generation != current.get("generation"):
                    # A verified current child with a different generation proves replacement;
                    # no completed/bytes claim is made for the interrupted old operation.
                    await asyncio.to_thread(self._write, current["operation_id"], state="failed")
                    return
                if (generation, credential) == (current.get("generation"), current.get("credential")):
                    try:
                        async with self.runtime.connection_lock:
                            if await asyncio.to_thread(self.identity) != identity:
                                continue
                            snapshot = await client.cache_storage(generation)
                            await asyncio.to_thread(self._snapshot, snapshot, generation, credential)
                            operation = snapshot["operation"]
                            if not operation or operation["operation_id"] != current["operation_id"]:
                                await asyncio.to_thread(self._write, current["operation_id"], state="uncertain")
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        self._unavailable(exc, generation, credential)
                        await asyncio.to_thread(self._write, current["operation_id"], state="uncertain")
            await asyncio.sleep(1)

    async def resume(self) -> None:
        if not self._task or self._task.done():
            if (await asyncio.to_thread(self.read)).get("state") in BUSY:
                self._task = asyncio.create_task(self._monitor())

    async def close(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
        current = await asyncio.to_thread(self.read)
        if current.get("state") == "running":
            await asyncio.to_thread(self._write, current["operation_id"], state="uncertain")
