"""Optional final-media removal; durable per-file outcomes, no destructive replay."""
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.db.models import (
    CacheCleanup,
    FileDeletionOperation,
    FileReservation,
    Job,
    MediaRequest,
    PublishJournal,
    StoredFile,
)
from app.db.session import Database
from app.services.cache_maintenance import require_no_maintenance
from app.storage.files import StorageManager
from app.storage.saved_media import parts, remove_verified

OUTCOMES = ("deleted", "missing", "retained_shared", "retained_unverified", "failed", "uncertain")


def _group_directory(session: Session, facts: list[StoredFile], root: str, path: str) -> str | None:
    """Use the original physical output's frozen group destination, never an alias title."""
    try:
        components = parts(path)
        if len(components) != 3 or date.fromisoformat(components[0]).isoformat() != components[0]:
            return None
    except ValueError:
        return None
    directory = "/".join(components[:-1])
    for fact in facts:
        job = session.get(Job, fact.job_id)
        request = session.get(MediaRequest, job.request_id) if job else None
        if (job and request and (job.root_id, job.relative_path) == (root, path)
                and request.root_id == root and request.kind == "group" and request.destination == directory):
            return directory
    return None


class SavedMediaService:
    def __init__(self, db: Database, storage: StorageManager) -> None:
        self.db, self.storage = db, storage
        self.runtime: Any = None

    def recover(self, operation_id: str | None = None) -> None:
        with self.db.transaction() as session:
            query = select(FileDeletionOperation).where(FileDeletionOperation.state == "running")
            if operation_id is not None:
                query = query.where(FileDeletionOperation.id == operation_id)
            for operation in session.scalars(query):
                operation.entries = [dict(entry, outcome="uncertain") if entry["outcome"] == "pending" else entry
                                     for entry in operation.entries]
                operation.state = "complete"

    @staticmethod
    def result(operation: FileDeletionOperation) -> dict[str, Any]:
        return {"deleted_ids": operation.request_ids, "operation_id": operation.id,
                "files": {key: sum(entry["outcome"] == key for entry in operation.entries) for key in OUTCOMES}}

    def existing(self, session: Session, operation_id: str, ids: list[str], delete_files: bool) -> dict[str, Any] | None:
        operation = session.get(FileDeletionOperation, operation_id)
        if not operation:
            return None
        if sorted(operation.request_ids) != sorted(ids) or operation.delete_files != delete_files:
            raise AppError("FILE_DELETE_CONFLICT", "删除操作编号与所选任务不匹配", 409)
        if operation.state == "running":
            raise AppError("FILE_DELETE_RUNNING", "文件删除正在处理，请稍后重试；如持续未恢复，请重启应用后重试", 409)
        return self.result(operation)

    def prepare(self, session: Session, operation_id: str, ids: list[str], *, cancellation: bool = False,
                cleanup_scope: set[str] | None = None) -> None:
        selected = list(session.scalars(select(Job).where(Job.request_id.in_(ids), Job.state.in_(["succeeded", "already_exists"]))))
        if cancellation and not selected:
            # No final path can be touched. Unrelated maintenance must not strand
            # a cancellation that has no output files at all.
            session.add(FileDeletionOperation(id=operation_id, request_ids=ids, entries=[]))
            return
        require_no_maintenance(session)
        runtime = self.runtime
        selected_ids = set(session.scalars(select(Job.id).where(Job.request_id.in_(ids))))
        if (runtime and (runtime.worker.cleanup_paused or any(not task.done() for key, task in tuple(runtime.running.items())
                                                             if not cancellation or key in selected_ids))):
            raise AppError("TASK_FILE_DELETE_BUSY", "下载或缓存处理尚未结束，请稍后重试", 409)
        busy_jobs = select(Job.id).where(Job.state.not_in({"succeeded", "already_exists", "failed", "cancelled"}))
        journals, reservations = select(PublishJournal.job_id), select(FileReservation.id)
        if cancellation:
            busy_jobs = busy_jobs.where(Job.request_id.in_(ids))
            journals = journals.where(PublishJournal.job_id.in_(selected_ids))
            reservations = reservations.where(FileReservation.job_id.in_(selected_ids))
        if (session.scalar(busy_jobs.limit(1)) or session.scalar(journals.limit(1)) or session.scalar(reservations.limit(1))):
            raise AppError("TASK_FILE_DELETE_BUSY", "存在下载或恢复任务，请等待处理结束后删除文件", 409)
        if session.scalar(select(CacheCleanup.job_id).where(CacheCleanup.error_code.in_(
                ["CACHE_CLEANUP_IN_FLIGHT", "CACHE_CLEANUP_UNAVAILABLE"])).limit(1)):
            raise AppError("TASK_FILE_DELETE_BUSY", "缓存清理结果尚未确认，请等待自动核对完成后重试", 409)
        entries = []
        for root, path in sorted({(job.root_id, job.relative_path) for job in selected}):
            owners = {owner for job in selected if (job.root_id, job.relative_path) == (root, path)
                      for owner in (job.id, job.linked_job_id) if owner}
            shared_query = select(Job.id).join(MediaRequest, MediaRequest.id == Job.request_id).where(
                Job.request_id.not_in(cleanup_scope if cancellation and cleanup_scope is not None else ids),
                Job.root_id == root, Job.relative_path == path, Job.state.not_in(["failed", "cancelled"]))
            if not cancellation:
                shared_query = shared_query.where(MediaRequest.deleted_at.is_(None))
            shared = session.scalar(shared_query.limit(1))
            facts = list(session.scalars(select(StoredFile).where(StoredFile.root_id == root, StoredFile.relative_path == path,
                                                                 StoredFile.job_id.in_(owners))))
            expected = {(row.size_bytes, row.sha256) for row in facts}
            entry: dict[str, Any] = {"root_id": root, "relative_path": path, "outcome": "pending", "owner_ids": sorted(owners)}
            if shared:
                entry["outcome"] = "retained_shared"
            elif len(expected) != 1:
                # No retained content proof: only establish already-absent paths;
                # an extant file is kept without reading/hash work.
                entry.update(size_bytes=-1, sha256=None, stored_ids=[])
            else:
                entry["size_bytes"], entry["sha256"] = expected.pop()
                entry["stored_ids"] = [row.id for row in facts]
                directory = _group_directory(session, facts, root, path)
                if directory is not None:
                    entry["prune_directory"] = directory
            entries.append(entry)
        session.add(FileDeletionOperation(id=operation_id, request_ids=ids, entries=entries))

    @staticmethod
    def retire_verified(session: Session, entry: dict[str, Any]) -> None:
        """Retire only the proven old publication; never an unknown native owner."""
        for row in session.scalars(select(StoredFile).where(StoredFile.id.in_(entry.get("stored_ids", [])))):
            if (row.root_id, row.relative_path, row.size_bytes, row.sha256) == (
                    entry["root_id"], entry["relative_path"], entry["size_bytes"], entry["sha256"]):
                session.delete(row)
        for cleanup in session.scalars(select(CacheCleanup).where(
                CacheCleanup.job_id.in_(entry.get("owner_ids", [])),
                CacheCleanup.root_id == entry["root_id"], CacheCleanup.relative_path == entry["relative_path"],
                CacheCleanup.size_bytes == entry["size_bytes"], CacheCleanup.sha256 == entry["sha256"],
                CacheCleanup.error_code.is_(None) | CacheCleanup.error_code.not_in(
                    ["CACHE_CLEANUP_IN_FLIGHT", "CACHE_CLEANUP_UNAVAILABLE"]))):
            session.delete(cleanup)

    def execute(self, operation_id: str) -> dict[str, Any]:
        with self.db.sessions() as session:
            operation = session.get(FileDeletionOperation, operation_id)
            assert operation
            entries = operation.entries
        for index, entry in enumerate(entries):
            if entry["outcome"] != "pending":
                continue
            try:
                outcome = remove_verified(self.storage.get(entry["root_id"]), entry)
            except AppError:
                outcome = "retained_unverified"
            with self.db.transaction() as session:
                operation = session.get(FileDeletionOperation, operation_id)
                assert operation and operation.state == "running"
                updated = [dict(value) for value in operation.entries]
                updated[index]["outcome"] = outcome
                operation.entries = updated
                if outcome in {"deleted", "missing"}:
                    self.retire_verified(session, entry)
        with self.db.transaction() as session:
            operation = session.get(FileDeletionOperation, operation_id)
            assert operation
            operation.state = "complete"
            return self.result(operation)
