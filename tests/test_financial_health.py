"""Unit tests for evaluate_financial_health(). Run with:

    python -m unittest tests.test_financial_health -v

No pytest dependency required — plain unittest.TestCase is also
auto-discovered by pytest if the project ever adds it later.
"""
import unittest

from src.utils import evaluate_financial_health


class TestEvaluateFinancialHealth(unittest.TestCase):
    # --- RED cases ---

    def test_red_negative_cash_flow(self):
        status, _, flags = evaluate_financial_health(revenue=100_000, cash_flow=-1, receivable=0, payable=0)
        self.assertEqual(status, "RED")
        self.assertIn("Cash flow is negative", flags)

    def test_red_takes_priority_over_yellow(self):
        # Would also match a YELLOW rule (cash_flow < payable), but negative cash flow is RED.
        status, _, flags = evaluate_financial_health(revenue=100_000, cash_flow=-5, receivable=100, payable=50)
        self.assertEqual(status, "RED")

    def test_insolvency_style_shortfall_no_longer_triggers_red(self):
        # Previously RED via (cash_flow + receivable) < payable — that rule was removed.
        # cash_flow (50) < payable (150) still trips the YELLOW "below payable" rule.
        status, _, flags = evaluate_financial_health(revenue=100_000, cash_flow=50, receivable=50, payable=150)
        self.assertEqual(status, "YELLOW")
        self.assertFalse(any("insolvency" in f.lower() for f in flags))

    def test_receivable_over_150_percent_no_longer_triggers_red(self):
        # Previously RED via receivable > 1.5x revenue — that rule was removed.
        # receivable (160,000) > revenue (100,000) still trips the YELLOW "exceeds revenue" rule.
        status, _, flags = evaluate_financial_health(revenue=100_000, cash_flow=10_000, receivable=160_000, payable=0)
        self.assertEqual(status, "YELLOW")
        self.assertNotIn("Receivable is more than 150% of revenue", flags)

    # --- YELLOW cases ---

    def test_yellow_cash_flow_below_payable_but_solvent(self):
        # cash_flow (80) < payable (100), but cash_flow + receivable (180) >= payable (100)
        status, _, flags = evaluate_financial_health(revenue=100_000, cash_flow=80, receivable=100, payable=100)
        self.assertEqual(status, "YELLOW")
        self.assertIn("Cash flow alone is below payable obligations", flags)

    def test_yellow_receivable_over_revenue(self):
        status, _, flags = evaluate_financial_health(revenue=100_000, cash_flow=100_000, receivable=120_000, payable=0)
        self.assertEqual(status, "YELLOW")
        self.assertIn("Receivable exceeds total revenue", flags)

    def test_yellow_thin_cash_flow_margin(self):
        # cash_flow positive but under 20% of revenue
        status, _, flags = evaluate_financial_health(revenue=1_000_000, cash_flow=100_000, receivable=0, payable=0)
        self.assertEqual(status, "YELLOW")
        self.assertIn("Cash flow is positive but under 20% of revenue", flags)

    # --- GREEN cases ---

    def test_green_healthy(self):
        status, _, flags = evaluate_financial_health(revenue=1_000_000, cash_flow=500_000, receivable=200_000, payable=100_000)
        self.assertEqual(status, "GREEN")
        self.assertEqual(flags, [])

    def test_green_when_revenue_zero_and_cash_flow_covers_payable(self):
        status, _, flags = evaluate_financial_health(revenue=0, cash_flow=1_000, receivable=0, payable=500)
        self.assertEqual(status, "GREEN")

    # --- Return shape / None-handling ---

    def test_returns_three_tuple_with_burmese_message(self):
        status, message, flags = evaluate_financial_health(100, 100, 0, 0)
        self.assertIsInstance(status, str)
        self.assertIsInstance(message, str)
        self.assertIsInstance(flags, list)
        self.assertTrue(len(message) > 0)

    def test_none_inputs_treated_as_zero(self):
        status, _, _ = evaluate_financial_health(revenue=None, cash_flow=None, receivable=None, payable=None)
        self.assertEqual(status, "GREEN")  # 0 cash flow, 0 payable, 0 receivable: no rule fires


if __name__ == "__main__":
    unittest.main()
