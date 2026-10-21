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
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.core.security import Secrets, private_file
from app.db.models import LocalApiRuntime, Settings, uid
from app.schemas.contracts import UserSettings
from app.services.diagnostics import classify_error
from app.services.request_safety import SafetyDeferred

BUSY_PHASES = {"draining", "waiting_network", "logging_out", "starting", "verifying", "cooldown"}
MESSAGES = {"idle": "", "draining": "等待当前下载和消息处理完成", "logging_out": "正在退出云端 Bot API",
            "starting": "正在应用本地 API 服务配置", "verifying": "正在验证 Bot 连接", "cooldown": "等待 Telegram 云端冷却结束",
            "blocked": "存在恢复待处理任务，请先重启本地 API 恢复", "logout_uncertain": "云端退出结果不确定；请核实退出后明确确认继续，系统不会自动重试退出或回连云端",
            "failed": "操作未完成，请检查凭据、网络或代理后重试"}
MESSAGES["waiting_network"] = "正在应用网络配置，等待直连或代理就绪"
ERROR_MESSAGES = {"LOCAL_API_CREDENTIALS": "请填写 API ID 和 API Hash 后保存配置",
                  "LOCAL_API_UNAVAILABLE": "本地 API 管理组件未连接，请检查部署",
                  "LOCAL_API_PROXY": "网络配置尚未生效，请检查网络服务、代理地址和端口后重试；无需更换部署文件",
                  "LOCAL_API_START_FAILED": "本地 API 进程启动失败，请检查凭据和网络后重试",
                  "LOCAL_API_COMMAND_TIMEOUT": "本地 API 操作等待超时，请检查服务状态后重试",
                  "LOCAL_API_CONNECTION_FAILED": "本地 API 进程已启动，但 Bot 连接验证失败；请检查应用与本地 API 之间的连接，以及本地 API 的 Telegram 网络或代理。进程运行不代表 Bot 已就绪，排查后可手动重试",
                  "LOCAL_API_ACTIVATION_REQUIRED": "已保留原本地 API 设置；请点击重试操作确认启用 WebUI 管理，之后才会执行云端退出和连接",
                  "LOCAL_API_PREFLIGHT_FAILED": "云端 Bot 预检查失败，请检查 Token 和云端代理后重试"}


def schedule_settings(session: Session, settings: Settings, candidate: UserSettings, crypto: Secrets) -> None:
    row = session.get(LocalApiRuntime, 1)
    if not row:
        row = LocalApiRuntime(id=1, values={"applied_mode": "cloud", "phase": "idle"})
        session.add(row)
    state = dict(row.values)
    old = dict(settings.values)
    old_hash = crypto.decrypt(old.get("telegram_api_hash", ""))
    changed = (candidate.bot_api_mode != old.get("bot_api_mode", "local") or
               candidate.telegram_api_id != old.get("telegram_api_id", os.getenv("TELEGRAM_API_ID", "")) or
               candidate.telegram_api_hash != (old_hash if "telegram_api_hash" in old else os.getenv("TELEGRAM_API_HASH", "")))
    old_proxy = UserSettings().proxy.model_dump() | old.get("proxy", {})
    old_proxy["password"] = crypto.decrypt(old_proxy["password"])
    if "TGD_PROXY" in os.environ:
        old_proxy = json.loads(os.environ["TGD_PROXY"])
    connection_changed = (candidate.bot_token != os.getenv("TGD_BOT_TOKEN", crypto.decrypt(old.get("bot_token", ""))) or
                          candidate.proxy.model_dump() != old_proxy)
    token_changed = candidate.bot_token != os.getenv("TGD_BOT_TOKEN", crypto.decrypt(old.get("bot_token", "")))
    if (token_changed and (os.getenv("TGD_BOT_TOKEN") or old.get("bot_token")) and
            candidate.bot_api_mode != old.get("bot_api_mode", "local")):
        raise AppError("LOCAL_API_BUSY", "请先在当前模式保存 Bot Token，再单独切换 API 模式", 409)
    if state.get("phase", "idle") != "idle" and state.get("logout_done") and token_changed:
        raise AppError("LOCAL_API_BUSY", "请先完成当前 Bot 的本地连接，再更换 Bot Token", 409)
    if state.get("phase", "idle") in BUSY_PHASES | {"logout_uncertain"} and (changed or connection_changed):
        raise AppError("LOCAL_API_BUSY", "本地 API 操作尚未完成，请完成或重试当前操作后修改连接配置", 409)
    network_changed = candidate.proxy.model_dump() != old_proxy and candidate.bot_api_mode == "local"
    if changed or network_changed or connection_changed and state.get("phase") in {"failed", "blocked"} or token_changed and candidate.bot_token and state.get("applied_mode") == "local":
        old_token = os.getenv("TGD_BOT_TOKEN", crypto.decrypt(old.get("bot_token", ""))) or candidate.bot_token
        cloud_snapshot = candidate.model_copy(update={"bot_token": old_token, "proxy": UserSettings.model_validate(
            {"proxy": json.loads(os.environ["TGD_PROXY"]) if "TGD_PROXY" in os.environ else old_proxy}).proxy,
            "bot_api_mode": "cloud"})
        state.update(operation_id=uid(), phase="draining", action="switch", target_mode=candidate.bot_api_mode,
                     snapshot=crypto.encrypt(candidate.model_dump_json()), command_id=None,
                     credential_revision=settings.revision + 1, error_code="",
                     cloud_snapshot=crypto.encrypt(cloud_snapshot.model_dump_json()))
        if network_changed and not changed and not token_changed and state.get("applied_mode") == "local" and not state.get("logout_uncertain"):
            state["action"] = "network"
        state.update(network_wait_started_at=None, network_retry=False)
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
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
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


class LocalApiService:
    def __init__(self, settings: Any) -> None:
        from app.services.diagnostics import DiagnosticLog

        self.diagnostics: DiagnosticLog | None = None
        self.settings, self.db, self.config = settings, settings.db, settings.config
        if self.config.managed_local_api:
            initial = self.settings.read()[1]
            with self.db.transaction() as session:
                row = session.get(LocalApiRuntime, 1)
                if not row:
                    state: dict[str, Any] = {"applied_mode": "cloud", "phase": "idle"}
                    if initial.bot_api_mode == "local":
                        # An old external deployment may already own this Bot.
                        # Installing management is not permission to log it out.
                        state.update(applied_mode=None, phase="failed", operation_id=uid(), action="switch",
                                     target_mode="local", logout_required=True,
                                     error_code="LOCAL_API_ACTIVATION_REQUIRED", command_id=None,
                                     snapshot=self.settings.secrets.encrypt(initial.model_dump_json()))
                    session.add(LocalApiRuntime(id=1, values=state))
                elif row.values.get("phase") == "logging_out":
                    row.values = dict(row.values) | {"phase": "logout_uncertain", "logout_uncertain": True}

    def read(self) -> dict[str, Any]:
        with self.db.sessions() as session:
            row = session.get(LocalApiRuntime, 1)
            return dict(row.values) if row else {"applied_mode": "cloud", "phase": "idle"}

    def update(self, **updates: Any) -> dict[str, Any]:
        with self.db.transaction() as session:
            row = session.get(LocalApiRuntime, 1)
            assert row
            row.values = dict(row.values) | updates
            values = dict(row.values)
        if self.diagnostics and "phase" in updates:
            self.diagnostics.emit("local_api_transition", dedupe=True, level="info",
                                  outcome=updates["phase"], mode=values.get("target_mode"),
                                  error_code="NETWORK_ERROR" if updates.get("error_code") == "LOCAL_API_CONNECTION_FAILED" else None)
        return values

    def applied(self) -> dict[str, Any]:
        try:
            value = private_json(self.config.local_api_control_dir / "applied.json")
            heartbeat = value["heartbeat_at"]
            if (value.get("version") != 1 or not isinstance(heartbeat, (int, float)) or isinstance(heartbeat, bool)
                    or not math.isfinite(heartbeat) or not -5 <= time.time() - heartbeat <= 15
                    or value.get("state") not in {"waiting_credentials", "stopped", "starting", "running", "stopping", "failed"}
                    or value.get("generation") is not None and (not isinstance(value["generation"], str) or len(value["generation"]) > 100)):
                return {}
            return value
        except (OSError, ValueError, KeyError, TypeError):
            return {}

    def status(self) -> dict[str, Any]:
        _, settings, _ = self.settings.read()
        state, applied = self.read(), self.applied() if self.config.managed_local_api else {}
        phase = state.get("phase", "idle")
        available = bool(applied)
        def iso(value: Any) -> str | None:
            return datetime.fromtimestamp(value, UTC).isoformat() if isinstance(value, (float, int)) else None
        return {"managed": self.config.managed_local_api, "state": applied.get("state", "unavailable"),
                "desired_mode": settings.bot_api_mode, "applied_mode": state.get("applied_mode"),
                "operation_id": state.get("operation_id"), "phase": phase,
                "message": ("当前部署使用外部管理的本地 API" if not self.config.managed_local_api else
                            "本地 API 管理组件未连接，请检查部署" if not available else
                            ERROR_MESSAGES.get(state.get("error_code", ""), MESSAGES.get(phase, MESSAGES["failed"]))),
                "generation": applied.get("generation"), "last_heartbeat_at": iso(applied.get("heartbeat_at")),
                "cloud_available_at": iso(state.get("cloud_available_at")),
                "can_restart": self.config.managed_local_api and available and
                    (settings.bot_api_mode == "local" or phase == "blocked" and state.get("applied_mode") == "local") and
                    phase not in BUSY_PHASES | {"logout_uncertain"} and state.get("error_code") != "LOCAL_API_ACTIVATION_REQUIRED",
                "can_retry": self.config.managed_local_api and available and phase in {"failed", "blocked", "logout_uncertain"}}

    def action(self, restart: bool, acknowledge: bool = False) -> dict[str, Any]:
        status = self.status()
        if not status["managed"]:
            raise AppError("LOCAL_API_EXTERNAL", "此部署的本地 API 由外部管理", 409)
        if status["state"] == "unavailable":
            raise AppError("LOCAL_API_UNAVAILABLE", "本地 API 管理组件未连接，请检查部署", 503)
        with self.db.transaction() as session:
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
            if restart and state.get("error_code") == "LOCAL_API_ACTIVATION_REQUIRED":
                raise AppError("LOCAL_API_ACTIVATION_REQUIRED", ERROR_MESSAGES["LOCAL_API_ACTIVATION_REQUIRED"], 409)
            if restart and state.get("logout_uncertain"):
                raise AppError("LOCAL_API_LOGOUT_UNCERTAIN", MESSAGES["logout_uncertain"], 409)
            if not restart and state.get("phase") == "idle":
                return status
            state.update(phase="draining", command_id=None, error_code="", network_retry=True, network_wait_started_at=None)
            if restart:
                state.update(action="recover_restart" if status["desired_mode"] == "cloud" else "restart",
                             operation_id=uid(), target_mode=status["desired_mode"])
            if state.get("logout_uncertain") and acknowledge:
                state.update(logout_uncertain=False, logout_done=True)
            row.values = state
        return self.status()

    def validate_allowed(self, candidate: UserSettings) -> None:
        if not self.config.managed_local_api:
            return
        state = self.read()
        if state.get("phase") != "idle" or candidate.bot_api_mode != state.get("applied_mode"):
            raise AppError("LOCAL_API_BUSY", "请等待 API 切换完成后校验连接", 409)
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
            if not self.config.managed_local_api or state.get("phase") != "failed" or state.get("logout_uncertain"):
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
                   "api_hash_encrypted": encrypted}
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
        if not self.config.managed_local_api:
            return False
        state = await asyncio.to_thread(self.read)
        revision, current, _ = await asyncio.to_thread(self.settings.read)
        if state.get("phase") == "idle":
            # Includes a legacy installation whose saved desired mode is local.
            if current.bot_api_mode != state.get("applied_mode"):
                await asyncio.to_thread(self.update, phase="draining", operation_id=uid(), action="switch",
                                        target_mode=current.bot_api_mode, snapshot=self.settings.secrets.encrypt(current.model_dump_json()),
                                        credential_revision=0, command_id=None)
                return True
            return False
        if state.get("phase") in {"failed", "blocked", "logout_uncertain"}:
            runtime.bot_status = {"state": "error", "username": "", "message":
                                  ERROR_MESSAGES.get(state.get("error_code", ""), MESSAGES[state["phase"]])}
            return True
        try:
            values = UserSettings.model_validate_json(self.settings.secrets.decrypt(state["snapshot"])) if state.get("snapshot") else current
            target = state.get("target_mode", values.bot_api_mode)
            active, blocked, _ = await asyncio.to_thread(runtime.counts)
            owners = any(not task.done() for task in runtime.running.values())
            if owners or active > blocked:
                return True
            restart = state.get("action") in {"restart", "recover_restart"}
            if blocked and not restart:
                await asyncio.to_thread(self.update, phase="blocked")
                return True
            async with runtime.poll_lock, runtime.notify_lock:
                # Network owners have finished and persisted their update/outbox result.
                runtime.bot_status = {"state": "connecting", "username": "", "message": MESSAGES.get(state["phase"], "正在切换 API")}
                if runtime.client:
                    await runtime.client.close()
                    runtime.client, runtime.bot_id = None, None
                    runtime.client_signature = ""
                applied = await asyncio.to_thread(self.applied)
                if not applied:
                    raise AppError("LOCAL_API_UNAVAILABLE", "管理组件未连接")
                if (target == "local" or restart) and (not values.telegram_api_id or not values.telegram_api_hash):
                    raise AppError("LOCAL_API_CREDENTIALS", "请填写 API ID 和 API Hash")
                if target == "local" and (self.config.proxy_capable or values.proxy.enabled):
                    frozen_revision = state.get("credential_revision", revision)
                    await asyncio.to_thread(self.settings.write_proxy_desired, frozen_revision, values,
                                            force=bool(state.get("network_retry")))
                    if state.get("network_retry"):
                        state = await asyncio.to_thread(self.update, network_retry=False)
                    proxy = await asyncio.to_thread(self.settings.proxy_status, frozen_revision, values)
                    if proxy["state"] in {"pending", "applying"}:
                        if not state.get("network_wait_started_at"):
                            await asyncio.to_thread(self.update, phase="waiting_network", network_wait_started_at=time.time())
                        elif time.time() - state["network_wait_started_at"] > 90:
                            raise AppError("LOCAL_API_PROXY", "网络组件未确认配置，请检查服务后重试")
                        return True
                    if proxy["state"] != ("active" if values.proxy.enabled else "disabled"):
                        raise AppError("LOCAL_API_PROXY", "等待本地代理配置生效")
                if target == "local" and not state.get("command_id"):
                    await asyncio.to_thread(self.preflight_control)
                if target == "local" and (state.get("applied_mode") == "cloud" or state.get("logout_required")) and not state.get("logout_done"):
                    # Irreversible intent is durable before the remote call. Any crash becomes uncertain.
                    cloud = (UserSettings.model_validate_json(self.settings.secrets.decrypt(state["cloud_snapshot"]))
                             if state.get("cloud_snapshot") else values.model_copy(update={"bot_api_mode": "cloud"}))
                    client = TelegramClient(cloud.bot_token, self.config, cloud)
                    client.safety = runtime.safety
                    try:
                        # Known credential/proxy errors fail before the logout attempt.
                        await client.identity()
                        await asyncio.to_thread(self.update, phase="logging_out", logout_uncertain=True,
                                                cloud_available_at=time.time() + 630)
                        if not await client.logout():
                            raise RuntimeError("Logout not acknowledged")
                    except (SafetyDeferred, TelegramRetryAfter, TelegramUnauthorizedError):
                        # A local rejection is provably unsent; a Telegram 429/401 is
                        # an explicit rejection, never an unknown logout outcome.
                        auth_paused = (await asyncio.to_thread(runtime.safety.status, cloud.bot_token))["state"] == "auth_paused"
                        await asyncio.to_thread(self.update, phase="failed" if auth_paused else "draining", logout_uncertain=False,
                                                cloud_available_at=state.get("cloud_available_at", 0))
                        return True
                    except Exception:
                        uncertain = (await asyncio.to_thread(self.read)).get("logout_uncertain", False)
                        await asyncio.to_thread(self.update, phase="logout_uncertain" if uncertain else "failed",
                                                error_code="" if uncertain else "LOCAL_API_PREFLIGHT_FAILED")
                        return True
                    finally:
                        await client.close()
                    state = await asyncio.to_thread(self.update, phase="starting", logout_done=True,
                                                    logout_uncertain=False, cloud_available_at=time.time() + 600)
                if target == "cloud" and not restart and state.get("cloud_available_at", 0) > time.time():
                    await asyncio.to_thread(self.update, phase="cooldown")
                    return True
                action = "restart" if restart else "start" if target == "local" else "stop"
                if state.get("action") == "network":
                    # Network-only edits keep the child/generation; never log out or restart it.
                    command_id = applied.get("command_id")
                    if applied.get("state") != "running" or not command_id:
                        raise AppError("LOCAL_API_START_FAILED", "本地 API 服务未运行，请手动重启")
                elif not state.get("command_id"):
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
                if applied.get("command_id") != command_id:
                    if time.time() - issued > 60:
                        raise AppError("LOCAL_API_COMMAND_TIMEOUT", "服务未确认操作")
                    return True
                if applied["state"] == "failed":
                    raise AppError("LOCAL_API_START_FAILED", "本地 API 进程启动失败")
                if applied["state"] != ("running" if target == "local" or restart else "stopped"):
                    if time.time() - issued > 150:
                        raise AppError("LOCAL_API_COMMAND_TIMEOUT", "服务未完成操作")
                    return True
                if restart and blocked:
                    # Generation file is written by the supervisor only after reaping the old process.
                    await asyncio.to_thread(runtime.recover_generation)
                    if (await asyncio.to_thread(runtime.counts))[1]:
                        return True
                if state.get("action") == "recover_restart":
                    await asyncio.to_thread(self.update, phase="draining", action="switch", command_id=None)
                    return True
                await asyncio.to_thread(self.update, phase="verifying")
                if values.bot_token:
                    candidate = values.model_copy(update={"bot_api_mode": target})
                    client = TelegramClient(candidate.bot_token, self.config, candidate)
                    client.safety = runtime.safety
                    try:
                        identity = await client.identity()
                        await asyncio.to_thread(runtime.remember_identity, identity)
                    except Exception as exc:
                        if target == "local" and classify_error(exc)[0] == "NETWORK_ERROR":
                            raise AppError("LOCAL_API_CONNECTION_FAILED", ERROR_MESSAGES["LOCAL_API_CONNECTION_FAILED"]) from exc
                        raise
                    finally:
                        await client.close()
                await asyncio.to_thread(self.update, applied_mode=target, phase="idle", operation_id=None,
                                        command_id=None, snapshot=None, cloud_snapshot=None, logout_done=target == "local", logout_required=False, error_code="")
                return False
        except (SafetyDeferred, TelegramRetryAfter, TelegramUnauthorizedError):
            if (await asyncio.to_thread(runtime.safety.status, values.bot_token))["state"] == "auth_paused":
                await asyncio.to_thread(self.update, phase="failed")
            return True
        except Exception as exc:
            await asyncio.to_thread(self.update, phase="failed", error_code=exc.code if isinstance(exc, AppError) else "LOCAL_API_OPERATION_FAILED")
            return True
