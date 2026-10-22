"""Credential-free production-cache and HTTP read-only routing verification.

Run only in a disposable Docker --network none container. No real credentials,
Telegram probes or cache directories are used. Raw HTTP/trace data never prints.
"""
from __future__ import annotations

import importlib.util
import json
import os
import signal
import socket
import subprocess
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

_spec = importlib.util.spec_from_file_location("transport", Path(__file__).with_name("verify-native-transport.py"))
assert _spec and _spec.loader
transport = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(transport)


def main() -> None:
    transport.assert_network_none()
    subprocess.run(["/verification/test-progress-cache"], check=True)
    with tempfile.TemporaryDirectory(prefix="tgd-progress-verification-") as temporary:
        directory = Path(temporary)
        (directory / "tmp").mkdir()
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        trace = directory / "connect.trace"
        env = dict(os.environ, TELEGRAM_API_ID="12345", TELEGRAM_API_HASH="a" * 32,
                   TGD_NATIVE_PROXY_MODE="direct", TGD_PROGRESS_GENERATION="synthetic-generation")
        process = subprocess.Popen(
            ["strace", "-f", "-e", "trace=connect", "-o", str(trace),
             "/usr/local/bin/telegram-bot-api", "--local", "--http-ip-address=127.0.0.1",
             f"--http-port={port}", f"--dir={directory}", f"--temp-dir={directory / 'tmp'}", "--verbosity=0"],
            env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, start_new_session=True,
        )
        try:
            transport.wait_for_listener(port, process)
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            # A unique syntactically valid fake token never becomes a native Client.
            token = "123456:" + "A" * 35
            batches = [json.dumps(["synthetic-alias"]), "{", "[]", json.dumps(["x"] * 17),
                       json.dumps(["x" * 81921])]
            for batch in batches:
                data = urllib.parse.urlencode({"file_ids": batch}).encode()
                request = urllib.request.Request(f"http://127.0.0.1:{port}/bot{token}/tgdGetFileProgress", data=data)
                try:
                    opener.open(request, timeout=2)
                    raise AssertionError("Missing Client unexpectedly produced progress")
                except urllib.error.HTTPError as error:
                    assert error.code == 404
                    body = json.loads(error.read(4096))
                    assert body["ok"] is False
            # Creating a Client necessarily creates its token-named directory.
            assert not (directory / token).exists()
            assert process.poll() is None
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)  # type: ignore[attr-defined]  # Linux-only helper.
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)  # type: ignore[attr-defined]
                process.wait(timeout=5)
        assert not transport.connection_destinations(trace), "Progress read initiated outbound connection"
    print(json.dumps({"production_cache": "passed", "missing_client_no_create": "passed",
                      "progress_read_outbound_connections": 0, "real_credentials": False}))


if __name__ == "__main__":
    main()
