"""Disposable real-filesystem fixtures; imported only by the loopback E2E tool."""
import hashlib
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException
from sqlalchemy import select

from app.db.models import Job, MediaItem, MediaRequest, StoredFile


def seed_task_files(app: FastAPI) -> None:
    storage = app.state.storage.get("default")
    storage.initialize()
    paths: dict[str, list[tuple[Path, bytes]]] = {}
    counter = 10000
    with app.state.db.transaction() as session:
        # Historical base fixtures must not own a live download in this scenario.
        for job in session.scalars(select(Job).where(Job.state == "queued")):
            job.state, job.phase = "cancelled", "cancelled"
            request = session.get(MediaRequest, job.request_id)
            if request:
                request.status = "cancelled"
        for width in (1440, 390):
            shared: dict[str, tuple[str, str, str]] = {}
            for kind in ("keep", "delete", "lost", "shared-a", "shared-b",
                         "batch-shared-c", "batch-shared-d", "mixed", "unavailable", "changed"):
                request_id = f"files-{kind}-{width}"
                request = MediaRequest(id=request_id, bot_id=98765, chat_id=12345, user_id=12345,
                                       kind="group" if kind == "mixed" else "single", title=request_id,
                                       status="succeeded", collect_until=0, destination="file-fixtures")
                session.add(request)
                session.flush()
                paths[request_id] = []
                for offset in range(2 if kind == "mixed" else 1):
                    counter += 1
                    content = f"isolated file fixture {request_id} {offset}\n".encode()
                    digest = hashlib.sha256(content).hexdigest()
                    relative = f"file-fixtures/{request_id}-{offset}.mp4"
                    linked_job_id = None
                    shared_key = "shared" if kind.startswith("shared-") else (
                        "batch-shared" if kind.startswith("batch-shared-") else "")
                    if shared_key in shared:
                        relative, linked_job_id, digest = shared[shared_key]
                        content = (storage.root / relative).read_bytes()
                    job_id = f"job-{request_id}-{offset}"
                    item = MediaItem(id=f"item-{request_id}-{offset}", request_id=request_id,
                                     bot_id=98765, chat_id=12345, message_id=counter,
                                     file_id=f"offline-{counter}", file_unique_id=f"unique-{counter}",
                                     media_type="video", original_name=f"{request_id}-{offset}.mp4",
                                     size_bytes=len(content))
                    session.add(item)
                    session.flush()
                    job = Job(id=job_id, item_id=item.id, request_id=request_id,
                              root_id="unconfigured-fixture-root" if kind == "unavailable" else "default",
                              relative_path=relative, state="already_exists" if linked_job_id else "succeeded",
                              phase="already_exists" if linked_job_id else "succeeded",
                              linked_job_id=linked_job_id, completed_bytes=len(content), finished_at=time.time())
                    session.add(job)
                    session.flush()
                    if not linked_job_id:
                        session.add(StoredFile(bot_id=98765, file_unique_id=item.file_unique_id,
                                               job_id=job_id, root_id=job.root_id, relative_path=relative,
                                               size_bytes=len(content), sha256=digest))
                    target = storage.root / relative
                    target.parent.mkdir(exist_ok=True)
                    if kind != "unavailable" and not (kind == "mixed" and offset == 1):
                        target.write_bytes(b"replacement with a different size" if kind == "changed" else content)
                    paths[request_id].append((target, content))
                    if shared_key and shared_key not in shared:
                        shared[shared_key] = relative, job_id, digest

    @app.get("/__e2e__/task-files/{request_id}")
    def inspect_file(request_id: str) -> dict[str, list[bool]]:
        if request_id not in paths:
            raise HTTPException(404)
        return {"present": [path.is_file() for path, _ in paths[request_id]]}

    @app.post("/__e2e__/task-files/{request_id}/{action}")
    def external_change(request_id: str, action: str) -> dict[str, str]:
        # Only these two fixed fixture files may simulate an external move/delete.
        if request_id not in {"files-keep-1440", "files-keep-390"} or action not in {"remove", "restore"}:
            raise HTTPException(404)
        target, content = paths[request_id][0]
        if action == "remove":
            target.unlink(missing_ok=True)
        else:
            target.write_bytes(content)
        return {"state": action}

    # create_app already registered its SPA catchall; these test-only GET routes
    # must precede it instead of returning index.html for fixture inspection.
    for route in tuple(app.router.routes):
        if getattr(route, "path", "").startswith("/__e2e__/task-files/"):
            app.router.routes.remove(route)
            app.router.routes.insert(0, route)
