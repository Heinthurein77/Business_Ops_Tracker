"""Unit tests for evaluate_cumulative_financial_health(). Run with:

    python -m unittest tests.test_daily_performance -v
"""
import unittest
from datetime import date

from src.utils import evaluate_cumulative_financial_health

# A 30-day month (April) makes the pro-rated Expected MTD Target arithmetic exact.
APRIL_MID_MONTH = date(2026, 4, 15)  # day 15 of 30 -> 50% of the month elapsed
APRIL_MONTH_END = date(2026, 4, 30)  # last day -> Expected MTD Target == full target

TARGET_3M = {"monthly_revenue_target": 3_000_000}  # -> 1,500,000 expected by day 15


class TestEvaluateCumulativeFinancialHealth(unittest.TestCase):
    def test_expected_mtd_target_is_prorated_by_day_of_month(self):
        result = evaluate_cumulative_financial_health(0, TARGET_3M, APRIL_MID_MONTH)
        self.assertEqual(result["expected_mtd_target"], 1_500_000.0)
        self.assertEqual(result["day_number"], 15)
        self.assertEqual(result["days_in_month"], 30)

    def test_month_end_expected_target_equals_full_target(self):
        # Same formula, no separate branch: on the last day of the month
        # day_number == days_in_month, so Expected MTD Target == full target.
        result = evaluate_cumulative_financial_health(3_000_000, TARGET_3M, APRIL_MONTH_END)
        self.assertEqual(result["expected_mtd_target"], 3_000_000.0)

    def test_red_below_50_percent_of_expected(self):
        # 600,000 / 1,500,000 = 40%
        result = evaluate_cumulative_financial_health(600_000, TARGET_3M, APRIL_MID_MONTH)
        self.assertEqual(result["status"], "RED")
        self.assertEqual(result["achievement_pct"], 40.0)

    def test_yellow_between_50_and_80_percent_of_expected(self):
        # 1,050,000 / 1,500,000 = 70%
        result = evaluate_cumulative_financial_health(1_050_000, TARGET_3M, APRIL_MID_MONTH)
        self.assertEqual(result["status"], "YELLOW")
        self.assertEqual(result["achievement_pct"], 70.0)

    def test_green_at_or_above_80_percent_of_expected(self):
        # 1,200,000 / 1,500,000 = 80% exactly
        result = evaluate_cumulative_financial_health(1_200_000, TARGET_3M, APRIL_MID_MONTH)
        self.assertEqual(result["status"], "GREEN")
        self.assertEqual(result["achievement_pct"], 80.0)

    def test_on_pace_reads_100_percent_and_green_mid_month(self):
        # Exactly on pace halfway through the month should read as fully on
        # track, not penalized for the month not being finished yet.
        result = evaluate_cumulative_financial_health(1_500_000, TARGET_3M, APRIL_MID_MONTH)
        self.assertEqual(result["achievement_pct"], 100.0)
        self.assertEqual(result["status"], "GREEN")

    def test_variance_amount_is_against_full_monthly_target_not_expected(self):
        # Cumulative 1,500,000 vs FULL target 3,000,000 -> -1,500,000, even
        # though this same figure is a perfectly on-pace 100% achievement.
        result = evaluate_cumulative_financial_health(1_500_000, TARGET_3M, APRIL_MID_MONTH)
        self.assertEqual(result["variance_amount"], -1_500_000.0)
        self.assertEqual(result["full_monthly_target"], 3_000_000.0)

    def test_variance_amount_positive_when_cumulative_exceeds_full_target(self):
        result = evaluate_cumulative_financial_health(3_500_000, TARGET_3M, APRIL_MONTH_END)
        self.assertEqual(result["variance_amount"], 500_000.0)

    def test_no_target_set_returns_none(self):
        no_target = {"monthly_revenue_target": 0}
        result = evaluate_cumulative_financial_health(1_000_000, no_target, APRIL_MID_MONTH)
        self.assertIsNone(result)

    def test_green_has_no_summary_notes(self):
        result = evaluate_cumulative_financial_health(1_500_000, TARGET_3M, APRIL_MID_MONTH)
        self.assertEqual(result["summary_notes"], [])

    def test_summary_notes_are_burmese_text_for_yellow_and_red(self):
        for cumulative in (600_000, 1_050_000):  # RED, YELLOW
            result = evaluate_cumulative_financial_health(cumulative, TARGET_3M, APRIL_MID_MONTH)
            self.assertTrue(len(result["summary_notes"]) > 0)
            for flag in result["summary_notes"]:
                # crude but effective: Burmese script sits outside the ASCII range
                self.assertTrue(any(ord(ch) > 127 for ch in flag))

    def test_cumulative_net_revenue_is_carried_through_and_rounded(self):
        result = evaluate_cumulative_financial_health(1_234_567.891, TARGET_3M, APRIL_MID_MONTH)
        self.assertEqual(result["cumulative_net_revenue"], 1_234_567.89)


if __name__ == "__main__":
    unittest.main()
