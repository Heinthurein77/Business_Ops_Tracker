"""Unit tests for src/throttle.py, src/client_ip.py and the password policy.
Run with:

    python -m unittest tests.test_throttle -v

Uses a private in-memory SQLite database; the project's own database is never
opened.
"""
import os

os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("DATABASE_URL", "sqlite://")

import unittest
from datetime import datetime, timedelta
from unittest import mock

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from starlette.requests import Request

from src import throttle
from src.client_ip import get_client_ip
from src.config import settings
from src.database import Base
from src.models import AuditLog
from src.schemas import password_policy_error
from src.utils import log_audit

# Real public addresses on purpose: the documentation ranges (203.0.113.0/24 etc.)
# count as private to the ipaddress module and would be skipped by the IP rules.
PUBLIC_A = "8.8.8.8"
PUBLIC_B = "1.1.1.1"
PRIVATE = "10.1.2.3"


def make_request(peer="192.0.2.1", headers=None):
    raw = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    return Request({"type": "http", "headers": raw, "client": (peer, 5555) if peer else None})


class DatabaseTestCase(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine, autoflush=False)()
        self.now = datetime.utcnow()
        throttle._warned_private_ip = False

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def add_rows(self, action, count, username=None, ip=None, age_seconds=10, distinct_usernames=False):
        for i in range(count):
            self.db.add(
                AuditLog(
                    action=action,
                    username=f"{username or 'user'}{i}" if distinct_usernames else username,
                    ip_address=ip,
                    timestamp=self.now - timedelta(seconds=age_seconds),
                )
            )
        self.db.commit()


class LoginThrottleTests(DatabaseTestCase):
    def check(self, username, ip):
        return throttle.check_login_throttle(self.db, username, ip, self.now)

    def test_public_ip_with_20_failures_is_blocked(self):
        self.add_rows("LOGIN_FAILED", 20, username="someone", ip=PUBLIC_A, distinct_usernames=True)
        blocked = self.check("brand_new_name", PUBLIC_A)
        self.assertIsNotNone(blocked)
        self.assertTrue(1 <= blocked.retry_after <= 900)

    def test_public_ip_with_19_failures_is_not_blocked(self):
        self.add_rows("LOGIN_FAILED", 19, username="someone", ip=PUBLIC_A, distinct_usernames=True)
        self.assertIsNone(self.check("brand_new_name", PUBLIC_A))

    def test_private_ip_with_25_failures_is_not_blocked_by_the_ip_rule(self):
        self.add_rows("LOGIN_FAILED", 25, username="someone", ip=PRIVATE, distinct_usernames=True)
        self.assertIsNone(self.check("brand_new_name", PRIVATE))

    def test_private_ip_warns_once_per_process(self):
        with self.assertLogs("business_ops_tracker.throttle", level="WARNING") as captured:
            self.check("a_name", PRIVATE)
            self.check("a_name", "127.0.0.1")
        self.assertEqual(len(captured.records), 1)
        self.assertIn("CLIENT_IP_HEADER", captured.records[0].getMessage())

    def test_private_ip_still_gets_the_username_rule(self):
        self.add_rows("LOGIN_FAILED", 5, username="ceo_bu5", ip=PUBLIC_B)
        self.assertIsNotNone(self.check("ceo_bu5", PRIVATE))

    def test_private_ip_never_earns_the_trusted_ip_bypass(self):
        # Every user behind the proxy shares this address; a past success must not
        # exempt an attacker who arrives through the same proxy.
        self.add_rows("LOGIN_FAILED", 5, username="ceo_bu5", ip=PRIVATE)
        self.add_rows("LOGIN_SUCCESS", 1, username="ceo_bu5", ip=PRIVATE, age_seconds=3600)
        self.assertIsNotNone(self.check("ceo_bu5", PRIVATE))

    def test_username_blocked_after_5_failures_but_other_usernames_are_not(self):
        self.add_rows("LOGIN_FAILED", 5, username="ceo_bu5", ip=PUBLIC_B)
        self.assertIsNotNone(self.check("ceo_bu5", PUBLIC_A))
        self.assertIsNone(self.check("ceo_bu6", PUBLIC_A))

    def test_four_failures_do_not_block(self):
        self.add_rows("LOGIN_FAILED", 4, username="ceo_bu5", ip=PUBLIC_B)
        self.assertIsNone(self.check("ceo_bu5", PUBLIC_A))

    def test_owner_on_usual_ip_is_not_blocked_by_noise_from_elsewhere(self):
        self.add_rows("LOGIN_FAILED", 9, username="ceo_bu5", ip=PUBLIC_B)
        self.add_rows("LOGIN_SUCCESS", 1, username="ceo_bu5", ip=PUBLIC_A, age_seconds=20 * 24 * 3600)
        self.assertIsNone(self.check("ceo_bu5", PUBLIC_A))
        self.assertIsNotNone(self.check("ceo_bu5", PUBLIC_B))

    def test_old_success_does_not_grant_the_bypass(self):
        self.add_rows("LOGIN_FAILED", 5, username="ceo_bu5", ip=PUBLIC_B)
        self.add_rows("LOGIN_SUCCESS", 1, username="ceo_bu5", ip=PUBLIC_A, age_seconds=31 * 24 * 3600)
        self.assertIsNotNone(self.check("ceo_bu5", PUBLIC_A))

    def test_old_failures_age_out_of_the_window(self):
        self.add_rows("LOGIN_FAILED", 5, username="ceo_bu5", ip=PUBLIC_B, age_seconds=16 * 60)
        self.assertIsNone(self.check("ceo_bu5", PUBLIC_A))

    def test_retry_after_is_never_below_the_exponential_floor_and_capped(self):
        self.assertEqual(throttle.user_backoff_seconds(5), 60)
        self.assertEqual(throttle.user_backoff_seconds(6), 120)
        self.assertEqual(throttle.user_backoff_seconds(30), 900)

    def test_global_circuit_breaker(self):
        self.add_rows("LOGIN_FAILED", 301, username="n", ip=None, distinct_usernames=True, age_seconds=5)
        blocked = self.check("innocent_name", PUBLIC_A)
        self.assertIsNotNone(blocked)
        self.assertEqual(blocked.retry_after, 60)

    def test_breaker_not_tripped_at_300(self):
        self.add_rows("LOGIN_FAILED", 300, username="n", ip=None, distinct_usernames=True, age_seconds=5)
        self.assertIsNone(self.check("innocent_name", PUBLIC_A))


class ForgotPasswordThrottleTests(DatabaseTestCase):
    def check(self, username, ip):
        return throttle.check_forgot_password_throttle(self.db, username, ip, self.now)

    def test_ip_limit_is_five_per_hour(self):
        self.add_rows("PASSWORD_RESET_REQUESTED", 4, username="u", ip=PUBLIC_A, distinct_usernames=True)
        self.assertFalse(self.check("fresh", PUBLIC_A).throttled)
        self.add_rows("PASSWORD_RESET_REQUESTED", 1, username="u9", ip=PUBLIC_A)
        self.assertTrue(self.check("fresh", PUBLIC_A).throttled)
        self.assertFalse(self.check("fresh", PUBLIC_B).throttled)

    def test_username_limit_is_three_per_day(self):
        self.add_rows("PASSWORD_RESET_REQUESTED", 3, username="ceo_bu5", ip=PUBLIC_A, age_seconds=3 * 3600)
        self.assertTrue(self.check("ceo_bu5", PUBLIC_B).throttled)
        self.assertFalse(self.check("ceo_bu6", PUBLIC_B).throttled)

    def test_private_ip_skips_the_ip_rule_only(self):
        self.add_rows("PASSWORD_RESET_REQUESTED", 8, username="u", ip=PRIVATE, distinct_usernames=True)
        self.assertFalse(self.check("fresh", PRIVATE).throttled)
        self.add_rows("PASSWORD_RESET_REQUESTED", 3, username="ceo_bu5", ip=PRIVATE)
        self.assertTrue(self.check("ceo_bu5", PRIVATE).throttled)

    def test_far_past_the_limit_stops_writing_audit_rows(self):
        self.add_rows("PASSWORD_RESET_REQUESTED", 50, username="u", ip=PUBLIC_A, distinct_usernames=True)
        state = self.check("fresh", PUBLIC_A)
        self.assertTrue(state.throttled)
        self.assertFalse(state.record_audit)


class PasswordChangeThrottleTests(DatabaseTestCase):
    def test_five_failures_in_fifteen_minutes_block(self):
        self.add_rows("PASSWORD_CHANGE_FAILED", 4, username="ceo_bu5")
        self.assertIsNone(throttle.check_password_change_throttle(self.db, "ceo_bu5", self.now))
        self.add_rows("PASSWORD_CHANGE_FAILED", 1, username="ceo_bu5")
        self.assertIsNotNone(throttle.check_password_change_throttle(self.db, "ceo_bu5", self.now))
        self.assertIsNone(throttle.check_password_change_throttle(self.db, "ceo_bu6", self.now))


class NormalizeUsernameTests(unittest.TestCase):
    def test_strips_control_chars_lowercases_and_truncates(self):
        self.assertEqual(throttle.normalize_username("  CEO_BU5\r\n\x00 "), "ceo_bu5")
        self.assertEqual(len(throttle.normalize_username("a" * 500)), 80)
        self.assertEqual(throttle.normalize_username(None), "")


class ClientIpTests(unittest.TestCase):
    def ip(self, request, header="", hops=1):
        with mock.patch.object(settings, "CLIENT_IP_HEADER", header), mock.patch.object(settings, "TRUSTED_PROXY_HOPS", hops):
            return get_client_ip(request)

    def test_peer_when_no_headers(self):
        self.assertEqual(self.ip(make_request(peer="192.0.2.1")), "192.0.2.1")

    def test_forged_left_most_forwarded_for_is_ignored(self):
        request = make_request(headers={"X-Forwarded-For": "6.6.6.6, 203.0.113.9"})
        self.assertEqual(self.ip(request), "203.0.113.9")

    def test_hops_counts_from_the_right(self):
        request = make_request(headers={"X-Forwarded-For": "6.6.6.6, 203.0.113.9, 10.0.0.2"})
        self.assertEqual(self.ip(request, hops=1), "10.0.0.2")
        self.assertEqual(self.ip(request, hops=2), "203.0.113.9")

    def test_too_few_entries_falls_back_to_peer(self):
        request = make_request(peer="192.0.2.1", headers={"X-Forwarded-For": "203.0.113.9"})
        self.assertEqual(self.ip(request, hops=3), "192.0.2.1")

    def test_garbage_falls_back_to_peer(self):
        request = make_request(peer="192.0.2.1", headers={"X-Forwarded-For": "6.6.6.6, not-an-ip"})
        self.assertEqual(self.ip(request), "192.0.2.1")

    def test_named_header_wins_and_takes_first_entry(self):
        request = make_request(headers={"CF-Connecting-IP": "198.51.100.4, 1.1.1.1", "X-Forwarded-For": "6.6.6.6"})
        self.assertEqual(self.ip(request, header="cf-connecting-ip"), "198.51.100.4")

    def test_named_header_missing_falls_back_to_peer_not_forwarded_for(self):
        request = make_request(peer="192.0.2.1", headers={"X-Forwarded-For": "6.6.6.6"})
        self.assertEqual(self.ip(request, header="do-connecting-ip"), "192.0.2.1")

    def test_ipv6_is_canonicalised(self):
        request = make_request(headers={"X-Forwarded-For": "2001:0DB8:0:0:0:0:0:1"})
        self.assertEqual(self.ip(request), "2001:db8::1")

    def test_no_request_or_no_peer(self):
        self.assertIsNone(get_client_ip(None))
        self.assertIsNone(self.ip(make_request(peer=None)))


class PasswordPolicyTests(unittest.TestCase):
    def test_accepts_a_reasonable_passphrase(self):
        self.assertIsNone(password_policy_error("correct horse battery", username="ceo_bu5"))

    def test_length_is_measured_in_bytes(self):
        self.assertIsNotNone(password_policy_error("short"))
        self.assertIsNotNone(password_policy_error("abcdefgh1" * 8 + "zz"))  # 74 bytes
        self.assertIsNone(password_policy_error("abcdefgh1" * 8))  # exactly 72
        self.assertIsNotNone(password_policy_error("က" * 25))  # 25 chars, 75 bytes

    def test_denylist_prefix_and_case_insensitive(self):
        for weak in ("Welcome123!extra", "PASSWORD-2026-xx", "Chairman2026!!", "abcmib-is-great", "BusinessOps#2026", "qwertyuiop123"):
            self.assertIsNotNone(password_policy_error(weak), weak)

    def test_contains_username_and_same_as_current(self):
        self.assertIsNotNone(password_policy_error("my-ceo_bu5-secret", username="ceo_bu5"))
        self.assertIsNotNone(password_policy_error("same-as-before-1", current_password="same-as-before-1"))

    def test_nul_and_repetitive_rejected(self):
        self.assertIsNotNone(password_policy_error("abc\x00defghijkl"))
        self.assertIsNotNone(password_policy_error("aaaaaaaaaaaaaaaa"))


class LogAuditTests(DatabaseTestCase):
    def last_row(self):
        return self.db.query(AuditLog).order_by(AuditLog.id.desc()).first()

    def test_username_param_used_without_a_user_and_scrubbed(self):
        log_audit(self.db, None, "LOGIN_FAILED", "username=x", None, username="ceo\r\n_bu5\x00" + "z" * 200)
        row = self.last_row()
        self.assertEqual(row.username, ("ceo_bu5" + "z" * 200)[:80])
        self.assertIsNone(row.user_id)
        self.assertIsNone(row.ip_address)

    def test_details_are_scrubbed_and_capped(self):
        log_audit(self.db, None, "X", "a\nb\r\nc" + "y" * 2000, None, username="u")
        row = self.last_row()
        self.assertNotIn("\n", row.details)
        self.assertNotIn("\r", row.details)
        self.assertEqual(len(row.details), 1000)

    def test_resubmit_json_gets_extra_room_so_it_stays_parseable(self):
        details = "date=2026-01-01 resubmit_changes_json=[" + ",".join(['{"field": "Revenue", "old": "1", "new": "2"}'] * 40) + "]"
        self.assertGreater(len(details), 1000)
        log_audit(self.db, None, "DAILY_REPORT_SUBMITTED", details, None, username="u")
        self.assertEqual(self.last_row().details, details)

    def test_ip_comes_from_get_client_ip_not_the_forgeable_left_most(self):
        request = make_request(peer="192.0.2.1", headers={"X-Forwarded-For": "6.6.6.6, 8.8.8.8"})
        with mock.patch.object(settings, "CLIENT_IP_HEADER", ""), mock.patch.object(settings, "TRUSTED_PROXY_HOPS", 1):
            log_audit(self.db, None, "X", "", request, username="u")
        self.assertEqual(self.last_row().ip_address, "8.8.8.8")


if __name__ == "__main__":
    unittest.main()
