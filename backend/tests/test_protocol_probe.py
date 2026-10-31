"""Real local socket fixtures; never public network or Telegram credentials."""
import asyncio
import json
import os
import struct
import time
from pathlib import Path
from typing import Any

import pytest
from cryptography.fernet import Fernet

from app.services import protocol_probe as wire


def request(proxy: dict[str, Any] | None = None) -> dict[str, Any]:
    proxy = proxy or {"enabled": False, "scheme": "socks5", "host": "", "port": 1080, "username": "", "password": ""}
    return {"version": 1, "request_id": "a" * 32, "expires_at": time.time() + 7,
        "settings_revision": 3, "proxy_signature": wire.proxy_signature(proxy), "proxy": proxy,
        "generation": "generation-1", "command_id": "command-1", "credential_revision": 2}


def response(nonce: bytes, *, wrong_nonce: bool = False) -> bytes:
    body = struct.pack("<I", 0x05162463) + (b"x" * 16 if wrong_nonce else nonce) + b"s" * 16
    body += b"\x08" + b"p" * 8 + b"\x00" * 3
    body += struct.pack("<IIQ", 0x1CB5C415, 1, 123)
    return struct.pack("<QQI", 0, (int(time.time()) << 32) | 1, len(body)) + body


async def answer(reader: asyncio.StreamReader, writer: asyncio.StreamWriter, *, bad: str | None = None) -> None:
    try:
        packet = await reader.readexactly(42)
        assert packet[:2] == b"\xef\x0a"
        assert packet[22:26] == struct.pack("<I", 0xBE7E8EF1)
        payload = response(packet[26:], wrong_nonce=bad == "nonce")
        if bad == "arbitrary":
            writer.write(b"HTTP/1.1 200 OK\r\n\r\n")
        elif bad == "oversized":
            writer.write(b"\x7f\xff\xff\xff")
        elif bad == "short":
            writer.write(bytes([len(payload) // 4]) + payload[:24])
        elif bad == "hang":
            await reader.read()
        else:
            # Deliberate fragments exercise exact bounded framing reads.
            writer.write(bytes([len(payload) // 4]) + payload[:7])
            await writer.drain()
            writer.write(payload[7:])
        await writer.drain()
    finally:
        writer.close()
        await writer.wait_closed()


@pytest.mark.parametrize("bad", [None, "nonce", "arbitrary", "oversized", "short", "hang"])
async def test_real_direct_framing_nonce_and_timeout(bad: str | None) -> None:
    closed = asyncio.Event()

    async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            await answer(reader, writer, bad=bad)
        finally:
            closed.set()

    async with await asyncio.start_server(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        outcome = await wire.probe_target(request()["proxy"], (1, "127.0.0.1", port), 0.15)
        assert outcome["state"] == ("reachable" if bad is None else "timeout" if bad == "hang" else "failed")
        assert outcome["failure_kind"] == (None if bad is None else "timeout" if bad == "hang" else "protocol")
        await asyncio.wait_for(closed.wait(), 1)


@pytest.mark.parametrize("scheme", ["http", "socks5"])
@pytest.mark.parametrize("authenticated", [False, True])
@pytest.mark.parametrize("reject", [False, True])
async def test_real_proxy_tunnels_and_auth(scheme: str, authenticated: bool, reject: bool) -> None:
    finished = asyncio.Event()

    async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            if scheme == "http":
                header = await reader.readuntil(b"\r\n\r\n")
                assert header.startswith(b"CONNECT 149.154.175.50:443 HTTP/1.1\r\n")
                assert (b"Proxy-Authorization: Basic" in header) == authenticated
                writer.write(b"HTTP/1.1 407 Proxy Authentication Required\r\n\r\n" if reject else b"HTTP/1.1 200 Connection established\r\n\r\n")
            else:
                assert await reader.readexactly(3) == (b"\x05\x01\x02" if authenticated else b"\x05\x01\x00")
                writer.write(b"\x05\xff" if reject else b"\x05\x02" if authenticated else b"\x05\x00")
                await writer.drain()
                if not reject:
                    if authenticated:
                        assert await reader.readexactly(1) == b"\x01"
                        size = (await reader.readexactly(1))[0]
                        assert await reader.readexactly(size) == "用户".encode()
                        size = (await reader.readexactly(1))[0]
                        assert await reader.readexactly(size) == b"private-password"
                        writer.write(b"\x01\x00")
                        await writer.drain()
                    assert await reader.readexactly(10) == b"\x05\x01\x00\x01\x95\x9a\xaf\x32\x01\xbb"
                    writer.write(b"\x05\x00\x00\x01" + b"\x00" * 6)
            await writer.drain()
            if not reject:
                await answer(reader, writer)
            else:
                writer.close()
                await writer.wait_closed()
        finally:
            finished.set()

    async with await asyncio.start_server(handler, "127.0.0.1", 0) as server:
        proxy = {"enabled": True, "scheme": scheme, "host": "127.0.0.1", "port": server.sockets[0].getsockname()[1],
            "username": "用户" if authenticated else "", "password": "private-password" if authenticated else ""}
        outcome = await wire.probe_target(proxy, wire.TARGETS[0], 0.5)
        assert outcome["state"] == ("failed" if reject else "reachable")
        assert outcome["failure_kind"] == ("proxy_auth" if reject else None)
        assert "private-password" not in json.dumps(outcome)
        await asyncio.wait_for(finished.wait(), 1)


@pytest.mark.parametrize("change", ["nonce", "key", "envelope_length", "message_id", "vector", "count", "padding", "pq"])
def test_respq_structure_rejects_malformed(change: str) -> None:
    nonce = b"n" * 16
    packet = bytearray(response(nonce))
    if change == "nonce":
        packet[24] ^= 1
    elif change == "key":
        packet[0] = 1
    elif change == "envelope_length":
        packet[16] ^= 4
    elif change == "message_id":
        packet[8] &= 0xFC
    elif change == "vector":
        packet[68] ^= 1
    elif change == "count":
        packet[72] = 65
    elif change == "padding":
        packet[65] = 1
    else:
        packet[56] = 254
    with pytest.raises(wire.ProbeError):
        wire.validate_res_pq(bytes(packet), nonce)


@pytest.mark.parametrize(("field", "value"), [("expires_at", 0), ("expires_at", float("inf")),
    ("expires_at", time.time() + 100), ("request_id", "../escape"), ("settings_revision", True),
    ("proxy_signature", "forged"), ("generation", "x" * 101), ("version", True)])
def test_private_request_validation(field: str, value: Any) -> None:
    payload = request()
    payload[field] = value
    with pytest.raises(ValueError):
        wire.validate_request(payload)


def test_bounded_private_files_and_encryption(tmp_path: Path) -> None:
    tmp_path.chmod(0o700)
    key = Fernet.generate_key()
    wire.write_private(tmp_path / "control.key", key)
    payload = request()
    wire.write_private(tmp_path / wire.REQUEST_FILE, Fernet(key).encrypt(json.dumps(payload).encode()))
    assert wire.load_request(tmp_path) == payload
    assert b"proxy" not in (tmp_path / wire.REQUEST_FILE).read_bytes()
    with pytest.raises(ValueError):
        wire.read_private(tmp_path / wire.REQUEST_FILE, 10)
    if os.name != "nt":
        (tmp_path / wire.REQUEST_FILE).chmod(0o644)
        with pytest.raises(ValueError):
            wire.load_request(tmp_path)


async def test_cancelled_transport_closes_socket() -> None:
    accepted, closed = asyncio.Event(), asyncio.Event()

    async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await reader.readexactly(42)
        accepted.set()
        assert await reader.read() == b""
        writer.close()
        await writer.wait_closed()
        closed.set()

    async with await asyncio.start_server(handler, "127.0.0.1", 0) as server:
        task = asyncio.create_task(wire.probe_target(request()["proxy"], (1, "127.0.0.1", server.sockets[0].getsockname()[1]), 5))
        await accepted.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.wait_for(closed.wait(), 1)


async def test_helper_execution_uses_exact_targets_and_sanitizes_result(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tmp_path.chmod(0o700)
    key = Fernet.generate_key()
    wire.write_private(tmp_path / "control.key", key)
    payload = request({"enabled": True, "scheme": "http", "host": "fixture.invalid", "port": 1080,
                       "username": "private-user", "password": "private-password"})
    wire.write_private(tmp_path / wire.REQUEST_FILE, Fernet(key).encrypt(json.dumps(payload).encode()))
    observed = []

    async def probe(proxy: dict[str, Any], target: tuple[int, str, int], timeout: float) -> dict[str, Any]:
        observed.append((proxy, target, timeout))
        dc, host, port = target
        return {"dc": dc, "host": host, "port": port, "state": "reachable", "latency_ms": 1, "failure_kind": None}

    monkeypatch.setattr(wire, "probe_target", probe)
    await wire.execute(tmp_path)
    assert tuple(item[1] for item in observed) == wire.TARGETS
    assert all(item[0] == payload["proxy"] and 0 < item[2] <= 5 for item in observed)
    result = json.loads(Fernet(key).decrypt(wire.read_private(tmp_path / wire.RESULT_FILE)))
    assert result["request_id"] == payload["request_id"] and len(result["targets"]) == 4
    assert all(secret not in json.dumps(result) for secret in ("private-password", "private-user", "fixture.invalid", '"proxy"'))


async def test_helper_rechecks_expiry_and_cancel_before_result_publication(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tmp_path.chmod(0o700)
    key = Fernet.generate_key()
    wire.write_private(tmp_path / "control.key", key)
    wire.write_private(tmp_path / wire.REQUEST_FILE, Fernet(key).encrypt(json.dumps(request()).encode()))

    async def probe(proxy: dict[str, Any], target: tuple[int, str, int], timeout: float) -> dict[str, Any]:
        (tmp_path / wire.REQUEST_FILE).unlink(missing_ok=True)
        return {}

    monkeypatch.setattr(wire, "probe_target", probe)
    with pytest.raises(FileNotFoundError):
        await wire.execute(tmp_path)
    assert not (tmp_path / wire.RESULT_FILE).exists()
