"""Portable settings only, encrypted independently of the deployment master key."""
import base64
import binascii
import os
from datetime import UTC, datetime
from threading import BoundedSemaphore
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
from pydantic import BaseModel, ConfigDict, ValidationError

from app.core.errors import AppError
from app.schemas.backup import BackupEnvelope
from app.schemas.contracts import ProxySettings, SettingsPatch, UserSettings
from app.services.settings import SettingsService

MAX_BACKUP_REQUEST_BYTES = 131072
AAD = b"tgdl-settings:1:scrypt-16384-8-1"


class BackupPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    created_at: str
    settings: dict[str, Any]
    excluded_fields: list[str]


class ConfigBackupService:
    def __init__(self, settings: SettingsService) -> None:
        self.settings = settings
        self._kdf_slot = BoundedSemaphore(1)

    def _key(self, password: str, salt: bytes) -> bytes:
        if not self._kdf_slot.acquire(blocking=False):
            raise AppError("BACKUP_BUSY", "正在处理其他配置备份，请稍后重试", 429)
        try:
            return Scrypt(salt=salt, length=32, n=16384, r=8, p=1).derive(password.encode("utf-8"))
        finally:
            self._kdf_slot.release()

    def export(self, password: str) -> dict[str, Any]:
        _, current, sources = self.settings.read()
        excluded = sorted(key for key, source in sources.items() if source == "environment")
        payload = BackupPayload(created_at=datetime.now(UTC).isoformat(),
                                settings=current.model_dump(exclude=set(excluded)), excluded_fields=excluded)
        plaintext = payload.model_dump_json().encode("utf-8")
        if len(plaintext) > 65536:
            raise AppError("BACKUP_TOO_LARGE", "配置过大，无法创建备份", 422)
        salt, nonce = os.urandom(16), os.urandom(12)
        ciphertext = AESGCM(self._key(password, salt)).encrypt(nonce, plaintext, AAD)
        def encode(value: bytes) -> str:
            return base64.b64encode(value).decode("ascii")
        envelope = BackupEnvelope(format="tgdl-settings", version=1, kdf="scrypt-16384-8-1",
                                  salt=encode(salt), nonce=encode(nonce), ciphertext=encode(ciphertext))
        return {"backup": envelope.model_dump(), "excluded_fields": excluded}

    def decrypt(self, envelope: BackupEnvelope, password: str) -> BackupPayload:
        try:
            salt = base64.b64decode(envelope.salt, validate=True)
            nonce = base64.b64decode(envelope.nonce, validate=True)
            ciphertext = base64.b64decode(envelope.ciphertext, validate=True)
            if len(salt) != 16 or len(nonce) != 12 or not 16 <= len(ciphertext) <= 65552:
                raise ValueError("Invalid lengths")
            plaintext = AESGCM(self._key(password, salt)).decrypt(nonce, ciphertext, AAD)
            payload = BackupPayload.model_validate_json(plaintext)
            fields = set(UserSettings.model_fields)
            excluded = set(payload.excluded_fields)
            if (len(excluded) != len(payload.excluded_fields) or not excluded <= fields
                    or set(payload.settings) != fields - excluded):
                raise ValueError("Invalid fields")
            if "proxy" in payload.settings and (
                not isinstance(payload.settings["proxy"], dict)
                or set(payload.settings["proxy"]) != set(ProxySettings.model_fields)
            ):
                raise ValueError("Incomplete proxy settings")
            timestamp = datetime.fromisoformat(payload.created_at)
            if timestamp.tzinfo is None:
                raise ValueError("Invalid timestamp")
            # Validate every imported value even if a target environment will override it.
            UserSettings.model_validate(UserSettings().model_dump() | payload.settings, strict=True)
            return payload
        except (InvalidTag, binascii.Error, ValueError, TypeError, ValidationError, OverflowError) as exc:
            raise AppError("INVALID_BACKUP", "备份密码不正确，或备份文件已损坏、格式不受支持", 422) from exc

    def prepare(self, payload: BackupPayload, revision: int) -> tuple[SettingsPatch, UserSettings, list[str]]:
        _, _, sources = self.settings.read()
        skipped = sorted(set(payload.excluded_fields) | {
            key for key, source in sources.items() if source == "environment"
        })
        updates = {key: value for key, value in payload.settings.items() if key not in skipped}
        # Blank means exact restoration, unlike ordinary settings form blank-secret preservation.
        clear = {"clear_bot_token": "bot_token" in updates and not updates["bot_token"],
                 "clear_telegram_api_hash": "telegram_api_hash" in updates and not updates["telegram_api_hash"],
                 "clear_proxy_password": "proxy" in updates and not updates["proxy"]["password"]}
        patch = SettingsPatch.model_validate({"revision": revision, **updates, **clear})
        candidate = self.settings.candidate(patch)
        root = self.settings.config.storage_roots[candidate.download_root_id]
        if not root.is_dir() or root.is_symlink():
            raise AppError("STORAGE_UNAVAILABLE", "备份中的下载目录未挂载或不可用，请先准备目标部署的下载目录", 422)
        return patch, candidate, skipped

    def preview(self, payload: BackupPayload) -> dict[str, Any]:
        revision, _, _ = self.settings.read()
        _, candidate, skipped = self.prepare(payload, revision)
        warnings = ["仅恢复平台配置，不包含管理员账号、任务、数据库、媒体、日志、缓存或运行状态。",
                    "恢复不会立即验证 Bot；连接配置将在保存后按安全切换流程生效。"]
        if skipped:
            warnings.append("环境变量控制或原备份未包含的配置保持目标部署当前值。")
        return {"revision": revision, "created_at": payload.created_at,
                "included_fields": sorted(set(payload.settings) - set(skipped)), "skipped_fields": skipped,
                "warnings": warnings,
                "configured": {"bot_token": bool(candidate.bot_token), "telegram_api_hash": bool(candidate.telegram_api_hash),
                               "proxy_password": bool(candidate.proxy.password)},
                "summary": {"bot_api_mode": candidate.bot_api_mode, "download_root_id": candidate.download_root_id,
                            "download_concurrency": candidate.download_concurrency,
                            "allowed_user_count": len(candidate.allowed_user_ids)}}
