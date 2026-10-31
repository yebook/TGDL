"""Manual recovery exercises real request protection with an offline SDK transport."""
import asyncio
import json
import time
from types import SimpleNamespace

import pytest
from aiogram import Bot
from aiogram.exceptions import TelegramNetworkError
from aiogram.methods import GetMe

from app.bot.client import TelegramClient
from app.core.security import digest
from app.db.models import BotIdentity, RequestSafety
from app.schemas.contracts import UserSettings
from tests.test_cloud_reconnect import save
from tests.test_connection_recovery import native as native_fixture
from tests.test_local_api import HASH, heartbeat

native = native_fixture
TOKEN = "123456789:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"


@pytest.fixture
def transport(native, monkeypatch):
    runtime = native.runtime
    clock = [time.time()]
    runtime.safety.clock = lambda: clock[0]
    runtime.safety.jitter = lambda: 0
    routes, calls = {}, []
    faults = {"cloud": 0, "local": 0, "required_proxy": None}
    original = TelegramClient.__init__

    def initialize(client, token, config, settings=None):
        original(client, token, config, settings)
        routes[client.bot] = settings

    async def identity(bot, **kwargs):
        values = routes[bot]
        calls.append(("getMe", values.bot_api_mode, values.proxy.model_dump(), bot.token))
        # Advance beyond normal one-second pacing without bypassing protection.
        clock[0] += 2
        required = faults["required_proxy"]
        wrong_route = required is not None and (not values.proxy.enabled or values.proxy.host != required)
        if faults[values.bot_api_mode] or wrong_route:
            faults[values.bot_api_mode] = max(0, faults[values.bot_api_mode] - 1)
            raise TelegramNetworkError(method=GetMe(), message="offline simulated transport")
        return SimpleNamespace(id=7, username="recovered_bot")

    async def logout(bot, **kwargs):
        values = routes[bot]
        calls.append(("logOut", values.bot_api_mode, values.proxy.model_dump(), bot.token))
        clock[0] += 2
        return True

    monkeypatch.setattr(TelegramClient, "__init__", initialize)
    monkeypatch.setattr(Bot, "get_me", identity)
    monkeypatch.setattr(Bot, "log_out", logout)
    return SimpleNamespace(clock=clock, calls=calls, faults=faults)


def configure(state, **updates):
    save(state, bot_token=TOKEN, allowed_user_ids=[42], bot_api_mode="local",
         telegram_api_id="1234", telegram_api_hash=HASH, **updates)


def protection(runtime):
    with runtime.db.sessions() as session:
        row = session.get(RequestSafety, digest(TOKEN))
        return dict(row.values) if row else {}


def acknowledge(service, generation):
    command = json.loads((service.config.local_api_control_dir / "desired.json").read_text())
    heartbeat(service, command_id=command["command_id"], state="running", generation=generation)
    return command


@pytest.mark.parametrize("old_proxy", [None, "broken-proxy.lan"])
async def test_failed_preflight_uses_new_saved_proxy_for_same_bot(native, transport, old_proxy):
    runtime, service = native.runtime, native.runtime.local_api
    configure(native, proxy={"enabled": bool(old_proxy), "host": old_proxy or ""})
    transport.faults["required_proxy"] = "working-proxy.lan"
    assert await service.reconcile(runtime)
    assert service.read()["error_code"] == "LOCAL_API_PREFLIGHT_FAILED"
    assert not service.read().get("logout_done") and not service.read().get("logout_uncertain")
    assert runtime.safety.status(TOKEN)["state"] == "backoff"
    before = protection(runtime)
    save(native, proxy={"enabled": True, "host": "working-proxy.lan", "password": "synthetic-secret"})
    assert protection(runtime) == before  # Saving a route cannot clear request protection.
    snapshot = UserSettings.model_validate_json(native.settings.secrets.decrypt(service.read()["cloud_snapshot"]))
    assert snapshot.bot_token == TOKEN
    assert snapshot.proxy.host == "working-proxy.lan"
    assert await service.reconcile(runtime)  # Backoff still prevents sending.
    assert len(transport.calls) == 1
    assert not service.read().get("logout_uncertain")
    transport.clock[0] += 200
    assert await service.reconcile(runtime)
    command = acknowledge(service, "proxy-recovery")
    proxy = json.loads(service.control_cipher().decrypt(command["proxy_encrypted"]))
    assert proxy["host"] == "working-proxy.lan" and proxy["password"] == "synthetic-secret"
    assert "synthetic-secret" not in json.dumps(command)
    assert not await service.reconcile(runtime)
    assert await runtime.ensure_client()
    assert runtime.connection_snapshot()[1]["state"] == "online"
    assert runtime.connection_snapshot()[3]["state"] == "active"
    assert all(call[2]["host"] == "working-proxy.lan" for call in transport.calls[1:])
    assert sum(call[0] == "logOut" for call in transport.calls) == 1
    await runtime.client.close()


@pytest.mark.parametrize("existing_cloud", [False, True], ids=["new-bot", "existing-bot"])
async def test_initial_local_and_proxy_save_routes_cloud_preflight_through_saved_proxy(native, transport, existing_cloud):
    if existing_cloud:
        save(native, bot_token=TOKEN, allowed_user_ids=[42])
        assert await native.runtime.ensure_client()
    configure(native, proxy={"enabled": True, "host": "working-proxy.lan"})
    transport.faults["required_proxy"] = "working-proxy.lan"
    assert await native.runtime.local_api.reconcile(native.runtime)
    calls = transport.calls[int(existing_cloud):]
    assert [call[:2] for call in calls] == [("getMe", "cloud"), ("logOut", "cloud")]
    assert all(call[2]["host"] == "working-proxy.lan" for call in calls)
    assert native.runtime.local_api.read()["logout_done"]


@pytest.mark.parametrize("restart", [False, True], ids=["retry", "restart"])
@pytest.mark.parametrize("stage", ["cloud", "local"])
async def test_repeated_network_failures_then_manual_recovery_keeps_safety_and_logout(native, transport, restart, stage):
    runtime, service = native.runtime, native.runtime.local_api
    configure(native)
    transport.faults[stage] = 2
    if stage == "local":
        assert await service.reconcile(runtime)
        acknowledge(service, "initial")
    assert await service.reconcile(runtime)
    for attempt in range(2):
        failed = service.read()
        assert failed["phase"] == "failed" and service.status()["can_retry"]
        assert service.status()["can_restart"]
        before = protection(runtime)
        assert before["failures"] == attempt + 1
        assert runtime.safety.status(TOKEN)["state"] == "backoff"
        service.action(restart)
        assert protection(runtime) == before
        assert service.read()["verification_started_at"] is None
        calls = len(transport.calls)
        assert await service.reconcile(runtime)
        assert len(transport.calls) == calls  # Clicking cannot skip the safety wait.
        transport.clock[0] += 200
        if service.read().get("command_id") is None:
            assert await service.reconcile(runtime)
        if service.read()["phase"] == "failed":
            continue  # Second cloud preflight failure; no command was sent.
        command = acknowledge(service, f"recovered-{attempt}")
        assert command["action"] == ("restart" if restart else "start")
        assert bool(await service.reconcile(runtime)) == (attempt == 0)
        if stage == "local":
            assert service.read()["cloud_available_at"] == failed["cloud_available_at"]
    assert service.read()["phase"] == "idle" and service.read()["applied_mode"] == "local"
    assert protection(runtime)["failures"] == 0
    assert sum(call[0] == "logOut" for call in transport.calls) == 1
    assert await runtime.ensure_client()
    assert runtime.connection_snapshot()[1]["username"] == "recovered_bot"
    await runtime.client.close()


@pytest.mark.parametrize("restart", [False, True], ids=["retry", "restart"])
async def test_postlogout_proxy_correction_and_further_failure_can_recover(native, transport, restart):
    runtime, service = native.runtime, native.runtime.local_api
    configure(native)
    assert await service.reconcile(runtime)
    acknowledge(service, "old")
    transport.faults["local"] = 1
    assert await service.reconcile(runtime)
    failed = service.read()
    before = protection(runtime)
    save(native, proxy={"enabled": True, "host": "working-proxy.lan"})
    assert protection(runtime) == before
    assert service.read()["logout_done"] and not service.read()["logout_uncertain"]
    assert service.read()["cloud_available_at"] == failed["cloud_available_at"]
    transport.clock[0] += 200
    assert await service.reconcile(runtime)
    command = acknowledge(service, "new-route")
    assert json.loads(service.control_cipher().decrypt(command["proxy_encrypted"]))["host"] == "working-proxy.lan"
    transport.faults["local"] = 1
    assert await service.reconcile(runtime)
    assert service.read()["phase"] == "failed"
    service.action(restart)
    transport.clock[0] += 200
    assert await service.reconcile(runtime)
    acknowledge(service, "manual-recovery")
    assert not await service.reconcile(runtime)
    assert sum(call[0] == "logOut" for call in transport.calls) == 1
    assert service.read()["cloud_available_at"] == failed["cloud_available_at"]


@pytest.mark.parametrize("observation", ["generation", "command_id", "credential_revision", "proxy_signature", "state", "heartbeat_at"])
async def test_late_local_identity_requires_current_supervisor_ack(native, monkeypatch, observation):
    runtime, service = native.runtime, native.runtime.local_api
    configure(native)
    service.update(phase="idle", applied_mode="local", command_id=None,
                   applied_command_id="current", applied_credential_revision=1)
    values = native.settings.read()[1]
    ack = {"command_id": "current", "credential_revision": 1, "state": "running",
           "generation": "generation-one", "proxy_signature": native.settings.proxy_signature(values)}
    heartbeat(service, **ack)
    entered, release = asyncio.Event(), asyncio.Event()

    async def identity(client):
        entered.set()
        await release.wait()
        return {"id": 7, "username": "obsolete"}

    monkeypatch.setattr(TelegramClient, "identity", identity)
    connecting = asyncio.create_task(runtime.ensure_client())
    await entered.wait()
    changed = {"generation": "generation-two", "command_id": "other", "credential_revision": 2,
               "proxy_signature": "other", "state": "stopped", "heartbeat_at": time.time() - 60}
    heartbeat(service, **(ack | {observation: changed[observation]}))
    release.set()
    assert not await connecting
    assert runtime.client is None
    with runtime.db.sessions() as session:
        assert session.get(BotIdentity, 7) is None
    # A later valid acknowledgement is eligible without clearing any state.
    heartbeat(service, **ack)
    assert await runtime.ensure_client()
    await runtime.client.close()


@pytest.mark.parametrize("observation", ["generation", "command_id", "credential_revision", "proxy_signature", "state", "heartbeat_at"])
async def test_operation_verification_cannot_commit_obsolete_process(native, monkeypatch, observation):
    runtime, service = native.runtime, native.runtime.local_api
    configure(native)
    service.update(logout_done=True, logout_uncertain=False)
    assert await service.reconcile(runtime)
    command = acknowledge(service, "generation-one")
    entered, release = asyncio.Event(), asyncio.Event()

    async def identity(client):
        entered.set()
        await release.wait()
        return {"id": 7, "username": "obsolete"}

    monkeypatch.setattr(TelegramClient, "identity", identity)
    verifying = asyncio.create_task(service.reconcile(runtime))
    await entered.wait()
    ack = service.applied()
    changed = {"generation": "generation-two", "command_id": "other", "credential_revision": 99,
               "proxy_signature": "other", "state": "stopped", "heartbeat_at": time.time() - 60}
    heartbeat(service, **(ack | {observation: changed[observation]}))
    release.set()
    assert await verifying
    assert service.read()["applied_mode"] == "cloud" and service.read()["phase"] == "verifying"
    with runtime.db.sessions() as session:
        assert session.get(BotIdentity, 7) is None
    heartbeat(service, **ack)
    assert not await service.reconcile(runtime)
    assert service.read()["applied_command_id"] == command["command_id"]
    assert service.read()["applied_generation"] == "generation-one"


async def test_corrected_token_and_proxy_preflight_the_new_bot_without_clearing_old_backoff(native, transport):
    runtime, service = native.runtime, native.runtime.local_api
    configure(native)
    transport.faults["required_proxy"] = "working-proxy.lan"
    assert await service.reconcile(runtime)
    old_protection = protection(runtime)
    corrected = "987654321:BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB"
    save(native, bot_token=corrected, proxy={"enabled": True, "host": "working-proxy.lan"})
    assert protection(runtime) == old_protection
    assert await service.reconcile(runtime)
    acknowledge(service, "corrected-bot")
    assert not await service.reconcile(runtime)
    assert await runtime.ensure_client()
    assert all(call[3] == corrected for call in transport.calls[1:])
    assert [call[:2] for call in transport.calls[1:3]] == [("getMe", "cloud"), ("logOut", "cloud")]
    assert protection(runtime) == old_protection
    await runtime.client.close()


async def test_unrelated_save_during_local_verification_does_not_invalidate_ack(native, transport, monkeypatch):
    runtime, service = native.runtime, native.runtime.local_api
    configure(native)
    assert await service.reconcile(runtime)
    command = acknowledge(service, "current")
    entered, release = asyncio.Event(), asyncio.Event()
    original = TelegramClient.identity

    async def identity(client):
        entered.set()
        await release.wait()
        return await original(client)

    monkeypatch.setattr(TelegramClient, "identity", identity)
    verifying = asyncio.create_task(service.reconcile(runtime))
    await entered.wait()
    save(native, download_concurrency=4)
    assert native.settings.read()[0] > command["credential_revision"]
    release.set()
    assert not await verifying
    assert service.read()["phase"] == "idle"
    assert service.read()["applied_credential_revision"] == command["credential_revision"]
    assert await runtime.ensure_client()
    await runtime.client.close()


async def test_verified_local_can_return_to_cloud_after_cooldown(native, transport):
    runtime, service = native.runtime, native.runtime.local_api
    configure(native, proxy={"enabled": True, "host": "working-proxy.lan"})
    assert await service.reconcile(runtime)
    acknowledge(service, "local")
    assert not await service.reconcile(runtime)
    assert await runtime.ensure_client()
    # Simulate the recorded cloud deadline expiring, independently of Bot pacing.
    service.update(cloud_available_at=time.time() - 1)
    save(native, bot_api_mode="cloud")
    assert await service.reconcile(runtime)
    command = json.loads((service.config.local_api_control_dir / "desired.json").read_text())
    assert command["action"] == "stop"
    heartbeat(service, command_id=command["command_id"], state="stopped", generation=None)
    assert not await service.reconcile(runtime)
    assert service.read()["phase"] == "idle" and service.read()["applied_mode"] == "cloud"
    assert await runtime.ensure_client()
    assert runtime.connection_snapshot()[1]["state"] == "online"
    assert runtime.connection_snapshot()[3]["state"] == "active"
    assert sum(call[0] == "logOut" for call in transport.calls) == 1
    await runtime.client.close()
