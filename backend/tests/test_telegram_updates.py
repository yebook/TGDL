import json
from unittest.mock import AsyncMock

import pytest
from aiogram.types import Update
from sqlalchemy import select

from app.bot.client import TelegramClient
from app.db.models import IngestUpdate, MediaItem, MediaRequest
from app.schemas.contracts import SettingsPatch, UserSettings

TOKEN = "123456789:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
USER = {"id": 42, "is_bot": False, "first_name": "fixture"}


def link_message(preview):
    message = {
        "message_id": 1, "date": 1793611950, "from": USER,
        "chat": {"id": 42, "type": "private"}, "text": "https://example.org",
    }
    if preview is not None:
        message["link_preview_options"] = preview
    return message


@pytest.mark.parametrize("preview", [
    None, {}, {"url": "https://example.org"}, {"is_disabled": True},
    {"is_disabled": False, "prefer_small_media": False, "show_above_text": True},
])
@pytest.mark.parametrize("callback", [False, True], ids=["message", "callback"])
async def test_updates_serialize_real_sdk_link_previews(config, monkeypatch, preview, callback):
    message = link_message(preview)
    raw = {"update_id": 100}
    if callback:
        raw["callback_query"] = {
            "id": "fixture-callback", "from": USER, "chat_instance": "fixture-chat",
            "message": message, "data": "y:request:nonce",
        }
    else:
        raw["message"] = message
    telegram = TelegramClient(TOKEN, config, UserSettings(bot_api_mode="cloud"))
    transport = AsyncMock(return_value=[Update.model_validate(raw)])
    monkeypatch.setattr(telegram.bot.session, "make_request", transport)
    try:
        updates = await telegram.updates(100)
        assert len(updates) == 1 and updates[0]["update_id"] == 100
        payload = updates[0]["callback_query"] if callback else updates[0]
        if callback:
            assert payload["from_user"]["id"] == 42
            assert payload["data"] == "y:request:nonce"
        dumped = payload["message"]
        assert dumped["from_user"]["id"] == 42
        assert dumped["text"] == message["text"]
        if preview is None:
            assert "link_preview_options" not in dumped
        else:
            assert dumped["link_preview_options"] == preview
        assert json.loads(json.dumps(updates)) == updates
        method = transport.await_args.args[1]
        assert method.offset == 100 and method.timeout == 25
        assert method.allowed_updates == ["message", "callback_query"]
        assert transport.await_args.kwargs["timeout"] == 35
    finally:
        await telegram.close()


@pytest.mark.integration
async def test_url_backlog_recovers_polling_persists_cursor_and_accepts_media(client, monkeypatch):
    state, runtime = client.app.state, client.app.state.runtime
    patch = SettingsPatch(revision=state.settings.read()[0], bot_token=TOKEN,
                          bot_api_mode="cloud", allowed_user_ids=[42])
    state.settings.save(patch, state.settings.candidate(patch))
    monkeypatch.setattr(TelegramClient, "identity", AsyncMock(return_value={"id": 7, "username": "fixture_bot"}))
    assert await runtime.ensure_client()
    telegram = runtime.client
    runtime.poll_result(telegram, error="previous poll failed")
    batch = [
        Update.model_validate({"update_id": 100, "message": link_message({"url": "https://example.org"})}),
        Update.model_validate({"update_id": 101, "message": {
            "message_id": 2, "date": 1793611951, "from": USER,
            "chat": {"id": 42, "type": "private"},
            "document": {"file_id": "fixture-file", "file_unique_id": "fixture-unique",
                         "file_name": "fixture.pdf", "file_size": 6},
        }}),
    ]
    clock = [1793611952.0]
    runtime.safety.clock = lambda: clock[0]
    offsets = []

    async def response(bot, method, timeout=None):
        assert method.__api_method__ == "getUpdates"
        offsets.append(method.offset)
        clock[0] += 10  # Advance the test clock, retaining the real admission gate.
        if len(offsets) == 1:
            return batch
        assert runtime.connection_snapshot()[1]["state"] == "online"
        if len(offsets) == 2:
            return batch  # Replayed delivery must remain idempotent.
        runtime.stopping = True
        return []

    async def unexpected_retry(delay):
        raise AssertionError("successful backlog processing must not enter the poll retry path")

    monkeypatch.setattr(telegram.bot.session, "make_request", response)
    monkeypatch.setattr("app.workers.runtime.asyncio.sleep", unexpected_retry)
    try:
        await runtime.poll_loop()
        assert offsets == [0, 102, 102]
        assert runtime.ingest.offset(7) == 102
        assert runtime.ingest.pending(7) == []
        with state.db.sessions() as session:
            inbox = list(session.scalars(select(IngestUpdate)))
            assert len(inbox) == 2
            assert all(row.processed and row.payload == {} for row in inbox)
            items = list(session.scalars(select(MediaItem)))
            assert len(items) == 1 and items[0].original_name == "fixture.pdf"
            requests = list(session.scalars(select(MediaRequest)))
            assert len(requests) == 1 and requests[0].name_override is None
        assert runtime.connection_snapshot()[1]["state"] == "online"
    finally:
        runtime.stopping = True
        await telegram.close()
