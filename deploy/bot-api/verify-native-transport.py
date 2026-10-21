"""Credential-free patched transport acceptance, run ONLY in a network-none container.

Requires strace in the disposable test image. Output contains aggregate evidence,
never child output, HTTP requests, environment values, or the synthetic Bot token.
The same temporary TDLib cache is reused to verify persisted-proxy removal.
"""
from __future__ import annotations

import argparse
import ipaddress
import json
import os
import re
import signal
import socket
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


class RejectingProxy:
    def __init__(self, scheme: str) -> None:
        self.scheme = scheme
        self.listener = socket.socket()
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen()
        self.listener.settimeout(0.2)
        self.port = self.listener.getsockname()[1]
        self.handshakes = 0
        self.closed = threading.Event()
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _serve(self) -> None:
        while not self.closed.is_set():
            try:
                connection, _ = self.listener.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            with connection:
                connection.settimeout(1)
                try:
                    if self.scheme == "socks5":
                        request = bytearray()
                        while len(request) < 2:
                            chunk = connection.recv(2 - len(request))
                            if not chunk:
                                break
                            request.extend(chunk)
                        if len(request) >= 2 and request[0] == 5:
                            self.handshakes += 1
                            # Reject negotiation: configured proxy is unusable.
                            connection.sendall(b"\x05\xff")
                    else:
                        request = bytearray()
                        while b"\r\n\r\n" not in request and len(request) < 4096:
                            chunk = connection.recv(512)
                            if not chunk:
                                break
                            request.extend(chunk)
                        if request.startswith(b"CONNECT "):
                            self.handshakes += 1
                            connection.sendall(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\n\r\n")
                except OSError:
                    pass

    def close(self) -> None:
        self.closed.set()
        self.listener.close()
        self.thread.join(timeout=2)


def assert_network_none() -> None:
    # Fail rather than accidentally running the synthetic authorization over a
    # real route. Docker --network none exposes only loopback, including IPv6.
    interfaces = {entry.name for entry in Path("/sys/class/net").iterdir()}
    if interfaces != {"lo"}:
        raise RuntimeError("This helper requires an isolated --network none container")


def wait_for_listener(port: int, process: subprocess.Popen[bytes]) -> None:
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("Native API exited before listening")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return
        except OSError:
            time.sleep(0.05)
    raise RuntimeError("Native API did not start listening")


def trigger_synthetic_client(port: int) -> None:
    # This fixture is never a valid real credential. No real network route exists.
    token = "123456:" + "A" * 35  # Canonical base64url, so upstream reaches TDLib initialization.
    url = f"http://127.0.0.1:{port}/bot{token}/getMe"
    try:
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(url, timeout=12) as response:
            response.read(4096)
    except (OSError, urllib.error.URLError):
        pass


def connection_destinations(trace: Path) -> list[tuple[str, int]]:
    destinations = []
    for line in trace.read_text(encoding="utf-8", errors="replace").splitlines():
        if "connect(" not in line:
            continue
        ipv4 = re.search(r'sin_port=htons\((\d+)\).*sin_addr=inet_addr\("([^"]+)"\)', line)
        ipv6 = re.search(r'sin6_port=htons\((\d+)\).*inet_pton\(AF_INET6, "([^"]+)"', line)
        matched = ipv4 or ipv6
        if matched:
            destinations.append((matched.group(2), int(matched.group(1))))
        elif "AF_INET" in line:
            raise RuntimeError("Unrecognized internet connect syscall; cannot verify isolation")
    return destinations


def run_case(binary: str, directory: Path, cache: Path, case: str | None,
             socks: RejectingProxy, http: RejectingProxy, duration: float) -> dict[str, Any]:
    mode = "socks5" if case == "invalid_proxy" else case
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    api_port = listener.getsockname()[1]
    listener.close()
    trace = directory / f"trace-{case or 'missing'}.txt"
    selected = socks if mode == "socks5" else http
    environment = {key: value for key, value in os.environ.items() if not key.startswith("TGD_NATIVE_PROXY_")}
    environment.update(TELEGRAM_API_ID="12345", TELEGRAM_API_HASH="a" * 32)
    if mode is not None:
        environment.update(TGD_NATIVE_PROXY_MODE=mode, TGD_NATIVE_PROXY_HOST="127.0.0.1",
                           TGD_NATIVE_PROXY_PORT="0" if case == "invalid_proxy" else str(selected.port),
                           TGD_NATIVE_PROXY_USERNAME="", TGD_NATIVE_PROXY_PASSWORD="")
    command = ["strace", "-f", "-s", "128", "-e", "trace=connect", "-o", str(trace), binary,
               "--local", "--http-ip-address=127.0.0.1", f"--http-port={api_port}",
               f"--dir={cache}", f"--temp-dir={cache / 'tmp'}", "--verbosity=0"]
    initial_socks, initial_http = socks.handshakes, http.handshakes
    process = subprocess.Popen(command, env=environment, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL, start_new_session=True)
    trigger: threading.Thread | None = None
    try:
        wait_for_listener(api_port, process)
        trigger = threading.Thread(target=trigger_synthetic_client, args=(api_port,), daemon=True)
        trigger.start()
        time.sleep(duration)
        if process.poll() is not None:
            raise RuntimeError("Native API unexpectedly exited during transport verification")
    finally:
        kill_group = os.killpg  # type: ignore[attr-defined]  # Linux-only helper, checked on Windows too.
        if process.poll() is None:
            kill_group(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            kill_group(process.pid, signal.SIGKILL)  # type: ignore[attr-defined]
            process.wait(timeout=5)
        if trigger:
            trigger.join(timeout=15)
    destinations = connection_destinations(trace)
    external = [(host, port) for host, port in destinations if not ipaddress.ip_address(host).is_loopback]
    observed_socks, observed_http = socks.handshakes - initial_socks, http.handshakes - initial_http
    if case in {"socks5", "http"}:
        if external or not (observed_socks if mode == "socks5" else observed_http):
            raise RuntimeError(f"Configured proxy was not exclusively selected: socks={observed_socks}, "
                               f"connect={observed_http}, external_attempts={len(external)}, "
                               f"total_attempts={len(destinations)}")
        if any(port != selected.port for host, port in destinations):
            raise RuntimeError("Unexpected loopback destination bypassed configured proxy")
    elif case == "direct":
        if observed_socks or observed_http or not external:
            raise RuntimeError("Direct mode did not clear the cached proxy selection")
    elif destinations or observed_socks or observed_http:
        raise RuntimeError("Invalid or missing proxy selection unexpectedly enabled networking")
    return {"case": case or "missing_mode", "socks_handshakes": observed_socks,
            "connect_handshakes": observed_http, "internet_connect_attempts": len(external),
            "passed": True}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", default="/usr/local/bin/telegram-bot-api")
    parser.add_argument("--duration", type=float, default=8)
    arguments = parser.parse_args()
    if not 3 <= arguments.duration <= 30:
        parser.error("duration must be between 3 and 30 seconds")
    assert_network_none()
    socks, http = RejectingProxy("socks5"), RejectingProxy("http")
    try:
        with tempfile.TemporaryDirectory(prefix="tgdl-native-acceptance-") as temporary:
            directory = Path(temporary)
            cache = directory / "cache"
            (cache / "tmp").mkdir(parents=True)
            results = [run_case(arguments.binary, directory, cache, case, socks, http, arguments.duration)
                       for case in ("socks5", "http", "direct", "invalid_proxy", None)]
            print(json.dumps({"isolated_network": "none", "real_credentials": False, "results": results}))
    finally:
        socks.close()
        http.close()


if __name__ == "__main__":
    main()
