import asyncio

import pytest
from test_file_acquisition import NativeClient, pipeline
from test_local_api import heartbeat

from app.core.errors import AppError
from app.schemas.contracts import UserSettings


class LostCancelReply(NativeClient):
    async def file_acquisition(self, generation, **kwargs):
        if kwargs.get("cancel"):
            self.cancel_calls += 1
            self.settled = True
            raise AppError("ACQUISITION_CONTROL_UNAVAILABLE", "lost accepted stop reply")
        return await super().file_acquisition(generation, **kwargs)


async def test_lost_accepted_stop_reply_queries_terminal_proof_without_repeating_getfile(client):
    state, worker, request_id, job_id = pipeline(client)
    fake = LostCancelReply("unused")
    task = asyncio.create_task(worker.run(fake, job_id))
    await fake.entered.wait()
    state.tasks.cancel(request_id)
    await asyncio.wait_for(task, 3)
    assert fake.cancel_calls == 1 and fake.get_calls == 1
    assert state.tasks.detail(request_id)["status"] == "cancelled"
    assert state.runtime.counts()[0] == 0


async def test_same_child_recovery_queries_proof_after_lost_stop_reply(client, monkeypatch):
    state, worker, request_id, job_id = pipeline(client)
    fake = LostCancelReply("unused")
    fake.disconnect = True
    await worker.run(fake, job_id)
    state.tasks.cancel(request_id)
    monkeypatch.setattr("app.workers.runtime.TelegramClient", lambda *args: fake)
    await state.runtime.reconcile_acquisition_cancellations()
    assert fake.cancel_calls == 1 and fake.closed
    assert state.tasks.detail(request_id)["status"] == "cancelled"
    assert worker.acquisitions.owners() == []


async def test_unavailable_controls_handoff_keeps_slot_but_allows_explicit_managed_restart(client, config):
    state, worker, request_id, job_id = pipeline(client)
    fake = NativeClient("unused")
    task = asyncio.create_task(worker.run(fake, job_id))
    state.runtime.running[job_id] = task
    await fake.entered.wait()
    fake.fail_query = True
    state.tasks.cancel(request_id)
    await asyncio.wait_for(task, 3)
    detail = state.tasks.detail(request_id)
    assert detail["status"] == "recovery_blocked" and not detail["can_cancel"]
    assert state.runtime.counts()[:2] == (1, 1)
    assert len(worker.acquisitions.owners(cancelled_only=True)) == 1
    assert not fake.finish.is_set()  # Original HTTP owner was joined, not completed.

    config.managed_local_api = True
    config.local_api_control_dir = config.data_dir / "control"
    service = state.runtime.local_api
    heartbeat(service, state="running", generation="child-one")
    service.update(phase="idle", applied_mode="local", logout_done=True,
                   snapshot=state.settings.secrets.encrypt(UserSettings(
                       bot_api_mode="local", telegram_api_id="1234", telegram_api_hash="a" * 32
                   ).model_dump_json()))
    assert service.status()["can_restart"]
    service.action(True)
    assert await service.reconcile(state.runtime)
    assert service.read()["command_id"]
    assert (config.local_api_control_dir / "desired.json").is_file()
    assert state.runtime.counts()[:2] == (1, 1)  # Command issuance is not replacement proof.


@pytest.mark.parametrize("code", ["CACHE_CLEANUP_BUSY", "ACQUISITION_REJECTED"])
async def test_known_rejection_retires_idle_preparation_and_preserves_original_outcome(client, code):
    state, worker, request_id, job_id = pipeline(client)

    class Rejected(NativeClient):
        async def get_file(self, file_id, **kwargs):
            self.get_calls += 1
            raise AppError(code, "definite unsent native rejection")

        async def file_acquisition(self, generation, **kwargs):
            if kwargs.get("cancel"):
                self.settled = True
            result = await super().file_acquisition(generation, **kwargs)
            if not kwargs.get("file_id") and not self.settled:
                result["state"] = "prepared"
            return result

    fake = Rejected("unused")
    await asyncio.wait_for(worker.run(fake, job_id), 3)
    item = state.tasks.detail(request_id)["items"][0]
    assert item["status"] == ("retry_wait" if code == "CACHE_CLEANUP_BUSY" else "failed")
    assert item["error_code"] == code
    assert fake.cancel_calls == 1 and fake.get_calls == 1
    assert state.runtime.counts()[0] == 0
    assert worker.acquisitions.owners() == []
