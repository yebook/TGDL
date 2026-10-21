import asyncio

import pytest
from fastapi.testclient import TestClient
from test_pipeline import confirm_all, message, setup_pipeline

from app.schemas.contracts import SettingsPatch


@pytest.mark.parametrize("mode", ["cloud", "local"])
async def test_claimed_transport_exposed_without_fabricated_get_file_progress(authenticated: TestClient, mode: str) -> None:
    state, ingest = setup_pipeline(authenticated)
    patch = SettingsPatch(revision=state.settings.read()[0], bot_api_mode=mode)
    state.settings.save(patch, state.settings.candidate(patch))
    ingest.message(7, message(), "in")
    request_id = confirm_all(state)
    assert authenticated.get(f"/api/v1/tasks/{request_id}").json()["items"][0]["acquisition_mode"] == "cloud"
    job_id = state.runtime.worker.claim(7, mode)
    entered, release = asyncio.Event(), asyncio.Event()
    source = state.runtime.config.bot_api_root / "synthetic.mp4"
    source.write_bytes(b"abcdef")

    class FakeClient:
        async def get_file(self, file_id: str) -> str:
            entered.set()
            await release.wait()
            return str(source) if mode == "local" else "videos/synthetic.mp4"

        async def file_chunks(self, path: str):
            yield b"abc"
            # Worker has persisted this real network-byte count before requesting the next chunk.
            item = authenticated.get(f"/api/v1/tasks/{request_id}").json()["items"][0]
            assert item["phase"] == "saving"
            assert item["acquisition_mode"] == "cloud"
            assert item["completed_bytes"] == 3
            yield b"def"

    fake = FakeClient()
    fake.mode = mode
    task = asyncio.create_task(state.runtime.worker.run(fake, job_id))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        item = authenticated.get(f"/api/v1/tasks/{request_id}").json()["items"][0]
        assert item["phase"] == "fetching"
        assert item["acquisition_mode"] == mode
        assert item["completed_bytes"] == 0
        assert item["speed_bps"] is None
    finally:
        release.set()
        await asyncio.wait_for(task, 5)
    item = authenticated.get(f"/api/v1/tasks/{request_id}").json()["items"][0]
    assert item["status"] == "succeeded"
    assert item["completed_bytes"] == 6
