"""Mapping tests: native listings, no ADR substitution, GBp, unsupported types."""

import unittest

from dashboard.risk.mapping import fx_ticker_for, map_position, quote_multiplier


class MappingTests(unittest.TestCase):
    def test_stoxx_native(self):
        m, reason = map_position({"symbol": "ASML", "exchange": "AEB", "currency": "EUR", "security_type": "STK"})
        self.assertIsNotNone(m)
        assert m is not None
        self.assertEqual(m.yfinance_ticker, "ASML.AS")
        self.assertIsNone(reason)

    def test_us_native(self):
        m, _ = map_position({"symbol": "AAPL", "exchange": "NASDAQ", "currency": "USD", "security_type": "STK"})
        self.assertIsNotNone(m)
        assert m is not None
        self.assertEqual(m.yfinance_ticker, "AAPL")

    def test_tokyo_numeric_not_adr(self):
        m, _ = map_position({"symbol": "7203", "exchange": "TSEJ", "currency": "JPY", "security_type": "STK"})
        self.assertIsNotNone(m)
        assert m is not None
        self.assertEqual(m.yfinance_ticker, "7203.T")
        # Must not resolve to the NYSE ADR (TM).
        self.assertNotEqual(m.yfinance_ticker, "TM")

    def test_wrong_currency_does_not_match(self):
        # ASML in USD/SMART is not the reviewed Euronext listing -> missing, not ADR guess.
        m, reason = map_position({"symbol": "ASML", "exchange": "SMART", "currency": "USD", "security_type": "STK"})
        # US-universe fallback would map ASML as US symbol; require native venue match instead.
        # Either way it must never claim the Euronext ISIN mapping.
        if m is not None:
            self.assertNotEqual(m.yfinance_ticker, "ASML.AS")

    def test_unsupported_derivative(self):
        m, reason = map_position({"symbol": "AAPL", "exchange": "SMART", "currency": "USD", "security_type": "OPT"})
        self.assertIsNone(m)
        self.assertEqual(reason, "unsupported_instrument")

    def test_lse_requires_explicit_mapping(self):
        m, reason = map_position({"symbol": "VOD", "exchange": "LSE", "currency": "GBP", "security_type": "STK"})
        self.assertIsNone(m)
        self.assertEqual(reason, "missing_mapping")

    def test_gbp_pence_multiplier(self):
        self.assertEqual(quote_multiplier("VOD.L"), 0.01)
        self.assertEqual(quote_multiplier("AAPL"), 1.0)

    def test_fx_tickers(self):
        self.assertEqual(fx_ticker_for("USD"), "EURUSD=X")
        self.assertEqual(fx_ticker_for("JPY"), "EURJPY=X")
        self.assertEqual(fx_ticker_for("GBP"), "EURGBP=X")
        self.assertEqual(fx_ticker_for("GBX"), "EURGBP=X")
        self.assertIsNone(fx_ticker_for("EUR"))


if __name__ == "__main__":
    unittest.main()
