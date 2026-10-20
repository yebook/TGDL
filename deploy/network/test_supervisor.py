"""Isolated control-plane tests; these do not claim Linux/TUN verification."""

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
    def test_failed_candidate_restores_previous_revision(self, resolve, firewall, run):
        self.supervisor.active_config = {"old": "validated"}
        self.supervisor.active_endpoint = ("192.168.1.2", 1080)
        self.supervisor.applied_revision = 1
        self.supervisor.verify = Mock(side_effect=[False, True])
        self.supervisor.apply(subject.decode(payload(revision=2)))
        self.assertEqual(self.supervisor.applied_revision, 1)
        self.assertEqual(
            self.supervisor.start_process.call_args.args, ({"old": "validated"},)
        )
        self.assertEqual(
            firewall.call_args.args, ("172.30.88.0/24", ("192.168.1.2", 1080))
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

    def test_disabling_requires_explicit_redeployment(self):
        self.supervisor.apply(subject.decode(payload(enabled=False, host="")))
        self.supervisor.start_process.assert_not_called()
        self.assertEqual(
            self.supervisor.status.call_args.args,
            (1, "restart_required", "redeploy_direct_stack"),
        )

    def test_ack_contains_no_credentials(self):
        self.supervisor.status = subject.Supervisor.status.__get__(self.supervisor)
        with patch.object(subject.os, "fchown", create=True):
            self.supervisor.status(1, "failed", "proxy_validation_failed")
        value = json.loads((self.supervisor.shared / "applied.json").read_text())
        self.assertEqual(
            set(value), {"desired_revision", "applied_revision", "state", "message"}
        )
        self.assertNotIn("secret", json.dumps(value))


if __name__ == "__main__":
    unittest.main()
