"""Control handoff uses private bounded files and crash-durable directory updates."""

import json
import os
from pathlib import Path
from unittest.mock import Mock

import pytest
from cryptography.fernet import Fernet

from app.schemas.contracts import UserSettings
from app.services.local_api import LocalApiService, private_bytes


def service_at(directory: Path) -> LocalApiService:
    service = object.__new__(LocalApiService)
    service.config = Mock(local_api_control_dir=directory)
    service.update = Mock()
    return service


def test_command_encrypts_hash_with_independent_private_key(tmp_path: Path) -> None:
    directory = tmp_path / "control"
    service = service_at(directory)
    values = UserSettings(telegram_api_id="1234", telegram_api_hash="a" * 32)
    command_id = service.command("start", values, {})
    payload = json.loads(private_bytes(directory / "desired.json"))
    key = private_bytes(directory / "control.key", 128)
    assert payload["command_id"] == command_id
    assert "a" * 32 not in json.dumps(payload)
    assert Fernet(key).decrypt(payload["api_hash_encrypted"].encode()) == b"a" * 32
    assert not list(directory.glob("*.new"))
    if os.name != "nt":
        assert directory.stat().st_mode & 0o777 == 0o700
        assert (directory / "control.key").stat().st_mode & 0o777 == 0o600


def test_read_is_bounded(tmp_path: Path) -> None:
    path = tmp_path / "oversized"
    path.write_bytes(b"x" * 129)
    path.chmod(0o600)
    with pytest.raises(ValueError):
        private_bytes(path, 128)


def test_preflight_checks_key_and_shared_cache(tmp_path: Path) -> None:
    service = service_at(tmp_path / "control")
    service.config.bot_api_root = tmp_path / "cache"
    service.config.bot_api_root.mkdir()
    service.preflight_control()
    assert not list(service.config.local_api_control_dir.glob("*.new"))
    (service.config.local_api_control_dir / "control.key").write_bytes(b"broken")
    with pytest.raises(ValueError):
        service.preflight_control()


@pytest.mark.skipif(os.name == "nt", reason="POSIX owner/mode and NOFOLLOW")
def test_command_rejects_symlink_key_and_public_control_directory(tmp_path: Path) -> None:
    directory = tmp_path / "control"
    directory.mkdir(mode=0o700)
    target = tmp_path / "other-key"
    target.write_bytes(Fernet.generate_key())
    target.chmod(0o600)
    (directory / "control.key").symlink_to(target)
    service = service_at(directory)
    values = UserSettings(telegram_api_id="1234", telegram_api_hash="a" * 32)
    with pytest.raises(OSError):
        service.command("start", values, {})
    assert not (directory / "desired.json").exists()
    (directory / "control.key").unlink()
    directory.chmod(0o755)
    with pytest.raises(ValueError):
        service.command("start", values, {})


@pytest.mark.skipif(os.name == "nt", reason="POSIX owner/mode")
def test_rejects_public_key(tmp_path: Path) -> None:
    path = tmp_path / "key"
    path.write_bytes(Fernet.generate_key())
    path.chmod(0o644)
    with pytest.raises(ValueError):
        private_bytes(path, 128)


@pytest.mark.skipif(os.name == "nt", reason="POSIX FIFO")
def test_rejects_fifo_without_waiting_for_writer(tmp_path: Path) -> None:
    path = tmp_path / "fifo"
    getattr(os, "mkfifo", None)(path, 0o600)
    with pytest.raises(ValueError):
        private_bytes(path)
