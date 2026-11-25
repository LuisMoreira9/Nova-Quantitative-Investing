# News sentiment and AI classification plan

Checkpoint: 25 November 2026. Status: pilot complete; production schema,
classifier, publisher and UI are not implemented by this document.

## Goal

Turn the member news panel into a source-linked research feed that:

1. Collects finance and macro headlines from a reviewed set of sources,
   including FT, Reuters-discovered stories and Yahoo Finance where permitted.
2. Lets active members open an article record and reach the original source.
3. Uses an AI model to score each item as bullish/bearish for defined market
   buckets and for each accepted strategy sleeve.
4. Adds podcast episodes and published transcripts as long-form inputs.
5. Keeps every score reproducible, labelled as AI-generated analysis, and
   separate from verified returns or trading signals.

## Decision summary (25 November 2026)

- Test all seven current sleeves. Four are scoreable now; three are test/demo or
  profile-incomplete and are flagged rather than scored.
- Market taxonomy covers regions, asset classes, seven equity styles and eleven
  sectors.
- Horizon is `weeks`.
- No dedicated article pages. Reuse the inline Home drawer and source link.
- First podcast batch is the three supplied episodes: The Macro Trading Floor,
  Morgan Stanley Thoughts on the Market and Goldman Sachs The Markets.
- Use OpenCode Go `deepseek-v4.1-flash` for the pilot. The API key route is
  confirmed; the Go workload-terms caveat remains open.

## What already exists

- `dashboard/terminal_news.py` collects GDELT, ECB and Fed feeds every five
  minutes, canonicalises URLs, deduplicates exact matches, applies a rules-only
  exposure/relevance ranking and writes member-only Supabase rows.
- `terminal_news_items` stores headline, snippet, publisher, source URL,
  timestamps, mandate-level geography, themes and instruments. Full article
  bodies are not stored.
- `terminal_news_relevance` stores the 0-100 portfolio relevance score, score
  components, explanation and ranking version. `strategy_match` is currently
  always zero.
- The terminal Home panel reads ranked/latest items; a headline expands inline
  and links to the source. There is no article detail route and no sentiment
  score.
- Supabase RLS makes all news tables member-only. The service role writes.
- The OpenCode Go catalog currently serves `deepseek-v4.1-flash` as
  **DeepSeek V4.1 Flash 1017** (`canonical_id`
  `deepseek/deepseek-v4.1-flash-1017`), with structured output, a 1M-token
  context, and reasoning effort `low`, `high` or `max`.

## Feasibility verdict

Feasible, with these boundaries:

| Area | Verdict | Reason |
| --- | --- | --- |
| FT headlines | Yes | Public RSS returns headline + one-line standfirst; articles are paywalled, so link out only. |
| Reuters | Partial | No public RSS since 2020; `reuters.com` rejects automated access. Use GDELT or a Google News `site:reuters.com` feed for headline discovery only. |
| Yahoo Finance | Yes, per symbol | General Yahoo Finance RSS is dead; per-symbol feeds still return headlines and summaries. |
| Official sources | Yes | ECB, Fed and similar feeds are safe, high-quality and already supported. |
| AI scoring | Yes | The OpenCode Go API key route works; the flat `scores` schema validated 6/6 pilot items. Validate every response. |
| Podcasts | Yes, with licensing care | All three selected episodes have a usable transcript route; do not redistribute full transcripts. |
| Full article text | No by default | Paywalls and publisher terms. Use snippets, licensed APIs, or explicit permission. |
| Predictive claims | No | Scores are contemporaneous analysis, not a validated forecast or trading signal. |

### Source and route checks run on 25 November 2026

| Source | Result |
| --- | --- |
| `https://www.ft.com/rss/home/international` | HTTP 200, 12 items, fresh headline + standfirst. |
| `https://feeds.finance.yahoo.com/rss/2.0/headline?s=ASML&region=US&lang=en-US` | HTTP 200, 16 items, fresh. |
| `https://news.google.com/rss/search?q=when:24h+site:reuters.com&ceid=US:en&hl=en-US&gl=US` | HTTP 200, 100 items, but Google News indexing can lag; links go through Google News. |
| Reuters direct feeds | No working public feed; old `feeds.reuters.com` addresses are dead. |
| OpenCode Go `deepseek-v4.1-flash` | HTTP 200; valid JSON at `max_tokens=32000`; `max` and `high` both work. |
| The Macro Trading Floor transcript | Public transcript page, 5,812 words; RSS has audio but no transcript tag. |
| Morgan Stanley transcript | Publisher page, 6,302 words. |
| Goldman Sachs transcript | Public PDF, 2,432 words. |

## Proposed architecture

```text
approved feeds / podcast RSS
        |
        v
collector (existing terminal_news.py, expanded sources)
        |
        +--> terminal_news_items (headline, snippet, source URL, metadata)
        |
        v
classification queue (items without current prompt/model result)
        |
        v
news_sentiment worker (Python, service role)
        |
        +--> OpenCode Go / DeepSeek API, flat JSON, effort=high|max
        |
        v
terminal_news_sentiment (article x market/strategy)
        |
        v
aggregate view / RPC (24h, 7d, 30d)
        |
        v
member-only terminal UI (Home market strip, Strategy cards, inline article drawer)
```

The classifier is a separate process from the collector so a model outage or
cost cap cannot stop news collection. Both use the existing service-role
credentials and file-lock pattern.

## Market and strategy taxonomy

Decisions accepted on 25 November 2026: score all seven current sleeves for the
pilot, use both regions and asset classes, split equities into style and sector
buckets, and use a `weeks` horizon. The taxonomy is fixed and versioned; the
model must not invent keys.

### Market buckets (34)

Regions (9): `us`, `euro_area`, `uk`, `europe_ex_uk`, `japan`, `china`,
`asia_ex_japan`, `emerging_markets`, `global`.

Asset classes (7): `equities`, `govt_bonds`, `credit`, `fx`, `commodities`,
`crypto`, `cash`.

Equity styles (7): `equity_value`, `equity_growth`, `equity_momentum`,
`equity_quality`, `equity_low_vol`, `equity_small_cap`, `equity_dividend`.

Sectors (11): `sector_tech`, `sector_financials`, `sector_healthcare`,
`sector_consumer_discretionary`, `sector_consumer_staples`, `sector_energy`,
`sector_industrials`, `sector_materials`, `sector_utilities`,
`sector_real_estate`, `sector_communication`.

Each item receives relevance, direction and confidence per bucket. Only buckets
with relevance >= 20 are returned; irrelevant buckets are omitted rather than
forced to zero. Do not sum overlapping buckets into one "global" score.

### Strategy profiles (7)

| Key | Display name | Direction | Universe | Pilot status |
| --- | --- | --- | --- | --- |
| `sp500_momentum` | S&P 500 momentum reversal | long | US large-cap | Scored |
| `sp500_short_momentum` | S&P 500 short momentum reversal | short | US large-cap | Scored |
| `europe_momentum` | Europe momentum reversal | long | European equities | Scored |
| `europe_order_flow_stress` | Europe order-flow stress | unknown | European equities | Profile incomplete; no strategy module found |
| `europe_short_hold` | Europe short hold paper | short | European equities | Provisional; no dedicated module found |
| `paper_order_smoke_test` | Paper order smoke test | test | Single test instrument | Not scored |
| `dashboard_position_demo` | Dashboard position demo | demo | Demo position | Not scored |

The pilot scores all seven so the pipeline and UI can be exercised. The three
not-scored rows (`europe_order_flow_stress`, `paper_order_smoke_test`,
`dashboard_position_demo`) return `relevance=0`, `direction=0`, `confidence=1`
and the exact rationale "Not scored: test/demo sleeve or incomplete strategy
profile." That lets the UI label them instead of inventing a directional view.
`europe_short_hold` is scoreable in the pilot but provisional; do not show its
scores to members until its mandate is confirmed. Normalise the two truncated
IBKR names (`EuropeOrderFlowStres`, `EuropeShortHoldPaper`) before display.

Without a direction and mandate, "bullish for the strategy" is ambiguous: a
bearish market story can be supportive for a short strategy. The profile is
therefore required, not optional.

## Data model (additive migration)

All tables are member-only reads unless stated; only the service role writes.

### `terminal_news_sentiment`

| Column | Type | Notes |
| --- | --- | --- |
| `news_id` | text | FK to `terminal_news_items`, cascade delete. |
| `run_id` | uuid | FK to `terminal_news_classification_runs`. |
| `scope_kind` | text | `market` or `strategy`. |
| `scope_key` | text | Taxonomy key, e.g. `us`, `euro_area`, `sp500_momentum`. |
| `direction` | numeric | `-100` strong bearish to `+100` strong bullish. |
| `confidence` | numeric | `1-100`; not-scored rows use `1`. |
| `relevance` | numeric | `0-100`; how much the item is about this scope. |
| `horizon` | text | `weeks` for this release; keep the column for later horizons. |
| `rationale` | text | Max 600 chars; no invented facts. |
| `evidence` | jsonb | Optional; short quoted spans, max 3. Deferred until the prompt is stable. |
| `model` | text | e.g. `deepseek-v4.1-flash`. |
| `model_checkpoint` | text | e.g. `1017`. |
| `prompt_version` | text | Versioned prompt/schema pair. |
| `classified_at` | timestamptz | Default now. |

Primary key: `(news_id, scope_kind, scope_key, run_id)`.
Index on `(scope_kind, scope_key, classified_at desc)`. The run row carries
`model`, `model_checkpoint`, `prompt_version`, `schema_version`,
`reasoning_effort` and `max_tokens`, so a `high` and a `max` run do not
overwrite each other.

### `terminal_news_classification_runs`

Private operational table (service role only, no member select): run id,
model, prompt version, schema version, reasoning effort, max tokens,
started/finished time, item count, failure count, input tokens, output tokens,
estimated cost, status and a redacted error code. Used for cost caps and source
health, never for public claims.

### `terminal_strategy_profiles`

Strategy key, display name, direction, universe, regions, sectors, symbols,
mandate description, `profile_status` (`confirmed`, `provisional`, `test`,
`demo`), `scoreable` boolean, active flag and updated time. Member-readable so
the UI can label scores; no balances. Seed the seven profiles from the taxonomy
table above; only `confirmed` and `scoreable` rows are shown as investment
views.

### `terminal_news_items` additions

- `content_kind` (`article`, `podcast`, `transcript`) default `article`.
- `summary` (nullable, short AI-generated summary, max 1,000 chars) or reuse
  `snippet`; do not store full text.
- `context_tier` (`headline_snippet`, `licensed_body`, `transcript`) for audit.
- `group_id` is already present; use it for duplicate/syndication clusters.

### Aggregation

Create a `security_invoker` view or SQL function that returns, per scope and
window (`24h`, `7d`, `30d`):

- weighted direction, confidence, article count, bullish/bearish/neutral counts,
  top 3 evidence items, last computed time and model/prompt version.

Weighting proposal:

```text
article_weight = relevance/100 * confidence/100 * source_quality * freshness
freshness = 0.5 ** (age_hours / half_life_hours)
half_life = 12h for news, 7d for podcasts
```

Require at least three contributing articles before showing a directional
aggregate; below that show "low coverage" with the individual items. Do not
sum overlapping market buckets into a global score.

## AI classification contract

### Request

System prompt (versioned) contains:

- Role: market-news analyst for a student paper-trading club.
- Taxonomy definitions and strategy profiles.
- Strict rules: use only the supplied headline/snippet/transcript; do not infer
  missing facts; distinguish relevance from direction; return JSON only.
- Disclaimer: analysis, not investment advice; no trade instruction.

User payload: item id, source, publisher, published time, headline, snippet or
transcript excerpt, existing themes/countries/instruments, and the strategy
profiles.

### Response schema

The pilot uses a flat `scores` array. A nested `markets`/`strategies` schema with
long enum lists was unreliable: one run returned `confidence: 0` for every
scope, and a stricter schema returned truncated JSON. The flat shape validated
6/6 pilot items after the output cap was raised.

```json
{
  "summary": "One or two sentences.",
  "scores": [
    {
      "scope": "us",
      "kind": "market",
      "relevance": 90,
      "direction": -60,
      "confidence": 70,
      "rationale": "Why, using only the supplied text."
    }
  ]
}
```

`kind` is `market` or `strategy`; the worker maps `scope` to the versioned
taxonomy and rejects unknown keys. The model returns only scopes with relevance
>= 20. `horizon` is fixed to `weeks` for this release, so it is stored once per
run rather than repeated on every score.

Validation rules:

- Reject unknown market/strategy keys and a `kind` that does not match the key.
- Reject out-of-range numbers, missing rationale, duplicate scopes, or a
  rationale longer than 600 characters.
- Reject `confidence=0`; require `1-100` for every included scope.
- Enforce the exact not-scored row for `europe_order_flow_stress`,
  `paper_order_smoke_test` and `dashboard_position_demo`. Do not use the
  not-scored rationale for `europe_short_hold`; omit it when relevance < 20.
- Retry once with the validation error; then mark the item failed and keep it
  unclassified. Never write a guessed score.
- `temperature=0`, `response_format={"type":"json_object"}`.
- `max_tokens` must be at least `32000`. At `8000`, `max` reasoning consumed the
  whole cap and returned empty or truncated content on long transcripts and one
  short headline.
- `reasoning_effort`: `high` for routine headlines, `max` for podcasts and
  ambiguous or market-moving items. In the pilot, `max` used about 11k
  reasoning tokens on a one-line ASML headline versus about 4k at `high`, with
  materially similar scores. Make the setting configurable per run.
- Store `model`, `model_checkpoint`, `prompt_version`, `schema_version`,
  `reasoning_effort` and `max_tokens`.

### Pilot findings (25 November 2026)

- Six items were classified: FT, Yahoo Finance/ASML, Reuters via Google News,
  The Macro Trading Floor, Morgan Stanley Thoughts on the Market and Goldman
  Sachs The Markets.
- The first run passed three items and failed three. All three failures were
  caused by the 8,000-token output cap, not by source access or invalid keys.
  With `max_tokens=32000`, all three passed on retry.
- The model correctly inverted a short strategy: bearish US equity news produced
  a negative score for `sp500_momentum` and a positive score for
  `sp500_short_momentum`.
- The model correctly flagged test/demo sleeves instead of inventing a view.
  `europe_short_hold` was also flagged in one podcast; the prompt now explicitly
  makes it scoreable and tells the model to omit it below the relevance
  threshold.
- The Reuters item omitted all strategy rows because none reached the relevance
  threshold. The production prompt should always emit all seven strategy keys,
  using the not-scored row for the three flagged sleeves.
- The pilot is a formatting and reasoning smoke test, not an accuracy benchmark.
  A human-labelled set of 50-100 items is still required.

Full score tables, token usage and latency: [NEWS_SENTIMENT_PILOT.md](./NEWS_SENTIMENT_PILOT.md).

### Idempotency

- Unique key on `(news_id, scope_kind, scope_key, run_id)`.
- A retry inside one run replaces that run's rows, not duplicates them.
- A new prompt, model or reasoning effort creates a new run and adds rows; it
  does not overwrite the old run.
- The member view selects the latest successful run per item. Old runs remain
  available for audit and calibration.
- Classify each deduplicated `group_id` once where possible, then copy the
  result to member items in the cluster with a `cluster_source` flag.

## Source expansion

### Phase 1 sources

| Source | Method | Content | Notes |
| --- | --- | --- | --- |
| FT international | RSS | headline + standfirst | Link out; paywall remains. |
| FT markets/companies/global economy | RSS | headline + standfirst | Same terms; cap items per cycle. |
| Reuters discovery | GDELT `domain:reuters.com` or Google News RSS | headline | No direct scraping; keep Google News attribution/link. |
| Yahoo Finance | per-symbol RSS from held/watchlist symbols | headline + summary | Cap symbols (e.g. 25); general feed is dead. |
| ECB / Fed | existing RSS | official release | Existing policy-mandate logic. |
| BIS / IMF / OECD / Eurostat | RSS or API | official release | Candidate; verify terms. |
| MarketWatch / WSJ / CNBC / Bloomberg | RSS | headline + summary | Candidate; verify display rights. |
| AP | Flipboard or Google News | headline | No official feed. |

### Later sources

- Licensed news APIs (Marketaux, Finnhub, Polygon, Tiingo, Benzinga,
  Alpha Vantage) only if the club accepts the cost and redistribution terms.
- Paywalled full text only with explicit permission or member-supplied access;
  do not bypass paywalls.

### Podcasts

First batch, selected by the user on 25 November 2026:

| Show | Episode | Date | Transcript route | Notes |
| --- | --- | --- | --- | --- |
| The Macro Trading Floor | Macro Doomerism - Right or Wrong? | 20 Nov 2026 | Public transcript at PodcastTranscript.ai; RSS audio at `feeds.megaphone.fm/ALFINVESTMENTSTRATEGYBV2974145286` | 5,812 words; the RSS has no `<podcast:transcript>` tag. |
| Morgan Stanley — Thoughts on the Market | 2027 Equities Outlook: Why the Correction Isn't the End of the Bull Market | 18 Nov 2026 | Episode page publishes a transcript | 6,302 words; fetch with a browser user agent. |
| Goldman Sachs — The Markets | Why Investors Are Reducing AI Exposure | 22 Oct 2026 | Public transcript PDF | 2,432 words; published 23 Oct 2026. |

Rules:

- Keep a curated list; do not crawl open-ended podcast directories.
- Prefer a publisher transcript, then a Podcasting 2.0
  `<podcast:transcript>` tag, then a licensed transcript API. Use
  Whisper/Deepgram only when the show's terms permit it.
- Store episode metadata, an AI summary and scores; do not store or republish
  the full transcript. Link to the publisher or episode page.
- The current model handled 5,800-6,300 words in one pass. Chunk episodes above
  roughly 15,000 words, summarize each chunk, then aggregate.
- Run podcasts daily or hourly, not every five minutes.
- Confirm display/linking terms before any member-facing podcast card.

## Model route and cost

### Route confirmed

The stored OpenCode Go credential works against:

- `POST https://opencode.ai/inference/go/openai/v1/chat/completions`
- `GET https://opencode.ai/inference/go/v1/usage`
- `GET https://opencode.ai/inference/go/v1/models`

The catalog describes `deepseek-v4.1-flash` as **DeepSeek V4.1 Flash 1017**
(`canonical_id` `deepseek/deepseek-v4.1-flash-1017`), with a 1M-token context,
384k output limit, structured output, reasoning effort `low`/`high`/`max`,
zero-data-retention and no training. The Go plan lists $100 of monthly usage
for this model.

Authentication is `Authorization: Bearer oc_sk_...` plus
`x-opencode-session` and a client user agent. The current desktop credential is
a stored API key and was used for the pilot. For production, create a dedicated
service-account key in Console → Settings → Service accounts, restrict it to
**Models: Request**, and store it in the local `.env` or Windows Credential
Manager. Do not make a scheduled worker read the desktop credential.

### Terms caveat

The Go documentation says it is designed for OpenCode and other coding agents
that produce similar requests, and that traffic is monitored for abuse. A
scheduled news classifier is outside that description. The API works, but this
is a terms risk, not a technical blocker. For production, either:

1. confirm with OpenCode support that a low-volume news classifier is acceptable
   under the Go plan, or
2. use a direct DeepSeek API key with the same 1017 model and pay-as-you-go
   pricing.

Use Go for the pilot; do not assume production approval until it is confirmed.

### Pilot smoke test

On 25 November 2026, the API classified six real items: FT, Yahoo Finance/ASML,
Reuters via Google News, The Macro Trading Floor, Morgan Stanley Thoughts on the
Market and Goldman Sachs The Markets. All six passed after `max_tokens` was
raised from 8,000 to 32,000. The model correctly inverted a short strategy:
bearish US equity news produced a negative score for `sp500_momentum` and a
positive score for `sp500_short_momentum`.

The pilot also found that a nested `markets`/`strategies` schema with long enum
lists was unreliable. The flat `scores` array is the supported shape. This is a
formatting and reasoning smoke test, not an accuracy benchmark; the 50-100 item
human-labelled set is still required.

### Cost estimate

Measured on the pilot (allowance cost, not list price):

| Setting | Short article | 300 articles/day | 30 days |
| --- | --- | --- | --- |
| `reasoning_effort=high` | about $0.0018 | about $0.54/day | about **$16/month** |
| `reasoning_effort=max` | about $0.0043 | about $1.29/day | about **$39/month** |

Add roughly $5-10/month for podcasts, retries and prompt overhead. A
`high`-for-headlines / `max`-for-podcasts policy lands near **$20-25/month**.
The 2x peak multiplier applies on weekdays 01:00-04:00 and 06:00-10:00 UTC, so
schedule bulk classification off-peak where possible. At 2,000 articles/day,
`max` would exceed the Go monthly allowance; use `high`, batch the work, or move
that volume to a direct API key.

Set `NOVA_NEWS_AI_DAILY_USD` and stop classifying when the cap is reached.
Record token usage from the API response; never estimate silently. The pilot
used about $0.02 of allowance for six items; the current Go workspace was at
18% monthly, 1% weekly and 0% rolling usage at test time.

## UI plan

### Home

- Add a compact **MARKET SENTIMENT** strip or heatmap above/below the world map:
  region, asset-class, equity-style and sector direction, confidence, article
  count and last update.
- Keep the existing ranked/latest list, but add a small sentiment chip
  (bullish/bearish/neutral) and a "why" expansion.
- Keep the original article headline exactly as published. The AI summary and
  scores are separate fields; do not rewrite the headline.
- No dedicated article route in this release (user decision). The existing
  inline expansion is the article view.

### Strategies

- Each accepted strategy card gets:
  - a directional gauge (-100 to +100) and confidence,
  - the top 3 supporting/contradicting headlines,
  - "based on N articles, last updated ..." and model/prompt version,
  - an explicit "AI analysis, not investment advice" label.
- Add a strategy x market heatmap so members can see where the book is
  exposed to the same narrative.
- Show `europe_order_flow_stress`, `paper_order_smoke_test` and
  `dashboard_position_demo` as "profile incomplete" or "test sleeve", not as
  investment views, until their mandates are confirmed.

### Article interaction

- Reuse the Home drawer: publisher, source time, short summary, market and
  strategy scores, rationale, and **Open source article**.
- Paywalled articles show "Source may require a subscription"; do not proxy or
  copy the article.
- Signed-out access redirects to `/terminal/sign-in`; do not accept an arbitrary
  `next` URL. Remember only a safe internal return path in session storage.
- No public/anonymous sentiment display in the first release.

## Operations

- New module: `dashboard/news_sentiment.py` (or `news_classifier.py`).
- CLI: `--once`, `--limit`, `--dry-run`, `--model`, `--since`, `--max-usd`,
  `--effort high|max`, `--max-tokens`, `--source`.
- Defaults: `max_tokens=32000`; `effort=high` for articles and `max` for
  podcasts or flagged items; `temperature=0`; `response_format=json_object`.
- Queue query: items with no current `(model, prompt_version)` result, oldest
  first, capped per run. Always request all seven strategy keys; the three
  flagged sleeves return the not-scored row.
- Windows tasks: `NQC-News-Sentiment` every 10 minutes for articles;
  `NQC-News-Podcast` daily for episodes. Limited privilege, interactive login,
  file lock, five-minute recovery trigger, logs under ignored `data/`.
- Retries: exponential backoff on 429/5xx; one schema-validation retry; mark
  permanent failures after N attempts.
- Health: write classifier status separately from source health; do not mark a
  source down because the model failed. Track schema failures and cap hits.
- Security: `OPENCODE_API_KEY` only in local `.env` or Windows Credential
  Manager; never commit, log or return it. Use a dedicated service-account key,
  not the desktop credential. Service-role writes; member-only reads.
- Retention: news items 30 days; sentiment rows 90 days; aggregates can be
  recomputed. Delete cascade with the item.

## Validation and governance

- Build a human-labelled pilot set of 50-100 articles across sources, markets
  and strategies before enabling member-facing scores.
- Measure: JSON validity, direction agreement with the human label, relevance
  agreement, confidence calibration, and stability across reruns.
- Target: at least 95% schema-valid after one retry; direction agreement target
  to be agreed (start at 70%, not a claim of predictive accuracy).
- Display model, prompt version, timestamp and article count next to every
  aggregate.
- Allow an admin override/review status before any score is treated as
  "reviewed"; overrides are additive and auditable.
- Never connect sentiment scores to order generation. The reporting path stays
  read-only.
- If scores are later shown publicly, use a separate reviewed aggregate; do not
  expose member-only article metadata.

## Rollout phases

### Phase 0 - pilot (no UI) — completed 25 November 2026

- FT, Yahoo per-symbol and Reuters-discovery sources fetched successfully.
- The flat sentiment schema, validation and real API route were exercised.
- Six real items were classified: three articles and the three selected
  podcast episodes.
- Initial 8,000-token cap caused three failures; 32,000 fixed all three.
- Cost, latency and schema validity were measured.
- Still required: a human-labelled set of 50-100 items and a second reviewer.

### Phase 1 - member news + market scores

- Production classifier for articles across regions, asset classes, equity
  styles and sectors.
- Add the Home sentiment strip and the inline "why" expansion. No dedicated
  article route.
- RLS, retention, cost cap, source health and tests.
- Member-only release; no public data.

### Phase 2 - strategies

- Seed the seven profiles and normalize display names.
- Confirm `europe_order_flow_stress` and `europe_short_hold` before showing
  their scores.
- Add strategy scores, strategy cards and the market/strategy heatmap.
- Add admin review/override workflow.

### Phase 3 - podcasts

- Ingest the three selected episodes and their transcript sources.
- Add episode metadata, summaries, scores and podcast badges.
- Keep full transcripts out of the database; link to the publisher.

### Phase 4 - calibration

- Expand the labelled set, tune prompts, compare model checkpoints.
- Decide whether to expose any aggregate publicly; default remains member-only.

## Acceptance criteria

- Anonymous reads of every new sentiment/profile table return zero rows.
- A member can open an article record and reach the original source.
- Rerunning the classifier does not duplicate or silently overwrite scores.
- Invalid model output never reaches the database.
- Every aggregate shows article count, timestamp, model and prompt version.
- A model outage or cost-cap hit does not stop news collection.
- No full article text or full podcast transcript is stored or republished.
- No verified return, trading decision or order path consumes sentiment scores.

## Resolved decisions (25 November 2026)

1. Test all seven current sleeves. Four are scoreable now; three are flagged as
   test/demo or profile incomplete.
2. Market scores cover regions and asset classes, with equities split into
   styles and sectors.
3. Horizon is `weeks`.
4. No dedicated article pages; reuse the inline Home drawer and source link.
5. First podcast batch: The Macro Trading Floor, Morgan Stanley Thoughts on the
   Market and Goldman Sachs The Markets (the three episodes supplied).
6. Use OpenCode Go for the pilot; the API key route is confirmed.

## Open decisions

1. Confirm the direction, universe and mandate of `europe_order_flow_stress` and
   `europe_short_hold`. Should either be shown to members, or kept as test
   sleeves?
2. Production model route: confirm with OpenCode that a low-volume news
   classifier is acceptable under Go, or use a direct DeepSeek API key.
3. Who reviews or overrides AI scores, and how often? Start with a weekly
   sample review.
4. Should any sentiment aggregate ever be public, or stay member-only? Default:
   member-only.
5. Podcast display: is linking to Spotify/publisher pages and showing an AI
   summary and scores acceptable for each show, or is explicit permission
   needed?
6. Approve the source list. Current proposal: FT, Yahoo per-symbol, Reuters via
   Google News, ECB/Fed. Add or remove before Phase 1.
7. Default reasoning effort: `high` for routine headlines and `max` for podcasts
   and ambiguous items, or `max` everywhere?
8. Set the daily USD cap and retention values before the first production run.
