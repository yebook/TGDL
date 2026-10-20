"""Independent regressions for safe activation, stalled commands and validation."""

import json
import os
import time
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

from app.bot.client import TelegramClient
from app.core.config import Config
from app.core.errors import AppError
from app.db.models import LocalApiRuntime
from app.main import create_app
from app.schemas.contracts import SettingsPatch
from app.services.local_api import LocalApiService


@pytest.fixture
def managed_app(config: Config):
    config.managed_local_api = True
    config.local_api_control_dir = config.data_dir / "control"
    with TestClient(create_app(config)) as client:
        directory = config.local_api_control_dir
        directory.mkdir(exist_ok=True, mode=0o700)
        path = directory / "applied.json"
        path.write_text(json.dumps({"version": 1, "command_id": None, "state": "stopped",
                                   "generation": None, "heartbeat_at": time.time()}))
        os.chmod(path, 0o600)
        yield client.app.state


def save(state, **changes):
    patch = SettingsPatch(revision=state.settings.read()[0], **changes)
    return state.settings.save(patch, state.settings.candidate(patch))


async def test_external_local_upgrade_waits_for_explicit_activation(managed_app, monkeypatch):
    state = managed_app
    save(state, bot_api_mode="local", telegram_api_id="1234", telegram_api_hash="a" * 32)
    with state.db.transaction() as session:
        session.execute(delete(LocalApiRuntime))
    service = LocalApiService(state.settings)
    identity, logout = AsyncMock(), AsyncMock()
    monkeypatch.setattr(TelegramClient, "identity", identity)
    monkeypatch.setattr(TelegramClient, "logout", logout)
    assert service.read()["phase"] == "failed"
    assert service.status()["applied_mode"] is None
    assert service.status()["can_retry"]
    assert not service.status()["can_restart"]
    assert await service.reconcile(state.runtime)
    identity.assert_not_awaited()
    logout.assert_not_awaited()
    service.action(False)
    assert service.read()["phase"] == "draining"


@pytest.mark.parametrize("process_state,acknowledge", [("stopped", False), ("starting", True)])
async def test_stalled_supervisor_command_becomes_retryable(managed_app, process_state, acknowledge):
    state = managed_app
    save(state, bot_api_mode="local", telegram_api_id="1234", telegram_api_hash="a" * 32)
    service = state.runtime.local_api
    service.update(phase="starting", logout_done=True, command_id="stalled", command_issued_at=time.time() - 300)
    path = state.runtime.config.local_api_control_dir / "applied.json"
    value = json.loads(path.read_text())
    value.update(state=process_state, command_id="stalled" if acknowledge else None)
    path.write_text(json.dumps(value))
    assert await service.reconcile(state.runtime)
    assert service.read()["phase"] == "failed"
    assert service.read()["error_code"] == "LOCAL_API_COMMAND_TIMEOUT"
    assert service.status()["can_retry"]


@pytest.mark.parametrize("field,value", [("bot_token", "999:new"), ("telegram_api_id", "9876"),
                                         ("telegram_api_hash", "b" * 32)])
def test_unsaved_local_identity_and_credentials_cannot_validate(managed_app, field, value):
    save(managed_app, bot_api_mode="local", telegram_api_id="1234", telegram_api_hash="a" * 32)
    service = managed_app.runtime.local_api
    service.update(phase="idle", applied_mode="local")
    candidate = managed_app.settings.read()[1].model_copy(update={field: value})
    with pytest.raises(AppError) as error:
        service.validate_allowed(candidate)
    assert error.value.code == "LOCAL_API_BUSY"
