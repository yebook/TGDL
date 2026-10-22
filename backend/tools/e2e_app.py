"""Test-only loopback server. Fresh temporary DB, no Bot and no production paths."""
import asyncio
import json
import os
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch as patch_method

import uvicorn
from fastapi import FastAPI

# Never inherit production credentials, root mappings or deployment overrides.
for key in tuple(os.environ):
    if key.startswith("TGD_") or key in {"TELEGRAM_API_ID", "TELEGRAM_API_HASH"}:
        del os.environ[key]

from app.core.config import Config  # noqa: E402
from app.core.security import password_hasher, private_file  # noqa: E402
from app.db.models import Admin, Attempt, Job, MediaItem, MediaRequest  # noqa: E402
from app.main import create_app  # noqa: E402
from app.schemas.contracts import SettingsPatch  # noqa: E402


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
        if scenario in {"credentials", "restart", "uncertain", "cooldown", "connection"}:
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
                seed_request_safety(application, os.getenv("E2E_REQUEST_SAFETY", ""))
                heartbeat = None
                if config.managed_local_api:
                    service = application.state.runtime.local_api
                    service.update(applied_mode="local", phase="idle", operation_id=None)
                    if scenario == "connection":
                        settings = application.state.settings
                        patch = SettingsPatch(revision=settings.read()[0],
                                              telegram_api_hash="0123456789abcdef0123456789abcdef",
                                              bot_token="98765:offline-e2e-only-bot-token",
                                              proxy={"enabled": False, "password": "offline-fixture-password"})
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
                                          proxy={"enabled": True, "host": "offline.invalid", "port": 1080})
                    settings.save(patch, settings.candidate(patch))
                    application.state.runtime.local_api.update(applied_mode="cloud", phase="idle", operation_id=None)
                    if scenario == "cloud-recovery":
                        patch = SettingsPatch(revision=settings.read()[0], proxy={"enabled": False})
                        settings.save(patch, settings.candidate(patch))
                        application.state.runtime.local_api.update(phase="failed", error_code="LOCAL_API_PREFLIGHT_FAILED")
                try:
                    yield
                finally:
                    if heartbeat:
                        heartbeat.cancel()
                        await asyncio.gather(heartbeat, return_exceptions=True)

        app.router.lifespan_context = fixture_lifespan
        uvicorn.run(app, host="127.0.0.1", port=8765, access_log=False)


if __name__ == "__main__":
    main()
