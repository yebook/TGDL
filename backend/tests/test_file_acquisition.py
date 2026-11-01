import asyncio
from dataclasses import replace
from types import SimpleNamespace

import pytest
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import GetFile
from aiohttp import web
from fastapi.testclient import TestClient
from sqlalchemy import select
from test_pipeline import confirm_all, message, setup_pipeline

from app.bot.client import TelegramClient
from app.core.errors import AppError
from app.db.models import FileReservation, Job, LocalApiRuntime
from app.schemas.contracts import UserSettings

TOKEN = "123456789:" + "a" * 35
NATIVE_ID = "f" * 64


async def until(predicate) -> None:
    async with asyncio.timeout(5):
        while not predicate():
            await asyncio.sleep(0.01)


class NativeClient:
    mode = "local"

    def __init__(self, source: str) -> None:
        self.bot = SimpleNamespace(token=TOKEN)
        self.source = source
        self.entered = asyncio.Event()
        self.finish = asyncio.Event()
        self.prepare_finish = asyncio.Event()
        self.prepare_finish.set()
        self.settled = False
        self.cancel_calls = 0
        self.get_calls = 0
        self.fail_query = False
        self.disconnect = False
        self.closed = False

    async def file_acquisition(self, generation, *, file_id=None, acquisition_id=None, cancel=False):
        assert generation == "child-one"
        if file_id:
            await self.prepare_finish.wait()
            return {"generation": generation, "acquisition_id": NATIVE_ID, "state": "prepared", "settled": False}
        assert acquisition_id == NATIVE_ID
        if cancel:
            self.cancel_calls += 1
        if self.fail_query:
            raise AppError("ACQUISITION_CONTROL_UNAVAILABLE", "control unavailable")
        return {"generation": generation, "acquisition_id": NATIVE_ID,
                "state": "cancelled" if self.settled else "cancelling", "settled": self.settled}

    async def get_file(self, file_id, **kwargs):
        assert kwargs == {"generation": "child-one", "acquisition_id": NATIVE_ID}
        self.get_calls += 1
        self.entered.set()
        if self.disconnect:
            raise ConnectionError("lost response")
        await self.finish.wait()
        return self.source

    async def close(self):
        self.closed = True


def pipeline(client):
    state, ingest = setup_pipeline(client)
    ingest.message(7, message(), "in")
    request_id = confirm_all(state)
    worker = state.runtime.worker
    worker.acquisition_generation = lambda: "child-one"
    job_id = worker.claim()
    return state, worker, request_id, job_id


async def test_cancel_requires_native_stop_proof_and_joins_http(client: TestClient):
    state, worker, request_id, job_id = pipeline(client)
    fake = NativeClient("unused")
    task = asyncio.create_task(worker.run(fake, job_id))
    await fake.entered.wait()
    owners = worker.acquisitions.owners()
    assert len(owners) == 1
    assert owners[0].token_encrypted != TOKEN
    state.tasks.cancel(request_id)
    await until(lambda: fake.cancel_calls > 0)
    assert not task.done()
    assert state.runtime.counts()[0] == 1
    with state.db.sessions() as session:
        assert session.scalar(select(FileReservation)) is not None
    fake.settled = True
    await asyncio.wait_for(task, 5)
    assert fake.get_calls == 1
    assert state.tasks.detail(request_id)["status"] == "cancelled"
    assert state.tasks.detail(request_id)["can_cancel"] is False
    assert state.runtime.counts()[0] == 0
    assert worker.acquisitions.owners() == []
    with state.db.sessions() as session:
        assert session.scalar(select(FileReservation)) is None


async def test_cancel_while_prepare_pending_cannot_issue_getfile(client: TestClient):
    state, worker, request_id, job_id = pipeline(client)
    fake = NativeClient("unused")
    fake.prepare_finish.clear()
    task = asyncio.create_task(worker.run(fake, job_id))
    await asyncio.sleep(0.1)
    state.tasks.cancel(request_id)
    fake.settled = True
    fake.prepare_finish.set()
    await asyncio.wait_for(task, 5)
    assert fake.get_calls == 0
    assert fake.cancel_calls == 1
    assert state.tasks.detail(request_id)["status"] == "cancelled"


async def test_lost_stop_response_keeps_owner_and_slot(client: TestClient, monkeypatch):
    state, worker, request_id, job_id = pipeline(client)
    fake = NativeClient("unused")
    task = asyncio.create_task(worker.run(fake, job_id))
    await fake.entered.wait()
    fake.fail_query = True
    state.tasks.cancel(request_id)
    await until(lambda: fake.cancel_calls > 0)
    await asyncio.wait_for(task, 5)
    assert state.tasks.detail(request_id)["status"] == "recovery_blocked"
    assert state.runtime.counts()[0] == 1
    assert len(worker.acquisitions.owners(cancelled_only=True)) == 1
    fake.fail_query, fake.settled = False, True
    monkeypatch.setattr("app.workers.runtime.TelegramClient", lambda *args: fake)
    await state.runtime.reconcile_acquisition_cancellations()
    assert state.runtime.counts()[0] == 0


async def test_same_child_recovery_uses_encrypted_original_credential_and_owner_fence(client, monkeypatch, authenticated):
    state, worker, request_id, job_id = pipeline(client)
    fake = NativeClient("unused")
    fake.disconnect = True
    await worker.run(fake, job_id)
    assert state.tasks.detail(request_id)["status"] == "recovery_blocked"
    assert "可取消任务" in state.tasks.detail(request_id)["items"][0]["error_message"]
    assert "取消获取" in authenticated.get("/api/v1/system/status").json()["recovery"]["message"]
    owner = worker.acquisitions.owners()[0]
    state.tasks.cancel(request_id)
    assert "正在确认取消结果" in state.tasks.detail(request_id)["items"][0]["error_message"]
    fake.settled = True
    made = []

    def factory(token, config, settings):
        assert token == TOKEN and settings.bot_api_mode == "local"
        made.append(token)
        return fake

    monkeypatch.setattr("app.workers.runtime.TelegramClient", factory)
    # Readiness and connection draining deliberately unavailable; recovery controls still work.
    state.runtime.bot_status["state"] = "error"
    state.runtime.local_api.update(phase="draining")
    await state.runtime.reconcile_acquisition_cancellations()
    assert made == [TOKEN] and fake.closed
    assert state.tasks.detail(request_id)["status"] == "cancelled"
    assert authenticated.get("/api/v1/system/status").json()["recovery"]["message"] == ""
    worker.failed(job_id, "CANCELLED", "stale", False, owner=replace(owner, attempt=owner.attempt + 1))
    with state.db.sessions() as session:
        assert session.get(Job, job_id).error_message == "下载已取消"
        assert session.get(LocalApiRuntime, 1).values["phase"] == "draining"


async def test_shutdown_retains_durable_control_for_same_child_cancel(client):
    state, worker, request_id, job_id = pipeline(client)
    fake = NativeClient("unused")
    task = asyncio.create_task(worker.run(fake, job_id))
    await fake.entered.wait()
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert state.tasks.detail(request_id)["status"] == "recovery_blocked"
    assert len(worker.acquisitions.owners()) == 1
    assert fake.cancel_calls == 0  # Shutdown does not invent user cancellation.


async def test_successful_getfile_waits_for_teardown_and_remains_cancellable(client, config):
    state, worker, request_id, job_id = pipeline(client)
    source = config.bot_api_root / "native-source"
    source.write_bytes(b"abcdef")
    fake = NativeClient(str(source))
    task = asyncio.create_task(worker.run(fake, job_id))
    await fake.entered.wait()
    fake.finish.set()
    await asyncio.sleep(0.2)
    assert not task.done() and state.runtime.counts()[0] == 1
    assert state.tasks.detail(request_id)["items"][0]["phase"] == "fetching"
    state.tasks.cancel(request_id)
    assert state.tasks.detail(request_id)["can_cancel"] is False
    fake.settled = True
    await asyncio.wait_for(task, 5)
    assert state.tasks.detail(request_id)["status"] == "cancelled"
    assert list(config.download_root.rglob("*.mp4")) == []


async def test_cancel_waiter_does_not_stop_shared_reservation_owner(client):
    state, worker, request_id, job_id = pipeline(client)
    fake = NativeClient("unused")
    task = asyncio.create_task(worker.run(fake, job_id))
    await fake.entered.wait()
    duplicate = message(2)
    duplicate["video"]["file_unique_id"] = "unique-1"
    state.runtime.ingest.message(7, duplicate, "in2")
    waiter_request = confirm_all(state)
    assert worker.claim() is None
    with state.db.sessions() as session:
        assert session.scalar(select(Job).where(Job.request_id == waiter_request)).state == "waiting_existing"
    state.tasks.cancel(waiter_request)
    assert state.tasks.detail(waiter_request)["status"] == "cancelled"
    assert fake.cancel_calls == 0
    assert state.runtime.counts()[0] == 1
    state.tasks.cancel(request_id)
    fake.settled = True
    await asyncio.wait_for(task, 5)


async def test_settled_completion_saves_and_clears_control_record(client, config):
    state, worker, request_id, job_id = pipeline(client)
    source = config.bot_api_root / "native-source"
    source.write_bytes(b"abcdef")

    class Completed(NativeClient):
        async def file_acquisition(self, generation, **kwargs):
            result = await super().file_acquisition(generation, **kwargs)
            if not kwargs.get("file_id"):
                result.update(state="completed", settled=True)
            return result

    fake = Completed(str(source))
    fake.finish.set()
    await worker.run(fake, job_id)
    assert state.tasks.detail(request_id)["status"] == "succeeded"
    with state.db.sessions() as session:
        assert session.get(LocalApiRuntime, 1).values["acquisition_controls"] == {}


async def test_fixed_native_cancel_reply_alone_is_not_stop_proof(client):
    state, worker, request_id, job_id = pipeline(client)

    class CancelledResponse(NativeClient):
        async def get_file(self, file_id, **kwargs):
            self.entered.set()
            raise AppError("ACQUISITION_CANCELLED", "local cancellation response")

    fake = CancelledResponse("unused")
    task = asyncio.create_task(worker.run(fake, job_id))
    await fake.entered.wait()
    await asyncio.sleep(0.2)
    assert not task.done()
    assert state.runtime.counts()[0] == 1
    fake.settled = True
    await asyncio.wait_for(task, 5)
    assert state.tasks.detail(request_id)["status"] == "cancelled"


async def test_private_transport_validation_and_protection_isolation(config):
    received = []
    result = {"generation": "child-one", "acquisition_id": NATIVE_ID, "state": "prepared", "settled": False}

    async def handler(request):
        received.append((request.path.rsplit("/", 1)[-1], dict(await request.post())))
        return web.json_response({"ok": True, "result": result})

    app = web.Application()
    app.router.add_post("/{tail:.*}", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    config.bot_api_base = f"http://127.0.0.1:{port}"
    api = TelegramClient(TOKEN, config, UserSettings(bot_api_mode="local"))

    class NoSafety:
        async def before(self, *args, **kwargs):
            raise AssertionError("private control entered request safety")

        def observe(self, *args, **kwargs):
            raise AssertionError("private control changed request safety")

    api.safety = NoSafety()
    try:
        assert await api.file_acquisition("child-one", file_id="alias") == result
        result.update(state="cancelled", settled=True)
        assert (await api.file_acquisition("child-one", acquisition_id=NATIVE_ID, cancel=True))["settled"]
        assert received[0] == ("tgdPrepareFileAcquisition", {"generation": "child-one", "file_id": "alias"})
        result["generation"] = "wrong"
        with pytest.raises(AppError, match="获取控制"):
            await api.file_acquisition("child-one", acquisition_id=NATIVE_ID)
        result["generation"], result["settled"] = "child-one", False
        with pytest.raises(AppError):
            await api.file_acquisition("child-one", acquisition_id=NATIVE_ID)
    finally:
        await api.close()
        await runner.cleanup()


async def test_fixed_cancel_error_bypasses_request_failure_accounting(config, monkeypatch):
    api = TelegramClient(TOKEN, config, UserSettings(bot_api_mode="local"))
    calls = []

    class Safety:
        async def before(self, *args, **kwargs):
            calls.append("before")

        def observe(self, *args, **kwargs):
            calls.append("observe")

    async def transport(bot, method, **kwargs):
        assert isinstance(method, GetFile)
        assert method.model_extra == {"tgd_generation": "child-one", "tgd_acquisition_id": NATIVE_ID}
        raise TelegramBadRequest(method=method, message="TGD_ACQUISITION_CANCELLED")

    monkeypatch.setattr(api.bot.session, "make_request", transport)
    api.safety = Safety()
    try:
        with pytest.raises(AppError) as error:
            await api.get_file("alias", generation="child-one", acquisition_id=NATIVE_ID)
        assert error.value.code == "ACQUISITION_CANCELLED"
        assert calls == ["before"]
    finally:
        await api.close()


async def test_actual_sdk_sends_protected_getfile_fields_and_decodes_local_cancel(config):
    received = []
    cancel_response = False

    async def handler(request):
        received.append(dict(await request.post()))
        if cancel_response:
            return web.json_response({"ok": False, "error_code": 400,
                                      "description": "Bad Request: TGD_ACQUISITION_CANCELLED"}, status=400)
        return web.json_response({"ok": True, "result": {"file_id": "alias", "file_unique_id": "unique",
                                                          "file_path": "/native/file", "file_size": 6}})

    app = web.Application()
    app.router.add_post("/{tail:.*}", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    config.bot_api_base = f"http://127.0.0.1:{port}"
    api = TelegramClient(TOKEN, config, UserSettings(bot_api_mode="local"))
    try:
        assert await api.get_file("alias", generation="child-one", acquisition_id=NATIVE_ID) == "/native/file"
        assert received == [{"file_id": "alias", "tgd_generation": "child-one", "tgd_acquisition_id": NATIVE_ID}]
        cancel_response = True
        with pytest.raises(AppError) as error:
            await api.get_file("alias", generation="child-one", acquisition_id=NATIVE_ID)
        assert error.value.code == "ACQUISITION_CANCELLED"
    finally:
        await api.close()
        await runner.cleanup()
