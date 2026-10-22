"""Ephemeral local-acquisition observations; never download ownership or durable progress."""
import math
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from app.db.models import Job


@dataclass(frozen=True)
class ProgressOwner:
    job_id: str
    item_id: str
    bot_id: int
    file_id: str
    attempt: int
    boot_id: str
    generation: str
    observation_id: str


@dataclass
class Observation:
    owner: ProgressOwner
    sampled_at: float
    value: dict[str, Any] | None = None


def decode_progress(payload: Any, file_ids: list[str]) -> tuple[str, dict[str, dict[str, Any]]]:
    """One strict boundary decoder. Native identifiers are stripped before projection."""
    if not isinstance(payload, dict) or payload.get("ok") is not True:
        raise ValueError("Invalid progress response")
    result = payload.get("result")
    if not isinstance(result, dict):
        raise ValueError("Invalid progress result")
    generation, files = result.get("generation"), result.get("files")
    if not isinstance(generation, str) or not 1 <= len(generation) <= 100:
        raise ValueError("Invalid progress generation")
    if not isinstance(files, list) or len(files) > len(file_ids):
        raise ValueError("Invalid progress files")
    decoded: dict[str, dict[str, Any]] = {}
    seen: set[str] = set()
    for item in files:
        if not isinstance(item, dict):
            raise ValueError("Invalid progress item")
        alias = item.get("file_id")
        if not isinstance(alias, str) or alias not in file_ids or alias in seen:
            raise ValueError("Invalid progress alias")
        seen.add(alias)
        if item.get("available") is False:
            continue
        if item.get("available") is not True:
            raise ValueError("Invalid progress availability")
        count, total, kind = item.get("downloaded_bytes"), item.get("total_bytes"), item.get("total_kind")
        if type(count) is not int or not 0 <= count <= 2**63 - 1:
            raise ValueError("Invalid progress count")
        if kind not in {"exact", "estimated", "unknown"}:
            raise ValueError("Invalid progress total kind")
        if kind == "unknown":
            if total is not None:
                raise ValueError("Unknown total must be null")
        elif type(total) is not int or not 0 < total <= 2**63 - 1:
            raise ValueError("Invalid progress total")
        if any(type(item.get(key)) is not bool for key in ("active", "completed")):
            raise ValueError("Invalid progress state")
        decoded[alias] = {"downloaded_bytes": count, "total_bytes": total, "total_kind": kind,
                          "active": item["active"], "completed": item["completed"], "speed_bps": None}
    return generation, decoded


class DownloadProgress:
    def __init__(self, *, clock: Callable[[], float] = time.monotonic,
                 generation: Callable[[], str] = lambda: "", capacity: int = 16, ttl: float = 4) -> None:
        self.clock, self.generation, self.capacity, self.ttl = clock, generation, capacity, ttl
        self._lock = threading.RLock()
        self._entries: dict[str, Observation] = {}

    def _current_generation(self) -> str:
        try:
            generation = self.generation()
            return generation if isinstance(generation, str) and 1 <= len(generation) <= 100 else ""
        except Exception:
            # Supervisor/DB telemetry failures must not affect download or task-detail availability.
            return ""

    def begin(self, job: Job, boot_id: str, *, bot_id: int, file_id: str) -> ProgressOwner | None:
        generation = self._current_generation()
        if not generation or not self.eligible(job, boot_id):
            return None
        owner = ProgressOwner(job.id, job.item_id, bot_id, file_id, job.attempt_sequence,
                              boot_id, generation, uuid4().hex)
        with self._lock:
            self._entries.pop(job.id, None)
            if len(self._entries) >= self.capacity:
                return None
            self._entries[job.id] = Observation(owner, self.clock())
        return owner

    @staticmethod
    def eligible(job: Job, boot_id: str) -> bool:
        return (job.acquisition_mode == "local" and job.state == "fetching" and job.phase == "fetching"
                and not job.cancel_requested and job.boot_id == boot_id)

    def clear(self, owner: ProgressOwner) -> None:
        with self._lock:
            entry = self._entries.get(owner.job_id)
            if entry and entry.owner == owner:
                del self._entries[owner.job_id]

    def unavailable(self, owner: ProgressOwner) -> None:
        with self._lock:
            entry = self._entries.get(owner.job_id)
            if entry and entry.owner == owner:
                entry.value = None

    def update(self, owner: ProgressOwner, generation: str, value: dict[str, Any]) -> None:
        if generation != owner.generation or self._current_generation() != owner.generation:
            self.unavailable(owner)
            return
        now = self.clock()
        with self._lock:
            entry = self._entries.get(owner.job_id)
            if not entry or entry.owner != owner:
                return
            value = dict(value)
            previous, elapsed = entry.value, now - entry.sampled_at
            speed = None
            if previous and 0 < elapsed <= self.ttl and value["downloaded_bytes"] >= previous["downloaded_bytes"]:
                speed = (value["downloaded_bytes"] - previous["downloaded_bytes"]) / elapsed
            value["speed_bps"] = speed if speed is not None and math.isfinite(speed) else None
            entry.value, entry.sampled_at = value, now

    def project(self, job: Job, *, bot_id: int, file_id: str) -> dict[str, Any] | None:
        generation = self._current_generation()
        with self._lock:
            entry = self._entries.get(job.id)
            if (not entry or not entry.value or not self.eligible(job, entry.owner.boot_id)
                    or (job.item_id, bot_id, file_id) != (entry.owner.item_id, entry.owner.bot_id, entry.owner.file_id)
                    or job.attempt_sequence != entry.owner.attempt or generation != entry.owner.generation
                    or not 0 <= self.clock() - entry.sampled_at <= self.ttl):
                return None
            return dict(entry.value)
