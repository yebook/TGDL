"""Offline Linux fixture: run in a disposable container, never with real volumes.

Mount current entrypoint/supervisor in their production locations, use network
none, read-only root, no capabilities, and private writable tmpfs control/cache.
Repeat with --user 0:0, 0:1000, 1000:0 and 1000:1000.
"""

import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path

spec = importlib.util.spec_from_file_location("supervisor", "/usr/local/lib/tgd-api-supervisor.py")
assert spec is not None and spec.loader is not None
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
shared = Path("/run/tgd-api-control")
module.atomic_write(shared / "control.key", b"synthetic-private-key")
assert module.read_private(shared / "control.key") == b"synthetic-private-key"
module.atomic_write(shared / "desired.json", json.dumps({
    "version": 1, "command_id": "offline-stop", "action": "stop",
    "credential_revision": 1, "api_id": None, "api_hash_encrypted": None,
    "proxy_encrypted": None, "proxy_signature": None,
}).encode())
process = subprocess.Popen(["/bin/sh", "/entrypoint.sh"])
try:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        assert process.poll() is None, "Entrypoint/supervisor exited before heartbeat"
        try:
            status = json.loads(module.read_private(shared / "applied.json"))
        except FileNotFoundError:
            time.sleep(0.05)
            continue
        assert status["state"] == "stopped" and status["command_id"] == "offline-stop"
        assert status["generation"] is None  # No native API or Telegram connection.
        break
    else:
        raise AssertionError("No fresh supervisor heartbeat")
finally:
    process.terminate()
    assert process.wait(timeout=5) == 0
assert json.loads(module.read_private(shared / "applied.json"))["state"] == "stopped"
assert shared.stat().st_uid == os.getuid() and shared.stat().st_mode & 0o777 == 0o700
# Zero does not bypass private-control-file mode checks.
key = shared / "control.key"
key.chmod(0o644)
try:
    module.read_private(key)
except module.ConfigError as error:
    assert str(error) == "control_permissions"
else:
    raise AssertionError("World-readable control key was accepted")
print(f"PASS identity {os.getuid()}:{os.getgid()}: entrypoint, heartbeat, private control and shutdown")
