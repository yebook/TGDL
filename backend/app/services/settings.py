import json
import os
from typing import Any

from sqlalchemy import select

from app.core.config import Config
from app.core.errors import AppError
from app.core.security import Secrets
from app.db.models import Job, Settings
from app.db.session import Database
from app.schemas.contracts import SettingsPatch, UserSettings


class SettingsService:
    def __init__(self, database: Database, config: Config, secrets: Secrets) -> None:
        self.db, self.config, self.secrets = database, config, secrets
        self.cloud_applied_signature = ""
        with self.db.transaction() as session:
            row = session.get(Settings, 1)
            if not row:
                session.add(Settings(id=1, values={"bot_api_mode": "cloud"}))
    @staticmethod
    def client_signature(values: UserSettings) -> str:
        from app.core.security import digest
        return digest(json.dumps({"token": values.bot_token, "mode": values.bot_api_mode,
                                  "proxy": values.proxy.model_dump() if values.bot_api_mode == "cloud" else None},
                                 sort_keys=True))

    def read(self) -> tuple[int, UserSettings, dict[str, str]]:
        with self.db.sessions() as session:
            row = session.get(Settings, 1)
            assert row
            revision, stored = row.revision, dict(row.values)
        if stored.get("bot_token"):
            stored["bot_token"] = self.secrets.decrypt(stored["bot_token"])
        if stored.get("telegram_api_hash"):
            stored["telegram_api_hash"] = self.secrets.decrypt(stored["telegram_api_hash"])
        if stored.get("proxy", {}).get("password"):
            stored["proxy"] = dict(stored["proxy"])
            stored["proxy"]["password"] = self.secrets.decrypt(stored["proxy"]["password"])
        values = UserSettings().model_dump() | stored
        sources = {key: "database" if key in stored else "default" for key in values}
        for key in values:
            if key in {"telegram_api_id", "telegram_api_hash"}:
                continue  # Application credentials are managed exclusively in WebUI.
            env = os.getenv("TGD_" + key.upper())
            if env is not None:
                values[key] = json.loads(env) if key in {
                    "confirmation_timeout_seconds", "download_concurrency", "max_auto_retries", "allowed_user_ids", "proxy",
                    "diagnostic_log_retention_days"
                } else env
                sources[key] = "environment"
        return revision, UserSettings.model_validate(values), sources

    def proxy_status(self, revision: int, values: UserSettings) -> dict[str, Any]:
        if values.bot_api_mode == "cloud":
            active = self.cloud_applied_signature == self.client_signature(values)
            return {"state": "disabled" if not values.proxy.enabled else "active" if active else "pending",
                    "desired_revision": revision, "applied_revision": revision if active else None,
                    "message": "" if active or not values.proxy.enabled else "等待云端 Bot 通过代理连接成功"}
        if not self.config.managed_local_api:
            return {"state": "unavailable" if values.proxy.enabled else "disabled", "desired_revision": revision,
                    "applied_revision": None, "message": "代理需要标准部署中的受管本地 API" if values.proxy.enabled else ""}
        from app.db.models import LocalApiRuntime
        from app.services.local_api import supervisor_status
        applied = supervisor_status(self.config)
        with self.db.sessions() as session:
            row = session.get(LocalApiRuntime, 1)
            runtime_state = dict(row.values) if row else {}
        expected_command = runtime_state.get("command_id") or runtime_state.get("applied_command_id")
        expected_revision = runtime_state.get("credential_revision") if runtime_state.get("command_id") else runtime_state.get("applied_credential_revision")
        signature = self.proxy_signature(values)
        ready = (applied.get("state") == "running" and applied.get("proxy_signature") == signature
                 and bool(applied.get("generation")) and bool(expected_command)
                 and applied.get("command_id") == expected_command and applied.get("credential_revision") == expected_revision)
        state = ("active" if values.proxy.enabled else "disabled") if ready else "failed" if applied.get("state") == "failed" else "pending"
        return {"state": state, "desired_revision": revision, "applied_revision": revision if ready else None,
                "message": "" if ready else "等待本地 API 应用网络配置；失败后请检查代理并重试"}

    @staticmethod
    def proxy_signature(values: UserSettings) -> str:
        from app.core.security import digest
        # Disabled is an explicit command that clears TDLib's persisted selection.
        return digest(json.dumps(values.proxy.model_dump() if values.proxy.enabled else {"enabled": False}, sort_keys=True))

    def public(self) -> dict[str, Any]:
        revision, values, sources = self.read()
        data = values.model_dump()
        configured = {"bot_token": bool(values.bot_token), "proxy_password": bool(values.proxy.password),
                      "telegram_api_hash": bool(values.telegram_api_hash)}
        data["bot_token"] = ""
        data["telegram_api_hash"] = ""
        data["proxy"]["password"] = ""
        return {"revision": revision, "values": data, "sources": sources,
                "editable": {key: source != "environment" for key, source in sources.items()},
                "configured": configured, "proxy_status": self.proxy_status(revision, values)}

    def candidate(self, patch: SettingsPatch) -> UserSettings:
        _, current, sources = self.read()
        updates = patch.model_dump(exclude_none=True, exclude={"revision", "clear_bot_token", "clear_proxy_password", "clear_telegram_api_hash"})
        if not updates.get("telegram_api_hash"):
            updates.pop("telegram_api_hash", None)
        if patch.clear_telegram_api_hash:
            updates["telegram_api_hash"] = ""
        for key in updates:
            if sources[key] == "environment":
                raise AppError("READ_ONLY_SETTING", "环境变量覆盖的配置不可修改")
        if not updates.get("bot_token"):
            updates.pop("bot_token", None)
        if patch.clear_bot_token:
            if sources["bot_token"] == "environment":
                raise AppError("READ_ONLY_SETTING", "环境变量覆盖的配置不可修改")
            updates["bot_token"] = ""
        if "proxy" in updates:
            if not updates["proxy"]["password"]:
                updates["proxy"]["password"] = current.proxy.password
        elif patch.clear_proxy_password:
            if sources["proxy"] == "environment":
                raise AppError("READ_ONLY_SETTING", "环境变量覆盖的配置不可修改")
            updates["proxy"] = current.proxy.model_dump()
        if patch.clear_proxy_password:
            updates["proxy"]["password"] = ""
        candidate = UserSettings.model_validate(current.model_dump() | updates)
        if candidate.download_root_id not in self.config.storage_roots:
            raise AppError("INVALID_ROOT", "下载目录不在部署允许列表中")
        if candidate.proxy.enabled and not candidate.proxy.host:
            raise AppError("INVALID_PROXY", "启用代理时需要地址")
        if candidate.bot_token and not candidate.allowed_user_ids:
            raise AppError("WHITELIST_REQUIRED", "配置 Bot 前请至少添加一个 Telegram 用户 ID")
        return candidate

    def save(self, patch: SettingsPatch, candidate: UserSettings) -> dict[str, Any]:
        _, _, sources = self.read()
        data = candidate.model_dump()
        data["bot_token"] = self.secrets.encrypt(data["bot_token"])
        data["telegram_api_hash"] = self.secrets.encrypt(data["telegram_api_hash"])
        data["proxy"]["password"] = self.secrets.encrypt(data["proxy"]["password"])
        # Never persist environment credentials into the database.
        data = {key: val for key, val in data.items() if sources[key] != "environment"}
        with self.db.transaction() as session:
            row = session.scalar(select(Settings).where(Settings.id == 1))
            assert row
            if patch.revision != row.revision:
                raise AppError("REVISION_CONFLICT", "配置已被修改，请刷新后重试", 409)
            current_mode = os.getenv("TGD_BOT_API_MODE", row.values.get("bot_api_mode", "cloud"))
            if not self.config.managed_local_api and candidate.bot_api_mode != current_mode and session.scalar(select(Job.id).where(
                    Job.state.in_(["fetching", "saving", "cancelling", "recovery_blocked"])).limit(1)):
                raise AppError("API_MODE_BUSY", "存在下载中或恢复待处理任务，请处理完成后切换 API 模式", 409)
            if self.config.managed_local_api:
                from app.services.local_api import schedule_settings
                schedule_settings(session, row, candidate, self.secrets)
            row.values, row.revision = data, row.revision + 1
        # Runtime drains active slots before handing new network configuration to the supervisor.
        return self.public()
