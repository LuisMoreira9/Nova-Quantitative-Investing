"""Offline checks for terminal news collection, matching and ranking."""

import unittest
from datetime import datetime, timezone

from dashboard.terminal_news import (
    SOURCES,
    backoff_delay,
    canonical_url,
    collect_source,
    match_exposure,
    parse_retry_after,
    rank_story,
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
        ranking = rank_story(item, {"US.SPY": 0.14},
                             datetime(2026, 9, 29, 8, 30, tzinfo=timezone.utc),
                             _now(), 0.8)
        self.assertGreater(ranking["score"], 0)
        self.assertLessEqual(ranking["score"], 100)
        self.assertEqual(ranking["mapping_kind"], "direct")
        self.assertIn("held instrument", ranking["explanation"])

    def test_unconnected_story_scores_zero(self):
        item = {"headline": "Local bakery wins prize", "snippet": None,
                "published_at": datetime(2026, 9, 29, 8, 0, tzinfo=timezone.utc)}
        ranking = rank_story(item, {"US.SPY": 0.14}, None, _now(), 0.8)
        self.assertEqual(ranking["score"], 0.0)
        self.assertEqual(ranking["mapping_kind"], "none")

    def test_watched_but_unheld_earns_no_score(self):
        item = {"headline": "Gold steadies as yields fall", "snippet": None,
                "published_at": datetime(2026, 9, 29, 8, 0, tzinfo=timezone.utc)}
        ranking = rank_story(item, {"US.SPY": 0.14}, None, _now(), 0.8)
        self.assertEqual(ranking["score"], 0.0)
        self.assertEqual(ranking["instrument_id"], "US.GLD")


if __name__ == "__main__":
    unittest.main()
