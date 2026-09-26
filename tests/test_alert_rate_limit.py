"""Dense unit tests for Slack health-alert rate limiting helpers.

Covers: identical-text detection, counter tally, exponential backoff vs
short identical-update interval, and next-count reset rules.
Pure functions only — no Slack, no filesystem, no network.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analyze_images import (
    ALERT_BASE_COOLDOWN_S,
    ALERT_MAX_COOLDOWN_S,
    IDENTICAL_UPDATE_S,
    alert_base_text,
    alert_cooldown,
    alert_next_count,
    alert_with_count,
)


class TestAlertWithCount(unittest.TestCase):
    def test_count_one_or_less_returns_base(self):
        self.assertEqual(alert_with_count("hello", 1), "hello")
        self.assertEqual(alert_with_count("hello", 0), "hello")
        self.assertEqual(alert_with_count("hello", -3), "hello")

    def test_count_two_appends_suffix(self):
        self.assertEqual(alert_with_count("hello", 2), "hello  _(×2)")

    def test_count_large(self):
        self.assertEqual(alert_with_count("disk full", 42), "disk full  _(×42)")

    def test_empty_base(self):
        self.assertEqual(alert_with_count("", 5), "  _(×5)")


class TestAlertBaseText(unittest.TestCase):
    def test_no_suffix_unchanged(self):
        self.assertEqual(alert_base_text("plain message"), "plain message")

    def test_strips_count_suffix(self):
        self.assertEqual(alert_base_text("plain message  _(×3)"), "plain message")
        self.assertEqual(alert_base_text("disk at 95%  _(×12)"), "disk at 95%")

    def test_empty_and_none_like(self):
        self.assertEqual(alert_base_text(""), "")
        self.assertEqual(alert_base_text(None), "")

    def test_suffix_only_at_end(self):
        # mid-string lookalike must not be stripped
        self.assertEqual(
            alert_base_text("see _(×2) in docs  _(×5)"),
            "see _(×2) in docs",
        )

    def test_identical_detection_roundtrip(self):
        base = "⚠️ *Webcam22*: storage at 95% of the 3GB budget — retention is deleting images."
        with_count = alert_with_count(base, 7)
        self.assertEqual(alert_base_text(with_count), base)
        self.assertEqual(alert_base_text(base), base)


class TestAlertCooldown(unittest.TestCase):
    def test_identical_with_ts_uses_short_interval(self):
        self.assertEqual(
            alert_cooldown(0, identical=True, has_slack_ts=True),
            IDENTICAL_UPDATE_S,
        )
        self.assertEqual(
            alert_cooldown(99, identical=True, has_slack_ts=True),
            IDENTICAL_UPDATE_S,
        )

    def test_identical_without_ts_uses_exponential(self):
        # no existing Slack message → treat as brand-new path
        self.assertEqual(
            alert_cooldown(0, identical=True, has_slack_ts=False),
            ALERT_BASE_COOLDOWN_S,
        )

    def test_exponential_sequence(self):
        self.assertEqual(alert_cooldown(0), ALERT_BASE_COOLDOWN_S)           # 30m
        self.assertEqual(alert_cooldown(1), ALERT_BASE_COOLDOWN_S * 2)       # 1h
        self.assertEqual(alert_cooldown(2), ALERT_BASE_COOLDOWN_S * 4)       # 2h
        self.assertEqual(alert_cooldown(3), ALERT_BASE_COOLDOWN_S * 8)       # 4h
        self.assertEqual(alert_cooldown(4), ALERT_BASE_COOLDOWN_S * 16)      # 8h
        self.assertEqual(alert_cooldown(5), ALERT_MAX_COOLDOWN_S)            # cap 12h
        self.assertEqual(alert_cooldown(9), ALERT_MAX_COOLDOWN_S)            # still capped

    def test_different_text_ignores_identical_flag_when_no_ts(self):
        self.assertEqual(
            alert_cooldown(2, identical=False, has_slack_ts=True),
            ALERT_BASE_COOLDOWN_S * 4,
        )


class TestAlertNextCount(unittest.TestCase):
    def test_identical_increments(self):
        self.assertEqual(alert_next_count(0, True), 1)
        self.assertEqual(alert_next_count(1, True), 2)
        self.assertEqual(alert_next_count(7, True), 8)

    def test_different_resets_to_one(self):
        self.assertEqual(alert_next_count(0, False), 1)
        self.assertEqual(alert_next_count(5, False), 1)
        self.assertEqual(alert_next_count(99, False), 1)


class TestIdenticalDecision(unittest.TestCase):
    """End-to-end pure decision: same base text → identical path."""

    def test_same_base_is_identical(self):
        prev = "⚠️ Webcam22 over budget"
        cur = "⚠️ Webcam22 over budget"
        self.assertTrue(alert_base_text(cur) == alert_base_text(prev) and bool(prev))

    def test_same_base_with_old_count_suffix_is_identical(self):
        prev = alert_with_count("⚠️ Webcam22 over budget", 4)
        cur = "⚠️ Webcam22 over budget"
        self.assertTrue(alert_base_text(cur) == alert_base_text(prev))

    def test_different_text_not_identical(self):
        prev = "⚠️ Webcam22 over budget"
        cur = "⚠️ Webcam21 over budget"
        self.assertFalse(alert_base_text(cur) == alert_base_text(prev))


if __name__ == "__main__":
    unittest.main()
