"""Device-scoped, conservative admission budgets shared by Bot and workers.

Call under the claim/admission transaction. Progress observations are deliberately
not subtracted: they do not prove allocation or release of a writer's ownership.
"""
import shutil
from dataclasses import asdict, dataclass, replace
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Config
from app.core.errors import AppError
from app.db.models import Job, MediaItem
from app.storage.files import StorageManager

ACTIVE_STATES = ("fetching", "saving", "cancelling", "recovery_blocked")


@dataclass(frozen=True)
class SpaceVolume:
    location: str
    free_bytes: int | None
    required_bytes: int
    reserve_bytes: int
    concurrent_bytes: int
    missing_bytes: int | None
    error_code: str | None = None


@dataclass(frozen=True)
class SpaceAssessment:
    phase: str
    volumes: tuple[SpaceVolume, ...]

    @property
    def sufficient(self) -> bool:
        return all(volume.error_code is None and volume.missing_bytes == 0 for volume in self.volumes)

    def to_dict(self) -> dict[str, Any]:
        return {"phase": self.phase, "sufficient": self.sufficient,
                "volumes": [asdict(volume) for volume in self.volumes]}

    def observed(self) -> "SpaceAssessment":
        """Monitor actual headroom while writing, without reserving copies again."""
        return replace(self, volumes=tuple(replace(
            volume, required_bytes=0, concurrent_bytes=0,
            missing_bytes=None if volume.free_bytes is None else max(0, volume.reserve_bytes - volume.free_bytes),
        ) for volume in self.volumes))

    def require(self) -> None:
        if self.sufficient:
            return
        code = next((volume.error_code for volume in self.volumes if volume.error_code), "NO_SPACE")
        message = "可用空间不足，请释放对应磁盘空间后手动重试" if code == "NO_SPACE" else "无法检查存储空间，请检查对应磁盘挂载与目录权限"
        raise AppError(code, message, details=self.to_dict())


@dataclass
class _Budget:
    locations: list[str]
    free: int | None
    required: int = 0
    concurrent: int = 0
    error_code: str | None = None


def assess_space(session: Session, storage: StorageManager, config: Config, root_id: str,
                 size: int | None, mode: str, phase: str,
                 exclude_job_id: str | None = None) -> SpaceAssessment:
    """Reserve once per filesystem; an existing complete cache needs no new copy.

    Unknown lengths receive one minimum-reserve-sized estimate per future copy.
    Missing/unmeasurable current mounts fail closed without exposing real paths.
    An active job with an unmeasurable target is charged conservatively to every
    current device, since it cannot safely be proved unrelated.
    """
    budgets: dict[int | str, _Budget] = {}
    reserve = max(0, config.minimum_free_bytes)

    def target_probe(key: str) -> tuple[int, int]:
        target = storage.get(key)
        status = target.check(check_space=False)
        return target.root.stat().st_dev, int(status["free_bytes"])

    def cache_probe() -> tuple[int, int]:
        metadata = config.bot_api_root.stat()
        if not config.bot_api_root.is_dir():
            raise OSError("Cache root is not a directory")
        return metadata.st_dev, shutil.disk_usage(config.bot_api_root).free

    def add_current(location: str, amount: int, *, cache: bool = False) -> None:
        try:
            device, free = cache_probe() if cache else target_probe(root_id)
            if device not in budgets:
                budgets[device] = _Budget([], free)
            entry = budgets[device]
            entry.free = min(entry.free if entry.free is not None else free, free)
        except (OSError, AppError) as exc:
            entry = _Budget([], None, error_code=exc.code if isinstance(exc, AppError) else "STORAGE_UNAVAILABLE")
            budgets[location] = entry
        entry.locations.append(location)
        entry.required += amount

    length = max(0, size) if size is not None else reserve
    add_current("target", length)
    if mode == "local" and phase in {"before_download", "downloading"}:
        add_current("cache", length, cache=True)

    rows = session.execute(select(Job, MediaItem.size_bytes).join(MediaItem, MediaItem.id == Job.item_id)
                           .where(Job.state.in_(ACTIVE_STATES)))
    for job, item_size in rows:
        if job.id == exclude_job_id:
            continue
        outstanding = max(0, item_size) if item_size is not None else reserve
        try:
            device, _ = target_probe(job.root_id)
            if device in budgets:
                budgets[device].concurrent += outstanding
        except (OSError, AppError):
            for entry in budgets.values():
                entry.concurrent += outstanding
        # Saving proves acquisition completed. Cancelling/uncertain fetching
        # retains the full cache budget until its owner has actually stopped.
        if job.acquisition_mode == "local" and job.phase != "saving" and getattr(job, "execution_mode", "download") != "save":
            try:
                device, _ = cache_probe()
                if device in budgets:
                    budgets[device].concurrent += outstanding
            except (OSError, AppError):
                for entry in budgets.values():
                    entry.concurrent += outstanding

    return SpaceAssessment(phase, tuple(SpaceVolume(
        location="+".join(entry.locations), free_bytes=entry.free,
        required_bytes=entry.required, reserve_bytes=reserve,
        concurrent_bytes=entry.concurrent,
        missing_bytes=None if entry.free is None else max(0, entry.required + reserve + entry.concurrent - entry.free),
        error_code=entry.error_code,
    ) for entry in budgets.values()))
