"""Cloud-only reconnect gates both intake and durable notification consumers."""
import asyncio
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db.models import Notification
from app.main import create_app
from app.schemas.contracts import SettingsPatch
from tests.test_pipeline import confirm_all, message


@pytest.mark.parametrize("consumer", ["poll_loop", "notify_loop"])
@pytest.mark.parametrize("phase", ["draining", "verifying", "failed"])
async def test_cloud_only_consumers_pause_without_dropping_outbox_then_resume(config, monkeypatch, consumer, phase):
    assert not config.managed_local_api
    with TestClient(create_app(config)) as client:
        state, runtime = client.app.state, client.app.state.runtime
        patch = SettingsPatch(revision=1, bot_token="123:synthetic-consumer", allowed_user_ids=[42])
        state.settings.save(patch, state.settings.candidate(patch))
        runtime.ingest.message(7, message(), "queued")
        confirm_all(state)
        runtime.local_api.update(action="cloud_reconnect", operation_id="operation", phase=phase)
        configured = state.settings.read()[1]
        # Match the saved signature so only the durable operation gate can stop requests.
        runtime.client_signature = state.settings.client_signature(configured)
        runtime.bot_id = 7
        fake = AsyncMock()
        runtime.client = fake
        ensure = AsyncMock()
        monkeypatch.setattr(runtime, "ensure_client", ensure)
        with state.db.sessions() as session:
            before = [(row.id, row.state, row.attempts) for row in session.scalars(select(Notification))]
        assert before

        async def stop_on_wait(delay):
            assert delay == 0.2
            runtime.stopping = True

        with monkeypatch.context() as scoped:
            scoped.setattr(asyncio, "sleep", stop_on_wait)
            await getattr(runtime, consumer)()
        fake.updates.assert_not_awaited()
        fake.send.assert_not_awaited()
        ensure.assert_not_awaited()
        with state.db.sessions() as session:
            assert [(row.id, row.state, row.attempts) for row in session.scalars(select(Notification))] == before

        # Releasing the same barrier must let the existing consumers make progress.
        runtime.local_api.update(phase="idle", action=None, operation_id=None)
        runtime.stopping = False

        async def updates(offset):
            runtime.stopping = True
            return []

        async def send(*args):
            runtime.stopping = True
            return 123

        fake.updates.side_effect = updates
        fake.send.side_effect = send
        await getattr(runtime, consumer)()
        if consumer == "poll_loop":
            ensure.assert_awaited_once()
            fake.updates.assert_awaited_once()
        else:
            fake.send.assert_awaited_once()
            with state.db.sessions() as session:
                assert sum(row.state == "sent" for row in session.scalars(select(Notification))) == 1
