import asyncio
from uuid import uuid4

import pytest
from sqlalchemy import select
from test_pipeline import confirm_all, message
from test_retained_cache import TOKEN, seed

from app.db.models import Job


@pytest.mark.parametrize("network_state", ["cooldown", "auth_paused", "offline"])
async def test_cached_save_dispatch_ignores_telegram_wait_but_never_claims_download(
        client, config, monkeypatch, network_state):
    state, native, request_id, item_id, job_id, cache_id = seed(client, config)
    runtime = state.runtime
    # A normal earlier queue item must stay pending when only saving is allowed.
    runtime.ingest.message(7, message(2), "pending-network")
    other_request = confirm_all(state)
    with state.db.transaction() as session:
        download = session.scalar(select(Job).where(Job.request_id == other_request))
        download.next_run_at = 0
        download_id = download.id
    if network_state == "cooldown":
        runtime.safety.observe(TOKEN, None, retry_after=600)
    elif network_state == "auth_paused":
        runtime.safety.observe(TOKEN, None, unauthorized=True)
        runtime.bot_status["state"] = "error"
    else:
        runtime.bot_status["state"] = "offline"
    result = await runtime.retained_cache.submit(request_id, item_id, "save", uuid4().hex, cache_id=cache_id)
    assert result["items"][0]["cache"]["operation"]["state"] == "queued"

    async def stop_tick(delay):
        assert delay == .5
        runtime.stopping = True

    monkeypatch.setattr("app.workers.runtime.asyncio.sleep", stop_tick)
    await runtime.tick_loop()
    await asyncio.gather(*runtime.running.values())
    with state.db.sessions() as session:
        assert session.get(Job, job_id).state == "succeeded"
        assert session.get(Job, download_id).state == "queued"
    assert native.calls == 0
    if network_state != "offline":
        assert runtime.safety.status(TOKEN)["state"] != "ready"
