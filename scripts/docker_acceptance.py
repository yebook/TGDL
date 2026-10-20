#!/usr/bin/env python3
"""Opt-in disposable production-image smoke test. Never uses deployment data."""
from __future__ import annotations

import argparse
import json
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
from http.client import HTTPException
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request

try:
    from .acceptance import MAX_RESPONSE, CheckError, Client, require
except ImportError:  # Direct script execution.
    from acceptance import MAX_RESPONSE, CheckError, Client, require

SENTINEL = b"tgd-disposable-storage-check\n"


class Smoke:
    def __init__(self, root: Path, image: str):
        self.root, self.image = root, image
        self.prefix = "tgd-smoke-" + secrets.token_hex(10)
        self.names: set[str] = set()
        self.checks: list[dict] = []
        self.client: Client
        self.csrf = ""
        self.username = "smoke-" + secrets.token_hex(4)
        self.password = secrets.token_hex(3)  # Accepted lower boundary, never logged.
        self.saved_revision = -1

    def docker(self, *args: str, timeout: int = 120) -> str:
        try:
            result = subprocess.run(["docker", *args], capture_output=True, text=True,
                                    encoding="utf-8", errors="replace", timeout=timeout, check=False)
        except FileNotFoundError:
            raise CheckError("docker_missing") from None
        except (OSError, subprocess.TimeoutExpired):
            raise CheckError("docker_command_failed") from None
        if result.returncode:
            raise CheckError("docker_command_failed")
        return result.stdout.strip()

    def passed(self, name: str) -> None:
        self.checks.append({"check": name, "status": "pass"})
        print(f"PASS: {name}", file=sys.stderr, flush=True)

    def helper(self, code: str) -> None:
        name = self.prefix + "-helper"
        self.names.add(name)
        self.docker("run", "--rm", "--name", name, "--pull=never", "--network=none",
                    "--user", "1000:1000", "--read-only", "--cap-drop=ALL",
                    "--security-opt=no-new-privileges:true", "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m",
                    "--mount", f"type=bind,src={self.root.as_posix()},dst=/work",
                    "--entrypoint", "python", self.image, "-c", code)
        self.names.discard(name)

    def start(self, stage: str) -> str:
        name = self.prefix + "-" + stage
        self.names.add(name)
        args = ["run", "-d", "--name", name, "--pull=never", "--init", "--user", "1000:1000",
                "--read-only", "--cap-drop=ALL", "--security-opt=no-new-privileges:true",
                "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m", "-p", "127.0.0.1::8000",
                "--env", "TGD_DATA_DIR=/data", "--env", "TGD_DOWNLOAD_ROOT=/downloads",
                "--env", "TGD_BOT_API_ROOT=/var/lib/telegram-bot-api",
                "--env", "TGD_BOT_API_BASE=http://127.0.0.1:8081",
                "--env", "TGD_STATIC_DIR=/app/frontend/dist", "--env", "TGD_PROXY_CAPABLE=false"]
        for source, target in (("data", "/data"), ("downloads", "/downloads"),
                               ("cache", "/var/lib/telegram-bot-api")):
            mount = f"type=bind,src={(self.root / stage / source).as_posix()},dst={target}"
            args.extend(["--mount", mount + (",readonly" if source == "cache" else "")])
        self.docker(*args, self.image)
        port = self.docker("port", name, "8000/tcp")
        require(port.startswith("127.0.0.1:") and port.count(":") == 1)
        number = int(port.rsplit(":", 1)[1])
        require(0 < number <= 65535)
        self.client = Client(f"http://127.0.0.1:{number}", 5)
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            try:
                if self.client.request("/health/ready").get("status") == "ok":
                    return name
            except CheckError:
                pass
            time.sleep(0.5)
        raise CheckError("startup_timeout")

    def stop(self, name: str) -> None:
        self.docker("stop", "--time", "90", name, timeout=110)
        self.docker("rm", name)
        self.names.discard(name)

    def api(self, path: str, payload: dict | None = None, method: str | None = None,
            expected: int = 200) -> dict:
        # Reuse the existing bounded, no-proxy/no-redirect client for GET/POST.
        if method is None and expected == 200:
            return self.client.request(path, payload, self.csrf)
        data = json.dumps(payload).encode() if payload is not None else None
        headers = {"Accept": "application/json", "Content-Type": "application/json",
                   "Origin": self.client.origin, "X-CSRF-Token": self.csrf}
        request = Request(self.client.origin + path, data=data, headers=headers, method=method)
        try:
            response = self.client.opener.open(request, timeout=5)
        except HTTPError as exc:
            response = exc
        except (URLError, OSError):
            raise CheckError("api_connection_failed") from None
        with response:
            require(response.code == expected)
            raw = response.read(MAX_RESPONSE + 1)
            require(len(raw) <= MAX_RESPONSE)
            try:
                body = json.loads(raw)
            except (ValueError, UnicodeDecodeError):
                raise CheckError("invalid_json") from None
            require(isinstance(body, dict))
            return body

    def login(self) -> None:
        self.csrf = self.api("/api/v1/auth/login", {"username": self.username,
                                                  "password": self.password})["csrf_token"]
        require(isinstance(self.csrf, str) and bool(self.csrf))

    def verify_state(self) -> None:
        require(self.api("/api/v1/setup/status").get("initialized") is True)
        self.login()
        saved = self.api("/api/v1/settings")
        require(saved.get("revision") == self.saved_revision)
        require(saved["values"]["confirmation_timeout_seconds"] == 420)
        require(saved["values"]["download_concurrency"] == 2)
        require(saved["configured"]["bot_token"] is False)
        state = self.api("/api/v1/system/status")
        require(state["bot"]["state"] == "unconfigured")
        require(all(root["available"] for root in state["storage"]["roots"]))
        require(bool(state["storage"]["roots"]))

    def execute(self) -> None:
        self.docker("version", "--format", "{{.Server.Version}}", timeout=30)
        self.docker("image", "inspect", self.image, "--format", "{{.Id}}", timeout=30)
        self.passed("docker_image_available")
        for subdir in ("data", "downloads", "cache"):
            path = self.root / "original" / subdir
            path.mkdir(parents=True)
            path.chmod(0o777)
        (self.root / "original").chmod(0o777)
        current = self.start("original")
        require(self.api("/health/live").get("status") == "ok")
        # Check actual built UI and one referenced local asset, without printing bodies.
        with self.client.opener.open(self.client.origin + "/", timeout=5) as response:
            raw = response.read(MAX_RESPONSE + 1)
            require(response.status == 200 and len(raw) <= MAX_RESPONSE)
            html = raw.decode("utf-8")
        asset = re.search(r'(?:src|href)="(/assets/[^"<>]+\.(?:js|css))"', html)
        require(asset is not None)
        with self.client.opener.open(self.client.origin + asset[1], timeout=5) as response:
            require(response.status == 200 and bool(response.read(1)))
        self.passed("production_ui_healthy")
        bootstrap = self.docker("exec", current, "python", "-c",
                                "from pathlib import Path; print(Path('/data/bootstrap.key').read_text().strip())")
        setup = {"username": self.username, "password": self.password, "bootstrap_secret": bootstrap}
        for length in (5, 21):
            self.api("/api/v1/setup", setup | {"password": secrets.token_hex(length)[:length]},
                     method="POST", expected=422)
            require(self.api("/api/v1/setup/status").get("initialized") is False)
            require((self.root / "original/data/bootstrap.key").exists())
        require(self.api("/api/v1/setup", setup).get("initialized") is True)
        self.login()
        self.passed("setup_password_boundaries")
        settings = self.api("/api/v1/settings")
        saved = self.api("/api/v1/settings", {"revision": settings["revision"],
                         "confirmation_timeout_seconds": 420, "download_concurrency": 2}, method="PATCH")
        # Save the returned revision rather than assuming initial revision numbering.
        require(saved["revision"] == settings["revision"] + 1)
        self.saved_revision = saved["revision"]
        self.passed("settings_saved_without_bot")
        for length in (5, 21):
            self.api("/api/v1/auth/password", {"current_password": self.password,
                     "new_password": secrets.token_hex(length)[:length]}, method="POST", expected=422)
            require(self.api("/api/v1/auth/me")["username"] == self.username)
            self.login()  # Old password remains valid.
        for length in (20, 6):
            new_password = secrets.token_hex(length)[:length]
            require(self.api("/api/v1/auth/password", {"current_password": self.password,
                            "new_password": new_password}).get("ok") is True)
            self.api("/api/v1/auth/me", method="GET", expected=401)
            self.api("/api/v1/auth/login", {"username": self.username, "password": self.password},
                     method="POST", expected=401)
            self.password = new_password
            self.login()
        self.passed("password_change_boundaries")
        self.helper("from pathlib import Path; Path('/work/original/downloads/smoke-sentinel').write_bytes("
                    + repr(SENTINEL) + ")")
        self.verify_state()  # Establish and check the original storage identity before restart.
        self.stop(current)
        current = self.start("original")
        self.verify_state()
        require((self.root / "original/downloads/smoke-sentinel").read_bytes() == SENTINEL)
        self.passed("container_recreation_persistence")
        self.stop(current)  # SQLite backup only after graceful stop.
        self.helper("import shutil; shutil.copytree('/work/original','/work/backup'); "
                    "shutil.copytree('/work/backup','/work/restored')")
        require((self.root / "restored/downloads/smoke-sentinel").read_bytes() == SENTINEL)
        self.helper("from pathlib import Path; "
                    "assert Path('/work/original/data/master.key').read_bytes() == "
                    "Path('/work/restored/data/master.key').read_bytes()")
        self.start("restored")
        self.verify_state()
        self.helper("from pathlib import Path; a=Path('/work/original'); b=Path('/work/restored'); "
                    "identities=list((a/'data').glob('storage-identity-*.json')); assert identities; "
                    "assert all(p.read_bytes()==(b/'data'/p.name).read_bytes() for p in identities); "
                    "assert (a/'downloads/.tgd-root').read_bytes()==(b/'downloads/.tgd-root').read_bytes()")
        self.passed("stopped_backup_restore")

    def cleanup(self) -> None:
        if self.names:
            # A failed --rm helper may already be gone. Never remove containers by broad prefix.
            existing = set(self.docker("ps", "-a", "--format", "{{.Names}}").splitlines())
            for name in tuple(self.names):
                if name in existing:
                    self.docker("rm", "-f", name, timeout=110)
                self.names.discard(name)
        # Do not delete data while any owned container could still be using it.
        if self.root.exists() and any(self.root.iterdir()):
            try:
                self.helper("import pathlib,shutil; "
                            "[shutil.rmtree(p) for p in pathlib.Path('/work').iterdir() if p.is_dir()]")
            except CheckError:
                for name in tuple(self.names):
                    self.docker("rm", "-f", name, timeout=110)
                    self.names.discard(name)
                raise CheckError("cleanup_failed") from None


def run(image: str) -> dict:
    root = Path(tempfile.mkdtemp(prefix="tgd-docker-smoke-")).resolve()
    root.chmod(0o777)
    smoke = Smoke(root, image)
    try:
        smoke.execute()
    except CheckError as exc:
        smoke.checks.append({"check": "smoke", "status": "fail", "code": str(exc)})
    except (OSError, ValueError, KeyError, TypeError, HTTPException):
        smoke.checks.append({"check": "smoke", "status": "fail", "code": "acceptance_failed"})
    except KeyboardInterrupt:
        smoke.checks.append({"check": "smoke", "status": "fail", "code": "interrupted"})
    finally:
        try:
            smoke.cleanup()
            shutil.rmtree(root)
            smoke.passed("disposable_resources_removed")
        except (CheckError, OSError):
            smoke.checks.append({"check": "cleanup", "status": "fail", "code": "cleanup_failed"})
    return {"schema_version": 1, "scope": "isolated_production_container",
            "result": "fail" if any(item["status"] == "fail" for item in smoke.checks) else "pass",
            "checks": smoke.checks,
            "unverified": ["real_telegram_media", "video_over_20mb", "fnos_filesystem",
                           "proxy_telegram_routing", "cross_device_storage_restore"]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", help="Explicitly allow disposable Docker mutations")
    parser.add_argument("--image", default="tgd-app:0.1.0", help="Already built local production image")
    args = parser.parse_args(argv)
    if not args.run:
        parser.error("--run is required; this creates and removes isolated disposable containers")
    result = run(args.image)
    print(json.dumps(result, ensure_ascii=True, indent=2))
    return 0 if result["result"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
