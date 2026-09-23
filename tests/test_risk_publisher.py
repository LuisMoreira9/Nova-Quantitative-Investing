"""Offline checks for risk payload sanitising and idempotent history keys."""

import unittest

from dashboard.supabase_publisher import (
    public_risk_rows,
    public_snapshot,
    risk_enabled,
)


def _risk(**over):
    base = {
        "status": "ready",
        "portfolio_id": "nova-paper-eur",
        "calculated_at": "2026-09-21T20:30:15+00:00",
        "observed_at": "2026-09-21T20:30:15+00:00",
        "source_portfolio_time": "2026-09-21T20:29:00+00:00",
        "verification_time": "2026-09-21T20:29:00+00:00",
        "history_cutoff": "2026-09-20",
        "confidence": 0.99,
        "horizon": "1d",
        "sample_count": 252,
        "lookback_start": "2025-09-19",
        "lookback_end": "2026-09-19",
        "quantile_convention": "nearest-rank-ceil-0.99",
        "base_currency": "EUR",
        "methodology_version": "hs-1d-99-n252-v1",
        "var_fraction": 0.02,
        "var_eur": 2000.0,
        "worst_21_fraction": 0.015,
        "coverage": {"required": 252, "available": 252},
        "reasons": [],
        "last_valid": None,
        "con_id": 999,  # must never leak (not in allowlist, but ensure)
    }
    base.update(over)
    return base


class PublisherRiskTests(unittest.TestCase):
    def test_schema_version_bumped_and_old_fields_kept(self):
        snap = {
            "account": {"equity": "100000", "cash": "10000", "buying_power": "90000"},
            "base_currency": "EUR",
            "loaded_at": "2026-09-21T20:29:00+00:00",
            "history": [],
            "positions": [
                {
                    "symbol": "AAPL", "market": "NASDAQ", "exchange": "NASDAQ", "currency": "USD",
                    "side": "long", "quantity": 1, "current_price": 100, "market_value": 100,
                    "unrealized_pl": 0, "native_market_value": 100, "native_market_value_base": 90,
                    "fx_to_base": 0.9, "country": "US", "region": "US", "sector": "IT",
                    "con_id": 123, "local_symbol": "AAPL",  # must be stripped
                }
            ],
            "geographic_exposure": [], "sector_exposure": [], "strategy_sleeves": [],
            "strategy_history": [], "fx_hedges": [], "risk": _risk(),
        }
        pub = public_snapshot(snap)
        self.assertEqual(pub["schema_version"], 3)
        self.assertEqual(pub["account"]["equity"], "100000")
        self.assertNotIn("con_id", pub["positions"][0])
        self.assertNotIn("local_symbol", pub["positions"][0])
        self.assertEqual(pub["risk"]["var_fraction"], 0.02)
        self.assertNotIn("con_id", pub["risk"])

    def test_backward_compat_no_risk(self):
        snap = {"account": {}, "positions": [], "risk": None}
        pub = public_snapshot(snap)
        self.assertEqual(pub["schema_version"], 3)
        self.assertIsNone(pub["risk"])

    def test_unavailable_uses_nulls_not_zero(self):
        snap = {"account": {}, "positions": [], "risk": _risk(status="unavailable", var_fraction=None, var_eur=None, worst_21_fraction=None)}
        pub = public_snapshot(snap)
        self.assertIsNone(pub["risk"]["var_fraction"])
        self.assertIsNone(pub["risk"]["var_eur"])

    def test_minute_idempotent_key(self):
        minute, daily = public_risk_rows(_risk(calculated_at="2026-09-21T20:30:15+00:00"))
        assert minute and daily
        self.assertEqual(minute["id"], "nova-paper-eur:2026-09-21T20:30:00+00:00")
        minute2, _ = public_risk_rows(_risk(calculated_at="2026-09-21T20:30:45+00:00"))
        assert minute2
        self.assertEqual(minute2["id"], minute["id"])  # same UTC minute
        self.assertEqual(minute2["calculated_at"], "2026-09-21T20:30:45+00:00")  # timestamp preserved per attempt

    def test_minute_key_follows_observation_time(self):
        # Reused calculations keep calculated_at; the observation minute still advances.
        minute, _ = public_risk_rows(_risk(
            calculated_at="2026-09-21T20:30:15+00:00",
            observed_at="2026-09-21T20:35:10+00:00",
        ))
        assert minute
        self.assertEqual(minute["id"], "nova-paper-eur:2026-09-21T20:35:00+00:00")
        self.assertEqual(minute["calculated_at"], "2026-09-21T20:30:15+00:00")

    def test_daily_key(self):
        _, daily = public_risk_rows(_risk(calculated_at="2026-09-21T20:30:15+00:00"))
        assert daily
        self.assertEqual(daily["id"], "nova-paper-eur:2026-09-21")

    def test_risk_enabled_switch_exists(self):
        self.assertTrue(isinstance(risk_enabled(), bool))


if __name__ == "__main__":
    unittest.main()
