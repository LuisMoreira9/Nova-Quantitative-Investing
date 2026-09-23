"""Offline fixtures for 1-day 99% historical-simulation VaR."""

from __future__ import annotations

import math
import unittest

from dashboard.risk.var_calculation import (
    VarInputs,
    calculate_historical_var,
    eur_return,
    quantile_index,
)


def _inputs(equity=100_000.0, weights=None, returns=None, cash=None, fx=None):
    return VarInputs(
        equity=equity,
        security_weights=weights or {},
        security_eur_returns=returns or {},
        cash_weights=cash or {},
        fx_returns=fx or {},
    )


class QuantileTests(unittest.TestCase):
    def test_n252_selects_third_largest(self):
        # Losses 1..252 ascending; rank ceil(0.99*252)=250 -> value 250; third-largest = 250.
        losses = list(range(1, 253))
        idx = quantile_index(252)
        self.assertEqual(idx, 249)
        self.assertEqual(sorted(losses)[idx], 250)

    def test_small_n_convention(self):
        self.assertEqual(quantile_index(100), 98)  # 99th of 100
        self.assertEqual(quantile_index(21), 20)  # ceil(20.79)=21 -> last
        self.assertEqual(quantile_index(1), 0)

    def test_known_ranked_losses(self):
        # 100 scenarios with portfolio returns crafted so losses are 0.00..0.99.
        n = 100
        rets = [[-i / 100.0] for i in range(n)]  # losses 0.00..0.99 but reversed order
        flat = [r[0] for r in rets]
        result = calculate_historical_var(
            _inputs(weights={"A": 1.0}, returns={"A": flat})
        )
        self.assertEqual(result.status, "ready")
        # 99th smallest loss of 0.00..0.99 is 0.98
        self.assertAlmostEqual(result.var_fraction, 0.98, places=9)
        self.assertAlmostEqual(result.var_eur, 0.98 * 100_000.0, places=6)


class ExposureTests(unittest.TestCase):
    def test_long_short_signs(self):
        # Same return shock: long loses, short gains.
        shock = [-0.10] * 10
        long_res = calculate_historical_var(_inputs(weights={"A": 0.5}, returns={"A": shock}))
        short_res = calculate_historical_var(_inputs(weights={"A": -0.5}, returns={"A": shock}))
        self.assertGreater(long_res.var_fraction, 0)
        # Short gains 5% per scenario -> losses negative -> VaR floored to 0.
        self.assertEqual(short_res.var_fraction, 0.0)

    def test_leverage_scales_var(self):
        shock = [-0.04] * 50
        base = calculate_historical_var(_inputs(weights={"A": 0.5}, returns={"A": shock}))
        levered = calculate_historical_var(_inputs(weights={"A": 1.0}, returns={"A": shock}))
        self.assertAlmostEqual(levered.var_fraction, 2 * base.var_fraction, places=9)

    def test_cash_dilution(self):
        shock = [-0.10] * 50
        concentrated = calculate_historical_var(_inputs(weights={"A": 1.0}, returns={"A": shock}))
        diluted = calculate_historical_var(
            _inputs(weights={"A": 0.5}, returns={"A": shock}, cash={"EUR": 0.5})
        )
        self.assertAlmostEqual(diluted.var_fraction, 0.5 * concentrated.var_fraction, places=9)

    def test_correlated_diversification(self):
        # Two perfectly offsetting positions cancel.
        up = [0.05] * 50
        down = [-0.05] * 50
        res = calculate_historical_var(
            _inputs(weights={"A": 0.5, "B": 0.5}, returns={"A": up, "B": down})
        )
        self.assertEqual(res.var_fraction, 0.0)
        self.assertEqual(res.worst_21_fraction, 0.0)

    def test_eur_cash_only_is_valid_zero(self):
        res = calculate_historical_var(_inputs(cash={"EUR": 1.0}))
        self.assertEqual(res.status, "ready")
        self.assertEqual(res.var_fraction, 0.0)
        self.assertEqual(res.var_eur, 0.0)

    def test_empty_portfolio_with_equity_is_zero(self):
        res = calculate_historical_var(_inputs())
        self.assertEqual(res.status, "ready")
        self.assertEqual(res.var_fraction, 0.0)


class FxTests(unittest.TestCase):
    def test_eur_return_formula(self):
        self.assertAlmostEqual(eur_return(0.10, 0.05), 1.10 * 1.05 - 1.0, places=12)
        self.assertAlmostEqual(eur_return(-0.10, 0.10), 0.90 * 1.10 - 1.0, places=12)

    def test_foreign_cash_uses_fx(self):
        fx = [0.02] * 30
        res = calculate_historical_var(_inputs(cash={"USD": 0.4}, fx={"USD": fx}))
        self.assertAlmostEqual(res.var_fraction, 0.0, places=9)  # gains -> floored
        self.assertAlmostEqual(res.worst_21_fraction, -0.008, places=9)  # -0.4*0.02

    def test_offsetting_currency_exposures_cancel(self):
        # Long USD security funded by short USD cash of equal size: FX cancels,
        # leaving only local return exposure.
        local_eur_combined = [0.03] * 40  # pretend already in EUR for this unit check
        # Model as: security weight 0.5 with EUR returns, USD cash -0.5 with FX 5%,
        # plus security local part implicitly in EUR returns. Here we check cash leg alone cancels:
        res_cash_only = calculate_historical_var(
            _inputs(cash={"USD": 0.5, "EUR": 0.0}, fx={"USD": [0.05] * 40})
        )
        res_offset = calculate_historical_var(
            _inputs(
                weights={"A": 0.5},
                returns={"A": local_eur_combined},
                cash={"USD": -0.5},
                fx={"USD": [0.05] * 40},
            )
        )
        # Cash-only USD long loses nothing when FX rises (it gains); offset portfolio
        # return = 0.5*0.03 - 0.5*0.05 = -0.01 -> loss 0.01.
        self.assertAlmostEqual(res_offset.var_fraction, 0.01, places=9)
        self.assertEqual(res_cash_only.var_fraction, 0.0)

    def test_missing_fx_is_unavailable(self):
        res = calculate_historical_var(_inputs(cash={"USD": 0.5}, fx={}))
        self.assertEqual(res.status, "unavailable")
        self.assertIsNone(res.var_fraction)
        self.assertIn("unavailable_fx", res.reasons)

    def test_no_double_counting_note(self):
        # Security EUR returns already embed FX; cash leg must not re-apply it.
        # If EUR returns are 10% (local 5% + FX ~4.76%), a 1.0 weight gives 10% loss when reversed.
        res = calculate_historical_var(_inputs(weights={"A": 1.0}, returns={"A": [-0.10] * 20}))
        self.assertAlmostEqual(res.var_fraction, 0.10, places=9)


class SensitivityTests(unittest.TestCase):
    def test_allocation_change_moves_result(self):
        rets = {"A": [0.01 * ((i % 5) - 2) for i in range(60)]}
        r1 = calculate_historical_var(_inputs(weights={"A": 0.3}, returns=rets))
        r2 = calculate_historical_var(_inputs(weights={"A": 0.6}, returns=rets))
        self.assertNotAlmostEqual(r1.var_fraction, r2.var_fraction, places=9)
        self.assertAlmostEqual(r2.var_fraction, 2 * r1.var_fraction, places=9)

    def test_price_change_moves_result(self):
        w = {"A": 1.0}
        r1 = calculate_historical_var(_inputs(weights=w, returns={"A": [-0.01] * 60}))
        r2 = calculate_historical_var(_inputs(weights=w, returns={"A": [-0.02] * 60}))
        self.assertAlmostEqual(r2.var_fraction, 2 * r1.var_fraction, places=9)


class FailureTests(unittest.TestCase):
    def test_invalid_equity(self):
        for bad in (0.0, -100.0, float("nan"), float("inf")):
            with self.subTest(equity=bad):
                res = calculate_historical_var(_inputs(equity=bad, weights={"A": 1.0}, returns={"A": [0.01] * 10}))
                self.assertEqual(res.status, "unavailable")
                self.assertIsNone(res.var_fraction)
                self.assertIn("nonpositive_equity", res.reasons)

    def test_missing_history(self):
        res = calculate_historical_var(_inputs(weights={"A": 1.0}, returns={}))
        self.assertEqual(res.status, "unavailable")
        self.assertIn("missing_history", res.reasons)

    def test_mismatched_lengths_unavailable(self):
        res = calculate_historical_var(
            _inputs(weights={"A": 0.5, "B": 0.5}, returns={"A": [0.01] * 50, "B": [0.01] * 40})
        )
        self.assertEqual(res.status, "unavailable")

    def test_invalid_returns_unavailable_not_zero(self):
        for bad_series in ([float("nan")] * 10, [float("inf")] * 10, [None] * 10):  # type: ignore[list-item]
            with self.subTest(series=str(bad_series[:1])):
                res = calculate_historical_var(_inputs(weights={"A": 1.0}, returns={"A": bad_series}))
                self.assertEqual(res.status, "unavailable")
                self.assertIsNone(res.var_fraction)  # never zero-filled

    def test_empty_scenarios_unavailable(self):
        res = calculate_historical_var(_inputs(weights={"A": 1.0}, returns={"A": []}))
        self.assertEqual(res.status, "unavailable")

    def test_invalid_exposure_unavailable(self):
        res = calculate_historical_var(
            _inputs(weights={"A": float("nan")}, returns={"A": [0.01] * 10})
        )
        self.assertEqual(res.status, "unavailable")
        self.assertIn("incomplete_broker_state", res.reasons)


class Worst21Tests(unittest.TestCase):
    def test_worst_uses_last_21(self):
        n = 50
        rets = [0.0] * n
        rets[-1] = -0.07  # large loss in latest scenario
        rets[0] = -0.20  # larger but outside 21-window
        res = calculate_historical_var(_inputs(weights={"A": 1.0}, returns={"A": rets}))
        self.assertAlmostEqual(res.worst_21_fraction, 0.07, places=9)
        # Full-sample 99% VaR with N=50 picks rank 50 -> the max (0.20),
        # while the 21-window worst stays at 0.07.
        self.assertAlmostEqual(res.var_fraction, 0.20, places=9)


if __name__ == "__main__":
    unittest.main()
