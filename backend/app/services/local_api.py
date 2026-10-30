"""Durable transition barrier and private, bounded API supervisor protocol."""
import json
import math
import os
import stat
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from aiogram.exceptions import TelegramRetryAfter, TelegramUnauthorizedError
from cryptography.fernet import Fernet
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.core.security import Secrets, private_file
from app.db.models import Job, LocalApiRuntime, Settings, uid
from app.schemas.contracts import UserSettings
from app.services import connection_waits as waits
from app.services.diagnostics import classify_error
from app.services.network_errors import classify_network_failure
from app.services.request_safety import SafetyDeferred

BUSY_PHASES = {"draining", "logging_out", "starting", "verifying", "cooldown"}
MESSAGES = {"idle": "", "draining": "正在准备切换连接", "logging_out": "正在退出云端 Bot API",
            "starting": "正在应用 API 服务配置", "verifying": "正在验证 Bot 连接", "cooldown": "等待 Telegram 云端冷却结束",
            "blocked": "存在恢复待处理任务，请先重启本地 API 恢复", "logout_uncertain": "云端退出结果不确定；请核实退出后明确确认继续，系统不会自动重试退出或回连云端",
            "failed": "操作未完成，请检查凭据、网络或代理后重试"}
ERROR_MESSAGES = {"LOCAL_API_CREDENTIALS": "请填写 API ID 和 API Hash 后保存配置",
                  "LOCAL_API_DRAIN_TIMEOUT": "旧连接的消息处理尚未结束，本次切换已暂停。请稍后重试；不会强行中断旧处理或下载",
                  "LOCAL_API_OPERATION_TIMEOUT": "连接操作超时，已停止本次尝试；请检查网络或代理后重试",
                  "LOCAL_API_VERIFY_TIMEOUT": "连接验证长时间未能完成，已暂停；请检查连接或请求保护状态后重试",
                  "LOCAL_API_UNAVAILABLE": "本地 API 管理组件未连接，请检查部署",
                  "LOCAL_API_PROXY": "网络配置尚未生效，请检查本地 API 服务及代理地址、端口后重试",
                  "LOCAL_API_START_FAILED": "本地 API 进程启动失败，请检查凭据和网络后重试",
                  "LOCAL_API_COMMAND_TIMEOUT": "本地 API 操作等待超时，请检查服务状态后重试",
                  "LOCAL_API_CONNECTION_FAILED": "本地 API 进程已启动，但 Bot 连接验证失败，原因尚未确定；请运行网络连通测试，并检查应用与本地 API 的连接、服务日志、保存配置及 Telegram 网络或代理。进程运行不代表 Bot 已就绪，排查后需手动重试",
                  "LOCAL_API_PREFLIGHT_FAILED": "云端 Bot 预检查失败，请检查 Token 和云端代理后重试"}


def schedule_settings(session: Session, settings: Settings, candidate: UserSettings, crypto: Secrets,
                      *, managed: bool = True) -> None:
    row = session.get(LocalApiRuntime, 1)
    if not row:
        row = LocalApiRuntime(id=1, values={"applied_mode": "cloud", "phase": "idle"})
        session.add(row)
    state = dict(row.values)
    old = dict(settings.values)
    old_hash = crypto.decrypt(old.get("telegram_api_hash", ""))
    changed = (candidate.bot_api_mode != old.get("bot_api_mode", "cloud") or
               candidate.telegram_api_id != old.get("telegram_api_id", "") or
               candidate.telegram_api_hash != old_hash)
    old_proxy = UserSettings().proxy.model_dump() | old.get("proxy", {})
    old_proxy["password"] = crypto.decrypt(old_proxy["password"])
    if "TGD_PROXY" in os.environ:
        old_proxy = json.loads(os.environ["TGD_PROXY"])
    old_proxy = UserSettings.model_validate({"proxy": old_proxy}).proxy.model_dump()
    effective_old = old_proxy if old_proxy["enabled"] else {"enabled": False}
    effective_new = candidate.proxy.model_dump() if candidate.proxy.enabled else {"enabled": False}
    proxy_changed = effective_old != effective_new
    connection_changed = (candidate.bot_token != os.getenv("TGD_BOT_TOKEN", crypto.decrypt(old.get("bot_token", ""))) or
                          proxy_changed)
    token_changed = candidate.bot_token != os.getenv("TGD_BOT_TOKEN", crypto.decrypt(old.get("bot_token", "")))
    cloud_reconnect = (candidate.bot_api_mode == old.get("bot_api_mode", "cloud") == "cloud"
                       and (proxy_changed or state.get("action") == "cloud_reconnect" and connection_changed))
    if not managed and not cloud_reconnect and state.get("action") != "cloud_reconnect":
        return
    if state.get("logout_uncertain") and (changed or connection_changed):
        raise AppError("LOCAL_API_BUSY", MESSAGES["logout_uncertain"], 409)
    if (token_changed and (os.getenv("TGD_BOT_TOKEN") or old.get("bot_token")) and
            candidate.bot_api_mode != old.get("bot_api_mode", "cloud")):
        raise AppError("LOCAL_API_BUSY", "请先在当前模式保存 Bot Token，再单独切换 API 模式", 409)
    if state.get("phase", "idle") != "idle" and state.get("logout_done") and token_changed:
        raise AppError("LOCAL_API_BUSY", "请先完成当前 Bot 的本地连接，再更换 Bot Token", 409)
    if state.get("phase", "idle") in BUSY_PHASES | {"logout_uncertain"} and (changed or connection_changed):
        raise AppError("LOCAL_API_BUSY", "本地 API 操作尚未完成，请完成或重试当前操作后修改连接配置", 409)
    if cloud_reconnect:
        state.update(operation_id=uid(), phase="draining", action="cloud_reconnect", target_mode="cloud",
                     snapshot=crypto.encrypt(candidate.model_dump_json()), command_id=None,
                     credential_revision=settings.revision + 1, error_code="",
                     drain_wait_started_at=None, verification_started_at=None)
        row.values = state
        return
    if not managed:
        if candidate.bot_api_mode != "cloud" and state.get("action") == "cloud_reconnect":
            state.update(phase="idle", action=None, operation_id=None, snapshot=None, error_code="")
            row.values = state
        return
    network_changed = proxy_changed and candidate.bot_api_mode == "local"
    if changed or network_changed or connection_changed and state.get("phase") in {"failed", "blocked"} or token_changed and candidate.bot_token and state.get("applied_mode") == "local":
        old_token = os.getenv("TGD_BOT_TOKEN", crypto.decrypt(old.get("bot_token", ""))) or candidate.bot_token
        cloud_snapshot = candidate.model_copy(update={"bot_token": old_token, "proxy": UserSettings.model_validate(
            {"proxy": json.loads(os.environ["TGD_PROXY"]) if "TGD_PROXY" in os.environ else old_proxy}).proxy,
            "bot_api_mode": "cloud"})
        state.update(operation_id=uid(), phase="draining", action="switch", target_mode=candidate.bot_api_mode,
                     snapshot=crypto.encrypt(candidate.model_dump_json()), command_id=None,
                     credential_revision=settings.revision + 1, error_code="",
                     cloud_snapshot=crypto.encrypt(cloud_snapshot.model_dump_json()),
                     drain_wait_started_at=None, verification_started_at=None)
        if network_changed and not changed and not token_changed and state.get("applied_mode") == "local" and not state.get("logout_uncertain"):
            state["action"] = "restart"
        if token_changed and candidate.bot_token:
            # A corrected/replaced credential must preflight and log out that
            # target Bot, rather than repeatedly retry a rejected old token.
            state.update(logout_done=False, logout_required=True,
                         cloud_snapshot=crypto.encrypt(candidate.model_copy(update={"bot_api_mode": "cloud"}).model_dump_json()))
        row.values = state


def private_bytes(path: Path, limit: int = 16384) -> bytes:
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        named = path.lstat()
        if (not stat.S_ISREG(info.st_mode) or not stat.S_ISREG(named.st_mode) or info.st_nlink != 1
                or (info.st_dev, info.st_ino) != (named.st_dev, named.st_ino) or info.st_size > limit):
            raise ValueError("Invalid control file")
        if os.name != "nt" and (info.st_uid != getattr(os, "getuid", lambda: -1)() or info.st_mode & 0o077):
            raise ValueError("Invalid control file permissions")
        raw = stream.read(limit + 1)
        if len(raw) > limit:
            raise ValueError("Invalid control file")
        return raw


def sync_directory(directory: Path) -> None:
    if os.name == "nt":
        return
    fd = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def private_json(path: Path) -> dict[str, Any]:
    result = json.loads(private_bytes(path))
    if not isinstance(result, dict):
        raise ValueError("Invalid control file")
    return result


def supervisor_status(config: Any) -> dict[str, Any]:
    try:
        value = private_json(config.local_api_control_dir / "applied.json")
        heartbeat = value["heartbeat_at"]
        if (value.get("version") != 1 or not isinstance(heartbeat, (int, float)) or isinstance(heartbeat, bool)
                or not math.isfinite(heartbeat) or not -5 <= time.time() - heartbeat <= 15
                or value.get("state") not in {"waiting_credentials", "stopped", "starting", "running", "stopping", "failed"}
                or value.get("generation") is not None and (not isinstance(value["generation"], str) or len(value["generation"]) > 100)):
            return {}
        return value
    except (OSError, ValueError, KeyError, TypeError):
        return {}


class LocalApiService:
    def __init__(self, settings: Any) -> None:
        from app.services.diagnostics import DiagnosticLog

        self.diagnostics: DiagnosticLog | None = None
        self.settings, self.db, self.config = settings, settings.db, settings.config
        self._read_query = select(LocalApiRuntime.values).where(LocalApiRuntime.id == 1)
        with self.settings.connection_publication_lock, self.db.transaction() as session:
            row = session.get(LocalApiRuntime, 1)
            if not row:
                state: dict[str, Any] = {"applied_mode": "cloud", "phase": "idle"}
                session.add(LocalApiRuntime(id=1, values=state))
            elif row.values.get("phase") == "logging_out":
                row.values = dict(row.values) | {"phase": "logout_uncertain", "logout_uncertain": True}

    def read(self) -> dict[str, Any]:
        # No state cache: transitions and ownership boundaries always observe the
        # current committed row, without constructing an ORM identity-map entry.
        with self.db.engine.connect() as connection:
            values = connection.scalar(self._read_query)
            return values if values is not None else {"applied_mode": "cloud", "phase": "idle"}

    def update(self, *, diagnostic_error: BaseException | None = None, **updates: Any) -> dict[str, Any]:
        with self.settings.connection_publication_lock, self.db.transaction() as session:
            row = session.get(LocalApiRuntime, 1)
            assert row
            prior = dict(row.values)
            row.values = prior | updates
            values = dict(row.values)
        if self.diagnostics and "phase" in updates:
            code = classify_error(diagnostic_error)[0] if diagnostic_error is not None else None
            reason = None
            if updates.get("error_code") == "LOCAL_API_CONNECTION_FAILED" or code == "NETWORK_ERROR":
                code = "NETWORK_ERROR"
                reason = classify_network_failure(diagnostic_error)[0] if diagnostic_error is not None else "network_unknown"
            elif code in {"RATE_LIMITED", "UNAUTHORIZED", "BOT_REQUEST_PAUSED"}:
                reason = ("cooldown" if code == "RATE_LIMITED" else "auth_paused" if code == "UNAUTHORIZED"
                          else getattr(diagnostic_error, "reason", None))
            self.diagnostics.emit("local_api_transition", dedupe=True, level="info",
                                  outcome=updates["phase"], mode=values.get("target_mode"),
                                  error_code=code, reason=reason,
                                  stage=prior.get("phase") if updates["phase"] in {"failed", "blocked", "logout_uncertain"} else updates["phase"],
                                  action=values.get("action") or prior.get("action"),
                                  operation_id=values.get("operation_id") or prior.get("operation_id"))
        return values

    def applied(self) -> dict[str, Any]:
        return supervisor_status(self.config)

    def status(self, *, settings: UserSettings | None = None, state: dict[str, Any] | None = None,
               applied: dict[str, Any] | None = None) -> dict[str, Any]:
        if settings is None:
            _, settings, _ = self.settings.read()
        state = self.read() if state is None else state
        applied = (self.applied() if self.config.managed_local_api else {}) if applied is None else applied
        phase = state.get("phase", "idle")
        available = bool(applied)
        cloud_reconnect = state.get("action") == "cloud_reconnect"
        message = ERROR_MESSAGES.get(state.get("error_code", ""), MESSAGES.get(phase, MESSAGES["failed"]))
        drain_reason = "connection" if phase == "draining" else None
        if phase == "draining" and not state.get("error_code"):
            with self.db.sessions() as session:
                downloading = session.scalar(select(Job.id).where(
                    Job.state.in_(["fetching", "saving", "cancelling"])).limit(1))
            if downloading:
                drain_reason = "downloads"
                message = "等待当前下载完成后切换连接"
        def iso(value: Any) -> str | None:
            return datetime.fromtimestamp(value, UTC).isoformat() if isinstance(value, (float, int)) else None
        return {"managed": self.config.managed_local_api, "state": applied.get("state", "unavailable"),
                "desired_mode": settings.bot_api_mode, "applied_mode": state.get("applied_mode"),
                "operation_id": state.get("operation_id"), "phase": phase, "drain_reason": drain_reason,
                "message": ("当前部署使用外部管理的本地 API" if not self.config.managed_local_api and not cloud_reconnect and settings.bot_api_mode == "local" else
                            "本地 API 管理组件未连接，请检查部署" if not available and not cloud_reconnect and settings.bot_api_mode == "local" else
                            message),
                "generation": applied.get("generation"), "last_heartbeat_at": iso(applied.get("heartbeat_at")),
                "cloud_available_at": iso(state.get("cloud_available_at")),
                "can_restart": self.config.managed_local_api and available and
                    (settings.bot_api_mode == "local" or phase == "blocked" and state.get("applied_mode") == "local") and
                    phase not in BUSY_PHASES | {"logout_uncertain"},
                "can_retry": (cloud_reconnect or self.config.managed_local_api and available)
                    and phase in {"failed", "blocked", "logout_uncertain"}}

    def action(self, restart: bool, acknowledge: bool = False) -> dict[str, Any]:
        status = self.status()
        cloud_reconnect = self.read().get("action") == "cloud_reconnect"
        if not status["managed"] and not (cloud_reconnect and not restart):
            raise AppError("LOCAL_API_EXTERNAL", "此部署的本地 API 由外部管理", 409)
        if status["state"] == "unavailable" and not cloud_reconnect:
            raise AppError("LOCAL_API_UNAVAILABLE", "本地 API 管理组件未连接，请检查部署", 503)
        with self.settings.connection_publication_lock, self.db.transaction() as session:
            row = session.get(LocalApiRuntime, 1)
            assert row
            state = dict(row.values)
            if state.get("phase") in BUSY_PHASES:
                raise AppError("LOCAL_API_BUSY", "已有操作正在进行", 409)
            if state.get("logout_uncertain") and not acknowledge:
                raise AppError("LOCAL_API_LOGOUT_UNCERTAIN", MESSAGES["logout_uncertain"], 409)
            if restart and status["desired_mode"] != "local" and not (
                    state.get("phase") == "blocked" and state.get("applied_mode") == "local"):
                raise AppError("LOCAL_API_NOT_LOCAL", "请先选择本地 API", 409)
            if restart and state.get("logout_uncertain"):
                raise AppError("LOCAL_API_LOGOUT_UNCERTAIN", MESSAGES["logout_uncertain"], 409)
            if not restart and state.get("phase") == "idle":
                return status
            state.update(phase="draining", command_id=None, error_code="",
                         drain_wait_started_at=None, verification_started_at=None)
            if restart:
                state.update(action="recover_restart" if status["desired_mode"] == "cloud" else "restart",
                             operation_id=uid(), target_mode=status["desired_mode"])
            if state.get("logout_uncertain") and acknowledge:
                state.update(logout_uncertain=False, logout_done=True)
            row.values = state
        if self.diagnostics:
            self.diagnostics.emit("local_api_action", level="info", outcome="queued", stage="draining",
                                  mode=state.get("target_mode"), operation_id=state.get("operation_id"),
                                  action="restart" if restart else "acknowledge_retry" if acknowledge else "retry")
        return self.status()

    def validate_allowed(self, candidate: UserSettings) -> None:
        state = self.read()
        if not self.config.managed_local_api and state.get("action") != "cloud_reconnect":
            return
        phase = state.get("phase", "idle")
        if phase in {"failed", "blocked", "logout_uncertain"}:
            message = ERROR_MESSAGES.get(state.get("error_code", ""), MESSAGES[phase])
            raise AppError("LOCAL_API_BUSY", message + "；请在连接状态中处理后再校验", 409)
        if state.get("phase") != "idle" or candidate.bot_api_mode != state.get("applied_mode"):
            raise AppError("LOCAL_API_BUSY", "正在应用 API 或代理配置，请等待服务操作完成后再校验连接", 409)
        if candidate.bot_api_mode == "local":
            saved = self.settings.read()[1]
            if any(getattr(candidate, field) != getattr(saved, field) for field in (
                    "bot_token", "telegram_api_id", "telegram_api_hash")):
                raise AppError("LOCAL_API_BUSY", "请先保存连接配置并完成切换，再校验本地 Bot", 409)
            revision = self.settings.read()[0]
            if candidate.proxy != saved.proxy or self.settings.proxy_status(revision, candidate)["state"] not in {"active", "disabled"}:
                raise AppError("LOCAL_API_PROXY", "请先保存代理配置并等待网络生效，再校验 Bot", 409)
        if candidate.bot_api_mode == "cloud" and state.get("cloud_available_at", 0) > time.time():
            raise AppError("LOCAL_API_COOLDOWN", "Telegram 云端仍在冷却中", 409)

    def recovery_candidate(self, saved: UserSettings) -> UserSettings:
        """Permit only a saved-token identity probe, never an API transition.

        Failed cloud preflight must remain recoverable without authorizing a
        logout. Unknown outcomes and incomplete target startup remain blocked;
        a proven running target permits identity recovery without mode commit.
        """
        try:
            self.validate_allowed(saved)
            return saved
        except AppError:
            state = self.read()
            if state.get("error_code") == "LOCAL_API_DRAIN_TIMEOUT":
                raise
            if ((not self.config.managed_local_api and state.get("action") != "cloud_reconnect")
                    or state.get("phase") != "failed" or state.get("logout_uncertain")):
                raise
            if (state.get("applied_mode") == "cloud" and not state.get("logout_done")
                    and state.get("cloud_available_at", 0) <= time.time()):
                return saved.model_copy(update={"bot_api_mode": "cloud"})
            # The target identity can be probed only after the known logout and
            # the exact supervisor command have already completed. This does not
            # commit applied_mode or retry any part of the transition.
            applied = self.applied()
            if (state.get("logout_done") and state.get("target_mode") == "local" and saved.bot_api_mode == "local"
                    and state.get("command_id") and applied.get("command_id") == state["command_id"]
                    and applied.get("state") == "running" and state.get("snapshot")
                    and self.settings.proxy_status(self.settings.read()[0], saved)["state"] == ("active" if saved.proxy.enabled else "disabled")):
                target = UserSettings.model_validate_json(self.settings.secrets.decrypt(state["snapshot"]))
                if all(getattr(saved, field) == getattr(target, field) for field in (
                        "bot_token", "telegram_api_id", "telegram_api_hash")):
                    return saved.model_copy(update={"bot_api_mode": "local"})
            raise

    def control_cipher(self) -> Fernet:
        directory = self.config.local_api_control_dir
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        info = directory.lstat()
        if not stat.S_ISDIR(info.st_mode) or (os.name != "nt" and (
                info.st_uid != getattr(os, "getuid", lambda: -1)() or info.st_mode & 0o077)):
            raise ValueError("Invalid control directory permissions")
        key = directory / "control.key"
        if not key.exists():
            try:
                private_file(key, Fernet.generate_key())
            except FileExistsError:
                pass
            sync_directory(directory)
        return Fernet(private_bytes(key, 128))

    def preflight_control(self) -> None:
        """Check private handoff and shared mount without logging into a Bot."""
        self.control_cipher()
        directory = self.config.local_api_control_dir
        probe = directory / ("preflight-" + uid() + ".new")
        try:
            private_file(probe, b"")
            sync_directory(directory)
        finally:
            probe.unlink(missing_ok=True)
            sync_directory(directory)
        cache = self.config.bot_api_root
        if not stat.S_ISDIR(cache.lstat().st_mode):
            raise ValueError("Invalid API cache directory")
        # The app mount is read-only: this verifies visibility, not API-side
        # write permissions or a live Telegram connection.
        with os.scandir(cache) as entries:
            next(entries, None)

    def command(self, action: str, values: UserSettings, state: dict[str, Any]) -> str:
        command_id = state.get("command_id") or uid()
        directory = self.config.local_api_control_dir
        cipher = self.control_cipher()
        encrypted = cipher.encrypt(values.telegram_api_hash.encode()).decode() if action != "stop" else None
        payload = {"version": 1, "command_id": command_id, "action": action,
                   "credential_revision": state.get("credential_revision", 0),
                   "api_id": int(values.telegram_api_id) if values.telegram_api_id and action != "stop" else None,
                   "api_hash_encrypted": encrypted,
                   "proxy_encrypted": cipher.encrypt(values.proxy.model_dump_json().encode()).decode() if action != "stop" else None,
                   "proxy_signature": self.settings.proxy_signature(values) if action != "stop" else None}
        # Persist intent before delivery, so recovery repeats exactly the same command.
        self.update(command_id=command_id, command_issued_at=(state.get("command_issued_at", time.time())
                    if state.get("command_id") else time.time()))
        temporary = directory / ("desired-" + uid() + ".new")
        try:
            private_file(temporary, json.dumps(payload).encode())
            os.replace(temporary, directory / "desired.json")
            sync_directory(directory)
        finally:
            temporary.unlink(missing_ok=True)
        return command_id

    async def reconcile(self, runtime: Any) -> bool:
        """Return true while consumers must remain paused. Never cancel file owners."""
        import asyncio

        from app.bot.client import TelegramClient
        state = await asyncio.to_thread(self.read)
        if not self.config.managed_local_api and state.get("action") != "cloud_reconnect":
            return False
        if state.get("logout_uncertain") and state.get("phase") != "logout_uncertain":
            await asyncio.to_thread(self.update, phase="logout_uncertain")
            return True
        revision, current, _ = await asyncio.to_thread(self.settings.read)
        if state.get("phase") == "idle":
            if current.bot_api_mode != state.get("applied_mode"):
                await asyncio.to_thread(self.update, phase="draining", operation_id=uid(), action="switch",
                                        target_mode=current.bot_api_mode, snapshot=self.settings.secrets.encrypt(current.model_dump_json()),
                                        credential_revision=revision, command_id=None,
                                        drain_wait_started_at=None, verification_started_at=None)
                return True
            return False
        if state.get("phase") in {"failed", "blocked", "logout_uncertain"}:
            # Operation failure is projected separately from transport evidence.
            return True
        try:
            values = UserSettings.model_validate_json(self.settings.secrets.decrypt(state["snapshot"])) if state.get("snapshot") else current
            target = state.get("target_mode", values.bot_api_mode)
            active, blocked, _ = await asyncio.to_thread(runtime.counts)
            owners = any(not task.done() for task in runtime.running.values())
            if owners or active > blocked:
                if state.get("drain_wait_started_at") is not None or state.get("verification_started_at") is not None:
                    await asyncio.to_thread(self.update, drain_wait_started_at=None, verification_started_at=None)
                return True
            restart = state.get("action") in {"restart", "recover_restart"}
            if blocked and not restart:
                await asyncio.to_thread(self.update, phase="blocked")
                return True
            async with waits.consumers(runtime.poll_lock, runtime.notify_lock):
                # Network owners have finished and persisted their update/outbox result.
                if state.get("drain_wait_started_at") is not None:
                    await asyncio.to_thread(self.update, drain_wait_started_at=None)
                if state.get("action") == "cloud_reconnect":
                    if state.get("cloud_available_at", 0) > time.time():
                        await asyncio.to_thread(self.update, phase="cooldown")
                        return True
                    await asyncio.to_thread(self.update, phase="verifying")
                    await self.prepare_verification(runtime, values.bot_token)
                    await runtime.ensure_client(reconnect_operation=state["operation_id"])
                    if values.bot_token and (runtime.client is None or runtime.bot_status["state"] != "online"
                            or runtime.client_signature != self.settings.client_signature(values)):
                        if runtime.safety.status(values.bot_token)["state"] == "auth_paused":
                            await asyncio.to_thread(self.update, phase="failed")
                        return True
                    await asyncio.to_thread(self.update, applied_mode="cloud", phase="idle", action=None,
                                            operation_id=None, snapshot=None, error_code="")
                    return False
                runtime.bot_status = {"state": "connecting", "username": "", "message": MESSAGES.get(state["phase"], "正在切换 API")}
                if runtime.client:
                    await waits.network(runtime.client.close(), waits.CLOSE_TIMEOUT)
                    runtime.client, runtime.bot_id = None, None
                    runtime.client_signature = ""
                applied = await asyncio.to_thread(self.applied)
                if not applied:
                    raise AppError("LOCAL_API_UNAVAILABLE", "管理组件未连接")
                if (target == "local" or restart) and (not values.telegram_api_id or not values.telegram_api_hash):
                    raise AppError("LOCAL_API_CREDENTIALS", "请填写 API ID 和 API Hash")
                if target == "local" and not state.get("command_id"):
                    await asyncio.to_thread(self.preflight_control)
                if target == "local" and (state.get("applied_mode") == "cloud" or state.get("logout_required")) and not state.get("logout_done"):
                    # Irreversible intent is durable before the remote call. Any crash becomes uncertain.
                    cloud = (UserSettings.model_validate_json(self.settings.secrets.decrypt(state["cloud_snapshot"]))
                             if state.get("cloud_snapshot") else values.model_copy(update={"bot_api_mode": "cloud"}))
                    client = TelegramClient(cloud.bot_token, self.config, cloud)
                    client.safety = runtime.safety
                    client.diagnostic_action = state.get("action")
                    client.diagnostic_operation_id = state.get("operation_id")
                    try:
                        # Known credential/proxy errors fail before the logout attempt.
                        await self.prepare_verification(runtime, cloud.bot_token)
                        await waits.network(client.identity(), waits.IDENTITY_TIMEOUT)
                        await asyncio.to_thread(self.update, phase="logging_out", logout_uncertain=True,
                                                cloud_available_at=time.time() + 630)
                        if not await waits.network(client.logout(), waits.LOGOUT_TIMEOUT):
                            raise RuntimeError("Logout not acknowledged")
                    except (SafetyDeferred, TelegramRetryAfter, TelegramUnauthorizedError):
                        # A local rejection is provably unsent; a Telegram 429/401 is
                        # an explicit rejection, never an unknown logout outcome.
                        auth_paused = (await asyncio.to_thread(runtime.safety.status, cloud.bot_token))["state"] == "auth_paused"
                        await asyncio.to_thread(self.update, phase="failed" if auth_paused else "draining", logout_uncertain=False,
                                                cloud_available_at=state.get("cloud_available_at", 0))
                        return True
                    except Exception as exc:
                        uncertain = (await asyncio.to_thread(self.read)).get("logout_uncertain", False)
                        await asyncio.to_thread(self.update, phase="logout_uncertain" if uncertain else "failed",
                                                error_code="" if uncertain else "LOCAL_API_PREFLIGHT_FAILED", diagnostic_error=exc)
                        return True
                    finally:
                        await waits.close_candidate(client)
                    state = await asyncio.to_thread(self.update, phase="starting", logout_done=True,
                                                    logout_uncertain=False, cloud_available_at=time.time() + 600)
                if target == "cloud" and not restart and state.get("cloud_available_at", 0) > time.time():
                    await asyncio.to_thread(self.update, phase="cooldown")
                    return True
                action = "restart" if restart else "start" if target == "local" else "stop"
                if not state.get("command_id"):
                    await asyncio.to_thread(self.update, phase="starting")
                    command_id = await asyncio.to_thread(self.command, action, values, state)
                    state = await asyncio.to_thread(self.read)
                else:
                    command_id = state["command_id"]
                    # Re-deliver missing desired file after a crash between intent/delivery.
                    try:
                        desired = await asyncio.to_thread(private_json, self.config.local_api_control_dir / "desired.json")
                    except (OSError, ValueError):
                        desired = {}
                    if desired.get("command_id") != command_id:
                        await asyncio.to_thread(self.command, action, values, state)
                issued = state.get("command_issued_at", time.time())
                if (applied.get("command_id") != command_id or
                        applied.get("credential_revision") != state.get("credential_revision", 0)):
                    if time.time() - issued > 60:
                        raise AppError("LOCAL_API_COMMAND_TIMEOUT", "服务未确认操作")
                    return True
                if applied["state"] == "failed":
                    raise AppError("LOCAL_API_START_FAILED", "本地 API 进程启动失败")
                if target == "local" and (applied.get("proxy_signature") != self.settings.proxy_signature(values) or not applied.get("generation")):
                    if time.time() - issued > 60:
                        raise AppError("LOCAL_API_COMMAND_TIMEOUT", "服务未确认网络配置与进程")
                    return True
                if applied["state"] != ("running" if target == "local" or restart else "stopped"):
                    if time.time() - issued > 150:
                        raise AppError("LOCAL_API_COMMAND_TIMEOUT", "服务未完成操作")
                    return True
                if restart and blocked:
                    # Generation file is written by the supervisor only after reaping the old process.
                    await asyncio.to_thread(runtime.recover_generation)
                    if (await asyncio.to_thread(runtime.counts))[1]:
                        if time.time() - issued > waits.GENERATION_TIMEOUT:
                            await asyncio.to_thread(self.update, phase="blocked")
                        return True
                if state.get("action") == "recover_restart":
                    await asyncio.to_thread(self.update, phase="draining", action="switch", command_id=None)
                    return True
                await asyncio.to_thread(self.update, phase="verifying")
                if values.bot_token:
                    candidate = values.model_copy(update={"bot_api_mode": target})
                    client = TelegramClient(candidate.bot_token, self.config, candidate)
                    client.safety = runtime.safety
                    client.diagnostic_action = state.get("action")
                    client.diagnostic_operation_id = state.get("operation_id")
                    try:
                        await self.prepare_verification(runtime, values.bot_token)
                        identity = await waits.network(client.identity(), waits.IDENTITY_TIMEOUT)
                        await asyncio.to_thread(runtime.remember_identity, identity)
                    except Exception as exc:
                        if target == "local" and (classify_error(exc)[0] == "NETWORK_ERROR"
                                or isinstance(exc, AppError) and exc.code == "LOCAL_API_OPERATION_TIMEOUT"):
                            raise AppError("LOCAL_API_CONNECTION_FAILED", ERROR_MESSAGES["LOCAL_API_CONNECTION_FAILED"]) from exc
                        raise
                    finally:
                        await waits.close_candidate(client)
                await asyncio.to_thread(self.update, applied_mode=target, phase="idle", operation_id=None,
                                        applied_command_id=command_id, applied_credential_revision=state.get("credential_revision", 0),
                                        applied_generation=applied.get("generation"),
                                        command_id=None, snapshot=None, cloud_snapshot=None, logout_done=target == "local", logout_required=False, error_code="")
                return False
        except waits.ConsumerBusy:
            started = state.get("drain_wait_started_at")
            if started is None:
                await asyncio.to_thread(self.update, drain_wait_started_at=time.time())
            elif time.time() - started >= waits.BARRIER_TIMEOUT:
                await asyncio.to_thread(self.update, phase="failed", error_code="LOCAL_API_DRAIN_TIMEOUT")
            return True
        except (SafetyDeferred, TelegramRetryAfter, TelegramUnauthorizedError):
            if (await asyncio.to_thread(runtime.safety.status, values.bot_token))["state"] == "auth_paused":
                await asyncio.to_thread(self.update, phase="failed")
            return True
        except Exception as exc:
            latest = await asyncio.to_thread(self.read)
            await asyncio.to_thread(self.update, phase="logout_uncertain" if latest.get("logout_uncertain") else "failed",
                                    error_code=exc.code if isinstance(exc, AppError) else "LOCAL_API_OPERATION_FAILED",
                                    diagnostic_error=exc)
            return True

    async def prepare_verification(self, runtime: Any, token: str) -> None:
        """Limit no-progress retries while excluding official request-protection waits."""
        import asyncio

        state = await asyncio.to_thread(self.read)
        now = time.time()
        if state.get("verification_started_at") is None:
            await asyncio.to_thread(self.update, verification_started_at=now)
        elif now - state["verification_started_at"] >= waits.VERIFY_TIMEOUT:
            safety = await asyncio.to_thread(runtime.safety.status, token)
            if safety.get("retry_at"):
                await asyncio.to_thread(self.update, verification_started_at=now)
            else:
                raise AppError("LOCAL_API_VERIFY_TIMEOUT", ERROR_MESSAGES["LOCAL_API_VERIFY_TIMEOUT"])
