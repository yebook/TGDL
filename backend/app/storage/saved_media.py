"""Bounded observations and descriptor-relative deletion of enrolled final media."""
import errno
import os
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import PurePosixPath
from typing import Any

from app.core.errors import AppError
from app.storage.files import Storage, contained, hash_stream


def root_identity(storage: Storage) -> dict[str, int]:
    # Read probes and deletion must never enroll a missing identity record.
    expected = storage._read_identity()
    if storage._root_identity() != expected:
        raise AppError("STORAGE_CHANGED", "下载目录身份已改变", 409)
    return expected


def parts(relative: str) -> tuple[str, ...]:
    value = PurePosixPath(relative)
    if (not relative or value.is_absolute() or "\\" in relative or "\x00" in relative
            or any(part in {"", ".", ".."} for part in relative.split("/"))):
        raise ValueError("Unsafe saved path")
    return value.parts


@contextmanager
def _parent_chain(storage: Storage, relative: str) -> Iterator[tuple[list[int], tuple[str, ...]]]:
    path = parts(relative)
    expected = root_identity(storage)
    if os.name != "posix" or not hasattr(os, "O_NOFOLLOW") or os.unlink not in os.supports_dir_fd:
        raise AppError("FILE_DELETE_UNSUPPORTED", "当前文件系统不支持安全删除", 409)
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptors = [os.open(storage.root, flags)]
    try:
        opened = os.fstat(descriptors[0])
        if {"device": opened.st_dev, "inode": opened.st_ino} != expected:
            raise ValueError("Changed root")
        for part in path[:-1]:
            descriptors.append(os.open(part, flags, dir_fd=descriptors[-1]))
        yield descriptors, path
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


@contextmanager
def parent_fd(storage: Storage, relative: str) -> Iterator[tuple[int, str]]:
    with _parent_chain(storage, relative) as (descriptors, path):
        yield descriptors[-1], path[-1]


def _directory_identity(metadata: os.stat_result) -> tuple[int, int]:
    return metadata.st_dev, metadata.st_ino


def _prune_empty_task_directory(storage: Storage, descriptors: list[int], path: tuple[str, ...], directory: Any) -> None:
    """Remove only the journal's proven group directory; retain date/root directories."""
    if (not isinstance(directory, str) or len(path) != 3 or directory != "/".join(path[:-1])
            or os.rmdir not in os.supports_dir_fd):
        return
    try:
        expected = root_identity(storage)
        root = os.fstat(descriptors[0])
        if {"device": root.st_dev, "inode": root.st_ino} != expected:
            return
        # Each edge must still address the pinned original directory. Directory
        # timestamps change on unlink, so compare device/inode, not size/time.
        for index in range(1, len(descriptors)):
            current = os.stat(path[index - 1], dir_fd=descriptors[index - 1], follow_symlinks=False)
            if (not stat.S_ISDIR(current.st_mode)
                    or _directory_identity(current) != _directory_identity(os.fstat(descriptors[index]))):
                return
        # rmdir atomically refuses a directory with any remaining/new entry.
        os.rmdir(path[-2], dir_fd=descriptors[-2])
        os.fsync(descriptors[-2])
    except (OSError, AppError, ValueError):
        # Directory pruning cannot turn proven file deletion into a false failure
        # or cause a later retry to unlink a replacement file.
        return


def observe(storage: Storage, relative: str, size: int) -> str:
    try:
        root_identity(storage)
        parts(relative)
        if os.name == "posix":
            with parent_fd(storage, relative) as (parent, name):
                metadata = os.stat(name, dir_fd=parent, follow_symlinks=False)
        else:
            metadata = contained(storage.root, relative).lstat()
        root_identity(storage)
        if not stat.S_ISREG(metadata.st_mode):
            return "unavailable"
        return "present" if metadata.st_size == size else "changed"
    except FileNotFoundError:
        # A disappearing mount/identity is not a disappearing saved child.
        try:
            root_identity(storage)
        except (OSError, AppError, ValueError):
            return "unavailable"
        return "missing"
    except (OSError, AppError, ValueError):
        return "unavailable"


def fingerprint(metadata: os.stat_result) -> tuple[int, int, int, int, int]:
    return metadata.st_dev, metadata.st_ino, metadata.st_size, metadata.st_mtime_ns, metadata.st_ctime_ns


def remove_verified(storage: Storage, entry: dict[str, Any]) -> str:
    """Never follow parents/leaf; hash before unlink without any DB write lock."""
    try:
        with _parent_chain(storage, entry["relative_path"]) as (original, path):
            parent, name = original[-1], path[-1]
            before = os.stat(name, dir_fd=parent, follow_symlinks=False)
            if (not stat.S_ISREG(before.st_mode) or before.st_size != entry["size_bytes"]
                    or not isinstance(entry.get("sha256"), str)):
                return "retained_unverified"
            descriptor = os.open(name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0), dir_fd=parent)
            with os.fdopen(descriptor, "rb") as stream:
                if fingerprint(os.fstat(stream.fileno())) != fingerprint(before):
                    return "retained_unverified"
                if hash_stream(stream) != (entry["size_bytes"], entry["sha256"]):
                    return "retained_unverified"
                if fingerprint(os.fstat(stream.fileno())) != fingerprint(before):
                    return "retained_unverified"
            root_identity(storage)
            # Rewalk parents to reject rename/replacement during the long hash.
            with _parent_chain(storage, entry["relative_path"]) as (descriptors, current_path):
                current, current_name = descriptors[-1], current_path[-1]
                if any(_directory_identity(os.fstat(left)) != _directory_identity(os.fstat(right))
                       for left, right in zip(original, descriptors, strict=True)):
                    return "retained_unverified"
                if fingerprint(os.fstat(current)) != fingerprint(os.fstat(parent)):
                    return "retained_unverified"
                if fingerprint(os.stat(current_name, dir_fd=current, follow_symlinks=False)) != fingerprint(before):
                    return "retained_unverified"
                os.unlink(current_name, dir_fd=current)
                # A failed fsync after unlink is ambiguous, never replay it.
                try:
                    os.fsync(current)
                except OSError:
                    return "uncertain"
                _prune_empty_task_directory(storage, descriptors, current_path, entry.get("prune_directory"))
            return "deleted"
    except FileNotFoundError:
        try:
            root_identity(storage)
        except (OSError, AppError, ValueError):
            return "retained_unverified"
        return "missing"
    except OSError as exc:
        return "retained_unverified" if exc.errno in {errno.ELOOP, errno.ENOTDIR} else "failed"
    except (AppError, ValueError):
        return "retained_unverified"
