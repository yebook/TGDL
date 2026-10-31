"""App side of the separate encrypted, bounded diagnostic handoff."""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import TYPE_CHECKING, Any, Literal

from cryptography.fernet import InvalidToken

from app.schemas.responses import TelegramProtocolResult, TelegramProtocolTarget
from app.services.protocol_probe import (
    REQUEST_FILE,
    REQUEST_LIFETIME,
    RESULT_FILE,
    TARGETS,
    cipher_for,
    proxy_signature,
    read_private,
    write_private,
)

if TYPE_CHECKING:
    from app.services.local_api import LocalApiService
    from app.services.network_diagnostics import ProbeSnapshot
    from app.services.settings import SettingsService


async def joined_snapshot(local_api: LocalApiService, settings: SettingsService) -> tuple[Any, ...]:
    def read() -> tuple[Any, ...]:
        revision, values, _ = settings.read()
        return revision, values.bot_api_mode, settings.proxy_signature(values), local_api.applied(), local_api.read()

    task = asyncio.create_task(asyncio.to_thread(read))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        await task
        raise


def observed_route(applied: dict[str, Any], state: dict[str, Any], signature: str) -> Literal["matched", "different", "unavailable"]:
    command = state.get("command_id") or state.get("applied_command_id")
    revision = state.get("credential_revision") if state.get("command_id") else state.get("applied_credential_revision")
    if (not applied or applied.get("state") != "running" or not applied.get("generation")
            or not command or applied.get("command_id") != command or applied.get("credential_revision") != revision):
        return "unavailable"
    return "matched" if applied.get("proxy_signature") == signature else "different"


async def protocol_round(settings: SettingsService, local_api: LocalApiService,
                         snapshot: ProbeSnapshot) -> TelegramProtocolResult:
    result = TelegramProtocolResult(state="unavailable", route="proxy" if snapshot.proxy.enabled else "direct",
        settings_revision=snapshot.revision)
    if snapshot.mode != "local":
        result.state = "not_applicable"
        return result
    if not settings.config.managed_local_api:
        result.state = "unsupported"
        return result
    directory = settings.config.local_api_control_dir
    request: dict[str, Any] | None = None
    result.reason = "helper_unavailable"
    try:
        cipher = cipher_for(directory)
        request = {"version": 1, "request_id": uuid.uuid4().hex, "expires_at": time.time() + REQUEST_LIFETIME,
            "settings_revision": snapshot.revision, "proxy_signature": proxy_signature(snapshot.proxy.model_dump()),
            "proxy": snapshot.proxy.model_dump(), "generation": snapshot.applied.get("generation"),
            "command_id": snapshot.applied.get("command_id"),
            "credential_revision": snapshot.applied.get("credential_revision")}
        # Only separate diagnostic files are written. No key/desired/applied,
        # saved settings, operation, Telegram safety or lifecycle mutation.
        write_private(directory / REQUEST_FILE, cipher.encrypt(json.dumps(request).encode()))
        deadline = time.monotonic() + REQUEST_LIFETIME
        expected = {key: value for key, value in request.items() if key != "proxy"}
        while time.monotonic() < deadline:
            try:
                payload = json.loads(cipher.decrypt(read_private(directory / RESULT_FILE), ttl=10))
                if (isinstance(payload, dict) and set(payload) == set(expected) | {"targets"}
                        and all(payload.get(key) == value for key, value in expected.items())
                        and time.time() < request["expires_at"]):
                    targets = [TelegramProtocolTarget.model_validate(row) for row in payload["targets"]]
                    if tuple((row.dc, row.host, row.port) for row in targets) != TARGETS:
                        raise ValueError("invalid_target_coverage")
                    if any((row.state == "reachable") != (row.failure_kind is None)
                           or (row.state == "timeout") != (row.failure_kind == "timeout")
                           or row.latency_ms is None or not 0 <= row.latency_ms <= 8000 for row in targets):
                        raise ValueError("invalid_probe_result")
                    result.targets = targets
                    break
            except FileNotFoundError:
                pass
            await asyncio.sleep(0.05)
        else:
            result.reason = "round_timeout"
            return result
        result.reason = "stale_observation"
        revision, mode, signature, applied, state = await joined_snapshot(local_api, settings)
        result.observed_generation = applied.get("generation")
        result.observed_command_id = applied.get("command_id")
        if revision != snapshot.revision or mode != snapshot.mode or signature != request["proxy_signature"]:
            result.reason = "configuration_changed"
            return result
        observation_fields = ("generation", "command_id", "credential_revision", "proxy_signature", "state")
        if any(applied.get(key) != snapshot.applied.get(key) for key in observation_fields):
            result.reason = "stale_observation"
            return result
        result.applied_route = observed_route(applied, state, signature)
        count = sum(row.state == "reachable" for row in result.targets)
        result.state = ("reachable" if count == len(TARGETS) else "partial" if count else
                        "timeout" if all(row.state == "timeout" for row in result.targets) else "failed")
        result.reason = None
        return result
    except (OSError, ValueError, TypeError, KeyError, InvalidToken):
        return result
    finally:
        if request is not None:
            for name in (REQUEST_FILE, RESULT_FILE):
                try:
                    path = directory / name
                    payload = json.loads(cipher_for(directory).decrypt(read_private(path), ttl=10))
                    if isinstance(payload, dict) and payload.get("request_id") == request["request_id"]:
                        path.unlink(missing_ok=True)
                except (OSError, ValueError, TypeError, InvalidToken):
                    pass
