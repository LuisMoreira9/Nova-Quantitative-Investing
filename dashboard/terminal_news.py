"""Terminal news collector: approved feeds in, ranked member news out.

Runs beside (never inside) the trading executors and the portfolio
publisher: ``python -m dashboard.terminal_news --watch``. It never touches
TWS, never submits orders, and only makes outbound HTTPS requests.

Pipeline per approved source: fetch with timeout and bounded body, parse
only supported content (GDELT JSON, RSS/Atom XML), canonicalise URLs,
deduplicate exact matches, rules-match against the exposure catalogue,
rank portfolio relevance, and publish permitted fields with evidence.
Full article bodies are never retrieved or stored.

Semantic grouping and controlled-theme matching arrive with the local
embedding host (desktop); until then every row is rules-only
(``ranking_version`` identifies the rules revision) and the map/list UI stays
useful without it.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from hashlib import sha256
from time import sleep
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse
from urllib.request import Request, urlopen


RANKING_VERSION = "terminal-rank-4-rules"

# Actual policy/rates topics, not the publisher name: routine approvals and
# enforcement notices do not establish a broad-market portfolio connection.
MACRO_KEYWORDS: tuple[str, ...] = (
    "fomc", "policy rate", "rate decision", "economic projections",
    "quantitative easing", "quantitative tightening",
    "interest rate", "interest rates", "treasury yield", "bond yield",
    "inflation", "monetary policy",
)
MACRO_SHARE_CAP = 0.30
# Policy mandate, never article event location. Reviewed 2026-10-07:
# https://www.ecb.europa.eu/euro/intro/html/index.en.html (BG joined Jan 2026)
# https://www.federalreserve.gov/monetarypolicy/principles-for-the-conduct-of-monetary-policy.htm
POLICY_COUNTRIES = {
    "fed-press": ("US",),
    "ecb-press": ("AT", "BE", "BG", "HR", "CY", "EE", "FI", "FR", "DE",
                  "GR", "IE", "IT", "LV", "LT", "LU", "MT", "NL", "PT",
                  "SK", "SI", "ES"),
}
EXPOSURE_CATALOGUE_VERSION = "terminal-exposure-1"
FETCH_TIMEOUT_SECONDS = 15
MAX_BODY_BYTES = 2 * 1024 * 1024
MAX_ITEMS_PER_CYCLE = 100
FRESHNESS_HALF_LIFE_HOURS = 12.0
RETENTION_DAYS = 30

TRACKING_PARAMS = frozenset({
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "fbclid", "gclid", "msclkid", "mc_cid", "mc_eid", "ref", "source",
})

# Versioned source registry. Permission flags mirror reviewed terms and fail
# closed: a source without member_display may not feed member panels.
# Terms reviewed 2026-09-29. GDELT is a discovery index (link out, keep the
# headline); ECB/Fed feeds are official announcements.
SOURCES: tuple[dict[str, Any], ...] = (
    {
        "id": "gdelt-markets",
        "kind": "gdelt",
        "url": ("https://api.gdeltproject.org/api/v2/doc/doc?query=%22stock%20market%22%20OR%20"
                "%22central%20bank%22%20OR%20economy%20sourcelang%3Aenglish&mode=artlist"
                "&maxrecords=50&format=json"),
        "attribution": "GDELT Project 2.1",
        "quality": 0.8,
        "member_display": True,
        "public_display": False,
        "retention_days": RETENTION_DAYS,
        "terms_reviewed": "2026-09-29",
    },
    {
        "id": "ecb-press",
        "kind": "rss",
        "url": "https://www.ecb.europa.eu/rss/press.html",
        "attribution": "European Central Bank",
        "quality": 1.0,
        "member_display": True,
        "public_display": False,
        "retention_days": RETENTION_DAYS,
        "terms_reviewed": "2026-09-29",
    },
    {
        "id": "fed-press",
        "kind": "rss",
        "url": "https://www.federalreserve.gov/feeds/press_all.xml",
        "attribution": "Federal Reserve",
        "quality": 1.0,
        "member_display": True,
        "public_display": False,
        "retention_days": RETENTION_DAYS,
        "terms_reviewed": "2026-09-29",
    },
)

# Built-in exposure catalogue (versioned above). Maps alias text to a stable
# instrument plus broad, sourced mandate facts. ETF look-through is mandate
# level only until dated holdings arrive; never infer from the name.
# Symbol keys double as the query-derived linkage for feed queries.
CATALOGUE: dict[str, dict[str, Any]] = {
    "SPY": {"instrument_id": "US.SPY", "aliases": ("SPY", "S&P 500", "SPDR S&P 500"),
            "sector": "broad equity", "countries": ("US",), "themes": ("us equity",)},
    "VT": {"instrument_id": "US.VT", "aliases": ("VT", "Vanguard Total World",),
           "sector": "broad equity", "countries": ("US", "EU", "JP"), "themes": ("global equity",)},
    "QQQ": {"instrument_id": "US.QQQ", "aliases": ("QQQ", "Nasdaq 100", "Nasdaq-100"),
            "sector": "technology", "countries": ("US",), "themes": ("us equity", "technology")},
    "IWM": {"instrument_id": "US.IWM", "aliases": ("IWM", "Russell 2000"),
            "sector": "small cap equity", "countries": ("US",), "themes": ("us equity",)},
    "EFA": {"instrument_id": "US.EFA", "aliases": ("EFA", "MSCI EAFE"),
            "sector": "broad equity", "countries": ("EU", "JP"), "themes": ("developed equity",)},
    "EEM": {"instrument_id": "US.EEM", "aliases": ("EEM", "MSCI Emerging Markets"),
            "sector": "broad equity", "countries": ("CN", "IN", "BR"), "themes": ("emerging equity",)},
    "GLD": {"instrument_id": "US.GLD", "aliases": ("GLD", "gold"),
            "sector": "commodity", "countries": (), "themes": ("gold", "rates")},
    "USO": {"instrument_id": "US.USO", "aliases": ("USO", "crude oil", "WTI"),
            "sector": "energy", "countries": (), "themes": ("oil", "energy")},
    "TLT": {"instrument_id": "US.TLT", "aliases": ("TLT", "long treasury", "long-term treasury"),
            "sector": "rates", "countries": ("US",), "themes": ("rates", "bonds")},
    "IEF": {"instrument_id": "US.IEF", "aliases": ("IEF", "intermediate treasury"),
            "sector": "rates", "countries": ("US",), "themes": ("rates", "bonds")},
    "BIL": {"instrument_id": "US.BIL", "aliases": ("BIL", "T-bill", "treasury bill"),
            "sector": "rates", "countries": ("US",), "themes": ("rates", "cash")},
    "VNQ": {"instrument_id": "US.VNQ", "aliases": ("VNQ", "REIT", "real estate"),
            "sector": "real estate", "countries": ("US",), "themes": ("real estate",)},
    "XETRA.VBTC": {"instrument_id": "XETRA.VBTC", "aliases": ("VBTC", "bitcoin"),
            "sector": "crypto", "countries": ("DE",), "themes": ("bitcoin",)},
}

# Reverse index for stamping mandate-level geography/themes on items.
INSTRUMENT_INDEX: dict[str, dict[str, Any]] = {
    entry["instrument_id"]: entry for entry in CATALOGUE.values()
}

# Region labels from the club's investable-region convention to matchable
# phrases. Two-letter codes never match raw text (too ambiguous); only the
# phrases below count, and every hit is labeled 'mapped', never 'direct'.
REGION_KEYWORDS: dict[str, tuple[str, ...]] = {
    "us": ("united states", "u.s.", "wall street"),
    "europe ex uk": ("europe", "european"),
    "uk": ("britain", "united kingdom", "london", "ftse"),
    "eu": ("euro area", "eurozone"),
    "asia": ("asia", "asian"),
    "japan": ("japan", "japanese", "nikkei"),
    "china": ("china", "chinese"),
}


def _mentions(haystack: str, phrase: str) -> bool:
    return re.search(rf"(?i)(?<![A-Za-z]){re.escape(phrase)}(?![A-Za-z])", haystack) is not None


def match_factor(haystack: str, countries: dict[str, float],
                 sectors: dict[str, float], regions: dict[str, float]
                 ) -> tuple[float, str, str]:
    """Strongest verified sector/country/region hit: (share, kind, label)."""

    best = (0.0, "none", "")
    for country, share in countries.items():
        if share > best[0] and _mentions(haystack, country):
            best = (share, "mapped", country)
    for sector, share in sectors.items():
        if share > best[0] and _mentions(haystack, sector):
            best = (share, "mapped", sector)
    for region, share in regions.items():
        if share <= best[0]:
            continue
        for phrase in REGION_KEYWORDS.get(region, ()):
            if _mentions(haystack, phrase):
                best = (share, "mapped", region)
                break
    return best


def news_enabled() -> bool:
    """Kill switch: NOVA_TERMINAL_NEWS_ENABLED=0 disables every source."""

    return os.getenv("NOVA_TERMINAL_NEWS_ENABLED", "1").strip().lower() not in {"0", "false", "no", "off"}


def canonical_url(raw: str) -> str | None:
    """Normalise tracking parameters, case, fragments; None when unusable."""

    try:
        parsed = urlparse(raw.strip())
    except Exception:
        return None
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    query = [(k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True)
             if k.lower() not in TRACKING_PARAMS]
    cleaned = parsed._replace(scheme=parsed.scheme.lower(), netloc=parsed.netloc.lower(),
                              query=urlencode(query), fragment="")
    return urlunparse(cleaned)


def story_id(url: str) -> str:
    return sha256(url.encode("utf-8")).hexdigest()


def _parse_gdelt_time(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    for fmt in ("%Y%m%dT%H%M%SZ", "%Y%m%dT%H%M%S"):
        try:
            parsed = datetime.strptime(value.strip(), fmt)
            return parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


MAX_BACKOFF_SECONDS = 1800


def parse_retry_after(value: Any) -> float | None:
    """Seconds from a Retry-After header (delay or HTTP date); None if unusable."""

    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return max(0.0, float(text))
    except ValueError:
        pass
    try:
        from email.utils import parsedate_to_datetime
        moment = parsedate_to_datetime(text)
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        return max(0.0, (moment - datetime.now(timezone.utc)).total_seconds())
    except Exception:
        return None


def backoff_delay(consecutive_rate_limits: int, retry_after: float | None) -> float:
    """429 backoff: honour Retry-After, else exponential from 60s, capped."""

    if retry_after is not None:
        return min(max(retry_after, 1.0), MAX_BACKOFF_SECONDS)
    return min(60.0 * (2.0 ** max(0, consecutive_rate_limits - 1)), MAX_BACKOFF_SECONDS)


# In-memory per-source backoff (restart resets to immediate; health table
# keeps the durable consecutive-failure count for operators).
_backoff_until: dict[str, float] = {}
_rate_limit_streak: dict[str, int] = {}


def _fetch_text(url: str) -> tuple[str | None, str, float | None]:
    """GET with timeout and byte cap; returns (text, error, retry_after)."""

    try:
        request = Request(url, headers={"User-Agent": "NovaQuantClub-terminal-news/1 (+https://novaquantclub.com)"})
        with urlopen(request, timeout=FETCH_TIMEOUT_SECONDS) as response:
            raw = response.read(MAX_BODY_BYTES + 1)
    except HTTPError as exc:
        if exc.code == 429:
            retry_after: float | None = None
            try:
                retry_after = parse_retry_after(exc.headers.get("Retry-After"))
            except Exception:
                retry_after = None
            return None, "http 429", retry_after
        return None, f"http {exc.code}", None
    except URLError as exc:
        return None, f"unreachable: {exc.reason}", None
    except Exception as exc:
        return None, f"fetch failed: {exc}", None
    if len(raw) > MAX_BODY_BYTES:
        return None, "body exceeds 2 MiB cap", None
    try:
        return raw.decode("utf-8", "replace"), "", None
    except Exception as exc:
        return None, f"decode failed: {exc}", None


def _rss_items(body: str, source: dict[str, Any]) -> list[dict[str, Any]]:
    try:
        root = ET.fromstring(body)
    except ET.ParseError as exc:
        raise ValueError(f"unparseable feed: {exc}") from exc
    items: list[dict[str, Any]] = []
    for entry in list(root.iter("item")) + [e for e in root.iter()
                                            if e.tag.endswith("entry")]:
        def text(names: tuple[str, ...]) -> str:
            for child in entry:
                tag = child.tag.split("}")[-1]
                if tag in names and child.text and child.text.strip():
                    return child.text.strip()
            return ""
        link = text(("link",))
        if not link:
            for child in entry:
                if child.tag.split("}")[-1] == "link" and child.get("href"):
                    link = child.get("href", "").strip()
                    break
        title = text(("title",))
        if not title or not link:
            continue
        published: datetime | None = None
        for raw in (text(("pubDate", "published", "updated")),):
            if not raw:
                continue
            try:
                from email.utils import parsedate_to_datetime
                parsed = parsedate_to_datetime(raw)
                published = parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
            except Exception:
                try:
                    published = datetime.fromisoformat(raw.replace("Z", "+00:00"))
                except Exception:
                    published = None
        items.append({
            "headline": title[:500],
            "snippet": text(("description", "summary"))[:1000] or None,
            "url": link,
            "publisher": source["attribution"],
            "publisher_country": None,
            "published_at": published,
            "via_query": None,
        })
    return items


def _gdelt_items(body: str, source: dict[str, Any]) -> list[dict[str, Any]]:
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise ValueError(f"unparseable GDELT payload: {exc}") from exc
    articles = payload.get("articles")
    if not isinstance(articles, list):
        raise ValueError("GDELT payload has no articles list")
    items: list[dict[str, Any]] = []
    for article in articles:
        if not isinstance(article, dict):
            continue
        title = str(article.get("title") or "").strip()
        url = str(article.get("url") or article.get("url_mobile") or "").strip()
        if not title or not url:
            continue
        items.append({
            "headline": title[:500],
            "snippet": None,
            "url": url,
            "publisher": str(article.get("domain") or source["attribution"]).strip()[:200],
            "publisher_country": None,
            "published_at": _parse_gdelt_time(article.get("seendate")),
            "via_query": source["id"],
        })
    return items


def collect_source(source: dict[str, Any]) -> tuple[list[dict[str, Any]], str, float | None, bool]:
    """Fetch and parse one source; (items, error, retry_after, rate_limited)."""

    if not source.get("member_display", False):
        return [], "display not permitted", None, False
    text, error, retry_after = _fetch_text(str(source["url"]))
    if text is None:
        return [], error, retry_after, error == "http 429"
    try:
        if source["kind"] == "gdelt":
            return _gdelt_items(text, source), "", None, False
        if source["kind"] == "rss":
            return _rss_items(text, source), "", None, False
    except ValueError as exc:
        return [], str(exc), None, False
    return [], f"unsupported source kind {source.get('kind')}", None, False


def match_exposure(headline: str, snippet: str | None) -> tuple[str | None, str, float]:
    """Rules-match text to the catalogue: (instrument_id, kind, strength).

    Direct (1.0): an alias appears in the headline or snippet. Mapped (0.6):
    reserved for query-derived linkage applied by the caller. Inferred theme
    matches arrive with the embedding host; rules never invent them.
    """

    haystack = f"{headline}\n{snippet or ''}"
    for key, entry in CATALOGUE.items():
        for alias in entry["aliases"]:
            if re.search(rf"(?i)(?<![A-Za-z]){re.escape(alias)}(?![A-Za-z])", haystack):
                return str(entry["instrument_id"]), "direct", 1.0
    return None, "none", 0.0


def rank_story(item: dict[str, Any], gross_weights: dict[str, float],
               countries: dict[str, float], sectors: dict[str, float],
               regions: dict[str, float],
               snapshot_time: datetime | None, now: datetime,
               source_quality: float) -> dict[str, Any]:
    """Score portfolio relevance 0-100 with evidence; zero when unconnected.

    Tier 1 is a direct whole-word mention of a held listing symbol or a
    catalogue alias (strength 1.0). Tier 2 is a verified sector, country or
    region mention weighted by its gross-exposure share (strength 0.6): the
    strongest applicable match wins, overlapping matches never sum.
    """

    headline = item["headline"]
    snippet = item.get("snippet")
    haystack = f"{headline}\n{snippet or ''}"
    instrument_id, kind, strength = match_exposure(headline, snippet)
    if instrument_id and instrument_id not in gross_weights:
        # Portfolio positions use listing keys (SPY); aliases use stable
        # catalogue identities (US.SPY). Resolve held aliases before ranking.
        instrument_id = next((symbol for symbol, entry in CATALOGUE.items()
                              if entry["instrument_id"] == instrument_id
                              and symbol in gross_weights), instrument_id)
    if (kind != "direct" or (instrument_id and instrument_id not in gross_weights)):
        # Held listing symbols are authoritative even when absent from the
        # catalogue. Symbols shorter than three characters are skipped: they
        # need ticker disambiguation before they can count as evidence.
        direct_symbol = next(
            (symbol for symbol in gross_weights
             if len(symbol) >= 3 and _mentions(haystack, symbol)),
            None,
        )
        if direct_symbol is not None:
            instrument_id, kind, strength = direct_symbol, "direct", 1.0
        elif kind == "direct" and instrument_id:
            # Watched but not held: kept as a mandate-level candidate for the
            # instrument filter, but it earns no relevance score without exposure.
            kind = "mapped"
    exposure_match = gross_weights.get(instrument_id, 0.0) if instrument_id and kind == "direct" else 0.0
    direct_match = 1.0 if kind == "direct" else 0.0
    factor_label = ""
    policy_text = re.sub(r"(?i)in addition to (?:the )?decisions setting interest rates", "", haystack)
    macro_topic = any(_mentions(policy_text, phrase) for phrase in MACRO_KEYWORDS)
    macro_hit = False
    if exposure_match <= 0 and direct_match <= 0:
        # Institution names establish provenance, not regional exposure.
        factor_text = re.sub(r"(?i)\b(?:European Central Bank|Federal Reserve)\b", "", haystack)
        share, factor_kind, factor_label = match_factor(factor_text, countries, sectors, regions)
        if share > 0:
            exposure_match, kind = share * 0.6, factor_kind
        elif macro_topic and any(weight > 0 for weight in gross_weights.values()):
            exposure_match, kind, macro_hit = MACRO_SHARE_CAP, "mapped", True
    published = item.get("published_at")
    if isinstance(published, datetime):
        age_hours = max(0.0, (now - published).total_seconds() / 3600.0)
        freshness = 0.5 ** (age_hours / FRESHNESS_HALF_LIFE_HOURS)
        confidence = 0.8
    else:
        freshness, confidence = 0.5, 0.5
    evidence_quality = max(0.0, min(1.0, source_quality * confidence))
    if exposure_match <= 0 and direct_match <= 0:
        if instrument_id:
            explanation = "Watchlist candidate; no holding, so no relevance score."
        else:
            explanation = "No supported connection to current exposures."
        return {"score": 0.0, "exposure_match": 0.0, "direct_match": 0.0,
                "strategy_match": 0.0, "freshness": round(freshness, 4),
                "evidence_quality": round(evidence_quality, 4), "mapping_kind": "none" if kind == "none" else kind,
                "explanation": explanation, "instrument_id": instrument_id,
                "macro_topic": macro_topic}
    score = 100.0 * evidence_quality * (
        0.50 * exposure_match + 0.25 * direct_match + 0.15 * 0.0 + 0.10 * freshness)
    score = max(0.0, min(100.0, score))
    if kind == "direct":
        explanation = "Direct mention of a held instrument."
    elif macro_hit:
        explanation = ("Rates/macro topic; broad-market "
                       "connection, capped with no slice attribution.")
    elif factor_label:
        explanation = (f"Connected through verified {factor_label} exposure "
                       f"({exposure_match / 0.6:.0%} of gross).")
    else:
        explanation = "Connected through the instrument watchlist; no holding."
    if snapshot_time is not None:
        age_min = max(0, int((now - snapshot_time).total_seconds() // 60))
        explanation += f" Exposure snapshot is {age_min} min old."
    _ = snapshot_time
    return {"score": round(score, 2), "exposure_match": round(exposure_match, 4),
            "direct_match": direct_match, "strategy_match": 0.0,
            "freshness": round(freshness, 4), "evidence_quality": round(evidence_quality, 4),
            "mapping_kind": kind, "explanation": explanation[:1000],
            "instrument_id": instrument_id, "macro_topic": macro_topic}


def mandate_geography(ranking: dict[str, Any], source_id: str
                      ) -> tuple[list[str], list[str]]:
    """Catalogue or official policy mandate; no publisher-HQ inference."""

    instrument = ranking.get("instrument_id") or ""
    entry = INSTRUMENT_INDEX.get(instrument) or CATALOGUE.get(instrument)
    if entry:
        return list(entry.get("countries", ())), list(entry.get("themes", ()))
    if ranking.get("macro_topic") and source_id in POLICY_COUNTRIES:
        return list(POLICY_COUNTRIES[source_id]), ["rates", "geo:central-bank-mandate"]
    return [], []


def _service_get(base_url: str, secret: str, path: str) -> Any:
    request = Request(f"{base_url}/rest/v1/{path}", headers={
        "apikey": secret, "Authorization": f"Bearer {secret}"})
    with urlopen(request, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def current_exposure(base_url: str, secret: str
                    ) -> tuple[dict[str, float], dict[str, float], dict[str, float],
                               dict[str, float], datetime | None]:
    """Exposure shares by symbol, country, sector and region (lowercased)."""

    empty: tuple[dict[str, float], dict[str, float], dict[str, float], dict[str, float], None] = \
        ({}, {}, {}, {}, None)
    try:
        rows = _service_get(base_url, secret,
                            "terminal_portfolio_latest?select=bundle,observed_at&order=published_at.desc&limit=1")
    except Exception:
        return empty
    if not rows:
        return empty
    try:
        bundle = rows[0].get("bundle", {})
        positions = bundle.get("positions", [])
        def gross_value(position: dict[str, Any]) -> float:
            raw = position.get("native_market_value_base")
            if raw is None:
                raw = position.get("market_value")
            try:
                value = float(raw)
            except (TypeError, ValueError):
                return 0.0
            return abs(value) if math.isfinite(value) else 0.0

        total = sum(gross_value(p) for p in positions if isinstance(p, dict))
        if total <= 0:
            return empty
        weights: dict[str, float] = {}
        countries: dict[str, float] = {}
        sectors: dict[str, float] = {}
        regions: dict[str, float] = {}

        def add(mapping: dict[str, float], label: Any, value: float) -> None:
            name = str(label or "").strip().lower()
            if name and name != "unclassified" and value > 0:
                mapping[name] = mapping.get(name, 0.0) + value / total

        for position in positions:
            if not isinstance(position, dict):
                continue
            symbol = str(position.get("symbol") or "").upper()
            value = gross_value(position)
            if symbol and value > 0:
                weights[symbol] = weights.get(symbol, 0.0) + value / total
            add(countries, position.get("country"), value)
            add(sectors, position.get("sector"), value)
            add(regions, position.get("region"), value)
        observed = rows[0].get("observed_at")
        snapshot_time = datetime.fromisoformat(str(observed).replace("Z", "+00:00")) \
            if observed else None
        return weights, countries, sectors, regions, snapshot_time
    except Exception:
        return empty


def retention_cutoff(now: datetime) -> str:
    """Percent-encoded ISO cutoff for the retention delete query."""

    from urllib.parse import quote

    return quote(datetime.fromtimestamp(now.timestamp() - RETENTION_DAYS * 86400,
                                        tz=timezone.utc).isoformat(), safe="")


def run_cycle(*, publish: bool = True) -> dict[str, int]:
    """One collection pass; returns counts. Never raises."""

    from dashboard.supabase_publisher import _settings, _write

    counts = {"items": 0, "ranked": 0, "sources_ok": 0, "sources_failed": 0}
    if not news_enabled():
        return counts
    try:
        base_url, secret = _settings()
    except Exception as exc:
        print(f"News collection skipped (no Supabase credentials): {exc}")
        return counts
    now = datetime.now(timezone.utc)
    # Exposure keyed by listing symbol; catalogue maps aliases to these keys.
    gross_weights, countries, sectors, regions, snapshot_time = current_exposure(base_url, secret)
    for source in SOURCES:
        try:
            source_id = str(source["id"])
            wait_until = _backoff_until.get(source_id, 0.0)
            if now.timestamp() < wait_until:
                # Rate-limited earlier: skip quietly, keep stored rows serving.
                continue
            items, error, retry_after, rate_limited = collect_source(source)
            if rate_limited:
                streak = _rate_limit_streak.get(source_id, 0) + 1
                _rate_limit_streak[source_id] = streak
                delay = backoff_delay(streak, retry_after)
                _backoff_until[source_id] = now.timestamp() + delay
                status, detail = "degraded", f"http 429: backing off {int(delay)}s (streak {streak})"
                counts["sources_failed"] += 1
            elif error:
                _rate_limit_streak.pop(source_id, None)
                status, detail = "down", error
                counts["sources_failed"] += 1
            else:
                _rate_limit_streak.pop(source_id, None)
                _backoff_until.pop(source_id, None)
                status, detail = "ok", f"attribution: {source['attribution']}"
                counts["sources_ok"] += 1
            if publish:
                try:
                    _write(base_url, secret, "terminal_source_health", {
                        "source": source["id"],
                        "last_attempt_at": now.isoformat(),
                        "last_success_at": now.isoformat() if not error else None,
                        "status": status,
                        "detail": detail[:1000],
                        "consecutive_failures": 0 if not error else 1,
                        "updated_at": now.isoformat(),
                    }, upsert=True, conflict="source")
                except Exception as exc:
                    print(f"Source health write failed for {source['id']}: {exc}")
            if error:
                continue
            for item in items[:MAX_ITEMS_PER_CYCLE]:
                url = canonical_url(item["url"])
                if not url:
                    continue
                ranking = rank_story(item, gross_weights, countries, sectors, regions,
                                   snapshot_time, now, float(source["quality"]))
                if not publish:
                    counts["items"] += 1
                    counts["ranked"] += 1 if ranking["score"] > 0 else 0
                    continue
                try:
                    mandate_countries, themes = mandate_geography(ranking, source_id)
                    _write(base_url, secret, "terminal_news_items", {
                        "id": story_id(url),
                        "canonical_url": url,
                        "headline": item["headline"],
                        "snippet": item.get("snippet"),
                        "publisher": item["publisher"],
                        "source_url": url,
                        "published_at": item["published_at"].isoformat()
                        if isinstance(item.get("published_at"), datetime) else None,
                        # Mandate-level geography from the catalogue, not the
                        # event location: the map labels this distinction.
                        "event_countries": mandate_countries,
                        "publisher_country": None,
                        "themes": themes,
                        "instruments": [ranking["instrument_id"]] if ranking["instrument_id"] else [],
                        "group_id": None,
                        "embedding_model": None,
                        "collected_by": "terminal-collector",
                    }, upsert=True)
                    counts["items"] += 1
                except Exception as exc:
                    print(f"News item write failed ({url}): {exc}")
                    continue
                # Upsert zero too: otherwise a former positive score survives
                # reclassification forever. Ranked reads filter score > 0.
                try:
                    _write(base_url, secret, "terminal_news_relevance", {
                        "news_id": story_id(url),
                        "scope": "portfolio",
                        "score": ranking["score"],
                        "exposure_match": ranking["exposure_match"],
                        "direct_match": ranking["direct_match"],
                        "strategy_match": ranking["strategy_match"],
                        "freshness": ranking["freshness"],
                        "evidence_quality": ranking["evidence_quality"],
                        "mapping_kind": "mapped" if ranking["mapping_kind"] == "none" else ranking["mapping_kind"],
                        "explanation": ranking["explanation"],
                        "ranking_version": RANKING_VERSION,
                        "exposure_snapshot_time": snapshot_time.isoformat() if snapshot_time else None,
                    }, upsert=True, conflict="news_id,scope")
                    counts["ranked"] += 1 if ranking["score"] > 0 else 0
                except Exception as exc:
                    print(f"Relevance write failed ({url}): {exc}")
        except Exception as exc:
            counts["sources_failed"] += 1
            print(f"Source {source.get('id')} failed: {exc}")
    if publish:
        # Bounded retention delete; failures never raise. The cutoff must be
        # percent-encoded: a raw +00:00 offset decodes as a space (HTTP 400).
        try:
            cutoff = retention_cutoff(now)
            request = Request(
                f"{base_url}/rest/v1/terminal_news_items?published_at=lt.{cutoff}&select=id",
                method="DELETE", headers={"apikey": secret, "Authorization": f"Bearer {secret}"})
            with urlopen(request, timeout=20):
                pass
        except Exception as exc:
            print(f"News retention cleanup skipped: {exc}")
    return counts


def _run() -> None:
    parser = argparse.ArgumentParser(description="Collect terminal news from approved feeds.")
    parser.add_argument("--watch", action="store_true", help="Keep collecting at the configured interval.")
    parser.add_argument("--once", action="store_true", help="Single collection pass (dry run without --publish).")
    parser.add_argument("--publish", action="store_true", help="Write to Supabase (default with --watch).")
    args = parser.parse_args()

    from core.main_executor import load_local_env

    load_local_env()
    interval = max(60, int(os.getenv("NOVA_TERMINAL_NEWS_POLL_SECONDS", "300")))
    publish = args.publish or args.watch
    while True:
        try:
            counts = run_cycle(publish=publish)
        except Exception as exc:
            print(f"News collection failed, will retry next cycle: {exc}")
            if not args.watch:
                raise
            sleep(interval)
            continue
        print(f"News cycle: {counts['items']} item(s), {counts['ranked']} ranked, "
              f"{counts['sources_ok']} source(s) ok, {counts['sources_failed']} failed.")
        if not args.watch:
            return
        sleep(interval)


def main() -> None:
    from dashboard.reporting_service import ReporterAlreadyRunning, reporter_instance
    try:
        with reporter_instance("news"):
            _run()
    except ReporterAlreadyRunning as exc:
        print(exc)


if __name__ == "__main__":
    main()
