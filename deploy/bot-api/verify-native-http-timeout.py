"""Exercise pinned native HTTP actors using accelerated, network-isolated handlers.

Build Dockerfile.verify-http-timeout, then docker run --rm --network none IMAGE.
The fixture scales production500s idle to1s; the previous7200s cap is4s.
The patched getFile waits beyond both. Every fixture wait has an external deadline;
production keeps normal finite idle/read/write deadlines after the handler returns.
"""
from __future__ import annotations

import concurrent.futures
import json
import os
import runpy
import socket
import struct
import subprocess
import tempfile
import time
from pathlib import Path

TOKEN = "123456:" + "x" * 35  # Synthetic fixture only; never contacts Telegram.
TRANSPORT = runpy.run_path(str(Path(__file__).with_name("verify-native-transport.py")))


def connect(port: int, *, small_buffer: bool = False) -> socket.socket:
    sock = socket.socket()
    sock.settimeout(7)
    if small_buffer:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1024)
    sock.connect(("127.0.0.1", port))
    return sock


def request(sock: socket.socket, method: str = "getFile", body: str = "fixture_delay=2") -> None:
    encoded = body.encode("ascii")
    headers = (f"POST /bot{TOKEN}/{method} HTTP/1.1\r\nHost: localhost\r\n"
               f"Content-Type: application/x-www-form-urlencoded\r\nContent-Length: {len(encoded)}\r\n\r\n")
    sock.sendall(headers.encode("ascii") + encoded)


def response(sock: socket.socket) -> int:
    data = bytearray()
    while b"\r\n\r\n" not in data:
        chunk = sock.recv(4096)
        if not chunk:
            raise AssertionError("Native connection closed before delayed response")
        data.extend(chunk)
        assert len(data) < 65536
    headers, body = bytes(data).split(b"\r\n\r\n", 1)
    length = next(int(line.split(b":", 1)[1]) for line in headers.split(b"\r\n")
                  if line.lower().startswith(b"content-length:"))
    assert length < 1024
    while len(body) < length:
        chunk = sock.recv(4096)
        assert chunk, "Native response truncated"
        body += chunk
    assert len(body) == length
    return int(headers.split(b" ", 2)[1])


def closed(sock: socket.socket, started: float, low: float = 0.7, high: float = 2.5) -> float:
    assert sock.recv(1) == b"", "Expected native deadline to close connection"
    elapsed = time.monotonic() - started
    assert low <= elapsed <= high, f"Native deadline outside expected range: {elapsed:.3f}s"
    return round(elapsed, 3)


def check(port: int, case: str) -> dict[str, object]:
    with connect(port, small_buffer=case == "blocked_write") as sock:
        started = time.monotonic()
        if case == "idle":
            elapsed = closed(sock, started)
        elif case in {"slow_header", "slow_body"}:
            payload = b"POST /bot" if case == "slow_header" else (
                f"POST /bot{TOKEN}/getFile HTTP/1.1\r\nHost: localhost\r\nContent-Length: 100\r\n\r\na"
            ).encode("ascii")
            sock.sendall(payload)
            elapsed = closed(sock, started)
        elif case in {"ordinary_pending", "baseline_cutoff", "previous_cap_cutoff"}:
            # The previous-cap binary reproduces the superseded native limit.
            previous = case == "previous_cap_cutoff"
            request(sock, "getMe" if case == "ordinary_pending" else "getFile",
                    "fixture_delay=5" if previous else "fixture_delay=2")
            elapsed = closed(sock, started, 3.5, 5.5) if previous else closed(sock, started)
        elif case == "blocked_write":
            request(sock, body="fixture_delay=1.3&fixture_large=1")
            time.sleep(3)
            received = 0
            while True:
                chunk = sock.recv(65536)
                if not chunk:
                    break
                received += len(chunk)
                assert received < 16 << 20, "Fixture response unexpectedly fully flushed"
            elapsed = round(time.monotonic() - started, 3)
            assert 0 < received < 16 << 20
            assert elapsed < 4.5, "Response write retained long pending deadline"
        else:
            delay = 5 if case == "beyond_previous_cap" else 2
            body = f"fixture_delay={delay}"
            method = "GeTfIlE" if case == "mixed_case" else "getFile"
            if case == "form_method":
                method, body = "", body + "&method=GeTfIlE"
            if case == "error_restoration":
                body += "&fixture_error=1"
            request(sock, method, body)
            assert response(sock) == (500 if case == "error_restoration" else 200)
            elapsed = round(time.monotonic() - started, 3)
            assert delay - 0.3 <= elapsed <= delay + 1.5, "Delayed getFile did not survive previous deadline"
            if case == "next_method_restoration":
                started = time.monotonic()
                request(sock, "getMe")
                closed(sock, started)
            elif case in {"keepalive_restoration", "error_restoration"}:
                closed(sock, time.monotonic())
        return {"case": case, "elapsed_seconds": elapsed, "passed": True}


def native_socket_count(process: subprocess.Popen[bytes]) -> int:
    count = 0
    for path in Path(f"/proc/{process.pid}/fd").iterdir():
        try:
            count += os.readlink(path).startswith("socket:")
        except FileNotFoundError:
            pass
    return count


def check_client_cleanup(port: int, process: subprocess.Popen[bytes], *, reset: bool) -> dict[str, object]:
    # No acquisition cancellation is inferred from this check: it verifies only
    # that the native HTTP socket is released when its client stops waiting.
    before = native_socket_count(process)
    with connect(port) as sock:
        request(sock, body="fixture_delay=99")
        started = time.monotonic()
        if reset:
            time.sleep(0.2)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
        else:
            # No bytes/progress and no handler completion across both historical
            # boundaries. Only the test's external socket deadline ends this wait.
            sock.settimeout(5.2)
            try:
                sock.recv(1)
            except TimeoutError:
                pass
            else:
                raise AssertionError("Pending getFile ended solely because time elapsed")
            sock.shutdown(socket.SHUT_RDWR)
        sock.close()
    deadline = time.monotonic() + 2
    while native_socket_count(process) > before and time.monotonic() < deadline:
        time.sleep(0.02)
    assert native_socket_count(process) == before, "Closed client leaked a native HTTP socket"
    return {"case": "client_reset_cleanup" if reset else "paused_pending_and_client_cancel_cleanup",
            "elapsed_seconds": round(time.monotonic() - started, 3), "passed": True}


def run(binary: str, cases: list[str]) -> list[dict[str, object]]:
    with tempfile.TemporaryDirectory(prefix="tgdl-http-deadline-") as temporary:
        directory = Path(temporary)
        (directory / "tmp").mkdir()
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        environment = {key: value for key, value in os.environ.items() if not key.startswith("TGD_")}
        environment.update(TELEGRAM_API_ID="12345", TELEGRAM_API_HASH="a" * 32, TGD_NATIVE_PROXY_MODE="direct")
        process = subprocess.Popen([
            binary, "--local", "--http-ip-address=127.0.0.1", f"--http-port={port}",
            f"--dir={directory}", f"--temp-dir={directory / 'tmp'}", "--verbosity=0",
        ], env=environment, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            TRANSPORT["wait_for_listener"](port, process)
            with concurrent.futures.ThreadPoolExecutor(max_workers=len(cases)) as executor:
                results = list(executor.map(lambda case: check(port, case), cases))
            if binary.endswith("/patched"):
                # Let ordinary1s idle connections from the readiness probe settle.
                time.sleep(1.2)
                results.append(check_client_cleanup(port, process, reset=False))
                results.append(check_client_cleanup(port, process, reset=True))
            assert process.poll() is None, "HTTP fixture crashed"
            return results
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def main() -> None:
    TRANSPORT["assert_network_none"]()
    cases = ["mixed_case", "form_method", "keepalive_restoration", "error_restoration",
             "next_method_restoration", "beyond_previous_cap", "idle", "slow_header", "slow_body",
             "ordinary_pending", "blocked_write"]
    results = run("/verification/baseline", ["baseline_cutoff"])
    results += run("/verification/previous-cap", ["previous_cap_cutoff"])
    results += run("/verification/patched", cases)
    print(json.dumps({"passed": True, "network": "none", "scaled_idle_seconds": 1,
                      "previous_cap_scaled_seconds": 4, "production_pending_timeout": None, "cases": results}))


if __name__ == "__main__":
    main()
