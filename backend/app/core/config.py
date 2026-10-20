"""Deployment-only configuration. Persisted user settings live in services/settings.py."""
import json
import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Config:
    data_dir: Path = field(default_factory=lambda: Path(os.getenv("TGD_DATA_DIR", "/data")))
    download_root: Path = field(default_factory=lambda: Path(os.getenv("TGD_DOWNLOAD_ROOT", "/downloads")))
    bot_api_root: Path = field(default_factory=lambda: Path(os.getenv("TGD_BOT_API_ROOT", "/var/lib/telegram-bot-api")))
    bot_api_base: str = field(default_factory=lambda: os.getenv("TGD_BOT_API_BASE", "http://bot-api:8081"))
    static_dir: Path = field(default_factory=lambda: Path(os.getenv("TGD_STATIC_DIR", "/app/frontend/dist")))
    network_dir: Path = field(default_factory=lambda: Path(os.getenv("TGD_NETWORK_DIR", "/run/tgd-network")))
    proxy_capable: bool = field(default_factory=lambda: os.getenv("TGD_PROXY_CAPABLE", "false").lower() == "true")
    cookie_secure: bool = field(default_factory=lambda: os.getenv("TGD_COOKIE_SECURE", "false").lower() == "true")
    get_file_timeout: int = field(default_factory=lambda: int(os.getenv("TGD_GET_FILE_TIMEOUT", "3600")))
    minimum_free_bytes: int = field(default_factory=lambda: int(os.getenv("TGD_MINIMUM_FREE_BYTES", "268435456")))
    run_background: bool = True
    managed_local_api: bool = field(default_factory=lambda: os.getenv("TGD_MANAGED_LOCAL_API", "false").lower() == "true")
    local_api_control_dir: Path = field(default_factory=lambda: Path(os.getenv("TGD_LOCAL_API_CONTROL_DIR", "/run/tgd-api-control")))

    @property
    def database_url(self) -> str:
        return "sqlite:///" + (self.data_dir / "tgd.sqlite3").resolve().as_posix()

    @property
    def storage_roots(self) -> dict[str, Path]:
        raw = json.loads(os.getenv("TGD_STORAGE_ROOTS", "{}"))
        return {"default": self.download_root, **{str(key): Path(value) for key, value in raw.items()}}
