import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from time import monotonic
from typing import Any

from app.core.errors import AppError
from app.storage.files import StorageManager

CAPACITY_SAMPLE_SECONDS = 15
Identity = tuple[int, int]


@dataclass(frozen=True)
class _CapacityReading:
    identity: Identity
    expires_at: float
    free_bytes: int
    total_bytes: int


class _CapacityProbe:
    def __init__(self, path: Path, identify: Callable[[], Identity]) -> None:
        self.path = path
        self.identify = identify
        self._lock = Lock()
        self._reading: _CapacityReading | None = None

    def read(self, *, allow_sample: bool) -> _CapacityReading:
        with self._lock:
            # Check the mount even on a cache hit. Serialize only this path's
            # display reads so several tabs don't all sample it at expiry.
            try:
                identity = self.identify()
                reading = self._reading
                if allow_sample and reading and reading.identity == identity and monotonic() < reading.expires_at:
                    return reading
                usage = shutil.disk_usage(self.path)
                if self.identify() != identity:
                    raise OSError("Storage identity changed during capacity sampling")
            except (OSError, AppError, ValueError):
                self._reading = None
                raise
            reading = _CapacityReading(identity, monotonic() + CAPACITY_SAMPLE_SECONDS, usage.free, usage.total)
            self._reading = reading
            return reading


class StorageDisplayStatus:
    """Bounded display-only capacity observations for configured storage paths."""

    def __init__(self, storage: StorageManager, cache_root: Path) -> None:
        self.cache_root = cache_root
        self._roots = [(root, _CapacityProbe(root.root, root.validate_identity)) for root in storage.roots.values()]
        self._cache = _CapacityProbe(cache_root, self._cache_identity)

    def _cache_identity(self) -> Identity:
        metadata = self.cache_root.stat()
        return metadata.st_dev, metadata.st_ino

    def read(self, *, allow_sample: bool) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        roots = []
        for root, probe in self._roots:
            try:
                capacity = probe.read(allow_sample=allow_sample)
                roots.append(root.capacity_status(capacity.free_bytes, capacity.total_bytes))
            except (OSError, AppError, ValueError):
                roots.append({"id": root.root_id, "label": root.root_id, "available": False, "free_bytes": None,
                              "total_bytes": None, "message": "下载目录不可用，请检查挂载、权限和空间"})
        cache: dict[str, Any] = {"free_bytes": None, "total_bytes": None}
        try:
            capacity = self._cache.read(allow_sample=allow_sample)
            cache.update(free_bytes=capacity.free_bytes, total_bytes=capacity.total_bytes)
        except (OSError, ValueError):
            pass
        return roots, cache
