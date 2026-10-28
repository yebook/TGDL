import hashlib
import json
import os
import re
import shutil
import stat
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, BinaryIO

from app.core.errors import AppError
from app.core.security import private_file


def safe_name(name: str | None, fallback: str) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f\x7f]', "_", name or fallback)
    name = name.replace("..", "_").strip(" .")
    if not name or name.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL", *[f"COM{i}" for i in range(10)], *[f"LPT{i}" for i in range(10)]}:
        name = "file_" + (name or fallback)
    while len(name.encode("utf-8")) > 180:
        name = name[:-1]
    return name


def contained(root: Path, relative: str, *, create_parent: bool = False) -> Path:
    if Path(relative).is_absolute() or ".." in Path(relative).parts:
        raise AppError("UNSAFE_PATH", "拒绝不安全的文件路径")
    base = root.resolve(strict=True)
    target = base / relative
    current = base
    for part in Path(relative).parts[:-1]:
        current = current / part
        if current.is_symlink():
            raise AppError("UNSAFE_PATH", "拒绝符号链接目录")
        if create_parent:
            current.mkdir(exist_ok=True)
    resolved = target.resolve()
    if not resolved.is_relative_to(base) or target.is_symlink():
        raise AppError("UNSAFE_PATH", "文件路径超出允许目录")
    return target


def source_file(root: Path, value: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        raise AppError("INVALID_API_PATH", "本地 Bot API 未返回共享绝对路径")
    resolved = path.resolve(strict=True)
    if not resolved.is_relative_to(root.resolve(strict=True)) or not resolved.is_file():
        raise AppError("INVALID_API_PATH", "Bot API 文件不在共享缓存目录")
    return resolved


class Storage:
    def __init__(self, root: Path, data_dir: Path, minimum_free: int, root_id: str = "default") -> None:
        self.root, self.data_dir, self.minimum_free = root, data_dir, minimum_free
        self.root_id = root_id
        self.identity_path = data_dir / f"storage-identity-{hashlib.sha256(root_id.encode()).hexdigest()[:16]}.json"

    def initialize(self) -> None:
        if self.identity_path.exists() or self.identity_path.is_symlink():
            return
        # Root must already exist. Keep identity outside the media output tree.
        private_file(self.identity_path, json.dumps(self._root_identity()).encode())

    def _root_identity(self) -> dict[str, int]:
        metadata = self.root.lstat()
        if not stat.S_ISDIR(metadata.st_mode) or metadata.st_ino <= 0:
            raise AppError("STORAGE_UNAVAILABLE", "下载目录未挂载、不是实际目录或无法识别其身份")
        return {"device": metadata.st_dev, "inode": metadata.st_ino}

    def _read_identity(self) -> dict[str, int]:
        # Fail closed for malformed/replaced metadata, including unpublished old
        # marker-based records. Never silently enroll a different download root.
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        if self.identity_path.is_symlink():
            raise AppError("UNSAFE_PATH", "存储身份文件不可为符号链接")
        with os.fdopen(os.open(self.identity_path, flags), "rb") as stream:
            metadata = os.fstat(stream.fileno())
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or metadata.st_size > 512:
                raise AppError("UNSAFE_PATH", "存储身份文件不是独立的普通文件")
            try:
                identity = json.loads(stream.read(513))
            except (ValueError, UnicodeError):
                identity = None
        if (not isinstance(identity, dict) or set(identity) != {"device", "inode"}
                or any(type(value) is not int or value < 0 for value in identity.values())
                or identity["inode"] == 0):
            raise AppError("STORAGE_CHANGED", "存储身份记录无效；请停服确认下载挂载后，按文档重新登记该目录")
        return identity

    def check(self, required: int = 0, *, check_space: bool = True) -> dict[str, Any]:
        if not self.identity_path.exists():
            self.initialize()
        identity = self._read_identity()
        if self._root_identity() != identity:
            raise AppError("STORAGE_CHANGED", "存储身份已改变，请检查挂载")
        usage = shutil.disk_usage(self.root)
        if check_space and usage.free < self.minimum_free + required:
            raise AppError("NO_SPACE", "可用空间不足，下载已暂停")
        return {"id": self.root_id, "label": self.root_id, "available": True,
                "free_bytes": usage.free, "total_bytes": usage.total, "message": ""}

    def existing(self, relative: str, size: int | None = None) -> bool:
        try:
            self.check()
            path = contained(self.root, relative)
            return path.is_file() and (size is None or path.stat().st_size == size)
        except (OSError, AppError, ValueError):
            return False


def file_hash(path: Path) -> tuple[int, str]:
    with path.open("rb") as stream:
        return hash_stream(stream)


def hash_stream(stream: BinaryIO) -> tuple[int, str]:
    sha = hashlib.sha256()
    count = 0
    for block in iter(lambda: stream.read(1024 * 1024), b""):
        sha.update(block)
        count += len(block)
    return count, sha.hexdigest()


def regular_file_hash(path: Path) -> tuple[int, str]:
    # A replaced final path must not hang housekeeping/shutdown on a FIFO. Check
    # the opened descriptor, rather than trusting the earlier pathname stat.
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    with os.fdopen(os.open(path, flags), "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise AppError("UNSAFE_PATH", "最终文件不是普通文件")
        return hash_stream(stream)


def copy_stream(source: Path, temporary: Path, expected: int | None,
                cancel: Callable[[], bool], progress: Callable[[int, float], None]) -> tuple[int, str]:
    sha, count, started = hashlib.sha256(), 0, time.monotonic()
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    source_fd = os.open(source, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    if not stat.S_ISREG(os.fstat(source_fd).st_mode):
        os.close(source_fd)
        raise AppError("INVALID_API_PATH", "源文件不是普通文件")
    with os.fdopen(source_fd, "rb") as src, os.fdopen(os.open(temporary, flags, 0o640), "wb") as dst:
        for block in iter(lambda: src.read(1024 * 1024), b""):
            if cancel():
                raise AppError("CANCELLED", "下载已取消")
            dst.write(block)
            sha.update(block)
            count += len(block)
            progress(count, count / max(time.monotonic() - started, 0.001))
        dst.flush()
        os.fsync(dst.fileno())
    if expected is not None and count != expected:
        raise AppError("SIZE_MISMATCH", "文件大小校验失败")
    return count, sha.hexdigest()


def publish_no_replace(temporary: Path, final: Path) -> None:
    # Atomic link creates the final name exclusively; unsupported filesystems fail closed.
    os.link(temporary, final, follow_symlinks=False)
    if os.name != "nt":
        fd = os.open(final.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    temporary.unlink()


class StorageManager:
    def __init__(self, roots: dict[str, Path], data_dir: Path, minimum_free: int) -> None:
        self.roots = {key: Storage(path, data_dir, minimum_free, key) for key, path in roots.items()}

    def get(self, root_id: str) -> Storage:
        if root_id not in self.roots:
            raise AppError("ROOT_UNAVAILABLE", "任务原下载目录已不在部署允许列表")
        return self.roots[root_id]
