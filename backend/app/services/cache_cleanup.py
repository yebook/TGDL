"""Durable post-publication work; never mutate API-owned files from this process."""
import stat
import time
from pathlib import Path

from sqlalchemy import select

from app.core.security import Secrets
from app.db.models import CacheCleanup, Job
from app.db.session import Database
from app.services.diagnostics import DiagnosticLog
from app.storage.files import StorageManager, contained, regular_file_hash


class CacheCleanupService:
    def __init__(self, db: Database, storage: StorageManager, diagnostics: DiagnosticLog, secrets: Secrets) -> None:
        self.db, self.storage, self.diagnostics = db, storage, diagnostics
        self.secrets = secrets

    def source_path(self, row: CacheCleanup) -> str:
        # Native Bot API paths can embed its Bot Token. Only decrypt at private I/O.
        return self.secrets.decrypt(row.source_path_encrypted)

    def due(self, bot_id: int) -> CacheCleanup | None:
        with self.db.sessions() as session:
            return session.scalar(select(CacheCleanup).where(CacheCleanup.bot_id == bot_id,
                CacheCleanup.next_run_at <= time.time()).order_by(CacheCleanup.next_run_at, CacheCleanup.job_id).limit(1))

    def local_owners(self) -> bool:
        with self.db.sessions() as session:
            return session.scalar(select(Job.id).where(Job.acquisition_mode == "local",
                Job.state.in_(["fetching", "saving", "cancelling", "recovery_blocked"])).limit(1)) is not None

    @staticmethod
    def fingerprint(path: Path) -> tuple[int, int, int, int, int]:
        value = path.stat(follow_symlinks=False)
        if not stat.S_ISREG(value.st_mode):
            raise ValueError("Final output is not regular")
        return value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns

    def verify_final(self, row: CacheCleanup) -> tuple[int, int, int, int, int] | None:
        storage = self.storage.get(row.root_id)
        storage.check(check_space=False)
        final = contained(storage.root, row.relative_path)
        before = self.fingerprint(final)
        if before[2] != row.size_bytes:
            return None
        matches = regular_file_hash(final) == (row.size_bytes, row.sha256)
        # Root identity must still match after the long read, too.
        storage.check(check_space=False)
        return before if matches and before == self.fingerprint(contained(storage.root, row.relative_path)) else None

    def final_unchanged(self, row: CacheCleanup, fingerprint: tuple[int, int, int, int, int]) -> bool:
        storage = self.storage.get(row.root_id)
        storage.check(check_space=False)
        return self.fingerprint(contained(storage.root, row.relative_path)) == fingerprint

    def retry(self, job_id: str, code: str = "CACHE_CLEANUP_UNAVAILABLE") -> None:
        with self.db.transaction() as session:
            row = session.get(CacheCleanup, job_id)
            if not row:
                return
            row.attempts += 1
            row.error_code = code
            row.next_run_at = time.time() + min(300, 5 * 2**min(row.attempts - 1, 6))
        self.diagnostics.emit("cache_cleanup", level="warning", mode="local", job_id=job_id,
                              outcome="retry_wait", error_code=code)

    def complete(self, job_id: str) -> None:
        with self.db.transaction() as session:
            row = session.get(CacheCleanup, job_id)
            if row:
                session.delete(row)
        self.diagnostics.emit("cache_cleanup", level="info", mode="local", job_id=job_id, outcome="success")
