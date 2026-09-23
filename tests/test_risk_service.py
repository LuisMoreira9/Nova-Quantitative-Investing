"""Service-orchestration and recovery regression tests (offline, network-patched)."""

from __future__ import annotations

import math
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from dashboard.risk import store as risk_store
from dashboard.risk.service import RiskService

from dashboard.supabase_publisher import public_risk_rows


def _end_yesterday() -> "date":
    from datetime import date

    return date.today() - timedelta(days=1)


def _dates(n: int):
    end = datetime.now(timezone.utc).date() - timedelta(days=1)
    return [(end - timedelta(days=n - 1 - i)).isoformat() for i in range(n)]


def _series(dates, start=100.0, drift=-0.01):
    rows = []
    price = start
    for d in dates:
        rows.append((d, price))
        price *= 1 + drift
    return rows


def _seed(service: RiskService, tickers, n=300, drift=-0.01, start=100.0, quote=None):
    dates = _dates(n)
    for t in tickers:
        if quote is not None and t.endswith("=X"):
            service._price_cache[t.upper()] = [(d, quote) for d in dates]
        else:
            service._price_cache[t.upper()] = _series(dates, start=start, drift=drift)
    service._last_fetch_complete_at = time.time()


def _snap(positions, cash=None, equity="1000", loaded_at=None):
    return {
        "positions": positions,
        "currency_cash": cash or {},
        "account": {"equity": equity},
        "base_currency": "EUR",
        "loaded_at": loaded_at or datetime.now(timezone.utc).isoformat(),
    }


def _pos(symbol="AAPL", exchange="NASDAQ", currency="USD", qty=10.0, price=10.0, mv=100.0):
    return {
        "symbol": symbol,
        "exchange": exchange,
        "currency": currency,
        "security_type": "STK",
        "quantity": qty,
        "current_price": price,
        "market_value": mv,
        "con_id": 1,
    }


def _no_network():
    return patch("dashboard.risk.service.fetch_daily_closes", side_effect=ValueError("offline"))


class ForeignValuationTests(unittest.TestCase):
    def test_usd_security_weight_uses_verified_eur_value(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = RiskService(db_path=Path(tmp) / "risk.db")
            # USD 100 native at EUR-per-USD 0.5 -> EUR 50 weight vs EUR 1000 equity = 5%.
            _seed(service, ["AAPL", "EURUSD=X"], quote=2.0)
            with _no_network():
                risk = service.compute_from_snapshot(_snap([_pos()]))
            self.assertEqual(risk["status"], "ready")
            # Constant -1% local drift, flat FX -> combined -1% daily loss on 5% weight.
            self.assertAlmostEqual(risk["var_fraction"], 0.05 * 0.01, places=9)
            self.assertAlmostEqual(risk["var_eur"], 0.05 * 0.01 * 1000.0, places=6)

    def test_missing_fx_is_unavailable_not_defaulted(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = RiskService(db_path=Path(tmp) / "risk.db")
            _seed(service, ["AAPL"])  # no EURUSD=X
            with _no_network():
                risk = service.compute_from_snapshot(_snap([_pos()]))
            self.assertEqual(risk["status"], "unavailable")
            self.assertIsNone(risk["var_fraction"])
            self.assertIn("unavailable_fx", risk["reasons"])


class CashOnlyTests(unittest.TestCase):
    def test_eur_cash_only_is_valid_zero_without_prices(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = RiskService(db_path=Path(tmp) / "risk.db")
            with _no_network():
                risk = service.compute_from_snapshot(_snap([], cash={"EUR": 5000.0}))
            self.assertEqual(risk["status"], "ready")
            self.assertEqual(risk["var_fraction"], 0.0)
            self.assertEqual(risk["var_eur"], 0.0)
            self.assertFalse(service._fetching)


class FreshnessTests(unittest.TestCase):
    def test_stale_history_sets_refresh_pending_but_stays_available(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = RiskService(db_path=Path(tmp) / "risk.db")
            # History ending 30 days ago: 252 scenarios exist but are stale.
            old_end = datetime.now(timezone.utc).date() - timedelta(days=30)
            dates = [(old_end - timedelta(days=299 - i)).isoformat() for i in range(300)]
            service._price_cache["AAPL"] = _series(dates)
            service._price_cache["EURUSD=X"] = [(d, 2.0) for d in dates]
            service._last_fetch_complete_at = 0.0
            with _no_network():
                risk = service.compute_from_snapshot(_snap([_pos()]))
            # Explicitly downgraded: values kept, but flagged stale with a warning.
            self.assertEqual(risk["status"], "stale")
            self.assertIn("stale_history", risk["reasons"])
            self.assertTrue(risk["coverage"]["refresh_pending"])

    def test_dataset_change_forces_recalculation(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = RiskService(db_path=Path(tmp) / "risk.db")
            _seed(service, ["AAPL", "EURUSD=X"], quote=2.0)
            with _no_network():
                first = service.compute_from_snapshot(_snap([_pos()]))
                # Same holdings -> reuse keeps the original calculation time.
                second = service.compute_from_snapshot(
                    _snap([_pos()], loaded_at=datetime.now(timezone.utc).isoformat())
                )
            self.assertEqual(first["calculated_at"], second["calculated_at"])
            # New trading day arrives -> dataset version changes -> recalculation.
            new_date = (datetime.now(timezone.utc).date() - timedelta(days=0)).isoformat()
            for ticker in ("AAPL", "EURUSD=X"):
                rows = service._price_cache[ticker]
                last_close = rows[-1][1]
                rows.append((new_date, last_close * 0.99))
            with _no_network():
                third = service.compute_from_snapshot(
                    _snap([_pos()], loaded_at=datetime.now(timezone.utc).isoformat())
                )
            self.assertNotEqual(third["calculated_at"], first["calculated_at"])
            self.assertNotEqual(
                third["coverage"]["dataset_version"], first["coverage"]["dataset_version"]
            )


class ReuseTimestampTests(unittest.TestCase):
    def test_reuse_refreshes_verification_and_observation_times(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = RiskService(db_path=Path(tmp) / "risk.db")
            _seed(service, ["AAPL", "EURUSD=X"], quote=2.0)
            now = datetime.now(timezone.utc)
            t1 = (now - timedelta(seconds=60)).isoformat()
            t2 = (now - timedelta(seconds=30)).isoformat()
            with _no_network():
                first = service.compute_from_snapshot(_snap([_pos()], loaded_at=t1))
                second = service.compute_from_snapshot(_snap([_pos()], loaded_at=t2))
            self.assertEqual(first["status"], "ready")
            self.assertEqual(second["calculated_at"], first["calculated_at"])
            self.assertEqual(second["verification_time"], t2)
            self.assertEqual(second["source_portfolio_time"], t2)
            self.assertNotEqual(second["observed_at"], first["observed_at"])

    def test_minute_key_follows_observation_not_calculation(self):
        base = {
            "status": "ready", "portfolio_id": "nova-paper-eur",
            "calculated_at": "2026-09-21T20:30:15+00:00",
            "verification_time": "2026-09-21T20:35:00+00:00",
            "history_cutoff": "2026-09-20", "sample_count": 252,
            "var_fraction": 0.02, "var_eur": 2000.0, "worst_21_fraction": 0.015,
            "reasons": [], "coverage": {},
        }
        early = dict(base, observed_at="2026-09-21T20:30:15+00:00")
        late = dict(base, observed_at="2026-09-21T20:35:10+00:00")
        minute_early, _ = public_risk_rows(early)
        minute_late, _ = public_risk_rows(late)
        assert minute_early and minute_late
        self.assertNotEqual(minute_early["id"], minute_late["id"])
        self.assertEqual(minute_early["calculated_at"], minute_late["calculated_at"])


class QueueTests(unittest.TestCase):
    def test_daily_queue_keeps_newest_and_ignores_older(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = risk_store.connect(Path(tmp) / "risk.db")
            try:
                key = "nova-paper-eur:2026-09-21"
                risk_store.queue_pending(conn, key, {"observed_at": "2026-09-21T10:00:00+00:00", "v": 1})
                risk_store.queue_pending(conn, key, {"observed_at": "2026-09-21T16:00:00+00:00", "v": 2})
                pending = risk_store.list_pending(conn)
                self.assertEqual(len(pending), 1)
                self.assertEqual(pending[0][1]["v"], 2)
                # A delayed older retry must not overwrite the newer staged record.
                risk_store.queue_pending(conn, key, {"observed_at": "2026-09-21T09:00:00+00:00", "v": 0})
                pending = risk_store.list_pending(conn)
                self.assertEqual(pending[0][1]["v"], 2)
            finally:
                conn.close()

    def test_minute_retry_keeps_original_timestamps(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = risk_store.connect(Path(tmp) / "risk.db")
            try:
                key = "nova-paper-eur:2026-09-21T20:30:00+00:00"
                risk_store.queue_pending(conn, key, {"observed_at": "2026-09-21T20:30:15+00:00"})
                risk_store.queue_pending(conn, key, {"observed_at": "2026-09-21T20:30:15+00:00"})
                pending = risk_store.list_pending(conn)
                self.assertEqual(len(pending), 1)
                self.assertEqual(pending[0][1]["observed_at"], "2026-09-21T20:30:15+00:00")
            finally:
                conn.close()


class PersistenceTests(unittest.TestCase):
    def test_prices_and_last_valid_survive_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "risk.db"
            service = RiskService(db_path=db)
            _seed(service, ["AAPL", "EURUSD=X"], quote=2.0)
            with _no_network():
                risk = service.compute_from_snapshot(_snap([_pos()]))
            self.assertEqual(risk["status"], "ready")
            # Fresh process with no network must still calculate from disk.
            service2 = RiskService(db_path=db)
            self.assertIn("AAPL", service2._price_cache)
            self.assertIsNotNone(service2._last_valid)
            with _no_network():
                risk2 = service2.compute_from_snapshot(_snap([_pos()]))
            self.assertEqual(risk2["status"], "ready")
            self.assertAlmostEqual(risk2["var_fraction"], risk["var_fraction"], places=9)

    def test_audit_trail_recorded(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "risk.db"
            service = RiskService(db_path=db)
            _seed(service, ["AAPL", "EURUSD=X"], quote=2.0)
            with _no_network():
                service.compute_from_snapshot(_snap([_pos()]))
            conn = risk_store.connect(db)
            try:
                rows = conn.execute("SELECT COUNT(*) FROM audit_inputs").fetchone()
                self.assertGreater(rows[0], 0)
            finally:
                conn.close()


class PublisherOutageTests(unittest.TestCase):
    def _risk(self, observed_at="2026-09-21T20:30:15+00:00"):
        return {
            "status": "ready", "portfolio_id": "nova-paper-eur",
            "calculated_at": observed_at, "observed_at": observed_at,
            "source_portfolio_time": observed_at, "verification_time": observed_at,
            "history_cutoff": "2026-09-20", "sample_count": 252,
            "lookback_start": "2025-09-19", "lookback_end": "2026-09-19",
            "methodology_version": "hs-1d-99-n252-v1", "base_currency": "EUR",
            "var_fraction": 0.02, "var_eur": 2000.0, "worst_21_fraction": 0.015,
            "reasons": [], "coverage": {},
        }

    def test_failed_portfolio_write_still_stages_risk(self):
        from dashboard import supabase_publisher as pub

        with tempfile.TemporaryDirectory() as tmp:
            real_connect = risk_store.connect
            tmp_db = Path(tmp) / "risk.db"
            with patch("dashboard.risk.store.connect", side_effect=lambda *a, **k: real_connect(tmp_db)):
                with patch.object(pub, "_write", side_effect=RuntimeError("outage")):
                    with patch.dict("os.environ", {"SUPABASE_URL": "https://x.supabase.co", "SUPABASE_SERVICE_ROLE_KEY": "k"}):
                        snapshot = {
                            "account": {}, "positions": [], "history": [],
                            "geographic_exposure": [], "sector_exposure": [],
                            "strategy_sleeves": [], "strategy_history": [],
                            "fx_hedges": [], "risk": self._risk(),
                        }
                        with self.assertRaises(RuntimeError):
                            pub.publish_snapshot(snapshot)
                conn = risk_store.connect(Path(tmp) / "risk.db")
                try:
                    pending = risk_store.list_pending(conn, limit=10)
                finally:
                    conn.close()
                # Minute + daily observations staged despite the failed write.
                self.assertEqual(len(pending), 2)

    def test_flush_recovers_queued_rows_in_order(self):
        from dashboard import supabase_publisher as pub

        with tempfile.TemporaryDirectory() as tmp:
            calls: list[str] = []

            def ok_write(base_url, secret, table, payload, *, upsert=False):
                calls.append(f"{table}:{payload['id'] if isinstance(payload, dict) else '?'}")

            real_connect = risk_store.connect
            tmp_db = Path(tmp) / "risk.db"
            with patch("dashboard.risk.store.connect", side_effect=lambda *a, **k: real_connect(tmp_db)):
                with patch.object(pub, "_write", side_effect=ok_write):
                    pub.publish_risk(self._risk("2026-09-21T20:30:15+00:00"), "https://x", "k")
                conn = risk_store.connect(Path(tmp) / "risk.db")
                try:
                    pending = risk_store.list_pending(conn, limit=10)
                finally:
                    conn.close()
                self.assertEqual(pending, [])
                # Daily flushed before minute history.
                self.assertEqual(len(calls), 2)
                self.assertTrue(calls[0].startswith("portfolio_risk_daily:"))
                self.assertTrue(calls[1].startswith("portfolio_risk_history:"))

    def test_publisher_rows_carry_observed_at_through_queue(self):
        # Full path: generated rows (not hand-made payloads) must keep the
        # comparison timestamp so a newer daily observation replaces an older one.
        from dashboard import supabase_publisher as pub

        with tempfile.TemporaryDirectory() as tmp:
            real_connect = risk_store.connect
            tmp_db = Path(tmp) / "risk.db"
            with patch("dashboard.risk.store.connect", side_effect=lambda *a, **k: real_connect(tmp_db)):
                early = dict(self._risk("2026-09-21T10:00:15+00:00"))
                late = dict(self._risk("2026-09-21T16:00:10+00:00"))
                minute_early, daily_early = pub.public_risk_rows(early)
                minute_late, daily_late = pub.public_risk_rows(late)
                assert minute_early and daily_early and minute_late and daily_late
                self.assertIn("observed_at", daily_early)
                pub._queue_rows(minute_early, daily_early)
                pub._queue_rows(minute_late, daily_late)
                conn = real_connect(tmp_db)
                try:
                    pending = {key: payload for key, payload, _ in risk_store.list_pending(conn, limit=10)}
                finally:
                    conn.close()
                # Same UTC date: the 16:00 observation replaced 10:00.
                self.assertEqual(
                    pending["nova-paper-eur:2026-09-21"]["observed_at"], "2026-09-21T16:00:10+00:00"
                )
                # Different UTC minutes: both observations kept.
                self.assertIn("nova-paper-eur:2026-09-21T10:00:00+00:00", pending)
                self.assertIn("nova-paper-eur:2026-09-21T16:00:00+00:00", pending)

    def test_drain_has_strict_time_budget(self):
        from dashboard import supabase_publisher as pub

        with tempfile.TemporaryDirectory() as tmp:
            real_connect = risk_store.connect
            tmp_db = Path(tmp) / "risk.db"
            conn = real_connect(tmp_db)
            try:
                for i in range(5):
                    risk_store.queue_pending(
                        conn, f"nova-paper-eur:2026-09-2{i}T00:00:00+00:00",
                        {"observed_at": f"2026-09-2{i}T00:00:00+00:00"},
                    )
            finally:
                conn.close()

            def slow_write(base_url, secret, table, payload, *, upsert=False):
                time.sleep(0.3)

            started = time.monotonic()
            with patch("dashboard.risk.store.connect", side_effect=lambda *a, **k: real_connect(tmp_db)):
                with patch.object(pub, "_write", side_effect=slow_write):
                    flushed = pub._flush_queue("https://x", "k", budget_seconds=0.5, max_rows=100)
            elapsed = time.monotonic() - started
            self.assertLess(flushed, 5)
            self.assertLess(elapsed, 5.0)
            conn = real_connect(tmp_db)
            try:
                remaining = risk_store.list_pending(conn, limit=10)
            finally:
                conn.close()
            self.assertEqual(len(remaining), 5 - flushed)

    def test_drain_stops_on_transport_failure(self):
        from dashboard import supabase_publisher as pub

        with tempfile.TemporaryDirectory() as tmp:
            real_connect = risk_store.connect
            tmp_db = Path(tmp) / "risk.db"
            conn = real_connect(tmp_db)
            try:
                for i in range(3):
                    risk_store.queue_pending(
                        conn, f"nova-paper-eur:2026-09-2{i}T00:00:00+00:00",
                        {"observed_at": f"2026-09-2{i}T00:00:00+00:00"},
                    )
            finally:
                conn.close()

            def down_write(base_url, secret, table, payload, *, upsert=False):
                err = RuntimeError("Could not reach Supabase: down")
                err.transport_error = True  # type: ignore[attr-defined]
                raise err

            with patch("dashboard.risk.store.connect", side_effect=lambda *a, **k: real_connect(tmp_db)):
                with patch.object(pub, "_write", side_effect=down_write):
                    flushed = pub._flush_queue("https://x", "k")
            self.assertEqual(flushed, 0)
            conn = real_connect(tmp_db)
            try:
                remaining = risk_store.list_pending(conn, limit=10)
            finally:
                conn.close()
            # First row attempted once, the rest untouched: drain stopped fast.
            attempts = sorted(a for _, _, a in remaining)
            self.assertEqual(attempts, [0, 0, 1])


class BootstrapExceptionTests(unittest.TestCase):
    def _service_with_short_history(self, tmp, n=230):
        from dashboard.risk.service import RiskService

        service = RiskService(db_path=Path(tmp) / "risk.db")
        _seed(service, ["AAPL", "EURUSD=X"], n=n, quote=2.0)
        return service

    def test_reduced_window_unavailable_by_default(self):
        import os

        with tempfile.TemporaryDirectory() as tmp:
            service = self._service_with_short_history(tmp)
            with _no_network():
                with patch.dict(os.environ, {}, clear=False):
                    os.environ.pop("NOVA_RISK_BOOTSTRAP_EXCEPTION", None)
                    risk = service.compute_from_snapshot(_snap([_pos()]))
            self.assertEqual(risk["status"], "unavailable")
            self.assertIn("insufficient_history", risk["reasons"])

    def test_reduced_window_allowed_with_labeled_exception(self):
        import os

        with tempfile.TemporaryDirectory() as tmp:
            service = self._service_with_short_history(tmp)
            with _no_network():
                with patch.dict(os.environ, {"NOVA_RISK_BOOTSTRAP_EXCEPTION": "1"}):
                    risk = service.compute_from_snapshot(_snap([_pos()]))
            self.assertEqual(risk["status"], "ready")
            self.assertTrue(risk["methodology_version"].endswith("-bootstrap"))
            self.assertIn("bootstrap_exception", risk["reasons"])
            self.assertEqual(risk["coverage"]["required"], 252)
            self.assertGreaterEqual(risk["sample_count"], 120)
            self.assertLess(risk["sample_count"], 252)

    def test_bootstrap_floor_still_unavailable(self):
        import os

        with tempfile.TemporaryDirectory() as tmp:
            service = self._service_with_short_history(tmp, n=100)
            with _no_network():
                with patch.dict(os.environ, {"NOVA_RISK_BOOTSTRAP_EXCEPTION": "1"}):
                    risk = service.compute_from_snapshot(_snap([_pos()]))
            self.assertEqual(risk["status"], "unavailable")

    def test_full_window_uses_plan_methodology_despite_flag(self):
        import os

        with tempfile.TemporaryDirectory() as tmp:
            service = self._service_with_short_history(tmp, n=300)
            with _no_network():
                with patch.dict(os.environ, {"NOVA_RISK_BOOTSTRAP_EXCEPTION": "1"}):
                    risk = service.compute_from_snapshot(_snap([_pos()]))
            self.assertEqual(risk["status"], "ready")
            self.assertFalse(risk["methodology_version"].endswith("-bootstrap"))
            self.assertNotIn("bootstrap_exception", risk["reasons"])


class PriceCorrectionTests(unittest.TestCase):
    def test_same_date_price_correction_invalidates_reuse(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = RiskService(db_path=Path(tmp) / "risk.db")
            _seed(service, ["AAPL", "EURUSD=X"], quote=2.0)
            with _no_network():
                first = service.compute_from_snapshot(_snap([_pos()]))
            self.assertEqual(first["status"], "ready")
            # Yahoo adjusted-close revision: identical dates, -1% becomes -2%.
            dates = _dates(300)
            service._price_cache["AAPL"] = _series(dates, drift=-0.02)
            with _no_network():
                second = service.compute_from_snapshot(
                    _snap([_pos()], loaded_at=datetime.now(timezone.utc).isoformat())
                )
            self.assertNotEqual(
                second["coverage"]["dataset_version"], first["coverage"]["dataset_version"]
            )
            self.assertNotEqual(second["calculated_at"], first["calculated_at"])
            self.assertAlmostEqual(second["var_fraction"], 2 * first["var_fraction"], places=9)


class HistoryAgeTests(unittest.TestCase):
    def _history_ending(self, days_ago: int):
        end = datetime.now(timezone.utc).date() - timedelta(days=days_ago)
        return [(end - timedelta(days=299 - i)).isoformat() for i in range(300)]

    def test_weekend_fresh_data_stays_ready(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = RiskService(db_path=Path(tmp) / "risk.db")
            dates = self._history_ending(2)
            service._price_cache["AAPL"] = _series(dates)
            service._price_cache["EURUSD=X"] = [(d, 2.0) for d in dates]
            service._last_fetch_complete_at = time.time()
            with _no_network():
                risk = service.compute_from_snapshot(_snap([_pos()]))
            self.assertEqual(risk["status"], "ready")
            self.assertNotIn("stale_history", risk["reasons"])

    def test_101_day_old_history_is_explicitly_stale(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = RiskService(db_path=Path(tmp) / "risk.db")
            dates = self._history_ending(101)
            service._price_cache["AAPL"] = _series(dates)
            service._price_cache["EURUSD=X"] = [(d, 2.0) for d in dates]
            service._last_fetch_complete_at = 0.0
            with _no_network():
                risk = service.compute_from_snapshot(_snap([_pos()]))
            self.assertEqual(risk["status"], "stale")
            self.assertIn("stale_history", risk["reasons"])
            # Values are preserved (downgrade, not withdrawal).
            self.assertIsNotNone(risk["var_fraction"])
            self.assertGreater(risk["coverage"]["history_age_days"], 100)


class CollectionCompletenessTests(unittest.TestCase):
    def test_missing_positions_and_cash_is_not_zero_risk(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = RiskService(db_path=Path(tmp) / "risk.db")
            snapshot = {
                "account": {"equity": "1000"},
                "base_currency": "EUR",
                "loaded_at": datetime.now(timezone.utc).isoformat(),
            }
            with _no_network():
                risk = service.compute_from_snapshot(snapshot)
            self.assertEqual(risk["status"], "unavailable")
            self.assertIsNone(risk["var_fraction"])
            self.assertIn("incomplete_broker_state", risk["reasons"])

    def test_explicit_empty_portfolio_is_valid_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = RiskService(db_path=Path(tmp) / "risk.db")
            snapshot = {
                "positions": [],
                "currency_cash": {},
                "account": {"equity": "1000"},
                "base_currency": "EUR",
                "loaded_at": datetime.now(timezone.utc).isoformat(),
            }
            with _no_network():
                risk = service.compute_from_snapshot(snapshot)
            self.assertEqual(risk["status"], "ready")
            self.assertEqual(risk["var_fraction"], 0.0)


class LastValidRecoveryTests(unittest.TestCase):
    def test_last_valid_survives_many_unavailable_observations(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "risk.db"
            service = RiskService(db_path=db)
            _seed(service, ["AAPL", "EURUSD=X"], quote=2.0)
            with _no_network():
                ready = service.compute_from_snapshot(_snap([_pos()]))
            self.assertEqual(ready["status"], "ready")
            # 55 subsequent unavailable observations (failed collection).
            bad = _snap([_pos()], equity="0")
            with _no_network():
                for _ in range(55):
                    service.compute_from_snapshot(bad)
            service2 = RiskService(db_path=db)
            self.assertIsNotNone(service2._last_valid)
            self.assertAlmostEqual(
                service2._last_valid["var_fraction"], ready["var_fraction"], places=9
            )


if __name__ == "__main__":
    unittest.main()
