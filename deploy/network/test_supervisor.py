"""Isolated control-plane tests; these do not claim Linux/TUN verification."""

import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import supervisor as subject


def payload(**changes):
    value = {
        "revision": 1,
        "enabled": True,
        "scheme": "socks5",
        "host": "proxy.example.org",
        "port": 1080,
        "username": "user",
        "password": "secret",
    }
    value.update(changes)
    return json.dumps(value).encode()


class ContractTests(unittest.TestCase):
    @unittest.skipIf(os.name == "nt", "POSIX link semantics")
    def test_desired_rejects_links_and_nonregular_files_without_blocking(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            source.write_bytes(payload())
            for name in ("hardlink", "symlink", "fifo"):
                target = root / name
                if name == "hardlink":
                    os.link(source, target)
                elif name == "symlink":
                    target.symlink_to(source)
                else:
                    os.mkfifo(target)
                with self.subTest(name=name), self.assertRaises((OSError, subject.ConfigError)):
                    subject.read_desired(target)

    @unittest.skipIf(os.name == "nt", "POSIX link semantics")
    def test_atomic_status_does_not_truncate_attacker_linked_temporary(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            outside = root / "outside"
            outside.write_text("preserve")
            os.link(outside, root / "applied.tmp")
            (root / "applied.json").symlink_to(outside)
            subject.atomic_json(root / "applied.json", {"state": "pending"}, 0o640)
            self.assertEqual(outside.read_text(), "preserve")
            self.assertFalse((root / "applied.json").is_symlink())
            self.assertEqual(json.loads((root / "applied.json").read_text()), {"state": "pending"})

    @unittest.skipIf(os.name == "nt", "POSIX directory permissions")
    def test_private_volume_init_only_changes_roots_and_rejects_symlink(self):
        spec = importlib.util.spec_from_file_location("volume_initializer", Path(__file__).with_name("init-volumes.py"))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            control, network = root / "control", root / "network"
            control.mkdir()
            network.mkdir()
            child = control / "existing"
            child.write_text("preserve")
            child.chmod(0o600)
            roots = {"/run/tgd-api-control": control, "/run/tgd-network": network}
            with patch.object(module, "Path", side_effect=lambda name: roots[name]):
                module.initialize(os.getuid() or 1000, os.getgid() or 1000)
            self.assertEqual(control.stat().st_mode & 0o777, 0o700)
            self.assertEqual(network.stat().st_mode & 0o777, 0o770)
            self.assertEqual(child.read_text(), "preserve")
            self.assertEqual(child.stat().st_mode & 0o777, 0o600)
            link = root / "link"
            link.symlink_to(network, target_is_directory=True)
            roots["/run/tgd-api-control"] = link
            with patch.object(module, "Path", side_effect=lambda name: roots[name]), self.assertRaises(ValueError):
                module.initialize(1000, 1000)

    def test_nonincreasing_intent_revokes_previous_direct_before_failed_ack(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            handlers = {}
            waits = []
            events = []
            def sleep(_):
                waits.append(True)
                if len(waits) == 2:
                    handlers[subject.signal.SIGTERM](None, None)
            supervisor = subject.Supervisor(root, root, "172.30.88.0/24", 1000)
            supervisor.status = lambda revision, state, message: events.append(("status", state))
            supervisor.stop_process = Mock()
            with (
                patch.object(subject, "SHARED", root), patch.object(subject, "PRIVATE", root),
                patch.object(subject.os, "chown", create=True),
                patch.object(subject.signal, "signal", side_effect=lambda sig, handler: handlers.update({sig: handler})),
                patch.object(subject, "Supervisor", return_value=supervisor),
                patch.object(subject, "read_desired", side_effect=[payload(revision=2, enabled=False), payload(revision=1)]),
                patch.object(subject, "install_firewall", side_effect=lambda *args, **kwargs: events.append(("firewall", kwargs.get("direct", False)))),
                patch.object(subject.time, "sleep", side_effect=sleep),
            ):
                subject.main()
            failed_index = events.index(("status", "failed"))
            self.assertEqual(events[failed_index - 1], ("firewall", False))
            self.assertIn(("firewall", True), events[:failed_index])
            self.assertIsNone(supervisor.applied_revision)

    def test_rejects_commands_urls_and_extra_fields(self):
        for value in (
            payload(command="echo unsafe"),
            payload(host="http://proxy:1080"),
            payload(host="a;rm"),
            payload(host="::1"),
            payload(host="bad..host"),
            payload(revision=True),
            payload(port=True),
            payload(username="bad\nvalue"),
            b"{",
            b" " * 8193,
        ):
            with self.subTest(value=value[:20]), self.assertRaises(subject.ConfigError):
                subject.decode(value)

    def test_hostname_and_host_gateway_supported(self):
        self.assertEqual(
            subject.decode(payload(host="host.docker.internal")).host,
            "host.docker.internal",
        )
        self.assertEqual(
            subject.decode(payload(host="192.168.1.2")).host, "192.168.1.2"
        )

    def test_credentials_match_application_length_contract(self):
        value = subject.decode(payload(username="u" * 200, password="p" * 1000))
        self.assertEqual(len(value.password), 1000)
        for invalid in (payload(username="u" * 201), payload(password="p" * 1001)):
            with self.assertRaises(subject.ConfigError):
                subject.decode(invalid)

    def test_resolver_rejects_localhost_and_picks_ipv4(self):
        with (
            patch.object(
                subject.socket,
                "getaddrinfo",
                return_value=[(2, 1, 6, "", ("127.0.0.1", 0))],
            ),
            self.assertRaises(subject.ConfigError),
        ):
            subject.resolve_proxy("localhost")
        with patch.object(
            subject.socket,
            "getaddrinfo",
            return_value=[(2, 1, 6, "", ("192.168.1.2", 0))],
        ):
            self.assertEqual(subject.resolve_proxy("proxy.local"), "192.168.1.2")

    def test_rules_deny_direct_traffic_even_with_existing_connections(self):
        rules = subject.firewall_rules("172.30.88.0/24", ("192.168.1.2", 7890))
        self.assertIn(":OUTPUT DROP", rules)
        self.assertIn("-d 192.168.1.2/32 -p tcp --dport 7890 -j ACCEPT", rules)
        self.assertNotIn("--ctstate ESTABLISHED -j ACCEPT\n-A OUTPUT -j ACCEPT", rules)
        allows = [
            line for line in rules.splitlines() if "--ctstate ESTABLISHED" in line
        ]
        self.assertEqual(len(allows), 1)
        self.assertIn("--sport 8081", allows[0])
        self.assertIn("-d 172.30.88.0/24", allows[0])
        self.assertIn("--uid-owner 0", rules)
        self.assertIn("! -o tgd-tun -j DROP", rules)
        self.assertNotIn("192.168.1.2", subject.firewall_rules("172.30.88.0/24", None))

    def test_both_protocols_proxy_dns_and_have_no_direct_outbound(self):
        for scheme, outbound_type in (("http", "http"), ("socks5", "socks")):
            config = subject.sing_config(
                subject.decode(payload(scheme=scheme)), "192.168.1.2", "172.30.88.0/24"
            )
            self.assertEqual(
                [entry["type"] for entry in config["outbounds"]], [outbound_type]
            )
            self.assertEqual(config["dns"]["servers"][0]["detour"], "upstream")
            self.assertEqual(config["route"]["final"], "upstream")
            self.assertTrue(config["log"]["disabled"])


class ApplyTests(unittest.TestCase):
    def setUp(self):
        # Only Linux can exercise actual fchmod; Windows runs control flow tests.
        if os.name == "nt":
            permissions = patch.object(subject.os, "fchmod", create=True)
            permissions.start()
            self.addCleanup(permissions.stop)
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.supervisor = subject.Supervisor(root, root, "172.30.88.0/24", 1000)
        self.supervisor.status = Mock()
        self.supervisor.start_process = Mock()
        self.supervisor.stop_process = Mock()

    @patch.object(subject, "run_quiet")
    @patch.object(subject, "install_firewall")
    @patch.object(subject, "resolve_proxy", return_value="192.168.1.2")
    def test_unverified_candidate_never_acknowledged(self, resolve, firewall, run):
        self.supervisor.verify = Mock(return_value=False)
        self.supervisor.apply(subject.decode(payload()))
        self.assertIsNone(self.supervisor.applied_revision)
        self.assertEqual(
            self.supervisor.status.call_args.args,
            (1, "failed", "proxy_live_check_failed"),
        )
        self.assertEqual(firewall.call_args.args, ("172.30.88.0/24", None))
        self.assertFalse(
            any(
                call.args[1] == "applied"
                for call in self.supervisor.status.call_args_list
            )
        )

    @patch.object(subject, "run_quiet")
    @patch.object(subject, "install_firewall")
    @patch.object(subject, "resolve_proxy", return_value="192.168.1.3")
    def test_failed_candidate_stays_blocked_without_previous_config_fallback(self, resolve, firewall, run):
        self.supervisor.active_config = {"old": "validated"}
        self.supervisor.active_endpoint = ("192.168.1.2", 1080)
        self.supervisor.applied_revision = 1
        self.supervisor.verify = Mock(return_value=False)
        self.supervisor.apply(subject.decode(payload(revision=2)))
        self.assertIsNone(self.supervisor.applied_revision)
        self.assertEqual(
            self.supervisor.start_process.call_count, 1
        )
        self.assertEqual(
            firewall.call_args.args, ("172.30.88.0/24", None)
        )
        self.assertEqual(
            self.supervisor.status.call_args.args,
            (2, "failed", "proxy_live_check_failed"),
        )

    @patch.object(subject, "run_quiet")
    @patch.object(subject, "install_firewall")
    @patch.object(subject, "resolve_proxy", return_value="192.168.1.2")
    def test_revision_only_advances_after_live_verification(
        self, resolve, firewall, run
    ):
        self.supervisor.verify = Mock(return_value=True)
        self.supervisor.apply(subject.decode(payload(revision=3)))
        self.assertEqual(self.supervisor.applied_revision, 3)
        self.assertEqual(
            self.supervisor.status.call_args.args,
            (3, "applied", "proxy_live_check_passed"),
        )

    @patch.object(subject, "install_firewall")
    def test_disabling_acknowledges_direct_without_redeployment(self, firewall):
        self.supervisor.apply(subject.decode(payload(enabled=False, host="")))
        self.supervisor.start_process.assert_not_called()
        self.assertEqual(
            self.supervisor.status.call_args.args,
            (1, "disabled", "direct_ready"),
        )
        self.assertEqual(self.supervisor.applied_revision, 1)
        self.assertTrue(firewall.call_args.kwargs["direct"])

    @patch.object(subject, "install_firewall")
    @patch.object(subject, "resolve_proxy", side_effect=subject.ConfigError("fail"))
    def test_enabling_blocks_previous_direct_before_resolution(self, resolve, firewall):
        self.supervisor.apply(subject.decode(payload(enabled=False)))
        events = []
        firewall.side_effect = lambda *args, **kwargs: events.append("firewall")
        resolve.side_effect = lambda *args: events.append("resolve") or (_ for _ in ()).throw(subject.ConfigError("fail"))
        self.supervisor.apply(subject.decode(payload(revision=2)))
        self.assertEqual(events, ["firewall", "resolve"])
        self.assertIsNone(self.supervisor.applied_revision)
        self.assertNotIn("direct", firewall.call_args.kwargs)
        self.assertEqual(self.supervisor.status.call_args.args[1], "failed")

    def test_ack_contains_no_credentials(self):
        self.supervisor.status = subject.Supervisor.status.__get__(self.supervisor)
        with patch.object(subject.os, "fchown", create=True):
            self.supervisor.status(1, "failed", "proxy_validation_failed")
        value = json.loads((self.supervisor.shared / "applied.json").read_text())
        self.assertEqual(
            set(value), {"desired_revision", "applied_revision", "state", "message", "heartbeat_at"}
        )
        self.assertNotIn("secret", json.dumps(value))


if __name__ == "__main__":
    unittest.main()
