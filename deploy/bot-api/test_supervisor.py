"""Real fake subprocesses only; never contact Telegram or the user's runtime."""

from __future__ import annotations

import importlib.util
import json
import os
import socket
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from cryptography.fernet import Fernet

sys.path.insert(0, str(Path(__file__).parents[2] / "backend" / "app" / "services"))
spec = importlib.util.spec_from_file_location("tgd_api_supervisor", Path(__file__).with_name("supervisor.py"))
assert spec is not None and spec.loader is not None
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)


@pytest.fixture
def service(tmp_path: Path) -> Any:
    shared, cache = tmp_path / "control", tmp_path / "cache"
    shared.mkdir()
    cache.mkdir()
    key = Fernet.generate_key()
    module.atomic_write(shared / "control.key", key)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    fake = tmp_path / "fake_api.py"
    fake.write_text(
        "import os, socket\n"
        "assert os.environ['TELEGRAM_API_ID'] == '12345'\n"
        "assert os.environ['TELEGRAM_API_HASH'] == 'a' * 32\n"
        "from pathlib import Path\n"
        f"Path({str(tmp_path / 'child-generation')!r}).write_text(os.environ['TGD_PROGRESS_GENERATION'])\n"
        f"server=socket.socket(); server.bind(('127.0.0.1', {port})); server.listen()\n"
        "while True:\n"
        " client, address = server.accept(); client.close()\n",
        encoding="utf-8",
    )
    supervisor = module.Supervisor(
        shared, cache, command=[sys.executable, str(fake)], port=port, retry_delay=0,
    )
    yield supervisor, key
    supervisor.stop_diagnostic()
    supervisor.stop_process()


def diagnostic_request(supervisor: Any, key: bytes, identifier: str = "a" * 32) -> dict[str, Any]:
    proxy = {"enabled": False, "scheme": "socks5", "host": "", "port": 1080, "username": "", "password": ""}
    value = {"version": 1, "request_id": identifier, "expires_at": time.time() + 7,
             "settings_revision": 1, "proxy_signature": module.protocol_probe.proxy_signature(proxy),
             "proxy": proxy, "generation": None, "command_id": None, "credential_revision": None}
    supervisor.shared.chmod(0o700)
    module.atomic_write(supervisor.shared / module.protocol_probe.REQUEST_FILE, Fernet(key).encrypt(json.dumps(value).encode()))
    return value


@pytest.mark.parametrize("stop_reason", ["cancel", "deadline", "supersede"])
def test_diagnostic_helper_is_bounded_reaped_and_does_not_block_heartbeat(
        service: Any, monkeypatch: pytest.MonkeyPatch, stop_reason: str) -> None:
    supervisor, key = service
    helper = supervisor.shared / "fake_helper.py"
    marker = supervisor.shared / "helper-started"
    helper.write_text("import os, time\nfrom pathlib import Path\n"
        "assert not any(name in os.environ for name in ['TELEGRAM_API_ID','TELEGRAM_API_HASH','TGD_BOT_TOKEN','TGD_PROXY','TGD_NATIVE_PROXY_PASSWORD'])\n"
        f"Path({str(marker)!r}).write_text('ready')\ntime.sleep(60)\n", encoding="utf-8")
    monkeypatch.setattr(module.protocol_probe, "__file__", str(helper))
    for name in ("TELEGRAM_API_ID", "TELEGRAM_API_HASH", "TGD_BOT_TOKEN", "TGD_PROXY", "TGD_NATIVE_PROXY_PASSWORD"):
        monkeypatch.setenv(name, "fixture-private-secret")
    diagnostic_request(supervisor, key)
    started = time.monotonic()
    supervisor.tick()
    process = supervisor.diagnostic_process
    assert process is not None
    deadline = time.monotonic() + 3
    while not marker.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert marker.exists()
    # A blocked resolver/remote socket lives only in the helper. Repeated
    # supervisor work and forced heartbeat publication stay prompt.
    old = json.loads((supervisor.shared / "applied.json").read_bytes())["heartbeat_at"]
    for _ in range(5):
        supervisor.published_at = time.monotonic() - 6
        supervisor.tick()
    assert time.monotonic() - started < 3
    assert json.loads((supervisor.shared / "applied.json").read_bytes())["heartbeat_at"] >= old
    assert supervisor.process is None and supervisor.state == "waiting_credentials"
    if stop_reason == "cancel":
        (supervisor.shared / module.protocol_probe.REQUEST_FILE).unlink()
    elif stop_reason == "deadline":
        supervisor.diagnostic_deadline = time.monotonic() - 1
    else:
        diagnostic_request(supervisor, key, "b" * 32)
    supervisor.diagnostic_tick()
    assert process.poll() is not None
    if stop_reason == "supersede":
        assert supervisor.diagnostic_process is not None and supervisor.diagnostic_process is not process
    else:
        assert supervisor.diagnostic_process is None
        supervisor.diagnostic_tick()
        assert supervisor.diagnostic_process is None  # Same request cannot replay.
    assert not (supervisor.shared / "desired.json").exists()


def test_invalid_expired_or_foreign_diagnostic_request_cannot_spawn(service: Any) -> None:
    supervisor, key = service
    value = diagnostic_request(supervisor, key)
    value["expires_at"] = time.time() - 1
    module.atomic_write(supervisor.shared / module.protocol_probe.REQUEST_FILE, Fernet(key).encrypt(json.dumps(value).encode()))
    supervisor.diagnostic_tick()
    assert supervisor.diagnostic_process is None
    value["expires_at"] = time.time() + 6
    module.atomic_write(supervisor.shared / module.protocol_probe.REQUEST_FILE, Fernet(Fernet.generate_key()).encrypt(json.dumps(value).encode()))
    supervisor.diagnostic_tick()
    assert supervisor.diagnostic_process is None


def test_diagnostic_nonce_cannot_replay_after_superseding_round(
        service: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    supervisor, key = service
    helper = supervisor.shared / "fake_helper.py"
    helper.write_text("import time\ntime.sleep(60)\n", encoding="utf-8")
    monkeypatch.setattr(module.protocol_probe, "__file__", str(helper))
    original = diagnostic_request(supervisor, key)
    supervisor.diagnostic_tick()
    first = supervisor.diagnostic_process
    diagnostic_request(supervisor, key, "b" * 32)
    supervisor.diagnostic_tick()
    second = supervisor.diagnostic_process
    assert first is not None and second is not None and first.poll() is not None
    module.atomic_write(supervisor.shared / module.protocol_probe.REQUEST_FILE, Fernet(key).encrypt(json.dumps(original).encode()))
    supervisor.diagnostic_tick()
    assert second.poll() is not None and supervisor.diagnostic_process is None


def command(supervisor: Any, key: bytes, identifier: str, action: str = "start", **changes: Any) -> None:
    desired = {
        "version": 1, "command_id": identifier, "action": action,
        "credential_revision": 1, "api_id": 12345,
        "api_hash_encrypted": Fernet(key).encrypt(b"a" * 32).decode(),
        "proxy_encrypted": Fernet(key).encrypt(json.dumps({"enabled": False,"scheme":"socks5","host":"","port":1080,"username":"","password":""}).encode()).decode(),
        "proxy_signature": module.hashlib.sha256(json.dumps({"enabled":False}, sort_keys=True).encode()).hexdigest(),
    }
    desired.update(changes)
    module.atomic_write(supervisor.shared / "desired.json", json.dumps(desired).encode())


def settle(supervisor: Any, state: str) -> None:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        supervisor.tick()
        if supervisor.state == state:
            return
        time.sleep(0.02)
    raise AssertionError(f"Expected {state}, got {supervisor.state}")


def test_stable_heartbeat_is_bounded_and_state_changes_publish_immediately(service: Any, monkeypatch) -> None:
    supervisor, _ = service
    clock = [100.0]
    monkeypatch.setattr(module, "time", SimpleNamespace(monotonic=lambda: clock[0], time=lambda: 1000 + clock[0]))
    write = module.atomic_write
    writes = []

    def record(path, value, *args, **kwargs):
        writes.append(json.loads(value))
        return write(path, value, *args, **kwargs)

    monkeypatch.setattr(module, "atomic_write", record)
    supervisor.publish()
    for increment in (1, 2, 3, 4, 4.99):
        clock[0] = 100 + increment
        supervisor.publish()
    assert len(writes) == 1
    clock[0] = 105
    supervisor.publish()
    assert len(writes) == 2
    assert writes[-1]["heartbeat_at"] == 1105
    supervisor.state, supervisor.generation = "running", "new-child"
    supervisor.publish()
    assert len(writes) == 3
    assert writes[-1]["generation"] == "new-child"
    supervisor.state = "failed"
    supervisor.publish()
    assert len(writes) == 4
    assert writes[-1]["state"] == "failed"


def test_failed_heartbeat_write_is_retried_without_waiting(service: Any, monkeypatch) -> None:
    supervisor, _ = service
    original = module.atomic_write
    failed = [False]

    def fail_once(*args, **kwargs):
        if not failed[0]:
            failed[0] = True
            raise OSError("temporary control I/O failure")
        return original(*args, **kwargs)

    monkeypatch.setattr(module, "atomic_write", fail_once)
    with pytest.raises(OSError):
        supervisor.publish()
    supervisor.publish()
    assert json.loads((supervisor.shared / "applied.json").read_text())["state"] == "waiting_credentials"


@pytest.mark.parametrize('scheme', ['http', 'socks5'])
def test_native_proxy_encrypted_handoff_and_restart(service: Any, scheme: str) -> None:
    supervisor, key = service
    command(supervisor, key, 'direct')
    settle(supervisor, 'running')
    generation = supervisor.generation
    proxy = {'enabled': True, 'scheme': scheme, 'host':'127.0.0.1', 'port':1080,
             'username':'user', 'password':'synthetic-secret'}
    encrypted = Fernet(key).encrypt(json.dumps(proxy).encode()).decode()
    signature = module.hashlib.sha256(json.dumps(proxy, sort_keys=True).encode()).hexdigest()
    command(supervisor, key, 'proxied', credential_revision=2, proxy_encrypted=encrypted, proxy_signature=signature)
    settle(supervisor, 'running')
    assert supervisor.generation != generation
    desired = supervisor.desired
    environment = supervisor.proxy_environment(desired)
    assert environment['TGD_NATIVE_PROXY_MODE'] == scheme
    assert environment['TGD_NATIVE_PROXY_PASSWORD'] == 'synthetic-secret'
    status = (supervisor.shared / 'applied.json').read_text()
    assert 'synthetic-secret' not in status
    assert json.loads(status)['proxy_signature'] == signature
    assert all('synthetic-secret' not in argument for argument in supervisor.process.args)
    command(supervisor, key, 'tampered', credential_revision=3, proxy_encrypted=encrypted, proxy_signature='0'*64)
    supervisor.tick()
    assert supervisor.state == 'failed'
    assert supervisor.error_code == 'invalid_proxy'
    assert json.loads((supervisor.shared/'applied.json').read_text())['proxy_signature'] is None


def test_disabled_proxy_is_explicit_direct_and_unicode_credentials_are_bounded(service: Any) -> None:
    supervisor, key = service
    proxy={'enabled':True,'scheme':'http','host':'proxy.lan','port':1080,'username':'😀'*200,'password':'😀'*1000}
    # Match the application's compact UTF-8 JSON encoding.
    encrypted=Fernet(key).encrypt(json.dumps(proxy, ensure_ascii=False, separators=(',', ':')).encode()).decode()
    signature=module.hashlib.sha256(json.dumps(proxy, sort_keys=True).encode()).hexdigest()
    command(supervisor,key,'unicode',proxy_encrypted=encrypted,proxy_signature=signature)
    supervisor.tick()
    assert supervisor.proxy_environment(supervisor.desired)['TGD_NATIVE_PROXY_MODE']=='http'
    command(supervisor,key,'direct-again',credential_revision=2)
    settle(supervisor,'running')
    assert supervisor.proxy_environment(supervisor.desired)['TGD_NATIVE_PROXY_MODE']=='direct'


@pytest.mark.parametrize('credential', ['x' * 256, '中' * 85 + 'x'])
def test_socks5_rejects_credentials_over_255_bytes_before_spawn(service: Any, credential: str) -> None:
    supervisor, key = service
    proxy = {'enabled':True,'scheme':'socks5','host':'proxy.lan','port':1080,'username':'user','password':credential}
    encrypted = Fernet(key).encrypt(json.dumps(proxy).encode()).decode()
    signature = module.hashlib.sha256(json.dumps(proxy, sort_keys=True).encode()).hexdigest()
    command(supervisor,key,'oversized-socks',proxy_encrypted=encrypted,proxy_signature=signature)
    supervisor.tick()
    assert supervisor.process is None
    assert supervisor.error_code == 'invalid_proxy'


def test_waits_without_credentials_and_publishes_heartbeat(service: Any) -> None:
    supervisor, _ = service
    supervisor.tick()
    first = json.loads((supervisor.shared / "applied.json").read_text())
    supervisor.tick()
    second = json.loads((supervisor.shared / "applied.json").read_text())
    assert first["state"] == "waiting_credentials"
    assert second["heartbeat_at"] >= first["heartbeat_at"]
    assert supervisor.process is None


def test_process_lifecycle_idempotency_and_generation(service: Any) -> None:
    supervisor, key = service
    command(supervisor, key, "start_1")
    settle(supervisor, "running")
    first_process, generation = supervisor.process, supervisor.generation
    assert (supervisor.cache.parent / 'child-generation').read_text() == generation
    supervisor.tick()
    assert supervisor.process is first_process
    # Re-encryption has a fresh nonce, but an unchanged credential revision must
    # not restart the same credential set for an idempotent start command.
    command(supervisor, key, "start_again")
    supervisor.tick()
    assert supervisor.process is first_process
    assert (supervisor.cache / ".tgd-api-generation").read_text().strip() == generation
    assert "a" * 32 not in (supervisor.shared / "applied.json").read_text()
    assert supervisor.process.args == supervisor.command
    command(supervisor, key, "restart_2", "restart")
    settle(supervisor, "running")
    assert first_process.poll() is not None
    assert supervisor.generation != generation
    assert (supervisor.cache.parent / 'child-generation').read_text() == supervisor.generation
    command(supervisor, key, "stop_3", "stop", api_id=None, api_hash_encrypted=None)
    settle(supervisor, "stopped")
    assert supervisor.process is None


def test_failed_spawn_does_not_publish_candidate_generation(service: Any, monkeypatch: Any) -> None:
    supervisor, key = service
    command(supervisor, key, "start")
    settle(supervisor, "running")
    supervisor.stop_process()
    original = supervisor.generation
    marker = (supervisor.cache / ".tgd-api-generation").read_bytes()
    captured: dict[str, str] = {}

    def fail_spawn(*args: Any, **kwargs: Any) -> None:
        captured.update(kwargs['env'])
        assert supervisor.generation == original
        raise OSError('synthetic spawn failure')

    monkeypatch.setattr(module.subprocess, 'Popen', fail_spawn)
    with pytest.raises(OSError):
        supervisor.start_process()
    assert captured['TGD_PROGRESS_GENERATION'] != original
    assert supervisor.generation == original
    assert (supervisor.cache / ".tgd-api-generation").read_bytes() == marker


def test_credential_errors_ack_without_killing_previous_process(service: Any) -> None:
    supervisor, key = service
    command(supervisor, key, "start")
    settle(supervisor, "running")
    process = supervisor.process
    command(supervisor, key, "bad", api_hash_encrypted="not-encrypted")
    supervisor.tick()
    status = json.loads((supervisor.shared / "applied.json").read_text())
    assert status["command_id"] == "bad" and status["state"] == "failed"
    assert status["error_code"] == "invalid_credentials"
    assert process.poll() is None
    command(supervisor, key, "recover", "restart")
    settle(supervisor, "running")
    assert process.poll() is not None


def test_reencrypted_restart_replay_does_not_restart_twice(service: Any) -> None:
    supervisor, key = service
    command(supervisor, key, "restart_once", "restart")
    settle(supervisor, "running")
    process, generation = supervisor.process, supervisor.generation
    command(supervisor, key, "restart_once", "restart")
    supervisor.tick()
    assert supervisor.process is process
    assert supervisor.generation == generation
    assert supervisor.state == "running"
    assert supervisor.error_code is None


def test_bounded_crash_retries_and_explicit_recovery(service: Any) -> None:
    supervisor, key = service
    supervisor.command = [sys.executable, "-c", "raise SystemExit(1)"]
    command(supervisor, key, "crash")
    deadline = time.monotonic() + 5
    while supervisor.failures < 4 and time.monotonic() < deadline:
        supervisor.tick()
        time.sleep(0.02)
    assert supervisor.failures == 4
    generation = supervisor.generation
    supervisor.tick()
    assert supervisor.process is None and supervisor.generation == generation
    assert supervisor.error_code == "process_exited"
    status = json.loads((supervisor.shared / "applied.json").read_text())
    assert status["retry_pending"] is False


def test_child_retry_publishes_pending_then_same_command_recovers(service: Any) -> None:
    supervisor, key = service
    working_command = supervisor.command
    supervisor.command = [sys.executable, "-c", "raise SystemExit(1)"]
    supervisor.retry_delay = 3600
    command(supervisor, key, "recovering")
    settle(supervisor, "failed")
    failed = json.loads((supervisor.shared / "applied.json").read_text())
    assert failed["retry_pending"] is True and failed["error_code"] == "process_exited"
    supervisor.command, supervisor.next_retry = working_command, 0
    settle(supervisor, "running")
    recovered = json.loads((supervisor.shared / "applied.json").read_text())
    assert recovered["retry_pending"] is False and recovered["error_code"] is None
    assert recovered["command_id"] == failed["command_id"] == "recovering"
    assert recovered["generation"] != failed["generation"]


def test_listen_timeout_reaps_process(service: Any) -> None:
    supervisor, key = service
    supervisor.command = [sys.executable, "-c", "import time; time.sleep(30)"]
    supervisor.startup_timeout = 0
    command(supervisor, key, "timeout")
    supervisor.tick()
    assert supervisor.process is None
    assert supervisor.error_code == "listen_timeout"


@pytest.mark.parametrize("changes", [
    {"version": 2}, {"version": True}, {"api_id": True}, {"action": "shell"},
    {"command_id": "../escape"}, {"extra": "no"}, {"credential_revision": -1},
])
def test_rejects_malformed_handoff(service: Any, changes: dict[str, Any]) -> None:
    supervisor, key = service
    command(supervisor, key, "bad", **changes)
    supervisor.tick()
    assert supervisor.state == "failed"
    assert supervisor.process is None


def test_bounded_read(service: Any) -> None:
    supervisor, _ = service
    module.atomic_write(supervisor.shared / "desired.json", b"x" * (module.MAX_BYTES + 1))
    supervisor.tick()
    assert supervisor.error_code == "invalid_configuration"


@pytest.mark.skipif(os.name == "nt", reason="Linux NOFOLLOW and POSIX permissions")
def test_rejects_symlink_and_world_readable_inputs(service: Any) -> None:
    supervisor, key = service
    command(supervisor, key, "bad")
    desired = supervisor.shared / "desired.json"
    desired.chmod(0o644)
    supervisor.tick()
    assert supervisor.error_code == "control_permissions"
    desired.unlink()
    desired.symlink_to(supervisor.shared / "control.key")
    supervisor.tick()
    assert supervisor.error_code == "control_io_failed"


@pytest.mark.skipif(os.name == "nt", reason="POSIX FIFO")
def test_rejects_fifo_without_blocking_heartbeat(service: Any) -> None:
    supervisor, _ = service
    getattr(os, "mkfifo", None)(supervisor.shared / "desired.json", 0o600)
    supervisor.tick()
    assert supervisor.error_code == "invalid_configuration"
    assert (supervisor.shared / "applied.json").exists()
