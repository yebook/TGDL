"""Behavioral acceptance-CLI tests using an isolated HTTP service."""
import argparse
import contextlib
import importlib.util
import io
import json
import os
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("acceptance", Path(__file__).parents[1] / "acceptance.py")
acceptance = importlib.util.module_from_spec(spec)
spec.loader.exec_module(acceptance)

SECRET = "private-password-session-csrf-token"
RAW_PATH = "/private/nas/media-secret.mp4"


class AcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.requests = []
        self.overrides = {}
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                self.respond()

            def do_POST(self):
                self.respond()

            def respond(self):
                body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                owner.requests.append((self.command, self.path, dict(self.headers), body))
                default = {
                    "/health/live": {"status": "ok"},
                    "/health/ready": {"status": "ok"},
                    "/api/v1/setup/status": {"initialized": True},
                    "/api/v1/auth/login": {"username": SECRET, "csrf_token": SECRET},
                    "/api/v1/auth/logout": {"ok": True},
                    "/api/v1/tasks?page=1&page_size=1": {"items": [{"title": SECRET, "relative_path": RAW_PATH}], "total": 1, "page": 1, "page_size": 1},
                    "/api/v1/settings": {"revision": 3, "values": {"bot_token": SECRET, "download_root_id": RAW_PATH, "confirmation_timeout_seconds": 300, "download_concurrency": 3}},
                    "/api/v1/system/status": {"bot": {"state": "online", "message": SECRET}, "proxy": {"state": "disabled"}, "storage": {"roots": [{"available": True, "id": RAW_PATH}]}, "recovery": {"blocked_jobs": 0}, "queue": {"active": 0, "pending": 0, "concurrency": 3}},
                }
                status, content = owner.overrides.get(self.path, (200, default.get(self.path, {})))
                if status == "slow":
                    time.sleep(0.1)
                    status = 200
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                if status == 302:
                    self.send_header("Location", "/credential-exfiltration")
                if self.path == "/api/v1/auth/login":
                    self.send_header("Set-Cookie", f"session={SECRET}; Path=/; HttpOnly")
                self.end_headers()
                try:
                    self.wfile.write(content if isinstance(content, bytes) else json.dumps(content).encode())
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def invoke(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = acceptance.main(["--base-url", self.url, "--json", *args])
        self.assertNotIn(SECRET, out.getvalue() + err.getvalue())
        self.assertNotIn(RAW_PATH, out.getvalue() + err.getvalue())
        return code, json.loads(out.getvalue()) if out.getvalue() else None

    def test_anonymous_is_explicitly_partial_and_read_only(self):
        code, result = self.invoke()
        self.assertEqual(code, 0)
        self.assertEqual(result["checks"][-1]["status"], "skipped")
        self.assertIn("video_over_20mb", result["unverified"])
        self.assertTrue(all(method == "GET" for method, *_ in self.requests))

    def test_authenticated_checks_and_session_cleanup_no_secret_evidence(self):
        with patch.dict(os.environ, {"TGD_ACCEPTANCE_USERNAME": "admin", "TGD_ACCEPTANCE_PASSWORD": SECRET}):
            code, result = self.invoke("--login")
        self.assertEqual(code, 0)
        self.assertEqual(len(result["checks"]), 8)
        mutations = [path for method, path, *_ in self.requests if method != "GET"]
        self.assertEqual(mutations, ["/api/v1/auth/login", "/api/v1/auth/logout"])
        self.assertEqual(self.requests[-1][2]["X-Csrf-Token"], SECRET)
        self.assertIn(SECRET, self.requests[-1][2]["Cookie"])

    def test_http_failure_and_uninitialized_are_failures(self):
        self.overrides["/health/ready"] = (503, {"secret": SECRET})
        self.overrides["/api/v1/setup/status"] = (200, {"initialized": False})
        code, result = self.invoke()
        self.assertEqual(code, 1)
        self.assertEqual([c.get("code") for c in result["checks"][:3]], [None, "http_503", "not_initialized"])

    def test_redirect_never_followed_even_on_login(self):
        self.overrides["/api/v1/auth/login"] = (302, {})
        with patch.dict(os.environ, {"TGD_ACCEPTANCE_USERNAME": "admin", "TGD_ACCEPTANCE_PASSWORD": SECRET}):
            code, result = self.invoke("--login")
        self.assertEqual(code, 1)
        self.assertEqual(result["checks"][3]["code"], "redirect_refused")
        self.assertFalse(any(path == "/credential-exfiltration" for _, path, *_ in self.requests))

    def test_invalid_json_is_sanitized(self):
        self.overrides["/health/live"] = (200, SECRET.encode())
        code, result = self.invoke()
        self.assertEqual(code, 1)
        self.assertEqual(result["checks"][0]["code"], "invalid_json")

    def test_timeout_is_bounded(self):
        self.overrides["/health/live"] = ("slow", {})
        code, result = self.invoke("--timeout", "0.02")
        self.assertEqual(code, 1)
        self.assertEqual(result["checks"][0]["code"], "timeout")

    def test_oversized_response_is_bounded_and_sanitized(self):
        self.overrides["/health/live"] = (200, b"x" * (acceptance.MAX_RESPONSE + 1))
        code, result = self.invoke()
        self.assertEqual(code, 1)
        self.assertEqual(result["checks"][0]["code"], "response_too_large")

    def test_connection_failure_returns_evidence_without_exception_text(self):
        with patch.object(acceptance.Client, "request", side_effect=acceptance.CheckError("connection_failed")):
            code, result = self.invoke()
        self.assertEqual(code, 1)
        self.assertEqual(result["checks"][0]["code"], "connection_failed")

    def test_truncated_http_response_is_sanitized(self):
        from http.client import IncompleteRead
        client = acceptance.Client(self.url, 1)
        with patch.object(client.opener, "open", side_effect=IncompleteRead(SECRET.encode())), self.assertRaises(acceptance.CheckError) as error:
            client.request("/health/live")
        self.assertEqual(str(error.exception), "connection_failed")

    def test_schema_failure_still_logs_out(self):
        self.overrides["/api/v1/system/status"] = (200, {"bot": {"state": [SECRET]}, "proxy": {}, "storage": {}, "recovery": {}, "queue": {}})
        with patch.dict(os.environ, {"TGD_ACCEPTANCE_USERNAME": "admin", "TGD_ACCEPTANCE_PASSWORD": SECRET}):
            code, result = self.invoke("--login")
        self.assertEqual(code, 1)
        self.assertEqual(result["checks"][-2]["code"], "invalid_response")
        self.assertEqual(result["checks"][-1]["check"], "logout")

    def test_url_and_timeout_validation(self):
        for value in ("https://user:secret@example.com", "file:///etc/passwd", "https://example.com/x", "http://example.com?token=x", "http://example.com/#x", "http://example.com?", "http://example.com/#", "http://example.com:99999", "http://example.com\n"):
            with self.subTest(value=value), self.assertRaises(argparse.ArgumentTypeError):
                acceptance.base_url(value)
        self.assertEqual(acceptance.base_url("http://192.168.1.3:8000/"), "http://192.168.1.3:8000")
        for value in ("nan", "inf", "0", "-1", "301"):
            with self.subTest(value=value), self.assertRaises(argparse.ArgumentTypeError):
                acceptance.timeout_seconds(value)

    def test_missing_credentials_exits_without_http(self):
        with patch.dict(os.environ, {}, clear=True), patch("sys.stdin.isatty", return_value=False):
            code, result = self.invoke("--login")
        self.assertEqual(code, 2)
        self.assertIsNone(result)
        self.assertFalse(self.requests)


if __name__ == "__main__":
    unittest.main()
