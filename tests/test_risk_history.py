"""Offline alignment tests: holidays, gaps, splits, quote units, mismatched intervals."""

import unittest

from dashboard.risk.history import align_close_series, align_max_available, compute_fx_returns


def _series(dates, start=100.0, step=0.01):
    rows = []
    price = start
    for d in dates:
        rows.append((d, price))
        price *= 1 + step
    return rows


class AlignmentTests(unittest.TestCase):
    def test_common_holiday_excluded_without_fill(self):
        # A has all 5 days, B missing day 3 (holiday). Common dates = 4 days -> 3 intervals,
        # but intervals bridging the holiday are multi-session for A -> excluded.
        dates_a = ["2026-01-05", "2026-01-06", "2026-01-07", "2026-01-08", "2026-01-09"]
        dates_b = ["2026-01-05", "2026-01-06", "2026-01-08", "2026-01-09"]
        aligned, reason = align_close_series(
            {"A": _series(dates_a), "B": _series(dates_b)}, required=1
        )
        self.assertIsNotNone(aligned)
        assert aligned is not None
        # Intervals: (05,06) valid; (06,08) invalid for A (gap); (08,09) valid.
        self.assertEqual(aligned.sample_count, 1)  # latest valid chosen
        self.assertEqual(aligned.scenario_dates[0], ("2026-01-08", "2026-01-09"))
        self.assertGreaterEqual(aligned.excluded_intervals, 1)

    def test_insufficient_history(self):
        aligned, reason = align_close_series({"A": _series(["2026-01-05", "2026-01-06"])}, required=252)
        self.assertIsNone(aligned)
        self.assertEqual(reason, "insufficient_history")

    def test_mismatched_intervals_excluded(self):
        # A trades Mon-Fri, B trades Mon,Wed,Fri only: only intervals consecutive in both survive.
        dates_a = ["2026-01-05", "2026-01-06", "2026-01-07", "2026-01-08", "2026-01-09"]
        dates_b = ["2026-01-05", "2026-01-07", "2026-01-09"]
        aligned, _ = align_close_series({"A": _series(dates_a), "B": _series(dates_b)}, required=1)
        # Common = 05,07,09. (05,07): A has 06 between -> excluded. (07,09): A has 08 -> excluded.
        self.assertIsNone(aligned)

    def test_split_adjusted_series_is_continuous(self):
        # Auto-adjusted closes already remove the split jump; alignment just needs valid positives.
        dates = ["2026-01-05", "2026-01-06", "2026-01-07"]
        # Simulate 2:1 split adjusted backwards: prices halved before split but returns smooth.
        a = [("2026-01-05", 50.0), ("2026-01-06", 50.5), ("2026-01-07", 51.0)]
        b = [("2026-01-05", 200.0), ("2026-01-06", 202.0), ("2026-01-07", 204.0)]
        aligned, _ = align_close_series({"A": a, "B": b}, required=2)
        self.assertIsNotNone(aligned)
        assert aligned is not None
        self.assertAlmostEqual(aligned.security_returns["A"][0], 50.5 / 50.0 - 1, places=9)

    def test_quote_unit_scaling_invariant(self):
        dates = ["2026-01-05", "2026-01-06", "2026-01-07"]
        gbp = [("2026-01-05", 100.0), ("2026-01-06", 101.0), ("2026-01-07", 102.0)]
        gbp_pence = [(d, c * 100.0) for d, c in gbp]  # GBp quote
        a, _ = align_close_series({"A": gbp}, required=2)
        b, _ = align_close_series({"A": gbp_pence}, required=2)
        assert a is not None and b is not None
        self.assertAlmostEqual(a.security_returns["A"][0], b.security_returns["A"][0], places=12)

    def test_invalid_prices_dropped(self):
        rows = [("2026-01-05", 100.0), ("2026-01-06", 0.0), ("2026-01-07", float("nan")), ("2026-01-08", 102.0)]
        aligned, reason = align_close_series({"A": rows}, required=1)
        # Only 05 and 08 survive cleaning -> 1 interval (multi-session but single ticker, consecutive in cleaned series).
        self.assertIsNotNone(aligned)

    def test_incomplete_current_bar_excluded_by_caller(self):
        # Caller must drop today's date before aligning; verify alignment respects what it is given.
        dates = ["2026-01-05", "2026-01-06", "2026-01-07"]
        aligned, _ = align_close_series({"A": _series(dates)}, required=2)
        self.assertIsNotNone(aligned)
        assert aligned is not None
        self.assertEqual(aligned.end_date, "2026-01-07")

    def test_fx_missing_bar_unavailable(self):
        scen = [("2026-01-05", "2026-01-06"), ("2026-01-06", "2026-01-07")]
        fx, reason = compute_fx_returns({"USD": [("2026-01-05", 1.1), ("2026-01-06", 1.2)]}, scen)
        self.assertIsNone(fx)
        self.assertEqual(reason, "unavailable_fx")

    def test_fx_direction_eur_per_foreign(self):
        scen = [("2026-01-05", "2026-01-06")]
        fx, _ = compute_fx_returns({"USD": [("2026-01-05", 1.10), ("2026-01-06", 1.21)]}, scen)
        assert fx is not None
        self.assertAlmostEqual(fx["USD"][0], 1.21 / 1.10 - 1, places=9)


    def test_max_available_keeps_full_window(self):
        dates = ["2026-01-05", "2026-01-06", "2026-01-07", "2026-01-08", "2026-01-09"]
        closes = {"A": _series(dates), "B": _series(dates)}
        tail, _ = align_close_series(closes, 1)
        full, reason = align_max_available(closes, 2)
        assert tail is not None and full is not None
        self.assertEqual(tail.sample_count, 1)
        self.assertEqual(full.sample_count, 4)
        self.assertEqual(full.scenario_dates[0], ("2026-01-05", "2026-01-06"))

    def test_max_available_respects_minimum(self):
        dates = ["2026-01-05", "2026-01-06", "2026-01-07"]
        aligned, reason = align_max_available({"A": _series(dates)}, 99)
        self.assertIsNone(aligned)
        self.assertEqual(reason, "insufficient_history")


if __name__ == "__main__":
    unittest.main()
