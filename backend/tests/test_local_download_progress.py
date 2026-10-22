import asyncio
import json
import time
from unittest.mock import AsyncMock

import pytest
from aiogram.client.telegram import TelegramAPIServer
from aiohttp import web
from fastapi.testclient import TestClient
from test_cloud_api import TOKEN, fake_api
from test_pipeline import confirm_all, message, setup_pipeline

from app.bot.client import TelegramClient
from app.core.config import Config
from app.db.models import Job, LocalApiRuntime
from app.schemas.contracts import SettingsPatch, UserSettings
from app.services.download_progress import DownloadProgress, decode_progress


def sample(count=0, total=100, kind="exact", **extra):
    return {"downloaded_bytes": count, "total_bytes": total, "total_kind": kind,
            "active": True, "completed": False, "speed_bps": None, **extra}


def envelope(*files, generation="generation-one"):
    return {"ok": True, "result": {"generation": generation, "files": list(files)}}


def job():
    return Job(id="job", request_id="request", item_id="item", root_id="root", relative_path="x",
               state="fetching", phase="fetching", boot_id="boot", acquisition_mode="local",
               attempt_sequence=1, cancel_requested=False)


def test_registry_speed_freshness_ownership_and_bounds():
    ticks, generation = [1.0], ["generation-one"]
    registry = DownloadProgress(clock=lambda: ticks[0], generation=lambda: generation[0], capacity=1)
    row = job()
    owner = registry.begin(row, "boot", bot_id=7, file_id="alias")
    assert owner
    registry.update(owner, generation[0], sample(50))
    assert registry.project(row, bot_id=7, file_id="alias")["speed_bps"] is None  # Cached bytes are not network throughput.
    assert registry.project(row, bot_id=8, file_id="alias") is None
    assert registry.project(row, bot_id=7, file_id="other-alias") is None
    ticks[0] += 1
    registry.update(owner, generation[0], sample(60))
    assert registry.project(row, bot_id=7, file_id="alias")["speed_bps"] == 10
    ticks[0] += 1
    registry.update(owner, generation[0], sample(60))
    assert registry.project(row, bot_id=7, file_id="alias")["speed_bps"] == 0  # Healthy unchanged read remains available.
    ticks[0] += 1
    registry.update(owner, generation[0], sample(10))
    assert registry.project(row, bot_id=7, file_id="alias")["speed_bps"] is None
    other = job()
    other.id = "other"
    assert registry.begin(other, "boot", bot_id=7, file_id="alias") is None
    row.attempt_sequence = 2
    assert registry.project(row, bot_id=7, file_id="alias") is None

    replacement = registry.begin(row, "boot", bot_id=7, file_id="alias")
    registry.update(owner, generation[0], sample(99))
    registry.clear(owner)
    registry.update(replacement, generation[0], sample(20))
    assert registry.project(row, bot_id=7, file_id="alias")["downloaded_bytes"] == 20
    for field, value in [("cancel_requested", True), ("state", "cancelling"), ("phase", "saving"),
                         ("boot_id", "old-boot"), ("acquisition_mode", "cloud")]:
        before = getattr(row, field)
        setattr(row, field, value)
        assert registry.project(row, bot_id=7, file_id="alias") is None
        setattr(row, field, before)
    ticks[0] += 5
    assert registry.project(row, bot_id=7, file_id="alias") is None
    registry.update(replacement, generation[0], sample(30))
    assert registry.project(row, bot_id=7, file_id="alias")["speed_bps"] is None
    generation[0] = "replacement-child"
    assert registry.project(row, bot_id=7, file_id="alias") is None
    registry.update(replacement, "replacement-child", sample(99))
    generation[0] = "generation-one"
    assert registry.project(row, bot_id=7, file_id="alias") is None


def test_runtime_generation_requires_fresh_exact_supervisor_ack(client: TestClient, tmp_path, monkeypatch):
    setup_pipeline(client)
    runtime = client.app.state.runtime
    runtime.config.managed_local_api = True
    runtime.config.local_api_control_dir = tmp_path
    runtime.client_generation = "generation-one"
    runtime.client = AsyncMock()
    runtime.client_signature = runtime.settings.client_signature(runtime.settings.read()[1])
    state = {"applied_mode": "local", "phase": "idle", "applied_generation": "historical-child",
             "applied_command_id": "cmd", "applied_credential_revision": 3}
    monkeypatch.setattr(runtime.local_api, "read", lambda: dict(state))
    ack = {"version": 1, "state": "running", "generation": "generation-one", "command_id": "cmd",
           "credential_revision": 3, "heartbeat_at": time.time(),
           "proxy_signature": runtime.settings.proxy_signature(runtime.settings.read()[1])}
    path = tmp_path / "applied.json"
    def write(value):
        path.write_text(json.dumps(value), encoding="utf8")
        path.chmod(0o600)
    write(ack)
    assert runtime.progress_generation() == "generation-one"
    for change in ({"heartbeat_at": time.time() - 20}, {"generation": "new-child"}, {"state": "starting"},
                   {"command_id": "other-command"}, {"credential_revision": 4}, {"proxy_signature": "wrong"}):
        write(ack | change)
        assert runtime.progress_generation() == ""
    write(ack)
    for change in ({"phase": "draining"}, {"command_id": "in-flight-command"}):
        original = dict(state)
        state.update(change)
        assert runtime.progress_generation() == ""
        state.clear()
        state.update(original)
    state["applied_mode"] = "cloud"
    assert runtime.progress_generation() == ""
    runtime.config.managed_local_api = False


async def test_same_command_child_restart_progress_uses_verified_live_generation(client: TestClient, tmp_path, monkeypatch):
    state, _ = setup_pipeline(client)
    runtime = state.runtime
    patch = SettingsPatch(revision=state.settings.read()[0], bot_token=TOKEN)
    state.settings.save(patch, state.settings.candidate(patch))
    values = {"applied_mode": "local", "phase": "idle", "applied_generation": "previous-child",
              "applied_command_id": "same-command", "applied_credential_revision": 3}
    with state.db.transaction() as session:
        row = session.get(LocalApiRuntime, 1)
        assert row is not None
        row.values = dict(values)
    runtime.config.managed_local_api = True
    runtime.config.local_api_control_dir = tmp_path
    path = tmp_path / "applied.json"
    ack = {"version": 1, "state": "running", "generation": "restarted-child", "command_id": "same-command",
           "credential_revision": 3, "heartbeat_at": time.time(),
           "proxy_signature": state.settings.proxy_signature(state.settings.read()[1])}
    path.write_text(json.dumps(ack), encoding="utf8")
    path.chmod(0o600)
    identity = AsyncMock(return_value={"id": 7, "username": "synthetic_bot"})
    monkeypatch.setattr(TelegramClient, "identity", identity)
    request = AsyncMock(side_effect=AssertionError("Unexpected Telegram request"))
    monkeypatch.setattr(TelegramClient, "request", request)
    assert runtime.progress_generation() == ""  # Fresh ACK alone cannot authorize observations.
    await runtime.ensure_client()  # The existing startup identity verification, no telemetry probe.
    identity.assert_awaited_once()
    request.assert_not_awaited()
    assert runtime.client_generation == "restarted-child"
    assert runtime.progress_generation() == "restarted-child"
    assert runtime.local_api.read() == values  # No durable generation adoption or protection workaround.
    await runtime.ensure_client()
    identity.assert_awaited_once()
    runtime.client_generation = "previous-child"
    assert runtime.progress_generation() == ""
    runtime.config.managed_local_api = False


def test_generation_reader_failure_is_only_unavailable_progress():
    def broken():
        raise OSError("Synthetic supervisor read failure")
    registry = DownloadProgress(generation=broken)
    assert registry.begin(job(), "boot", bot_id=7, file_id="alias") is None
    assert registry.project(job(), bot_id=7, file_id="alias") is None


@pytest.mark.parametrize(("kind", "total"), [("exact", 100), ("estimated", 100), ("unknown", None)])
def test_decoder_totals_and_public_projection(kind, total):
    payload = envelope({"file_id": "alias", "available": True, **sample(25, total, kind), "native_id": 42,
                        "path": "/private/cache", "token": "hidden"})
    generation, decoded = decode_progress(payload, ["alias"])
    assert generation == "generation-one"
    assert decoded["alias"] == sample(25, total, kind)
    assert decode_progress(envelope({"file_id": "alias", "available": False}), ["alias"])[1] == {}


@pytest.mark.parametrize("change", [{"downloaded_bytes": True}, {"downloaded_bytes": -1},
    {"total_bytes": 0}, {"total_kind": "unknown"}, {"active": 1}, {"file_id": "unrequested"},
    {"available": "true"}, {"total_kind": []}])
def test_decoder_rejects_invalid_samples(change):
    with pytest.raises((ValueError, TypeError)):
        decode_progress(envelope({"file_id": "alias", "available": True, **sample(), **change}), ["alias"])


async def test_local_http_read_is_private_bounded_and_does_not_touch_safety(config: Config, monkeypatch):
    calls, response_mode = [], ["valid"]

    async def handler(request):
        calls.append(request.method)
        assert json.loads((await request.post())["file_ids"]) == ["alias"]
        if response_mode[0] == "large":
            return web.Response(body=b"x" * (512 * 1024 + 1))
        if response_mode[0] == "redirect":
            return web.Response(status=302, headers={"Location": "/redirect-target"})
        if response_mode[0] == "unsupported":
            return web.Response(status=404)
        if response_mode[0] == "timeout":
            await asyncio.sleep(1.2)
        return web.json_response(envelope({"file_id": "alias", "available": True, **sample()}))

    client = TelegramClient(TOKEN, config, UserSettings(bot_api_mode="local"))
    client.safety = AsyncMock()
    monkeypatch.setenv("HTTP_PROXY", "http://invalid.invalid:9")
    monkeypatch.setenv("HTTPS_PROXY", "http://invalid.invalid:9")
    async with fake_api(handler) as base:
        client.bot.session.api = TelegramAPIServer.from_base(base, is_local=True)
        try:
            assert (await client.file_progress(["alias"]))[1]["alias"]["downloaded_bytes"] == 0
            assert client._progress_session.trust_env is False
            assert await client.file_progress([]) is None
            assert await client.file_progress(["x" * 4097]) is None
            for mode in ("large", "redirect", "unsupported", "timeout"):
                response_mode[0] = mode
                client._progress_retry_at = 0
                assert await client.file_progress(["alias"]) is None
                previous_calls = len(calls)
                assert await client.file_progress(["alias"]) is None
                assert len(calls) == previous_calls  # Negative cache, no redirect or tight retry loop.
            client.safety.before.assert_not_called()
            client.safety.observe.assert_not_called()
        finally:
            await client.close()


@pytest.mark.parametrize("cancel", [False, True])
async def test_worker_and_dto_observe_single_getfile_without_owning_download(authenticated: TestClient, cancel: bool):
    state, ingest = setup_pipeline(authenticated)
    ingest.message(7, message(), "in")
    request_id = confirm_all(state)
    job_id = state.runtime.worker.claim(7)
    registry = state.runtime.download_progress
    registry.generation = lambda: "generation-one"
    entered, release, read = asyncio.Event(), asyncio.Event(), asyncio.Event()
    source = state.runtime.config.bot_api_root / "synthetic.mp4"
    source.write_bytes(b"abcdef")

    class FakeClient:
        mode = "local"
        get_count = 0
        read_count = 0

        async def get_file(self, file_id):
            self.get_count += 1
            entered.set()
            await release.wait()
            return str(source)

        async def file_progress(self, aliases):
            self.read_count += 1
            await entered.wait()
            read.set()
            return "generation-one", {aliases[0]: sample(6, 6, completed=True)}

    client = FakeClient()
    task = asyncio.create_task(state.runtime.worker.run(client, job_id))
    try:
        await asyncio.wait_for(read.wait(), 5)
        for _ in range(100):
            detail = authenticated.get(f"/api/v1/tasks/{request_id}").json()
            if detail["items"][0]["download_progress"]:
                break
            await asyncio.sleep(.01)
        item = detail["items"][0]
        assert item["status"] == "fetching"  # Native completed never completes the task.
        assert item["download_progress"] == sample(6, 6, completed=True)
        assert item["completed_bytes"] == 0
        assert client.get_count == 1
        if cancel:
            assert state.tasks.cancel(request_id)["items"][0]["download_progress"] is None
            assert not task.done()  # Cancellation must not release a native acquisition's owner.
        assert client.read_count == 1
    finally:
        release.set()
        await asyncio.wait_for(task, 5)
    item = state.tasks.detail(request_id)["items"][0]
    assert item["status"] == ("cancelled" if cancel else "succeeded")
    assert item["download_progress"] is None
    assert registry._entries == {}
    assert client.get_count == 1


async def test_late_observation_and_shutdown_keep_uncertain_owner(client: TestClient):
    state, ingest = setup_pipeline(client)
    ingest.message(7, message(), "in")
    request_id = confirm_all(state)
    job_id = state.runtime.worker.claim(7)
    registry = state.runtime.download_progress
    registry.generation = lambda: "generation-one"
    reading, release = asyncio.Event(), asyncio.Event()

    class FakeClient:
        mode = "local"
        async def get_file(self, alias):
            await asyncio.Event().wait()
        async def file_progress(self, aliases):
            reading.set()
            await release.wait()
            return "generation-one", {aliases[0]: sample(99)}

    task = asyncio.create_task(state.runtime.worker.run(FakeClient(), job_id))
    await asyncio.wait_for(reading.wait(), 5)
    state.tasks.cancel(request_id)
    release.set()
    await asyncio.sleep(.05)
    assert state.tasks.detail(request_id)["items"][0]["download_progress"] is None
    assert not task.done()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    with state.db.sessions() as session:
        row = session.get(Job, job_id)
        assert row.state == "recovery_blocked"
        assert row.error_code == "ACQUISITION_UNCERTAIN"
    assert registry._entries == {}


@pytest.mark.parametrize("failure", ["exception", "unavailable", "generation"])
async def test_observation_failure_never_changes_download_outcome(client: TestClient, failure):
    state, ingest = setup_pipeline(client)
    ingest.message(7, message(), "in")
    request_id = confirm_all(state)
    job_id = state.runtime.worker.claim(7)
    registry = state.runtime.download_progress
    registry.generation = lambda: "generation-one"
    read, release = asyncio.Event(), asyncio.Event()
    source = state.runtime.config.bot_api_root / "synthetic.mp4"
    source.write_bytes(b"abcdef")

    class FakeClient:
        mode = "local"
        count = 0
        async def get_file(self, alias):
            self.count += 1
            await release.wait()
            return str(source)
        async def file_progress(self, aliases):
            read.set()
            if failure == "exception":
                raise RuntimeError("Synthetic observation failure")
            if failure == "unavailable":
                return None
            return "other-child", {aliases[0]: sample(99)}

    fake = FakeClient()
    task = asyncio.create_task(state.runtime.worker.run(fake, job_id))
    try:
        await asyncio.wait_for(read.wait(), 5)
        assert state.tasks.detail(request_id)["items"][0]["download_progress"] is None
        assert not task.done()
    finally:
        release.set()
        await asyncio.wait_for(task, 5)
    assert state.tasks.detail(request_id)["status"] == "succeeded"
    assert fake.count == 1
    assert registry._entries == {}
