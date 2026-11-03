"""A retained target part never authorizes deletion on a replaced mount."""
import pytest
from test_retained_cache import seed

from app.core.errors import AppError
from app.db.models import Job, PublishJournal


@pytest.mark.parametrize("operation", ["retry", "failure"])
@pytest.mark.parametrize("replaced", [False, True])
def test_target_part_cleanup_validates_enrolled_root(client, config, monkeypatch, operation, replaced):
    state, _, _, _, job_id, _ = seed(client, config)
    temporary = ".part.owned-target"
    original = config.download_root / temporary
    original.write_bytes(b"retained target part")
    with state.db.transaction() as session:
        job = session.get(Job, job_id)
        relative = job.relative_path
        session.add(PublishJournal(job_id=job_id, temporary_path=temporary,
                                   final_path=relative, stage="cleanup_failed"))
    if replaced:
        previous = config.download_root.with_name("previous-enrolled-downloads")
        config.download_root.rename(previous)
        config.download_root.mkdir()
        original = previous / temporary
        (config.download_root / temporary).write_bytes(b"unrelated replacement file")
    storage = state.storage.get("default")
    check = storage.check
    def identity_only(required=0, *, check_space=True):
        assert not check_space, "part cleanup must work even when free space is exhausted"
        return check(required, check_space=False)
    monkeypatch.setattr(storage, "check", identity_only)
    if operation == "retry":
        if replaced:
            with pytest.raises(AppError, match="目标临时文件仍无法清理"):
                state.runtime.worker.journal_copying(job_id, ".part.next", relative)
        else:
            state.runtime.worker.journal_copying(job_id, ".part.next", relative)
    else:
        state.runtime.worker.failed(job_id, "NO_SPACE", "保存空间不足", False)
    if replaced:
        assert original.read_bytes() == b"retained target part"
        assert (config.download_root / temporary).read_bytes() == b"unrelated replacement file"
        with state.db.sessions() as session:
            assert session.get(PublishJournal, job_id).temporary_path == temporary
    else:
        assert not original.exists()
