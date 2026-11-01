"""Durable per-attempt local acquisition authority; telemetry is never stop proof."""
from dataclasses import asdict, dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.db.models import Job, LocalApiRuntime, MediaItem
from app.db.session import Database


def uncertain_message(session: Session, job: Job) -> str:
    row = session.get(LocalApiRuntime, 1)
    value = row.values.get("acquisition_controls", {}).get(job.id) if row else None
    if value and value.get("attempt") == job.attempt_sequence and value.get("boot_id") == job.boot_id:
        return ("正在确认取消结果；连接恢复后继续核对，无法确认时请重启本地 Bot API 恢复"
                if job.cancel_requested else "获取结果待确认，可取消任务以停止获取；无法确认时请重启本地 Bot API 恢复")
    return "本地 Bot API 可能仍在获取文件；请重启该服务以安全恢复并发槽"


@dataclass(frozen=True)
class AcquisitionOwner:
    job_id: str
    attempt: int
    boot_id: str
    bot_id: int
    credential: str
    token_encrypted: str
    generation: str
    acquisition_id: str


class AcquisitionStore:
    def __init__(self, db: Database) -> None:
        self.db = db

    def persist(self, owner: AcquisitionOwner) -> bool:
        with self.db.transaction() as session:
            job = session.get(Job, owner.job_id)
            item = session.get(MediaItem, job.item_id) if job else None
            if (not job or not item or job.attempt_sequence != owner.attempt or job.boot_id != owner.boot_id
                    or item.bot_id != owner.bot_id or job.phase != "fetching"
                    or job.state not in {"fetching", "cancelling"}):
                return False
            row = session.get(LocalApiRuntime, 1)
            if row is None:
                row = LocalApiRuntime(id=1, values={})
                session.add(row)
            records = dict(row.values.get("acquisition_controls", {}))
            # Never evict an unresolved native owner to admit another one.
            if owner.job_id not in records and len(records) >= 256:
                raise AppError("ACQUISITION_CONTROL_BUSY", "获取控制记录已满，请先处理恢复任务")
            records[owner.job_id] = asdict(owner)
            row.values = dict(row.values) | {"acquisition_controls": records}
            return True

    def owners(self, *, cancelled_only: bool = False) -> list[AcquisitionOwner]:
        with self.db.sessions() as session:
            row = session.get(LocalApiRuntime, 1)
            records = row.values.get("acquisition_controls", {}) if row else {}
            result = []
            for value in records.values():
                try:
                    owner = AcquisitionOwner(**value)
                except (TypeError, ValueError):
                    continue
                job = session.get(Job, owner.job_id)
                item = session.get(MediaItem, job.item_id) if job else None
                if (job and item and job.attempt_sequence == owner.attempt and job.boot_id == owner.boot_id
                        and item.bot_id == owner.bot_id and job.phase == "fetching"
                        and job.state in {"fetching", "cancelling", "recovery_blocked"}
                        and (not cancelled_only or job.cancel_requested)):
                    result.append(owner)
            return result

    def remove(self, owner: AcquisitionOwner) -> None:
        with self.db.transaction() as session:
            row = session.get(LocalApiRuntime, 1)
            if row:
                records = dict(row.values.get("acquisition_controls", {}))
                if records.get(owner.job_id) == asdict(owner):
                    del records[owner.job_id]
                    row.values = dict(row.values) | {"acquisition_controls": records}

    def prune(self) -> None:
        """Only terminal/replaced attempts can discard their durable authority."""
        with self.db.transaction() as session:
            row = session.get(LocalApiRuntime, 1)
            if not row:
                return
            records = dict(row.values.get("acquisition_controls", {}))
            jobs = {job.id: job for job in session.scalars(select(Job).where(Job.id.in_(records)))}
            keep: dict[str, Any] = {}
            for key, value in records.items():
                job = jobs.get(key)
                if (job and job.state in {"fetching", "cancelling", "recovery_blocked"}
                        and value.get("attempt") == job.attempt_sequence and value.get("boot_id") == job.boot_id):
                    keep[key] = value
            row.values = dict(row.values) | {"acquisition_controls": keep}
