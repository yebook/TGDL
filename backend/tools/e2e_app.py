"""Test-only loopback server. Fresh temporary DB, no Bot and no production paths."""
import asyncio
import json
import os
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import patch as patch_method

import uvicorn
from fastapi import FastAPI
from sqlalchemy import select

# Never inherit production credentials, root mappings or deployment overrides.
for key in tuple(os.environ):
    if key.startswith("TGD_") or key in {"TELEGRAM_API_ID", "TELEGRAM_API_HASH"}:
        del os.environ[key]

from app.core.config import Config  # noqa: E402
from app.core.security import password_hasher, private_file  # noqa: E402
from app.db.models import Admin, Attempt, Job, MediaItem, MediaRequest  # noqa: E402
from app.main import create_app  # noqa: E402
from app.schemas.contracts import ProxySettings, SettingsPatch  # noqa: E402


def seed_request_safety(app: FastAPI, scenario: str) -> None:
    """Fake only Telegram transport; exercise real guards, status and retry routes."""
    if scenario not in {"auth", "throttled"}:
        return
    from app.bot.client import TelegramClient

    token = "98765:offline-e2e-only-bot-token"
    settings = app.state.settings
    patch = SettingsPatch(revision=settings.read()[0], bot_token=token)
    settings.save(patch, settings.candidate(patch))
    safety = app.state.runtime.safety
    if scenario == "throttled":
        safety.observe(token, None, retry_after=600)
    else:
        safety.observe(token, None, unauthorized=True)

    async def identity(client: TelegramClient, *, probe: bool = False) -> dict:
        async def offline_identity() -> dict:
            await asyncio.sleep(0.3)
            return {"id": 98765, "username": "e2e_offline_bot"}

        return await client.request(offline_identity, probe=probe)

    patch_method.object(TelegramClient, "identity", identity).start()


def seed_network_diagnostics(app: FastAPI) -> None:
    """Replace only network primitives; the real diagnostic API/limits still run."""
    from app.schemas.contracts import ProxySettings
    from app.schemas.responses import NetworkProbeResult, TelegramProtocolResult, TelegramProtocolTarget
    from app.services.network_diagnostics import TELEGRAM_URL, ProbeSnapshot
    from app.services.protocol_probe import TARGETS

    async def https(proxy: ProxySettings, url: str) -> NetworkProbeResult:
        await asyncio.sleep(0.15)
        if url == TELEGRAM_URL:
            return NetworkProbeResult(state="reachable", latency_ms=120, http_status=302)
        return NetworkProbeResult(state="reachable", latency_ms=80, http_status=204)

    async def tcp() -> NetworkProbeResult:
        return NetworkProbeResult(state="reachable", latency_ms=2)

    async def protocol(snapshot: ProbeSnapshot) -> TelegramProtocolResult:
        # Fake only the remote diagnostic boundary. The real service still
        # constructs the response, preserves API failure and enforces limits.
        await asyncio.sleep(0.15)
        return TelegramProtocolResult(
            state="timeout", route="proxy" if snapshot.proxy.enabled else "direct",
            settings_revision=snapshot.revision, applied_route="matched",
            observed_generation="e2e-process-instance", observed_command_id="e2e-control",
            targets=[TelegramProtocolTarget(dc=dc, host=host, port=port, state="timeout",
                                             latency_ms=5000, failure_kind="timeout")
                     for dc, host, port in TARGETS],
        )

    service = app.state.network_diagnostics
    patch_method.object(service, "_probe_https", https).start()
    patch_method.object(service, "_probe_tcp", tcp).start()
    patch_method.object(service, "_probe_protocol", protocol).start()


def seed(app: FastAPI) -> None:
    """Represent deterministic historical tasks, then exercise real REST mutations."""
    db = app.state.db
    settings = app.state.settings
    patch = SettingsPatch(revision=settings.read()[0], allowed_user_ids=[12345], bot_api_mode="local")
    settings.save(patch, settings.candidate(patch))
    with db.transaction() as session:
        session.add(Admin(id=1, username="e2e-admin", password_hash=password_hasher.hash("local-e2e-password")))
        for index, (request_id, title, states) in enumerate([
            ("e2e-queued", "E2E queued video", ["queued"]),
            ("e2e-mixed", "E2E mixed group", ["succeeded", "failed"]),
            ("e2e-done", "E2E saved image", ["succeeded"]),
            ("e5f6a7b80123456789abcdef01234567", "E2E full identifier", ["succeeded"]),
            ("delete-queued", "Delete fixture queued video", ["queued"]),
            ("delete-mixed", "Delete fixture mixed group", ["succeeded", "failed"]),
            ("delete-done", "Delete fixture saved image", ["succeeded"]),
        ]):
            request = MediaRequest(id=request_id, bot_id=98765, chat_id=12345, user_id=12345,
                                   kind="group" if len(states) > 1 else "single", title=title,
                                   status="partial_success" if len(states) > 1 else states[0],
                                   collect_until=time.time(), destination=f"groups/2026-10/{request_id}")
            session.add(request)
            session.flush()
            for offset, state in enumerate(states):
                item_id = f"{request_id}-{offset}"
                item = MediaItem(id=item_id, request_id=request_id, bot_id=98765, chat_id=12345,
                                 message_id=index * 10 + offset, file_id="never-used-in-e2e",
                                 media_type="video" if offset == 0 else "image",
                                 original_name=f"{item_id}.mp4", size_bytes=33554432)
                session.add(item)
                session.flush()
                job = Job(id=f"job-{item_id}", item_id=item_id, request_id=request_id, state=state,
                          phase=state, relative_path=f"{request.destination}/{item.original_name}",
                          completed_bytes=item.size_bytes if state == "succeeded" else 0,
                          attempt_sequence=1 if state != "queued" else 0,
                          error_code="SIMULATED_FAILURE" if state == "failed" else None,
                          error_message="Test-only simulated transfer failure" if state == "failed" else None)
                session.add(job)
                session.flush()
                if state != "queued":
                    session.add(Attempt(job_id=job.id, sequence=1, finished_at=time.time(),
                                        error_code=job.error_code))
        # Query-isolated terminal records exercise real pagination and deletion APIs.
        for index in range(21):
            session.add(MediaRequest(
                id=f"pagination-{index:02d}", bot_id=98765, chat_id=12345, user_id=12345,
                kind="single", title=f"Pagination fixture {index:02d}", status="expired",
                collect_until=time.time(), created_at=time.time() - 3600 - index,
            ))


def seed_multi_type(app: FastAPI) -> None:
    """Historical pending-card records; render via real list/detail DTOs, no network."""
    fixtures: list[tuple[str, list[tuple[str | None, int | None, str]]]] = [
        ("document", [
            ("应用安装包" * 14 + ".APK", None, "application/vnd.android.package-archive"),
            ("user-guide-" + "longfilename" * 18 + ".pdf", None, "application/pdf"),
        ]),
        ("audio", [("播客录音.mp3", 4110, "audio/mpeg")]),
        ("voice", [(None, 135, "audio/ogg")]),
        ("animation", [("动画.gif", 0, "image/gif")]),
        ("video_note", [(None, 45, "video/mp4")]),
        ("video", [("原有视频.mp4", 60, "video/mp4")]),
        ("image", [("原有图片.jpg", None, "image/jpeg")]),
    ]
    with app.state.db.transaction() as session:
        for index, (media_type, files) in enumerate(fixtures):
            request_id = f"multi-type-{media_type}-case"
            session.add(MediaRequest(
                id=request_id, bot_id=98765, chat_id=12345, user_id=12345,
                kind="group" if len(files) > 1 else "single", title=f"Multi-type fixture {media_type}",
                status="awaiting_confirmation", collect_until=time.time(), expires_at=time.time() + 1800,
                created_at=time.time() - 1800 - index, destination=f"2026-11-02/{request_id}",
            ))
            session.flush()
            for offset, (name, duration, mime_type) in enumerate(files):
                session.add(MediaItem(
                    id=f"{request_id}-{offset}", request_id=request_id, bot_id=98765, chat_id=12345,
                    message_id=10000 + index * 10 + offset, file_id=f"offline-{request_id}-{offset}",
                    media_type=media_type, original_name=name, duration_seconds=duration,
                    mime_type=mime_type, size_bytes=1024,
                ))


@asynccontextmanager
async def cache_storage(app: FastAPI) -> AsyncIterator[None]:
    """Real cache REST/service/store; substitute only the private native boundary."""
    runtime = app.state.runtime
    generation = "e2e-process-instance"

    class NativeCacheBoundary:
        mode = "local"
        bot = SimpleNamespace(token="98765:" + "e" * 35)

        def __init__(self) -> None:
            self.used = 4 * 1024**3
            self.operation: dict[str, Any] | None = None
            self.finishes_at = 0.0

        def snapshot(self) -> dict[str, Any]:
            if self.operation and self.operation["state"] == "running" and time.monotonic() >= self.finishes_at:
                self.used = 0
                self.operation = self.operation | {"state": "succeeded", "after_bytes": 0,
                                                    "released_bytes": self.operation["before_bytes"]}
            return {"generation": generation, "scope": "current_bot", "scan_state": "ready",
                    "usage": {"used_bytes": self.used, "file_count": 3 if self.used else 0},
                    "operation": self.operation}

        async def cache_storage(self, observed_generation: str, *, refresh: bool = False) -> dict[str, Any]:
            assert observed_generation == generation
            await asyncio.sleep(.15)
            return self.snapshot()

        async def clear_cache_storage(self, observed_generation: str, operation_id: str) -> dict[str, Any]:
            assert observed_generation == generation
            assert not self.operation or self.operation["state"] != "running", "duplicate native clear"
            self.operation = {"operation_id": operation_id, "state": "running", "before_bytes": self.used,
                              "after_bytes": None, "released_bytes": None, "error_code": None}
            self.finishes_at = time.monotonic() + 1.8
            return self.snapshot()

        async def close(self) -> None:
            pass

    # Historical queued fixtures are terminal only in this opt-in scenario.
    with app.state.db.transaction() as session:
        for job in session.scalars(select(Job).where(Job.state == "queued")):
            job.state, job.phase = "cancelled", "cancelled"
            request = session.get(MediaRequest, job.request_id)
            assert request
            request.status = "cancelled"
    runtime.client, runtime.bot_id = cast(Any, NativeCacheBoundary()), 98765
    runtime.progress_generation = lambda: generation
    runtime.local_api.update(applied_mode="local", phase="idle", operation_id=None)
    try:
        async with acquisition_cancellation(app):
            yield
    finally:
        await runtime.cache_maintenance.close()
        runtime.client, runtime.bot_id = None, None


@asynccontextmanager
async def acquisition_cancellation(app: FastAPI) -> AsyncIterator[None]:
    """Real worker/DB/REST lifecycle; replace only the native network boundary."""
    from app.bot.client import TelegramClient

    generation = "e2e-process-instance"
    token = "98765:" + "e" * 35
    # Small known size keeps real storage admission portable on development/CI disks.
    total_bytes = 32 * 1024**2
    settings = app.state.settings
    patch = SettingsPatch(revision=settings.read()[0], bot_token=token)
    settings.save(patch, settings.candidate(patch))
    runtime = app.state.runtime
    worker = runtime.worker
    worker.acquisition_generation = lambda: generation
    runtime.download_progress.generation = lambda: generation
    runtime.local_api.update(applied_mode="local", phase="idle", operation_id=None)
    with app.state.db.transaction() as session:
        # Do not claim unrelated historical action fixtures in this opt-in scenario.
        for job in session.scalars(select(Job).where(Job.state == "queued")):
            job.next_run_at = time.time() + 3600
        for width in (1440, 390):
            request_id = f"cancel-acquisition-{width}"
            session.add(MediaRequest(
                id=request_id, bot_id=98765, chat_id=12345, user_id=12345,
                kind="single", title=f"Acquisition cancellation fixture {width}",
                status="queued", collect_until=time.time(), destination=f"2026-11-01/{request_id}",
            ))
            session.flush()
            session.add(MediaItem(
                id=f"{request_id}-item", request_id=request_id, bot_id=98765, chat_id=12345,
                message_id=width, file_id=f"offline-{width}", file_unique_id=f"unique-{width}",
                media_type="video", original_name=f"pending-{width}.mp4", size_bytes=total_bytes,
            ))
            session.flush()
            session.add(Job(id=f"job-{request_id}", item_id=f"{request_id}-item", request_id=request_id,
                            state="queued", phase="queued", next_run_at=0,
                            relative_path=f"2026-11-01/{request_id}/pending-{width}.mp4"))

    class NativeBoundary:
        mode = "local"

        def __init__(self, index: int) -> None:
            self.bot = SimpleNamespace(token=token)
            self.acquisition_id = f"{index:064x}"
            self.entered = asyncio.Event()
            self.joined = asyncio.Event()
            self.cancelled_at: float | None = None
            self.stopped = False

        async def file_acquisition(self, observed_generation: str, *, file_id: str | None = None,
                                   acquisition_id: str | None = None, cancel: bool = False) -> dict[str, Any]:
            assert observed_generation == generation
            if file_id is not None:
                state = "prepared"
            else:
                assert acquisition_id == self.acquisition_id
                if cancel and self.cancelled_at is None:
                    self.cancelled_at = time.monotonic()
                if self.cancelled_at is None:
                    state = "downloading"
                elif self.stopped or time.monotonic() - self.cancelled_at >= 1.8:
                    self.stopped, state = True, "cancelled"
                else:
                    state = "cancelling"
            return {"generation": generation, "acquisition_id": self.acquisition_id,
                    "state": state, "settled": state == "cancelled"}

        async def get_file(self, file_id: str, **identity: str) -> str:
            assert identity == {"generation": generation, "acquisition_id": self.acquisition_id}
            self.entered.set()
            try:
                # Never return a successful path or time out the original getFile.
                await asyncio.Future[None]()
                raise AssertionError("pending getFile cannot complete")
            finally:
                assert self.stopped, "HTTP owner released before native stop proof"
                self.joined.set()

        async def file_progress(self, file_ids: list[str]) -> tuple[str, dict[str, dict[str, Any]]]:
            return generation, {file_id: {"downloaded_bytes": total_bytes // 2,
                                          "total_bytes": None, "total_kind": "unknown",
                                          "active": True, "completed": False, "speed_bps": None}
                                for file_id in file_ids}

    owners: list[tuple[str, NativeBoundary, asyncio.Task[None]]] = []
    try:
        for index in (1, 2):
            job_id = worker.claim(bot_id=98765, mode="local")
            assert job_id and job_id.startswith("job-cancel-acquisition-")
            boundary = NativeBoundary(index)
            task = asyncio.create_task(worker.run(cast(TelegramClient, boundary), job_id))
            runtime.running[job_id] = task
            owners.append((job_id, boundary, task))
            await asyncio.wait_for(boundary.entered.wait(), 5)
        yield
    finally:
        # Graceful fixture shutdown uses the same durable intent and joins every owner.
        for job_id, boundary, task in owners:
            if not task.done():
                app.state.tasks.cancel(job_id.removeprefix("job-"))
                boundary.stopped = True
        await asyncio.gather(*(task for _, _, task in owners))
        assert all(boundary.joined.is_set() for _, boundary, _ in owners)
        for job_id, _, _ in owners:
            runtime.running.pop(job_id, None)


def main() -> None:
    # No caller-provided data directory is accepted; every run creates and deletes its own.
    with TemporaryDirectory(prefix="tgd-e2e-") as temporary:
        root = Path(temporary)
        for name in ("data", "downloads", "cache", "api-control"):
            (root / name).mkdir()
        config = Config(data_dir=root / "data", download_root=root / "downloads",
                        bot_api_root=root / "cache",
                        static_dir=Path(__file__).resolve().parents[2] / "frontend" / "dist",
                        bot_api_base="http://127.0.0.1:1", run_background=False,
                        cookie_secure=False, minimum_free_bytes=1024)
        # A fixed test scenario is allowed; no caller-provided paths, secrets or real upstream.
        scenario = os.getenv("E2E_MANAGED_API", "")
        if scenario in {"credentials", "restart", "uncertain", "cooldown", "connection"} or os.getenv("E2E_CACHE_STORAGE") == "1":
            config.managed_local_api = True
            config.local_api_control_dir = root / "api-control"
        if not (config.static_dir / "index.html").is_file():
            raise RuntimeError("Build frontend first: pnpm --dir frontend build")
        app = create_app(config)
        original_lifespan = app.router.lifespan_context

        @asynccontextmanager
        async def fixture_lifespan(application: FastAPI) -> AsyncIterator[None]:
            async with original_lifespan(application):
                seed(application)
                seed_multi_type(application)
                if os.getenv("E2E_TASK_FILES") == "1":
                    from tools.task_files_fixture import seed_task_files

                    seed_task_files(application)
                seed_network_diagnostics(application)
                seed_request_safety(application, os.getenv("E2E_REQUEST_SAFETY", ""))
                if not scenario:
                    application.state.runtime.local_api.update(
                        applied_mode="local", phase="failed", error_code="LOCAL_API_CONNECTION_FAILED")
                heartbeat = None
                if config.managed_local_api:
                    service = application.state.runtime.local_api
                    service.update(applied_mode="local", phase="idle", operation_id=None)
                    if scenario == "connection":
                        settings = application.state.settings
                        patch = SettingsPatch(revision=settings.read()[0],
                                              telegram_api_hash="0123456789abcdef0123456789abcdef",
                                              bot_token="98765:offline-e2e-only-bot-token",
                                              proxy=ProxySettings(enabled=False, password="offline-fixture-password"))
                        settings.save(patch, settings.candidate(patch))
                    service.update(applied_mode="local", phase="idle", operation_id=None)
                    if scenario == "uncertain":
                        service.update(phase="logout_uncertain", logout_uncertain=True,
                                       applied_mode="cloud", operation_id="e2e-uncertain",
                                       cloud_available_at=time.time() + 600)
                    elif scenario == "cooldown":
                        service.update(phase="cooldown", operation_id="e2e-cooldown",
                                       cloud_available_at=time.time() + 600)

                    async def fake_heartbeat() -> None:
                        # Fake only the private supervisor boundary, never a successful REST response.
                        while True:
                            temporary_status = config.local_api_control_dir / "applied.new"
                            private_file(temporary_status, json.dumps({
                                "version": 1, "command_id": "e2e-control", "state": "waiting_credentials" if scenario == "credentials" else "running",
                                "generation": "e2e-process-instance", "heartbeat_at": time.time(),
                                "credential_revision": 0, "error_code": "",
                            }).encode())
                            temporary_status.replace(config.local_api_control_dir / "applied.json")
                            await asyncio.sleep(1)

                    heartbeat = asyncio.create_task(fake_heartbeat())
                    await asyncio.sleep(0)
                elif scenario in {"cloud-connection", "cloud-recovery"}:
                    settings = application.state.settings
                    patch = SettingsPatch(revision=settings.read()[0], bot_api_mode="cloud",
                                          proxy=ProxySettings(enabled=True, host="offline.invalid", port=1080))
                    settings.save(patch, settings.candidate(patch))
                    application.state.runtime.local_api.update(applied_mode="cloud", phase="idle", operation_id=None)
                    if scenario == "cloud-recovery":
                        patch = SettingsPatch(revision=settings.read()[0], proxy=ProxySettings(enabled=False))
                        settings.save(patch, settings.candidate(patch))
                        application.state.runtime.local_api.update(phase="failed", error_code="LOCAL_API_PREFLIGHT_FAILED")
                try:
                    if os.getenv("E2E_CACHE_STORAGE") == "1":
                        async with cache_storage(application):
                            yield
                    elif os.getenv("E2E_ACQUISITION_CANCEL") == "1":
                        async with acquisition_cancellation(application):
                            yield
                    else:
                        yield
                finally:
                    if heartbeat:
                        heartbeat.cancel()
                        await asyncio.gather(heartbeat, return_exceptions=True)

        app.router.lifespan_context = fixture_lifespan
        # Containers publish this fixture port to host loopback only. Native runs
        # remain loopback-bound; the override is test-only, never app configuration.
        uvicorn.run(app, host=os.environ.get("TGDL_E2E_HOST", "127.0.0.1"),
                    port=int(os.environ.get("TGDL_E2E_PORT", "8765")), access_log=False)


if __name__ == "__main__":
    main()
