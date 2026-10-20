"""Safety/failure contracts; actual image behavior is checked by the opt-in runner."""
import contextlib
import io
import json
import subprocess
import tempfile
import unittest
from http.client import IncompleteRead
from pathlib import Path
from unittest.mock import patch

from scripts import docker_acceptance as smoke


class DockerAcceptanceTests(unittest.TestCase):
    def test_requires_explicit_opt_in(self):
        with patch.object(smoke, "run") as run, contextlib.redirect_stderr(io.StringIO()), \
                self.assertRaises(SystemExit) as raised:
            smoke.main([])
        self.assertEqual(raised.exception.code, 2)
        run.assert_not_called()

    def test_missing_docker_fails_without_claiming_pass(self):
        with patch.object(smoke.subprocess, "run", side_effect=FileNotFoundError), \
                contextlib.redirect_stderr(io.StringIO()):
            result = smoke.run("image")
        self.assertEqual(result["result"], "fail")
        self.assertEqual(result["checks"][0]["code"], "docker_missing")
        self.assertIn("real_telegram_media", result["unverified"])

    def test_subprocess_failure_never_exposes_output(self):
        sensitive = "secret-cookie-password-bootstrap"
        completed = subprocess.CompletedProcess([], 1, sensitive, sensitive)
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(smoke.subprocess, "run", return_value=completed) as command:
            instance = smoke.Smoke(Path(directory), "image")
            with self.assertRaises(smoke.CheckError) as error:
                instance.docker("version")
        self.assertNotIn(sensitive, str(error.exception))
        self.assertEqual(command.call_args.args[0], ["docker", "version"])
        self.assertNotIn("shell", command.call_args.kwargs)

    def test_cleanup_only_removes_owned_existing_names(self):
        with tempfile.TemporaryDirectory() as directory:
            instance = smoke.Smoke(Path(directory), "image")
            owned = instance.prefix + "-original"
            instance.names.update((owned, instance.prefix + "-gone"))
            calls = []

            def docker(*args, **kwargs):
                calls.append(args)
                return f"user-app\n{owned}" if args[0] == "ps" else ""

            with patch.object(instance, "docker", side_effect=docker):
                instance.cleanup()
            self.assertEqual(calls[1:], [("rm", "-f", owned)])
            self.assertFalse(instance.names)

    def test_stop_failure_does_not_delete_mounted_data(self):
        with tempfile.TemporaryDirectory() as directory:
            instance = smoke.Smoke(Path(directory), "image")
            instance.names.add("owned")
            sentinel = Path(directory) / "keep"
            sentinel.write_bytes(b"keep")
            with patch.object(instance, "docker", side_effect=["owned", smoke.CheckError("failure")]), \
                    patch.object(instance, "helper") as helper:
                with self.assertRaises(smoke.CheckError):
                    instance.cleanup()
                helper.assert_not_called()
                self.assertEqual(sentinel.read_bytes(), b"keep")

    def test_unexpected_errors_are_redacted_in_json(self):
        with patch.object(smoke.Smoke, "execute", side_effect=ValueError("private path and password")), \
                contextlib.redirect_stderr(io.StringIO()):
            result = smoke.run("image")
        self.assertEqual(result["result"], "fail")
        self.assertNotIn("private path", json.dumps(result))

    def test_truncated_http_response_is_redacted(self):
        with patch.object(smoke.Smoke, "execute", side_effect=IncompleteRead(b"secret", 50)), \
                contextlib.redirect_stderr(io.StringIO()):
            result = smoke.run("image")
        self.assertEqual(result["result"], "fail")
        self.assertNotIn("secret", json.dumps(result))


if __name__ == "__main__":
    unittest.main()
