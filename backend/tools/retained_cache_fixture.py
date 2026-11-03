"""Disposable retained-cache browser scenarios; only native IO is substituted."""
import asyncio
import hashlib
import os
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import patch

from fastapi import FastAPI, HTTPException
from sqlalchemy import select

from app.bot.client import TelegramClient
from app.core.security import digest
from app.db.models import Attempt, CacheAction, Job, MediaItem, MediaRequest
from app.schemas.contracts import SettingsPatch
from app.storage.space import SpaceAssessment, SpaceVolume


@asynccontextmanager
async def retained_cache(app: FastAPI) -> AsyncIterator[None]:
    runtime = app.state.runtime
    worker, db, config = runtime.worker, app.state.db, runtime.config
    generation = "e2e-process-instance"
    token = "98765:" + "e" * 35
    settings = app.state.settings
    candidate = SettingsPatch(revision=settings.read()[0], bot_token=token, bot_api_mode="local")
    settings.save(candidate, settings.candidate(candidate))
    records: dict[str, dict[str, Any]] = {}
    controls: dict[str, dict[str, Any]] = {}
    sources: dict[str, Path] = {}
    calls: dict[str, int] = {}
    size = 64 * 1024

    class NativeBoundary:
        mode = "local"
        bot = SimpleNamespace(token=token)

        async def file_acquisition(self, observed_generation: str, *, file_id: str | None = None,
                                   acquisition_id: str | None = None, cancel: bool = False,
                                   expected_cache_identity: str | None = None) -> dict[str, Any]:
            assert observed_generation == generation
            if file_id is not None:
                acquisition_id = hashlib.sha256(f"{file_id}:{len(controls)}".encode()).hexdigest()
                controls[acquisition_id] = {"file_id": file_id, "state": "prepared", "settled": False}
            assert acquisition_id and acquisition_id in controls
            owner = controls[acquisition_id]
            if cancel:
                owner.update(state="cancelled", settled=True)
            return {"generation": generation, "acquisition_id": acquisition_id,
                    "state": owner["state"], "settled": owner["settled"]}

        async def get_file(self, file_id: str, **identity: str) -> str:
            calls[file_id] = calls.get(file_id, 0) + 1
            owner = controls[identity["acquisition_id"]]
            owner["state"] = "downloading"
            if "partial" in file_id:
                try:
                    await asyncio.Future[None]()
                finally:
                    assert owner["settled"], "fixture cannot release an unsettled native writer"
            await asyncio.sleep(.2)
            # Redownload uses another cache file while retaining the old partial.
            previous = sources[file_id]
            fresh = previous.with_name("fresh-" + previous.name)
            fresh.write_bytes(b"f" * size)
            sources[file_id] = fresh
            owner.update(state="completed", settled=True)
            return str(fresh)

        async def inspect_file_cache(self, file_id: str, observed_generation: str, *,
                                     acquisition_id: str | None = None, expected_path: str | None = None,
                                     expected_size: int | None = None, expected_identity: str | None = None) -> dict[str, Any]:
            assert observed_generation == generation
            await asyncio.sleep(.03)
            path = Path(expected_path) if expected_path else sources[file_id]
            if not path.exists():
                return {"generation": generation, "completeness": "absent", "path": None,
                        "size": 0, "allocated_bytes": 0, "identity": None,
                        "resumable": False, "reason": "absent", "settled": True}
            current_size = path.stat().st_size
            complete = current_size == size
            return {"generation": generation, "completeness": "complete" if complete else "partial",
                    "path": str(path), "size": current_size, "allocated_bytes": current_size,
                    "identity": hashlib.sha256(str(path).encode()).hexdigest(),
                    "resumable": not complete and "restart" not in file_id,
                    "reason": None if "restart" not in file_id else "generation_changed", "settled": True}

        async def delete_partial_file_cache(self, file_id: str, expected_path: str, expected_size: int,
                                            observed_generation: str, *, expected_identity: str) -> str:
            assert observed_generation == generation
            path = Path(expected_path)
            assert path.resolve().is_relative_to(config.bot_api_root.resolve())
            assert expected_identity == hashlib.sha256(str(path).encode()).hexdigest()
            if not path.exists():
                return "absent"
            assert path.stat().st_size == expected_size
            await asyncio.sleep(.2)
            path.unlink()
            return "deleted"

        async def delete_file_cache(self, file_id: str, expected_path: str, expected_size: int,
                                    observed_generation: str, *, expected_sha256: str) -> str:
            path = Path(expected_path)
            assert path.resolve().is_relative_to(config.bot_api_root.resolve())
            assert observed_generation == generation and path.stat().st_size == expected_size
            assert hashlib.sha256(path.read_bytes()).hexdigest() == expected_sha256
            path.unlink()
            return "deleted"

        async def file_progress(self, file_ids: list[str]) -> tuple[str, dict[str, dict[str, Any]]]:
            return generation, {file_id: {"downloaded_bytes": size // 2, "total_bytes": size,
                "total_kind": "exact", "active": True, "completed": False, "speed_bps": 1024}
                for file_id in file_ids}

        async def close(self) -> None:
            pass

    boundary = NativeBoundary()
    runtime.client, runtime.bot_id = cast(TelegramClient, boundary), 98765
    runtime.client_signature = settings.client_signature(settings.read()[1])
    runtime.bot_status = {"state": "online"}
    runtime.acquisition_generation = lambda: generation
    worker.acquisition_generation = lambda: generation
    runtime.download_progress.generation = lambda: generation
    runtime.local_api.update(applied_mode="local", phase="idle", operation_id=None)
    with db.transaction() as session:
        for old_job in session.scalars(select(Job).where(Job.state == "queued")):
            old_job.next_run_at = time.time() + 3600
        for width in (1440, 390):
            for offset, kind in enumerate(("complete", "partial", "restart", "clean", "lost")):
                request_id = f"cache-{kind}-{width}"
                bot_directory = "offline-bot" if os.name == "nt" else token
                path = config.bot_api_root / bot_directory / ("缓存文件路径较长请确认换行" * 3) / f"{request_id}.bin"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"s" * (size if kind == "complete" else size // 2))
                sources[request_id] = path
                records[request_id] = {"path": path, "space": kind != "complete", "kind": kind}
                session.add(MediaRequest(id=request_id, bot_id=98765, chat_id=12345, user_id=12345,
                    title=f"Cache {kind} fixture {width}", status="failed", collect_until=0,
                    destination=f"2026-11-03/{request_id}"))
                session.flush()
                session.add(MediaItem(id=request_id + "-item", request_id=request_id, bot_id=98765,
                    chat_id=12345, message_id=100000 + width * 10 + offset,
                    file_id=request_id, file_unique_id=request_id, media_type="document",
                    original_name=request_id + ".bin", size_bytes=size))
                session.flush()
                session.add(Job(id="job-" + request_id, request_id=request_id, item_id=request_id + "-item",
                    state="failed", phase="failed", acquisition_mode="local", attempt_sequence=1,
                    relative_path=f"2026-11-03/{request_id}/{request_id}.bin",
                    error_code="NO_SPACE" if kind == "complete" else "CANCELLED",
                    error_message="保存盘空间不足，完整缓存已保留，可继续保存或清理。" if kind == "complete" else "下载已取消，部分缓存保留。"))
                session.flush()
                session.add(Attempt(job_id="job-" + request_id, sequence=1, finished_at=time.time()))
    for request_id, record in records.items():
        complete = record["kind"] == "complete"
        runtime.retained_cache.capture("job-" + request_id, str(record["path"]),
            completeness="complete" if complete else "partial",
            generation="previous-api-child" if record["kind"] == "restart" else generation,
            credential=digest(token), native_identity=hashlib.sha256(str(record["path"]).encode()).hexdigest(),
            resumable=not complete and record["kind"] != "restart", reason="generation_changed" if record["kind"] == "restart" else None)
        if complete:
            with db.transaction() as session:
                job = session.get(Job, "job-" + request_id)
                job.storage_failure = SpaceAssessment("before_save", (SpaceVolume("target", 1024, size, 1024, 0, size),)).to_dict()

    @app.get("/__e2e__/retained-cache/{request_id}")
    def evidence(request_id: str) -> dict[str, Any]:
        record = records.get(request_id)
        if not record:
            raise HTTPException(404)
        with db.sessions() as session:
            job = session.get(Job, "job-" + request_id)
            return {"source_exists": record["path"].exists(),
                    "final_exists": (config.download_root / job.relative_path).is_file(),
                    "attempt_sequence": job.attempt_sequence,
                    "actions": len(list(session.scalars(select(CacheAction).where(CacheAction.job_id == job.id)))),
                    "get_file_calls": calls.get(request_id, 0)}

    @app.post("/__e2e__/retained-cache/{request_id}/space")
    def release_space(request_id: str) -> dict[str, bool]:
        if request_id not in records:
            raise HTTPException(404)
        records[request_id]["space"] = True
        return {"released": True}

    # Production static fallback is already registered. Keep test-only routes
    # ahead of it without adding any fixture route to the production app.
    for fixture_route in list(app.router.routes[-2:]):
        app.router.routes.remove(fixture_route)
        app.router.routes.insert(0, fixture_route)

    original_check = worker.check_space

    def check_space(job_id: str, phase: str, **kwargs: Any) -> None:
        request_id = job_id.removeprefix("job-")
        if request_id in records and not records[request_id]["space"] and phase == "before_save":
            SpaceAssessment(phase, (SpaceVolume("target", 1024, size, 1024, 0, size),)).require()
        original_check(job_id, phase, **kwargs)

    stop = asyncio.Event()

    async def dispatch() -> None:
        while not stop.is_set():
            for job_id, task in list(runtime.running.items()):
                if task.done():
                    task.result()
                    runtime.running.pop(job_id, None)
            await runtime.cancel_cleanup.run_due()
            if len(runtime.running) < 3:
                job_id = await asyncio.to_thread(worker.claim, 98765, "local")
                if job_id:
                    runtime.running[job_id] = asyncio.create_task(worker.run(cast(TelegramClient, boundary), job_id))
            await asyncio.sleep(.1)

    with patch.object(worker, "check_space", check_space):
        dispatcher = asyncio.create_task(dispatch())
        try:
            yield
        finally:
            stop.set()
            await dispatcher
            for job_id, task in list(runtime.running.items()):
                if not task.done():
                    app.state.tasks.cancel(job_id.removeprefix("job-"))
                    for owner in controls.values():
                        owner.update(state="cancelled", settled=True)
            await asyncio.gather(*runtime.running.values())
            runtime.running.clear()
            runtime.client, runtime.bot_id = None, None
