"""Strict file handoff to a fail-closed sing-box network namespace.

No shell, Docker socket, arbitrary URLs or user-provided command arguments.
Only applied.json crosses back into the application. Never log source payloads,
sing-box diagnostics (which may contain passwords), or exception text.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
import signal
import socket
import stat
import subprocess
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

MAX_CONFIG_BYTES = 8192
FIELDS = {"revision", "enabled", "scheme", "host", "port", "username", "password"}
SHARED = Path("/run/tgd-network")
PRIVATE = Path("/run/tgd-private")


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Desired:
    revision: int
    enabled: bool
    scheme: str
    host: str
    port: int
    username: str
    password: str


def decode(raw: bytes) -> Desired:
    if len(raw) > MAX_CONFIG_BYTES:
        raise ConfigError("invalid_config")
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        raise ConfigError("invalid_config") from None
    if not isinstance(value, dict) or set(value) != FIELDS:
        raise ConfigError("invalid_config")
    if type(value["revision"]) is not int or not 1 <= value["revision"] <= 2**53 - 1:
        raise ConfigError("invalid_config")
    if type(value["enabled"]) is not bool or value["scheme"] not in ("http", "socks5"):
        raise ConfigError("invalid_config")
    if type(value["port"]) is not int or not 1 <= value["port"] <= 65535:
        raise ConfigError("invalid_config")
    for field, limit in (("host", 253), ("username", 200), ("password", 1000)):
        if (
            not isinstance(value[field], str)
            or len(value[field]) > limit
            or any(ord(c) < 32 or ord(c) == 127 for c in value[field])
        ):
            raise ConfigError("invalid_config")
    host = value["host"]
    if not value["enabled"]:
        return Desired(**value)
    # Accept DNS labels or IPv4 literals only. No URL/path/IPv6/shell syntax.
    if not host or not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?", host):
        raise ConfigError("invalid_config")
    if any(
        not label or len(label) > 63 or label.startswith("-") or label.endswith("-")
        for label in host.split(".")
    ):
        raise ConfigError("invalid_config")
    return Desired(**value)


def read_desired(path: Path) -> bytes:
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    with os.fdopen(fd, "rb") as source:
        info, current = os.fstat(source.fileno()), path.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or (info.st_dev, info.st_ino) != (current.st_dev, current.st_ino)):
            raise ConfigError("invalid_config")
        return source.read(MAX_CONFIG_BYTES + 1)


def atomic_json(
    path: Path, value: dict[str, Any], mode: int, gid: int | None = None
) -> None:
    if not stat.S_ISDIR(path.parent.lstat().st_mode):
        raise ConfigError("invalid_config")
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    fd = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        mode,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as target:
            os.fchmod(target.fileno(), mode)
            if gid is not None:
                os.fchown(target.fileno(), -1, gid)
            json.dump(value, target, separators=(",", ":"))
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def resolve_proxy(host: str) -> str:
    # Only UID 0 may query Docker's loopback resolver. Besides this endpoint
    # lookup, live_probe's fixed TLS hostname may use that bootstrap exception.
    # The API runs unprivileged and cannot use it.
    answers = socket.getaddrinfo(host, None, socket.AF_INET, socket.SOCK_STREAM)
    for answer in answers:
        ip = ipaddress.IPv4Address(answer[4][0])
        if not (
            ip.is_loopback or ip.is_unspecified or ip.is_multicast or ip.is_link_local
        ):
            return str(ip)
    raise ConfigError("proxy_resolution_failed")


def sing_config(desired: Desired, proxy_ip: str, subnet: str) -> dict[str, Any]:
    outbound: dict[str, Any] = {
        "type": "http" if desired.scheme == "http" else "socks",
        "tag": "upstream",
        "server": proxy_ip,
        "server_port": desired.port,
    }
    if desired.scheme == "socks5":
        outbound["version"] = "5"
    if desired.username or desired.password:
        outbound.update(username=desired.username, password=desired.password)
    return {
        "log": {"disabled": True},
        "dns": {
            "servers": [
                {"tag": "remote", "address": "tcp://1.1.1.1", "detour": "upstream"}
            ],
            "final": "remote",
            "strategy": "ipv4_only",
        },
        "inbounds": [
            {
                "type": "tun",
                "tag": "tgd",
                "interface_name": "tgd-tun",
                "address": ["172.29.255.1/30"],
                "auto_route": True,
                "strict_route": True,
                "stack": "system",
                "route_exclude_address": [subnet, proxy_ip + "/32"],
            }
        ],
        "outbounds": [outbound],
        "route": {
            "auto_detect_interface": True,
            "final": "upstream",
            "rules": [{"action": "sniff"}, {"protocol": "dns", "action": "hijack-dns"}],
        },
    }


def firewall_rules(subnet: str, proxy: tuple[str, int] | None, *, direct: bool = False) -> str:
    network = str(ipaddress.IPv4Network(subnet))
    rules = [
        "*filter",
        ":INPUT ACCEPT [0:0]",
        ":FORWARD DROP [0:0]",
        ":OUTPUT DROP [0:0]",
        "-F OUTPUT",
    ]
    if direct:
        # Explicit acknowledged direct mode, never a failure fallback. IPv6 stays blocked.
        return "\n".join([*rules, "-A OUTPUT -j ACCEPT", "COMMIT", ""])
    # DROP remains the default, including on a missing TUN or dead supervisor.
    rules += [
        "-A OUTPUT -o lo -p udp --dport 53 -m owner --uid-owner 0 -j ACCEPT",
        "-A OUTPUT -o lo -p tcp --dport 53 -m owner --uid-owner 0 -j ACCEPT",
        "-A OUTPUT -p udp --dport 53 ! -o tgd-tun -j DROP",
        "-A OUTPUT -p tcp --dport 53 ! -o tgd-tun -j DROP",
        "-A OUTPUT -o lo -j ACCEPT",
        "-A OUTPUT -o tgd-tun -j ACCEPT",
        f"-A OUTPUT -d {network} -p tcp --sport 8081 -m conntrack --ctstate ESTABLISHED -j ACCEPT",
    ]
    if proxy:
        address, port = proxy
        ipaddress.IPv4Address(address)
        if type(port) is not int or not 1 <= port <= 65535:
            raise ConfigError("invalid_config")
        rules.append(f"-A OUTPUT -d {address}/32 -p tcp --dport {port} -j ACCEPT")
    return "\n".join([*rules, "COMMIT", ""])


def run_quiet(
    argv: list[str], *, input_text: str | None = None, timeout: int = 20
) -> None:
    subprocess.run(
        argv,
        input=input_text,
        text=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=timeout,
        check=True,
    )


def install_firewall(subnet: str, proxy: tuple[str, int] | None, *, direct: bool = False) -> None:
    # Rule batches commit atomically. Never temporarily reset OUTPUT to ACCEPT.
    run_quiet(
        ["iptables-restore", "--noflush"], input_text=firewall_rules(subnet, proxy, direct=direct)
    )
    run_quiet(
        ["ip6tables-restore", "--noflush"],
        input_text="*filter\n:INPUT ACCEPT [0:0]\n:FORWARD DROP [0:0]\n:OUTPUT DROP [0:0]\n-F OUTPUT\n-A OUTPUT -o lo -j ACCEPT\nCOMMIT\n",
    )


def live_probe() -> bool:
    # Fixed destination, validated TLS, no environment proxy. This is a fresh
    # external TLS connection through TUN, never a cached local getMe response.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open("https://api.telegram.org/", timeout=12) as response:
            return response.status in (200, 302, 404)
    except urllib.error.HTTPError as error:
        return error.code == 404
    except (OSError, ValueError, urllib.error.URLError):
        return False


class Supervisor:
    def __init__(self, shared: Path, private: Path, subnet: str, gid: int) -> None:
        self.shared, self.private, self.subnet, self.gid = shared, private, subnet, gid
        self.process: subprocess.Popen[bytes] | None = None
        self.applied_revision: int | None = None
        self.active_config: dict[str, Any] | None = None
        self.active_endpoint: tuple[str, int] | None = None
        self.last_status: tuple[int, str, str] = (0, "pending", "waiting_for_configuration")

    def status(self, revision: int, state: str, message: str) -> None:
        self.last_status = revision, state, message
        atomic_json(
            self.shared / "applied.json",
            {
                "desired_revision": revision,
                "applied_revision": self.applied_revision,
                "state": state,
                "message": message,
                "heartbeat_at": time.time(),
            },
            0o640,
            self.gid,
        )

    def stop_process(self) -> None:
        if self.process is not None:
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
            self.process = None

    def start_process(self, config: dict[str, Any]) -> None:
        atomic_json(self.private / "active.json", config, 0o600)
        self.process = subprocess.Popen(
            ["sing-box", "run", "-c", str(self.private / "active.json")],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def verify(self) -> bool:
        # Allow TUN creation and routes to settle, then test a real TLS exchange.
        for _ in range(3):
            if self.process is None or self.process.poll() is not None:
                return False
            if live_probe():
                return True
            time.sleep(1)
        return False

    def apply(self, desired: Desired) -> None:
        self.status(desired.revision, "pending", "applying_network")
        try:
            # Revoke direct and established egress BEFORE validation or DNS.
            install_firewall(self.subnet, None)
            self.stop_process()
            self.applied_revision = None
            self.active_config, self.active_endpoint = None, None
            if not desired.enabled:
                install_firewall(self.subnet, None, direct=True)
                self.applied_revision = desired.revision
                self.status(desired.revision, "disabled", "direct_ready")
                return
            proxy_ip = resolve_proxy(desired.host)
            endpoint = (proxy_ip, desired.port)
            config = sing_config(desired, proxy_ip, self.subnet)
            atomic_json(self.private / "candidate.json", config, 0o600)
            run_quiet(["sing-box", "check", "-c", str(self.private / "candidate.json")])
        except (ConfigError, OSError, subprocess.SubprocessError):
            self.status(desired.revision, "failed", "proxy_validation_failed")
            return
        try:
            install_firewall(self.subnet, endpoint)
            self.start_process(config)
            if not self.verify():
                raise ConfigError("proxy_live_check_failed")
        except (ConfigError, OSError, subprocess.SubprocessError):
            self.stop_process()
            install_firewall(self.subnet, None)
            self.status(desired.revision, "failed", "proxy_live_check_failed")
            return
        self.active_config, self.active_endpoint = config, endpoint
        self.applied_revision = desired.revision
        self.status(desired.revision, "applied", "proxy_live_check_passed")


def main() -> None:
    os.umask(0o027)
    gid = int(os.environ.get("TGD_GID", "1000"))
    subnet = str(
        ipaddress.IPv4Network(os.environ.get("TGD_NETWORK_SUBNET", "172.30.88.0/24"))
    )
    SHARED.mkdir(parents=True, exist_ok=True)
    PRIVATE.mkdir(parents=True, exist_ok=True)
    os.chown(SHARED, -1, gid)
    SHARED.chmod(0o770)
    PRIVATE.chmod(0o700)
    install_firewall(subnet, None)
    (PRIVATE / "firewall-ready").touch(mode=0o600)
    supervisor = Supervisor(SHARED, PRIVATE, subnet, gid)
    supervisor.status(0, "pending", "waiting_for_configuration")
    stopped = False

    def stop(_signal: int, _frame: Any) -> None:
        nonlocal stopped
        stopped = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    last_hash: bytes | None = None
    highest_revision = 0
    while not stopped:
        try:
            raw = read_desired(SHARED / "desired.json")
            content_hash = hashlib.sha256(raw).digest()
            if content_hash != last_hash:
                last_hash = content_hash
                desired = decode(raw)
                if desired.revision <= highest_revision:
                    install_firewall(subnet, None)
                    supervisor.stop_process()
                    supervisor.applied_revision = None
                    supervisor.status(
                        desired.revision, "failed", "revision_not_increasing"
                    )
                else:
                    highest_revision = desired.revision
                    supervisor.apply(desired)
            if supervisor.process is not None and supervisor.process.poll() is not None:
                supervisor.process = None
                supervisor.applied_revision = None
                install_firewall(subnet, None)
                supervisor.status(highest_revision, "failed", "proxy_process_exited")
            supervisor.status(*supervisor.last_status)
        except FileNotFoundError:
            if last_hash is not None:
                install_firewall(subnet, None)
                supervisor.stop_process()
                supervisor.applied_revision = None
                last_hash = None
                supervisor.status(highest_revision, "failed", "configuration_missing")
            else:
                supervisor.status(*supervisor.last_status)
        except (ConfigError, OSError, ValueError):
            install_firewall(subnet, None)
            supervisor.stop_process()
            supervisor.applied_revision = None
            supervisor.status(highest_revision, "failed", "invalid_config")
        time.sleep(1)
    supervisor.stop_process()
    install_firewall(subnet, None)
    # Do not remove DROP policies; API may outlive this process.


if __name__ == "__main__":
    main()
