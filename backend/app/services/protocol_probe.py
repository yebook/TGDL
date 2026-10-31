"""Credential-free MTProto probe and private wire format, shared by both images.

This module has no application dependencies. The API image copies this exact
source alongside the supervisor; target lists and validation cannot drift.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import ipaddress
import json
import math
import os
import re
import socket
import stat
import struct
import time
import uuid
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet

TARGETS = ((1, "149.154.175.50", 443), (1, "149.154.175.50", 5222),
           (2, "149.154.167.51", 443), (4, "149.154.167.91", 443))
REQUEST_FILE = "diagnostic-request.enc"
RESULT_FILE = "diagnostic-result.enc"
MAX_BYTES = 32768
PROBE_TIMEOUT = 5.0
REQUEST_LIFETIME = 7.5
REQUEST_FIELDS = {"version", "request_id", "expires_at", "settings_revision", "proxy_signature", "proxy",
                  "generation", "command_id", "credential_revision"}


def read_private(path: Path, limit: int = MAX_BYTES) -> bytes:
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    with os.fdopen(fd, "rb") as source:
        info, named = os.fstat(source.fileno()), path.lstat()
        if (not stat.S_ISREG(info.st_mode) or not stat.S_ISREG(named.st_mode) or info.st_nlink != 1
                or (info.st_dev, info.st_ino) != (named.st_dev, named.st_ino)
                or os.name != "nt" and (info.st_uid != getattr(os, "getuid", lambda: -1)() or info.st_mode & 0o077)):
            raise ValueError("invalid_private_file")
        raw = source.read(limit + 1)
        if len(raw) > limit:
            raise ValueError("oversized_private_file")
        return raw


def write_private(path: Path, raw: bytes) -> None:
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        with os.fdopen(fd, "wb") as target:
            target.write(raw)
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def cipher_for(directory: Path) -> Fernet:
    info = directory.lstat()
    if (not stat.S_ISDIR(info.st_mode)
            or os.name != "nt" and (info.st_uid != getattr(os, "getuid", lambda: -1)() or info.st_mode & 0o077)):
        raise ValueError("invalid_private_directory")
    return Fernet(read_private(directory / "control.key", 128))


def proxy_signature(proxy: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(proxy if proxy["enabled"] else {"enabled": False}, sort_keys=True).encode()).hexdigest()


def validate_request(value: Any, now: float | None = None) -> dict[str, Any]:
    now = time.time() if now is None else now
    if not isinstance(value, dict) or set(value) != REQUEST_FIELDS or type(value["version"]) is not int or value["version"] != 1:
        raise ValueError("invalid_request")
    if not isinstance(value["request_id"], str) or not re.fullmatch(r"[a-f0-9]{32}", value["request_id"]):
        raise ValueError("invalid_request")
    expiry = value["expires_at"]
    if (type(expiry) not in (int, float) or not math.isfinite(expiry) or not 0 < expiry - now <= REQUEST_LIFETIME + 1):
        raise ValueError("expired_request")
    if type(value["settings_revision"]) is not int or not 0 <= value["settings_revision"] < 2**53:
        raise ValueError("invalid_revision")
    for field in ("command_id", "generation"):
        if value[field] is not None and (not isinstance(value[field], str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", value[field])):
            raise ValueError("invalid_observation")
    revision = value["credential_revision"]
    if revision is not None and (type(revision) is not int or not 0 <= revision < 2**53):
        raise ValueError("invalid_observation")
    proxy = value["proxy"]
    if (not isinstance(proxy, dict) or set(proxy) != {"enabled", "scheme", "host", "port", "username", "password"}
            or type(proxy["enabled"]) is not bool or proxy["scheme"] not in {"http", "socks5"}
            or type(proxy["port"]) is not int or not 1 <= proxy["port"] <= 65535):
        raise ValueError("invalid_proxy")
    for field, limit in (("host", 253), ("username", 200), ("password", 1000)):
        text = proxy[field]
        if not isinstance(text, str) or len(text) > limit or any(ord(c) < 32 or ord(c) == 127 for c in text):
            raise ValueError("invalid_proxy")
    if proxy["enabled"] and not re.fullmatch(r"[A-Za-z0-9.-]{1,253}", proxy["host"]):
        raise ValueError("invalid_proxy")
    if proxy["enabled"] and proxy["scheme"] == "socks5" and any(len(proxy[f].encode()) > 255 for f in ("username", "password")):
        raise ValueError("invalid_proxy")
    if value["proxy_signature"] != proxy_signature(proxy):
        raise ValueError("invalid_signature")
    return value


def load_request(directory: Path) -> dict[str, Any]:
    return validate_request(json.loads(cipher_for(directory).decrypt(read_private(directory / REQUEST_FILE), ttl=10)))


class ProbeError(ValueError):
    """Only fixed safe error codes cross the diagnostic wire."""


async def proxy_tunnel(reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
                       proxy: dict[str, Any], host: str, port: int) -> None:
    if proxy["scheme"] == "http":
        auth = b""
        if proxy["username"] or proxy["password"]:
            token = base64.b64encode(f'{proxy["username"]}:{proxy["password"]}'.encode())
            auth = b"Proxy-Authorization: Basic " + token + b"\r\n"
        writer.write(f"CONNECT {host}:{port} HTTP/1.1\r\nHost: {host}:{port}\r\n".encode() + auth + b"\r\n")
        await writer.drain()
        header = await reader.readuntil(b"\r\n\r\n")
        if len(header) > 8192:
            raise ProbeError("proxy_connection")
        line = header.split(b"\r\n", 1)[0].split(b" ")
        if len(line) < 2 or line[0] not in (b"HTTP/1.0", b"HTTP/1.1"):
            raise ProbeError("proxy_connection")
        if line[1] == b"407":
            raise ProbeError("proxy_auth")
        if line[1] != b"200":
            raise ProbeError("proxy_connection")
        return
    authenticated = bool(proxy["username"] or proxy["password"])
    writer.write(b"\x05\x01" + (b"\x02" if authenticated else b"\x00"))
    await writer.drain()
    if await reader.readexactly(2) != (b"\x05\x02" if authenticated else b"\x05\x00"):
        raise ProbeError("proxy_auth")
    if authenticated:
        username, password = proxy["username"].encode(), proxy["password"].encode()
        writer.write(b"\x01" + bytes([len(username)]) + username + bytes([len(password)]) + password)
        await writer.drain()
        if await reader.readexactly(2) != b"\x01\x00":
            raise ProbeError("proxy_auth")
    writer.write(b"\x05\x01\x00\x01" + ipaddress.IPv4Address(host).packed + struct.pack("!H", port))
    await writer.drain()
    response = await reader.readexactly(4)
    if response[:3] != b"\x05\x00\x00":
        raise ProbeError("proxy_connection")
    if response[3] == 1:
        await reader.readexactly(6)
    elif response[3] == 4:
        await reader.readexactly(18)
    elif response[3] == 3:
        length = (await reader.readexactly(1))[0]
        if not length:
            raise ProbeError("proxy_connection")
        await reader.readexactly(length + 2)
    else:
        raise ProbeError("proxy_connection")


def validate_res_pq(message: bytes, nonce: bytes) -> None:
    if len(message) < 20:
        raise ProbeError("protocol")
    auth_key, message_id, length = struct.unpack_from("<QQI", message)
    body = message[20:]
    if auth_key != 0 or message_id % 4 not in (1, 3) or length != len(body) or len(body) < 56:
        raise ProbeError("protocol")
    if body[:4] != struct.pack("<I", 0x05162463) or body[4:20] != nonce:
        raise ProbeError("protocol")
    # resPQ: constructor, nonce, server_nonce, TL bytes pq, vector<long>.
    size = body[36]
    if not 1 <= size <= 8:
        raise ProbeError("protocol")
    offset = 36 + ((1 + size + 3) // 4) * 4
    if len(body) < offset + 8 or any(body[37 + size:offset]):
        raise ProbeError("protocol")
    vector, count = struct.unpack_from("<II", body, offset)
    if vector != 0x1CB5C415 or not 1 <= count <= 64 or len(body) != offset + 8 + count * 8:
        raise ProbeError("protocol")


async def handshake(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    nonce = os.urandom(16)
    body = struct.pack("<I", 0xBE7E8EF1) + nonce
    message = struct.pack("<QQI", 0, int(time.time()) << 32, len(body)) + body
    writer.write(b"\xef" + bytes([len(message) // 4]) + message)
    await writer.drain()
    first = (await reader.readexactly(1))[0]
    if first == 0x7F:
        words = int.from_bytes(await reader.readexactly(3), "little")
        if words < 127:
            raise ProbeError("protocol")
    elif 1 <= first < 0x7F:
        words = first
    else:
        raise ProbeError("protocol")
    if not 5 <= words <= 1024:
        raise ProbeError("protocol")
    validate_res_pq(await reader.readexactly(words * 4), nonce)


async def probe_target(proxy: dict[str, Any], target: tuple[int, str, int], timeout: float) -> dict[str, Any]:
    dc, host, port = target
    started = time.monotonic()
    writer: asyncio.StreamWriter | None = None
    phase = "proxy_connection" if proxy["enabled"] else "tcp"
    kind: str | None = None
    try:
        async with asyncio.timeout(timeout):
            reader, writer = await asyncio.open_connection(proxy["host"] if proxy["enabled"] else host,
                proxy["port"] if proxy["enabled"] else port, limit=8192)
            if proxy["enabled"]:
                await proxy_tunnel(reader, writer, proxy, host, port)
            phase = "protocol"
            await handshake(reader, writer)
    except TimeoutError:
        kind = "timeout"
    except ProbeError as error:
        kind = str(error)
    except socket.gaierror:
        kind = "dns"
    except (OSError, asyncio.IncompleteReadError, asyncio.LimitOverrunError):
        kind = phase
    except Exception:
        kind = "network_unknown"
    finally:
        if writer is not None:
            writer.close()
            writer.transport.abort()
    return {"dc": dc, "host": host, "port": port, "state": "reachable" if kind is None else "timeout" if kind == "timeout" else "failed",
            "latency_ms": int((time.monotonic() - started) * 1000), "failure_kind": kind}


async def execute(directory: Path) -> None:
    request = load_request(directory)
    timeout = min(PROBE_TIMEOUT, request["expires_at"] - time.time() - 0.1)
    if timeout <= 0:
        return
    targets = await asyncio.gather(*(probe_target(request["proxy"], target, timeout) for target in TARGETS))
    # Cancellation/replacement of the request invalidates every output.
    if load_request(directory) != request:
        return
    result = {key: value for key, value in request.items() if key != "proxy"}
    result["targets"] = targets
    write_private(directory / RESULT_FILE, cipher_for(directory).encrypt(json.dumps(result).encode()))


def main() -> None:
    os.umask(0o077)
    try:
        asyncio.run(execute(Path(os.environ.get("TGD_LOCAL_API_CONTROL_DIR", "/run/tgd-api-control"))))
    except Exception:
        # The parent publishes unavailable; no arbitrary configuration/error text.
        pass


if __name__ == "__main__":
    main()
