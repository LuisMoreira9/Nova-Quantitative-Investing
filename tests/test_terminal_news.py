"""Offline checks for terminal news collection, matching and ranking."""

import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from dashboard.terminal_news import (
    SOURCES,
    backoff_delay,
    canonical_url,
    collect_source,
    match_exposure,
    match_factor,
    parse_retry_after,
    rank_story,
    retention_cutoff,
    story_id,
    _gdelt_items,
    _rss_items,
)


def _now():
    return datetime(2026, 9, 29, 9, 0, tzinfo=timezone.utc)


class CanonicalUrlTest(unittest.TestCase):
    def test_strips_tracking_and_fragment(self):
        self.assertEqual(
            canonical_url("HTTP://Example.COM/a?utm_source=x&b=1#frag"),
            "http://example.com/a?b=1",
        )

    def test_rejects_non_http(self):
        self.assertIsNone(canonical_url("ftp://example.com/a"))
        self.assertIsNone(canonical_url("not a url"))
        self.assertIsNone(canonical_url(""))

    def test_story_id_stable(self):
        self.assertEqual(story_id("http://example.com/a"), story_id("http://example.com/a"))
        self.assertNotEqual(story_id("http://example.com/a"), story_id("http://example.com/b"))


class RegistryTest(unittest.TestCase):
    def test_every_source_has_permission_flags(self):
        self.assertTrue(len(SOURCES) >= 1)
        for source in SOURCES:
            self.assertIn("member_display", source)
            self.assertIn("terms_reviewed", source)
            self.assertIn("attribution", source)

    def test_fail_closed_without_display_flag(self):
        items, error, retry_after, rate_limited = collect_source(
            {"id": "evil", "kind": "rss", "url": "http://example.com"})
        self.assertEqual(items, [])
        self.assertTrue(error)
        self.assertFalse(rate_limited)


class BackoffTest(unittest.TestCase):
    def test_retry_after_seconds(self):
        self.assertEqual(parse_retry_after("120"), 120.0)
        self.assertIsNone(parse_retry_after(None))
        self.assertIsNone(parse_retry_after(""))
        self.assertIsNone(parse_retry_after("not-a-date-or-number"))

    def test_retry_after_http_date(self):
        from email.utils import formatdate
        from time import time
        future = formatdate(time() + 90, usegmt=True)
        parsed = parse_retry_after(future)
        assert parsed is not None
        self.assertGreater(parsed, 0)
        self.assertLessEqual(parsed, 90)

    def test_honours_retry_after_with_cap(self):
        self.assertEqual(backoff_delay(5, 30.0), 30.0)
        self.assertEqual(backoff_delay(1, 99999.0), 1800)

    def test_exponential_without_header(self):
        self.assertEqual(backoff_delay(1, None), 60.0)
        self.assertEqual(backoff_delay(2, None), 120.0)
        self.assertEqual(backoff_delay(10, None), 1800)


class RetentionTest(unittest.TestCase):
    def test_cutoff_is_url_safe_and_round_trips(self):
        from urllib.parse import parse_qsl

        now = datetime(2026, 9, 29, 12, 0, tzinfo=timezone.utc)
        cutoff = retention_cutoff(now)
        self.assertNotIn("+", cutoff)
        self.assertNotIn(" ", cutoff)
        key, value = parse_qsl(f"published_at=lt.{cutoff}")[0]
        self.assertEqual(key, "published_at")
        self.assertTrue(value.startswith("lt.2026-08-30"))
        self.assertIn("+00:00", value)


class ParsingTest(unittest.TestCase):
    def test_gdelt_articles(self):
        body = ('{"articles": [{"title": "Markets rally", "url": "https://n.example/1?utm=x",'
                ' "domain": "n.example", "seendate": "20260929T083000Z"}]}')
        items = _gdelt_items(body, {"id": "g", "attribution": "GDELT"})
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["headline"], "Markets rally")
        self.assertEqual(items[0]["publisher"], "n.example")
        self.assertEqual(items[0]["published_at"],
                         datetime(2026, 9, 29, 8, 30, tzinfo=timezone.utc))

    def test_gdelt_rejects_bad_payload(self):
        with self.assertRaises(ValueError):
            _gdelt_items('{"nope": true}', {"id": "g", "attribution": "GDELT"})

    def test_rss_items(self):
        body = ("<rss><channel><item><title>Rates held</title>"
                "<link>https://ecb.example/p1</link>"
                "<pubDate>Mon, 29 Sep 2026 08:00:00 GMT</pubDate>"
                "<description>Short.</description></item></channel></rss>")
        items = _rss_items(body, {"id": "e", "attribution": "ECB"})
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["snippet"], "Short.")
        self.assertIsNotNone(items[0]["published_at"])


class MatchingTest(unittest.TestCase):
    def test_direct_alias_in_headline(self):
        instrument_id, kind, strength = match_exposure("SPY closes at a record", None)
        self.assertEqual((instrument_id, kind, strength), ("US.SPY", "direct", 1.0))

    def test_no_invented_match(self):
        self.assertEqual(match_exposure("Local bakery wins prize", None), (None, "none", 0.0))


class RankingTest(unittest.TestCase):
    def test_held_instrument_scores_with_evidence(self):
        item = {"headline": "SPY closes at a record", "snippet": None,
                "published_at": datetime(2026, 9, 29, 8, 0, tzinfo=timezone.utc)}
        ranking = rank_story(item, {"US.SPY": 0.14}, {}, {}, {},
                             datetime(2026, 9, 29, 8, 30, tzinfo=timezone.utc),
                             _now(), 0.8)
        self.assertGreater(ranking["score"], 0)
        self.assertLessEqual(ranking["score"], 100)
        self.assertEqual(ranking["mapping_kind"], "direct")
        self.assertIn("held instrument", ranking["explanation"])

    def test_held_listing_symbol_scores_direct(self):
        item = {"headline": "ASML beats expectations", "snippet": None,
                "published_at": datetime(2026, 9, 29, 8, 0, tzinfo=timezone.utc)}
        ranking = rank_story(item, {"ASML": 0.05}, {}, {}, {}, None, _now(), 0.8)
        self.assertGreater(ranking["score"], 0)
        self.assertEqual(ranking["mapping_kind"], "direct")
        self.assertEqual(ranking["instrument_id"], "ASML")

    def test_short_symbols_need_disambiguation(self):
        item = {"headline": "C the light", "snippet": None,
                "published_at": datetime(2026, 9, 29, 8, 0, tzinfo=timezone.utc)}
        ranking = rank_story(item, {"C": 0.05}, {}, {}, {}, None, _now(), 0.8)
        self.assertEqual(ranking["score"], 0.0)

    def test_sector_hit_scores_mapped_with_share(self):
        item = {"headline": "Technology shares lead the rally", "snippet": None,
                "published_at": datetime(2026, 9, 29, 8, 0, tzinfo=timezone.utc)}
        ranking = rank_story(item, {"ASML": 0.05}, {}, {"technology": 0.40}, {}, None, _now(), 0.8)
        self.assertGreater(ranking["score"], 0)
        self.assertEqual(ranking["mapping_kind"], "mapped")
        self.assertIn("technology", ranking["explanation"])

    def test_country_hit_scores_mapped(self):
        item = {"headline": "Netherlands unveils chip fund", "snippet": None,
                "published_at": datetime(2026, 9, 29, 8, 0, tzinfo=timezone.utc)}
        ranking = rank_story(item, {"ASML": 0.05}, {"netherlands": 0.06}, {}, {}, None, _now(), 0.8)
        self.assertGreater(ranking["score"], 0)
        self.assertEqual(ranking["mapping_kind"], "mapped")

    def test_strongest_factor_wins(self):
        share, kind, label = match_factor(
            "Technology shares lead as the Netherlands unveils a fund",
            {"netherlands": 0.06}, {"technology": 0.40}, {})
        self.assertEqual((share, kind, label), (0.40, "mapped", "technology"))

    def test_region_phrases_match_without_bare_codes(self):
        share, kind, label = match_factor("European stocks climb", {}, {}, {"europe ex uk": 0.20})
        self.assertEqual((share, kind, label), (0.20, "mapped", "europe ex uk"))
        self.assertEqual(match_factor("US stocks climb", {}, {}, {"us": 0.50}), (0.0, "none", ""))

    def test_macro_announcement_scores_capped(self):
        item = {"headline": "Federal Reserve holds interest rates steady", "snippet": None,
                "published_at": datetime(2026, 9, 29, 8, 0, tzinfo=timezone.utc)}
        ranking = rank_story(item, {"ASML": 0.05}, {}, {}, {}, None, _now(), 1.0)
        self.assertGreater(ranking["score"], 0)
        self.assertEqual(ranking["mapping_kind"], "mapped")
        self.assertIn("broad-market", ranking["explanation"])
        direct = rank_story(
            {"headline": "ASML beats expectations", "snippet": None,
             "published_at": datetime(2026, 9, 29, 8, 0, tzinfo=timezone.utc)},
            {"ASML": 0.05}, {}, {}, {}, None, _now(), 1.0)
        self.assertGreater(direct["score"], ranking["score"])

    def test_unconnected_story_scores_zero(self):
        item = {"headline": "Local bakery wins prize", "snippet": None,
                "published_at": datetime(2026, 9, 29, 8, 0, tzinfo=timezone.utc)}
        ranking = rank_story(item, {"US.SPY": 0.14}, {}, {}, {}, None, _now(), 0.8)
        self.assertEqual(ranking["score"], 0.0)
        self.assertEqual(ranking["mapping_kind"], "none")

    def test_watched_but_unheld_earns_no_score(self):
        item = {"headline": "Gold steadies as yields fall", "snippet": None,
                "published_at": datetime(2026, 9, 29, 8, 0, tzinfo=timezone.utc)}
        ranking = rank_story(item, {"US.SPY": 0.14}, {}, {}, {}, None, _now(), 0.8)
        self.assertEqual(ranking["score"], 0.0)
        self.assertEqual(ranking["instrument_id"], "US.GLD")


class PublishingTest(unittest.TestCase):
    def test_multiple_items_keep_exposure_factors_after_geography_stamp(self):
        from dashboard.terminal_news import run_cycle

        published = datetime.now(timezone.utc)
        items = [
            {"headline": "SPY closes at a record", "snippet": None,
             "url": "https://example.com/spy", "publisher": "Test",
             "published_at": published},
            {"headline": "Technology shares lead the rally", "snippet": None,
             "url": "https://example.com/technology", "publisher": "Test",
             "published_at": published},
        ]
        source = {"id": "test-feed", "quality": 0.8, "attribution": "Test"}
        with patch("dashboard.terminal_news.news_enabled", return_value=True), \
             patch("dashboard.terminal_news.SOURCES", [source]), \
             patch("dashboard.terminal_news.collect_source",
                   return_value=(items, None, None, False)), \
             patch("dashboard.terminal_news.current_exposure",
                   return_value=({"US.SPY": 0.14}, {}, {"technology": 0.40}, {}, published)), \
             patch("dashboard.supabase_publisher._settings",
                   return_value=("https://example.com", "test-secret")), \
             patch("dashboard.supabase_publisher._write") as write, \
             patch("dashboard.terminal_news.urlopen", return_value=MagicMock()):
            counts = run_cycle(publish=True)

        self.assertEqual(counts, {"items": 2, "ranked": 2,
                                  "sources_ok": 1, "sources_failed": 0})
        writes = [(call.args[2], call.args[3]) for call in write.call_args_list]
        self.assertEqual(sum(table == "terminal_news_relevance" for table, _ in writes), 2)
        stamped = [row for table, row in writes if table == "terminal_news_items"]
        self.assertEqual(len(stamped), 2)
        self.assertEqual(stamped[0]["event_countries"], ["US"])


if __name__ == "__main__":
    unittest.main()
