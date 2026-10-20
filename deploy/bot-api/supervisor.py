"""Non-root fixed-command Bot API supervisor; encrypted, bounded file handoff.

Process health is deliberately separate from authenticated Telegram connectivity.
No request payload, child output, environment or exception text is logged.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import signal
import socket
import stat
import subprocess
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

MAX_BYTES = 8192
FIELDS = {
    "version", "command_id", "action", "credential_revision", "api_id", "api_hash_encrypted"
}
COMMAND = [
    "/usr/local/bin/telegram-bot-api", "--local", "--http-ip-address=0.0.0.0",
    "--http-port=8081", "--dir=/var/lib/telegram-bot-api",
    "--temp-dir=/var/lib/telegram-bot-api/tmp", "--verbosity=0",
]


class ConfigError(ValueError):
    pass


def read_private(path: Path, limit: int = MAX_BYTES) -> bytes:
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    with os.fdopen(fd, "rb") as source:
        info = os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode):
            raise ConfigError("invalid_configuration")
        if os.name != "nt" and (info.st_uid != os.getuid() or info.st_mode & 0o077):
            raise ConfigError("control_permissions")
        raw = source.read(limit + 1)
        if len(raw) > limit:
            raise ConfigError("invalid_configuration")
        return raw


def sync_directory(directory: Path) -> None:
    if os.name == "nt":
        return
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic_write(path: Path, data: bytes, mode: int = 0o600) -> None:
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), mode)
    try:
        with os.fdopen(fd, "wb") as target:
            if os.name != "nt":
                os.fchmod(target.fileno(), mode)
            target.write(data)
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary, path)
        sync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


@dataclass(frozen=True)
class Desired:
    command_id: str
    action: str
    credential_revision: int
    api_id: int | None
    api_hash_encrypted: str | None


def decode(raw: bytes) -> Desired:
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        raise ConfigError("invalid_configuration") from None
    if not isinstance(value, dict) or set(value) != FIELDS or type(value["version"]) is not int:
        raise ConfigError("invalid_configuration")
    if value.pop("version") != 1:
        raise ConfigError("invalid_configuration")
    if not isinstance(value["command_id"], str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", value["command_id"]):
        raise ConfigError("invalid_configuration")
    if value["action"] not in ("start", "stop", "restart"):
        raise ConfigError("invalid_configuration")
    if type(value["credential_revision"]) is not int or not 0 <= value["credential_revision"] < 2**53:
        raise ConfigError("invalid_configuration")
    api_id, encrypted = value["api_id"], value["api_hash_encrypted"]
    if api_id is not None and (type(api_id) is not int or not 1 <= api_id <= 2**31 - 1):
        raise ConfigError("invalid_configuration")
    if encrypted is not None and (not isinstance(encrypted, str) or len(encrypted) > 2048):
        raise ConfigError("invalid_configuration")
    return Desired(**value)


class Supervisor:
    def __init__(
        self, shared: Path, cache: Path, *, command: list[str] | None = None,
        port: int = 8081, startup_timeout: float = 20, retry_delay: float = 2,
    ) -> None:
        self.shared, self.cache = shared, cache
        self.command = COMMAND if command is None else command
        self.port, self.startup_timeout, self.retry_delay = port, startup_timeout, retry_delay
        self.process: subprocess.Popen[bytes] | None = None
        self.desired: Desired | None = None
        self.last_hash: bytes | None = None
        self.generation: str | None = None
        self.state = "waiting_credentials"
        self.error_code: str | None = None
        self.spawned_at = 0.0
        self.next_retry = 0.0
        self.failures = 0
        self.active_credentials: bytes | None = None

    def publish(self) -> None:
        value = {
            "version": 1,
            "command_id": self.desired.command_id if self.desired else None,
            "credential_revision": self.desired.credential_revision if self.desired else 0,
            "state": self.state, "generation": self.generation,
            "heartbeat_at": time.time(), "error_code": self.error_code,
        }
        atomic_write(self.shared / "applied.json", json.dumps(value, separators=(",", ":")).encode())

    def stop_process(self) -> None:
        if self.process is None:
            return
        self.state = "stopping"
        self.publish()
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        self.process = None

    def credentials(self, desired: Desired) -> tuple[str, str]:
        if desired.api_id is None or not desired.api_hash_encrypted:
            raise ConfigError("missing_credentials")
        try:
            key = read_private(self.shared / "control.key", 128).strip()
            plaintext = Fernet(key).decrypt(desired.api_hash_encrypted.encode(), ttl=None).decode("ascii")
        except (InvalidToken, ValueError, UnicodeError, FileNotFoundError):
            raise ConfigError("invalid_credentials") from None
        if not re.fullmatch(r"[A-Fa-f0-9]{32}", plaintext):
            raise ConfigError("invalid_credentials")
        return str(desired.api_id), plaintext

    def start_process(self) -> None:
        assert self.desired is not None
        api_id, api_hash = self.credentials(self.desired)
        environment = dict(os.environ, TELEGRAM_API_ID=api_id, TELEGRAM_API_HASH=api_hash)
        self.cache.joinpath("tmp").mkdir(exist_ok=True)
        # The previous process has been waited for before a new marker is visible.
        self.process = subprocess.Popen(
            self.command, env=environment, stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        self.active_credentials = hashlib.sha256(f"{api_id}:{api_hash}".encode()).digest()
        self.generation = str(uuid.uuid4())
        try:
            atomic_write(self.cache / ".tgd-api-generation", (self.generation + "\n").encode(), 0o644)
        except OSError:
            self.stop_process()
            raise
        self.spawned_at = time.monotonic()
        self.state, self.error_code = "starting", None

    def apply(self, desired: Desired) -> None:
        # Validate candidate before stopping a healthy old process.
        fingerprint = None
        if desired.action != "stop":
            api_id, api_hash = self.credentials(desired)
            fingerprint = hashlib.sha256(f"{api_id}:{api_hash}".encode()).digest()
        same_credentials = (
            self.desired is not None
            and desired.credential_revision == self.desired.credential_revision
            and desired.api_id == self.desired.api_id
            and fingerprint == self.active_credentials
        )
        self.desired = desired
        self.failures, self.next_retry, self.error_code = 0, 0, None
        if desired.action == "stop":
            self.stop_process()
            self.state = "stopped"
        elif (
            desired.action == "start" and same_credentials and self.process is not None
            and self.state in ("starting", "running")
        ):
            pass
        else:
            self.stop_process()
            self.start_process()

    def failed_child(self, code: str) -> None:
        self.stop_process()
        self.failures += 1
        self.state, self.error_code = "failed", code
        self.next_retry = time.monotonic() + self.retry_delay * 2 ** (self.failures - 1)

    def tick(self) -> None:
        candidate: Desired | None = None
        try:
            raw = read_private(self.shared / "desired.json")
            digest = hashlib.sha256(raw).digest()
            if digest != self.last_hash:
                self.last_hash = digest
                candidate = decode(raw)
                if self.desired and candidate.command_id == self.desired.command_id:
                    previous = self.desired
                    same_command = (
                        candidate.action == previous.action
                        and candidate.credential_revision == previous.credential_revision
                        and candidate.api_id == previous.api_id
                        and (candidate.action == "stop" or self.credentials(candidate) == self.credentials(previous))
                    )
                    if not same_command:
                        raise ConfigError("command_reused")
                    # Recovery can encrypt the same command again with a fresh
                    # Fernet nonce. A repeated restart must never execute twice.
                else:
                    self.apply(candidate)
        except FileNotFoundError:
            pass
        except (ConfigError, OSError, ValueError) as error:
            if candidate is not None:
                self.desired = candidate
            self.error_code = str(error) if isinstance(error, ConfigError) else "control_io_failed"
            # A malformed handoff never terminates the previously healthy process.
            self.state = "waiting_credentials" if self.error_code == "missing_credentials" else "failed"
            self.failures = 0

        if self.process is not None:
            if self.process.poll() is not None:
                self.failed_child("process_exited")
            elif self.state == "starting":
                try:
                    with socket.create_connection(("127.0.0.1", self.port), timeout=0.2):
                        self.state = "running"
                except OSError:
                    if time.monotonic() - self.spawned_at >= self.startup_timeout:
                        self.failed_child("listen_timeout")
        elif (
            self.desired is not None and self.desired.action != "stop"
            and self.state == "failed" and 0 < self.failures <= 3
            and time.monotonic() >= self.next_retry
        ):
            try:
                self.start_process()
            except (ConfigError, OSError):
                self.failures = 4
                self.state, self.error_code = "failed", "process_start_failed"
        self.publish()


def main() -> None:
    os.umask(0o077)
    if os.getuid() == 0:
        raise SystemExit("Bot API supervisor requires a non-root UID")
    shared = Path(os.environ.get("TGD_LOCAL_API_CONTROL_DIR", "/run/tgd-api-control"))
    cache = Path("/var/lib/telegram-bot-api")
    shared.mkdir(parents=True, exist_ok=True)
    if shared.is_symlink() or shared.stat().st_uid != os.getuid():
        raise SystemExit("Bot API control directory ownership is invalid")
    shared.chmod(0o700)
    # Advisory lock prevents two supervisors from controlling the same cache.
    import fcntl

    lock = os.open(shared / "supervisor.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    supervisor = Supervisor(shared, cache)
    stopped = False

    def stop(_signal: int, _frame: Any) -> None:
        nonlocal stopped
        stopped = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        while not stopped:
            try:
                supervisor.tick()
            except OSError:
                # Retain child ownership on a missing/temporarily inaccessible mount.
                pass
            time.sleep(1)
    finally:
        supervisor.stop_process()
        supervisor.state = "stopped"
        supervisor.publish()
        os.close(lock)


if __name__ == "__main__":
    main()
