#!/usr/bin/env python3
"""Read-only deployed-service checks; stdlib only, with optional temporary login."""
from __future__ import annotations

import argparse
import getpass
import http.cookiejar
import json
import math
import os
import sys
from datetime import datetime, timezone
from http.client import HTTPException
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import (
    HTTPCookieProcessor,
    HTTPRedirectHandler,
    ProxyHandler,
    Request,
    build_opener,
)

MAX_RESPONSE = 2 * 1024 * 1024


class CheckError(Exception):
    """Contains a fixed safe code, never an upstream message."""


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def base_url(value: str) -> str:
    """Only a literal HTTP(S) origin is accepted; reverse proxies mount at root."""
    try:
        parts = urlsplit(value)
        if (parts.scheme not in {"http", "https"} or not parts.hostname
                or parts.username is not None or parts.password is not None
                or parts.path not in {"", "/"} or "?" in value or "#" in value
                or "\\" in value or any(ord(c) <= 32 or ord(c) == 127 for c in value)):
            raise ValueError
        if parts.port is not None and not 1 <= parts.port <= 65535:
            raise ValueError
        return f"{parts.scheme}://{parts.netloc}"
    except ValueError:
        raise argparse.ArgumentTypeError("Use an HTTP(S) origin without credentials, path, query or fragment") from None


def timeout_seconds(value: str) -> float:
    try:
        seconds = float(value)
        if not math.isfinite(seconds) or not 0 < seconds <= 300:
            raise ValueError
        return seconds
    except ValueError:
        raise argparse.ArgumentTypeError("Timeout must be between 0 and 300 seconds") from None


class Client:
    def __init__(self, origin: str, timeout: float):
        self.origin, self.timeout = origin, timeout
        # Respect the exact chosen origin; no ambient HTTP proxy or redirect can receive credentials.
        self.opener = build_opener(ProxyHandler({}), HTTPCookieProcessor(http.cookiejar.CookieJar()), NoRedirect())

    def request(self, path: str, payload: dict | None = None, csrf: str | None = None) -> dict:
        headers = {"Accept": "application/json"}
        data = None
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers.update({"Content-Type": "application/json", "Origin": self.origin})
        if csrf:
            headers["X-CSRF-Token"] = csrf
        request = Request(self.origin + path, data=data, headers=headers)
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                if response.status != 200:
                    raise CheckError("unexpected_http_status")
                raw = response.read(MAX_RESPONSE + 1)
                if len(raw) > MAX_RESPONSE:
                    raise CheckError("response_too_large")
                if response.headers.get_content_type() != "application/json":
                    raise CheckError("invalid_content_type")
                result = json.loads(raw)
                if not isinstance(result, dict):
                    raise CheckError("invalid_response")
                return result
        except HTTPError as exc:
            code = "redirect_refused" if 300 <= exc.code < 400 else f"http_{exc.code}"
            exc.close()
            raise CheckError(code) from None
        except TimeoutError:
            raise CheckError("timeout") from None
        except URLError as exc:
            raise CheckError("timeout" if isinstance(exc.reason, TimeoutError) else "connection_failed") from None
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise CheckError("invalid_json") from None
        except (OSError, ValueError, HTTPException):
            raise CheckError("connection_failed") from None


def require(condition: bool) -> None:
    if not condition:
        raise CheckError("invalid_response")


def integer(value: object) -> bool:
    return type(value) is int and value >= 0


def run(origin: str, timeout: float, username: str | None = None, password: str | None = None) -> dict:
    """Output is a fixed allowlist; never copy raw server strings into evidence."""
    client = Client(origin, timeout)
    checks: list[dict] = []

    def check(name, action):
        try:
            evidence = action()
            checks.append({"check": name, "status": "pass", "evidence": evidence})
            return evidence
        except CheckError as exc:
            checks.append({"check": name, "status": "fail", "code": str(exc)})
            return None

    def health(path):
        result = client.request(path)
        require(result.get("status") == "ok")
        return {"status_ok": True}

    check("live", lambda: health("/health/live"))
    check("ready", lambda: health("/health/ready"))

    def setup():
        result = client.request("/api/v1/setup/status")
        require(type(result.get("initialized")) is bool)
        if not result["initialized"]:
            raise CheckError("not_initialized")
        return {"initialized": True}

    initialized = check("setup", setup)
    csrf = None
    if username and password and initialized:
        def login():
            nonlocal csrf
            result = client.request("/api/v1/auth/login", {"username": username, "password": password})
            token = result.get("csrf_token")
            require(isinstance(token, str) and bool(token) and len(token) <= 1024
                    and not any(ord(c) < 32 or ord(c) == 127 for c in token))
            csrf = token
            return {"authenticated": True}

        authenticated = check("login", login)
        if authenticated:
            def tasks():
                result = client.request("/api/v1/tasks?page=1&page_size=1")
                require(isinstance(result.get("items"), list) and integer(result.get("total"))
                        and result.get("page") == 1 and result.get("page_size") == 1)
                return {"total": result["total"]}

            def settings():
                result = client.request("/api/v1/settings")
                values = result.get("values")
                require(integer(result.get("revision")) and isinstance(values, dict))
                require(integer(values.get("confirmation_timeout_seconds"))
                        and integer(values.get("download_concurrency")))
                return {"revision": result["revision"],
                        "confirmation_timeout_seconds": values["confirmation_timeout_seconds"],
                        "download_concurrency": values["download_concurrency"]}

            def system():
                result = client.request("/api/v1/system/status")
                bot, proxy = result.get("bot"), result.get("proxy")
                storage, recovery, queue = result.get("storage"), result.get("recovery"), result.get("queue")
                require(all(isinstance(v, dict) for v in (bot, proxy, storage, recovery, queue)))
                require(isinstance(bot.get("state"), str)
                        and bot["state"] in {"unconfigured", "connecting", "online", "error"})
                require(isinstance(proxy.get("state"), str)
                        and proxy["state"] in {"disabled", "pending", "applying", "active", "failed", "restart_required"})
                roots = storage.get("roots")
                require(isinstance(roots, list) and all(isinstance(r, dict) and type(r.get("available")) is bool for r in roots))
                require(integer(recovery.get("blocked_jobs")) and integer(queue.get("active"))
                        and integer(queue.get("pending")) and integer(queue.get("concurrency")))
                return {"bot_state": bot["state"], "proxy_state": proxy["state"],
                        "root_count": len(roots), "available_roots": sum(r["available"] for r in roots),
                        "blocked_jobs": recovery["blocked_jobs"], "active": queue["active"],
                        "pending": queue["pending"], "concurrency": queue["concurrency"]}

            try:
                check("tasks", tasks)
                check("settings", settings)
                check("system", system)
            finally:
                def logout():
                    require(client.request("/api/v1/auth/logout", {}, csrf).get("ok") is True)
                    return {"session_removed": True}
                check("logout", logout)
        else:
            checks.extend({"check": name, "status": "skipped", "code": "login_failed"}
                          for name in ("tasks", "settings", "system"))
    else:
        checks.append({"check": "authenticated_api", "status": "skipped",
                       "code": "no_credentials" if not username else "setup_failed"})
    return {"schema_version": 1, "checked_at": datetime.now(timezone.utc).isoformat(),  # noqa: UP017 -- Python 3.10
            "result": "fail" if any(c["status"] == "fail" for c in checks) else "pass",
            "scope": "service_api_only", "checks": checks,
            "unverified": ["real_telegram_media", "video_over_20mb", "nas_filesystem",
                           "proxy_telegram_routing", "container_rebuild_persistence"]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check a deployed TGD without changing media or settings")
    parser.add_argument("--base-url", type=base_url, default="http://127.0.0.1:8000")
    parser.add_argument("--timeout", type=timeout_seconds, default=10.0, help="Per-request seconds (default: 10)")
    parser.add_argument("--login", action="store_true", help="Read TGD_ACCEPTANCE_USERNAME/PASSWORD or prompt securely")
    parser.add_argument("--json", action="store_true", help="Emit sanitized JSON evidence")
    args = parser.parse_args(argv)
    username = password = None
    if args.login:
        username = os.environ.get("TGD_ACCEPTANCE_USERNAME")
        password = os.environ.get("TGD_ACCEPTANCE_PASSWORD")
        try:
            if not username and sys.stdin.isatty():
                username = input("Administrator username: ").strip()
            if not password and sys.stdin.isatty():
                password = getpass.getpass("Administrator password: ")
        except (EOFError, KeyboardInterrupt):
            return 2
        if not username or not password:
            print("credentials_missing: use environment variables or an interactive terminal", file=sys.stderr)
            return 2
    result = run(args.base_url, args.timeout, username, password)
    if args.json:
        print(json.dumps(result, ensure_ascii=True, indent=2))
    else:
        for item in result["checks"]:
            print(f"{item['status'].upper()}: {item['check']}" + (f" ({item['code']})" if "code" in item else ""))
        print("Scope: service API only; real Telegram, large media, NAS and proxy routing remain unverified.")
    return 0 if result["result"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
