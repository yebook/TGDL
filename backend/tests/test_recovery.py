import asyncio
import time

import pytest
from aiogram.exceptions import TelegramRetryAfter
from aiogram.methods import GetFile
from fastapi.testclient import TestClient
from sqlalchemy import select
from test_pipeline import confirm_all, message, setup_pipeline

from app.core.config import Config
from app.db.models import Attempt, Job, MediaRequest, PublishJournal
from app.schemas.contracts import SettingsPatch
from app.storage.files import contained, file_hash


def prepare(client: TestClient):
    state, ingest = setup_pipeline(client)
    ingest.message(7, message(), "in")
    request_id = confirm_all(state)
    job_id = state.runtime.worker.claim()
    return state, request_id, job_id


def test_incomplete_copy_crash_is_cleaned_and_requeued(client: TestClient, config: Config) -> None:
    state, request_id, job_id = prepare(client)
    with state.db.sessions() as session:
        job = session.get(Job, job_id)
        final = contained(config.download_root, job.relative_path, create_parent=True)
        relative = job.relative_path
    part = final.with_name(f".part.{job_id}")
    state.runtime.worker.journal_copying(job_id, part.relative_to(config.download_root).as_posix(), relative)
    part.write_bytes(b"part")
    state.runtime.worker.recover(False)
    assert not part.exists()
    assert state.tasks.detail(request_id)["status"] == "queued"


def test_ready_journal_recovers_and_respects_cancellation(client: TestClient, config: Config) -> None:
    state, request_id, job_id = prepare(client)
    with state.db.sessions() as session:
        job = session.get(Job, job_id)
        final = contained(config.download_root, job.relative_path, create_parent=True)
        relative = job.relative_path
    part = final.with_name(f".part.{job_id}")
    state.runtime.worker.journal_copying(job_id, part.relative_to(config.download_root).as_posix(), relative)
    part.write_bytes(b"abcdef")
    state.runtime.worker.journal_ready(job_id, *file_hash(part))
    state.tasks.cancel(request_id)
    state.runtime.worker.recover(False)
    assert not final.exists() and not part.exists()
    assert state.tasks.detail(request_id)["status"] == "cancelled"


def test_collision_journal_commits_new_path_before_second_publish(client: TestClient, config: Config) -> None:
    state, request_id, job_id = prepare(client)
    with state.db.sessions() as session:
        job = session.get(Job, job_id)
        final = contained(config.download_root, job.relative_path, create_parent=True)
        relative = job.relative_path
    part = final.with_name(f".part.{job_id}")
    state.runtime.worker.journal_copying(job_id, part.relative_to(config.download_root).as_posix(), relative)
    part.write_bytes(b"abcdef")
    final.write_bytes(b"existing-user-file")
    state.runtime.worker.journal_ready(job_id, *file_hash(part))
    state.runtime.worker.publish(job_id)
    with state.db.sessions() as session:
        journal = session.get(PublishJournal, job_id)
        assert journal.final_path != relative
    state.runtime.worker.recover(False)
    detail = state.tasks.detail(request_id)
    assert detail["status"] == "succeeded"
    assert final.read_bytes() == b"existing-user-file"
    assert (config.download_root / detail["items"][0]["relative_path"]).read_bytes() == b"abcdef"


def test_retry_budget_and_manual_sequence_preserve_destination(client: TestClient) -> None:
    state, request_id, job_id = prepare(client)
    original = state.tasks.detail(request_id)["items"][0]["relative_path"]
    worker = state.runtime.worker
    for retry in range(3):
        worker.failed(job_id, "NETWORK_ERROR", "网络故障", True)
        assert state.tasks.detail(request_id)["status"] == "retry_wait"
        with state.db.transaction() as session:
            job = session.get(Job, job_id)
            assert job.auto_retries == retry + 1
            assert job.next_run_at > time.time()
            job.next_run_at = 0
        assert worker.claim() == job_id
    worker.failed(job_id, "NETWORK_ERROR", "网络故障", True)
    assert state.tasks.detail(request_id)["status"] == "failed"
    state.tasks.retry(request_id, None)
    assert worker.claim() == job_id
    detail = state.tasks.detail(request_id)
    assert detail["items"][0]["relative_path"] == original
    assert len(detail["items"][0]["attempts"]) == 5
    with state.db.sessions() as session:
        assert len(list(session.scalars(select(Attempt).where(Attempt.job_id == job_id)))) == 5


def test_rate_limit_delay_and_permanent_error_classification(client: TestClient) -> None:
    state, request_id, job_id = prepare(client)
    code, _, retry, delay = state.runtime.worker.classify(TelegramRetryAfter(method=GetFile(file_id="x"), message="limited", retry_after=100))
    assert code == "RATE_LIMITED" and retry and delay == 100
    state.runtime.worker.failed(job_id, code, "限流", retry, False, delay)
    with state.db.sessions() as session:
        job = session.get(Job, job_id)
        assert job.next_run_at >= time.time() + 99


def test_group_scheduler_shares_global_slots(client: TestClient) -> None:
    state, ingest = setup_pipeline(client)
    for index in range(1, 5):
        ingest.message(7, message(index, "group"), f"in{index}")
    group = confirm_all(state)
    ingest.message(7, message(9), "single")
    single = confirm_all(state)
    first = state.runtime.worker.claim()
    second = state.runtime.worker.claim()
    with state.db.sessions() as session:
        ids = {session.get(Job, first).request_id, session.get(Job, second).request_id}
    assert ids == {group, single}


def test_root_change_invalidates_confirmation_card(client: TestClient, config: Config, monkeypatch: pytest.MonkeyPatch) -> None:
    import json
    extra = config.download_root.parent / "extra"
    extra.mkdir()
    monkeypatch.setenv("TGD_STORAGE_ROOTS", json.dumps({"extra": str(extra)}))
    state, ingest = setup_pipeline(client)
    ingest.message(7, message(), "in")
    state.tasks.freeze_due()
    with state.db.sessions() as session:
        request = session.scalar(select(MediaRequest))
        request_id, nonce = request.id, request.nonce
    candidate = state.settings.candidate(SettingsPatch(download_root_id="extra"))
    state.settings.save(SettingsPatch(revision=2), candidate)
    assert state.tasks.confirm(request_id, nonce, 42, 42, "confirm") == "configuration_changed"
    state.tasks.freeze_due()
    with state.db.sessions() as session:
        request = session.get(MediaRequest, request_id)
        assert request.root_id == "extra" and request.nonce != nonce


@pytest.mark.integration
async def test_dispatcher_global_three_and_dynamic_lowering(client: TestClient, config: Config) -> None:
    state, ingest = setup_pipeline(client)
    for index in range(1, 6):
        ingest.message(7, message(index), f"in{index}")
        confirm_all(state)
    settings = state.settings.read()[1].model_copy(update={"bot_token": "123456:fake-token-used-only-by-local-tests"})
    state.settings.save(SettingsPatch(revision=2), settings)
    source = config.bot_api_root / "source"
    source.write_bytes(b"abcdef")
    gates = [asyncio.Event() for _ in range(5)]
    entered = []

    class ControlledClient:
        async def get_file(self, file_id: str) -> str:
            slot = len(entered)
            entered.append(file_id)
            await gates[slot].wait()
            return str(source)

    runtime = state.runtime
    runtime.client = ControlledClient()
    from app.core.security import digest
    runtime.bot_id, runtime.token_hash = 7, digest(settings.bot_token)
    runtime.client_signature = state.settings.client_signature(settings)
    runtime.bot_status["state"] = "online"
    tick = asyncio.create_task(runtime.tick_loop())

    async def wait_for(predicate) -> None:
        async with asyncio.timeout(5):
            while not predicate():
                await asyncio.sleep(0.01)

    try:
        await wait_for(lambda: len(entered) == 3)
        assert runtime.counts()[0] == 3
        state.settings.save(SettingsPatch(revision=3), settings.model_copy(update={"download_concurrency": 1}))
        gates[0].set()
        await wait_for(lambda: runtime.counts()[0] == 2)
        await asyncio.sleep(0.6)
        assert len(entered) == 3
        gates[1].set()
        gates[2].set()
        await wait_for(lambda: len(entered) == 4)
        await asyncio.sleep(0.6)
        assert len(entered) == 4 and runtime.counts()[0] == 1
    finally:
        tick.cancel()
        await asyncio.gather(tick, return_exceptions=True)
        for gate in gates:
            gate.set()
        await asyncio.gather(*runtime.running.values(), return_exceptions=True)
