# News sentiment and AI classification plan

Checkpoint: 25 November 2026. Status: proposed; no schema, classifier,
publisher or UI change is implemented by this document.

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
| AI scoring | Yes | Structured output + reasoning effort `max` matches the requested model. Validate every response. |
| Podcasts | Yes, with licensing care | Published transcripts or licensed transcript APIs are available; do not redistribute full transcripts. |
| Full article text | No by default | Paywalls and publisher terms. Use snippets, licensed APIs, or explicit permission. |
| Predictive claims | No | Scores are contemporaneous analysis, not a validated forecast or trading signal. |

### Source checks run on 25 November 2026

| Source | Result |
| --- | --- |
| `https://www.ft.com/rss/home/international` | HTTP 200, 12 items, fresh headline + standfirst. |
| `https://feeds.finance.yahoo.com/rss/2.0/headline?s=ASML&region=US&lang=en-US` | HTTP 200, 16 items, fresh. |
| `https://news.google.com/rss/search?q=when:24h+site:reuters.com&ceid=US:en&hl=en-US&gl=US` | HTTP 200, 100 items, but Google News indexing can lag; links go through Google News. |
| Reuters direct feeds | No working public feed; old `feeds.reuters.com` addresses are dead. |

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
        +--> OpenCode Go / DeepSeek API, structured JSON, effort=max
        |
        v
terminal_news_sentiment (article x market/strategy)
        |
        v
aggregate view / RPC (24h, 7d, 30d)
        |
        v
member-only terminal UI (Home market strip, Strategy cards, article detail)
```

The classifier is a separate process from the collector so a model outage or
cost cap cannot stop news collection. Both use the existing service-role
credentials and file-lock pattern.

## Market and strategy taxonomy

Start with a fixed, versioned taxonomy. Do not let the model invent buckets.

### Market buckets

- Regions: US, Euro area, UK, Europe ex UK, Japan, China, Asia ex Japan,
  Emerging markets, Global.
- Asset classes: Equities, Government bonds/rates, Credit, FX, Commodities,
  Crypto, Cash.

Each article receives a relevance and direction per bucket; irrelevant buckets
are omitted rather than forced to zero.

### Strategy profiles

The published bundle currently contains seven sleeves:

- Dashboard position demo
- Europe momentum reversal
- EuropeOrderFlowStres
- EuropeShortHoldPaper
- Paper order smoke test
- S&P 500 momentum reversal
- S&P 500 short momentum reversal

Proposed treatment:

- Score only accepted live/paper strategies.
- Exclude `Dashboard position demo` and `Paper order smoke test` from member
  sentiment, or label them as test sleeves.
- Normalise the two truncated names before display.
- Add a `terminal_strategy_profiles` row per accepted strategy with display
  name, direction (`long`, `short`, `market_neutral`), universe, regions,
  sectors, current attributed symbols and a short mandate description.

Without a direction and mandate, "bullish for the strategy" is ambiguous: a
bearish market story can be supportive for a short strategy. The profile is
therefore required, not optional.

## Data model (additive migration)

All tables are member-only reads unless stated; only the service role writes.

### `terminal_news_sentiment`

| Column | Type | Notes |
| --- | --- | --- |
| `news_id` | text | FK to `terminal_news_items`, cascade delete. |
| `scope_kind` | text | `market` or `strategy`. |
| `scope_key` | text | Taxonomy key, e.g. `us`, `euro_area`, `sp500_momentum`. |
| `direction` | numeric | `-100` strong bearish to `+100` strong bullish. |
| `confidence` | numeric | `0-100`. |
| `relevance` | numeric | `0-100`; how much the item is about this scope. |
| `horizon` | text | `intraday`, `days`, `weeks`, `months`. |
| `rationale` | text | Max 1,000 chars; no invented facts. |
| `evidence` | jsonb | Short quoted spans from the supplied text, max 3. |
| `model` | text | e.g. `deepseek-v4.1-flash`. |
| `model_checkpoint` | text | e.g. `1017`. |
| `prompt_version` | text | Versioned prompt/schema pair. |
| `classified_at` | timestamptz | Default now. |

Primary key: `(news_id, scope_kind, scope_key, model, prompt_version)`.
Index on `(scope_kind, scope_key, classified_at desc)`.

### `terminal_news_classification_runs`

Private operational table (service role only, no member select): run id,
model, prompt version, started/finished time, item count, failure count, input
tokens, output tokens, estimated cost, status and a redacted error code. Used
for cost caps and source health, never for public claims.

### `terminal_strategy_profiles`

Strategy key, display name, direction, universe, regions, sectors, symbols,
mandate description, active flag and updated time. Member-readable so the UI
can label scores; no balances.

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

```json
{
  "summary": "One or two sentences.",
  "event_type": "macro|earnings|policy|geopolitics|company|market|other",
  "novelty": 0,
  "materiality": 0,
  "markets": [
    {
      "key": "us",
      "relevance": 0,
      "direction": 0,
      "confidence": 0,
      "horizon": "weeks",
      "rationale": "Why, using only the supplied text.",
      "evidence": ["short quote"]
    }
  ],
  "strategies": [
    {
      "key": "sp500_momentum",
      "relevance": 0,
      "direction": 0,
      "confidence": 0,
      "horizon": "weeks",
      "rationale": "Why this supports or undermines this strategy.",
      "evidence": ["short quote"]
    }
  ]
}
```

Validation rules:

- Reject unknown market/strategy keys.
- Reject out-of-range numbers, missing rationale, or more than three evidence
  quotes.
- Retry once with the validation error; then mark the item failed and keep it
  unclassified. Never write a guessed score.
- `temperature=0`, `reasoning_effort="max"`, JSON schema structured output.
- Store `model`, `model_checkpoint`, `prompt_version` and `schema_version`.

### Idempotency

- Unique key on `(news_id, scope_kind, scope_key, model, prompt_version)`.
- Rerunning an item replaces its current-version rows, not duplicates them.
- A new prompt version does not overwrite old rows; it adds a new version.
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

Start with a curated list, not open-ended crawling:

- FT Unhedged, Reuters Econ World, Bloomberg Surveillance, Odd Lots, The Macro
  Compass, or similar.
- Prefer Podcasting 2.0 `<podcast:transcript>` tags or a licensed transcript API
  (Spoken, Podchaser, Exa, etc.).
- If no transcript exists, transcribe with Whisper/Deepgram only when the show's
  terms permit it.
- Store episode metadata, an AI summary and scores; do not store or republish
  the full transcript.
- Chunk long episodes (e.g. 8k tokens), summarize each chunk, then aggregate.
- Run hourly/daily, not every five minutes.

## Model route and cost

### Recommended route

Use the OpenAI-compatible endpoint
`https://opencode.ai/inference/go/openai/v1/chat/completions` with
`model: "deepseek-v4.1-flash"`, `reasoning_effort: "max"` and structured JSON.
This is the requested model/checkpoint and supports a 1M-token context.

### Pilot smoke test

On 25 November 2026, `opencode run --archived -m
opencode-go/deepseek-v4.1-flash` returned schema-shaped JSON for two synthetic
examples: a US macro headline (market direction `-25`, confidence `62`) and a
US rally headline against a mock short strategy (market `+75`, strategy `-75`).
The second result confirms the model can invert direction correctly when the
strategy profile says `short`. This is a formatting and reasoning smoke test,
not an accuracy benchmark; the 50-100 item human-labelled pilot is still required.

**Terms risk:** OpenCode Go is documented for OpenCode and other coding-agent
traffic; a high-volume scheduled news classifier may not fit that intended use.
For production, either:

1. confirm with OpenCode that this workload is acceptable under the Go plan, or
2. use a direct DeepSeek API key with the same 1017 model and pay-as-you-go
   pricing.

A pilot can run through Go at low volume; production should not depend on an
unconfirmed interpretation of the plan terms.

### Cost estimate

At roughly 300 articles/day, 1,200 input and 600 output tokens each:

- input: 360k tokens/day x $0.15/M = $0.054/day
- output: 180k tokens/day x $0.60/M = $0.108/day
- total: about **$0.16/day**, or **$5/month** off-peak; about **$10/month** at
  the 2x peak multiplier.
- Add podcasts, retries and prompt overhead: budget **$15-25/month**.
- A 2,000-article/day workload is roughly $30-35/month before podcasts.

Set `NOVA_NEWS_AI_DAILY_USD` and stop classifying when the cap is reached.
Record token usage from the API response; never estimate silently.

## UI plan

### Home

- Add a compact **MARKET SENTIMENT** strip or heatmap above/below the world map:
  region and asset-class direction, confidence, article count and last update.
- Keep the existing ranked/latest list, but add a small sentiment chip
  (bullish/bearish/neutral) and a "why" expansion.
- Clicking a headline opens a member article detail view rather than only
  expanding the drawer.

### Strategies

- Each accepted strategy card gets:
  - a directional gauge (-100 to +100) and confidence,
  - the top 3 supporting/contradicting headlines,
  - "based on N articles, last updated ..." and model/prompt version,
  - an explicit "AI analysis, not investment advice" label.
- Add a strategy x market heatmap so members can see where the book is
  exposed to the same narrative.

### Article detail

- Route: `/terminal/news` list and `/terminal/news/[id]` detail (member-only).
- Detail shows headline, publisher, source time, short summary, AI rationale,
  evidence quotes, market/strategy scores, and **Open source article**.
- Paywalled articles show "Source may require a subscription"; do not proxy or
  copy the article.
- Signed-out access redirects to `/terminal/sign-in`; add a safe allowlisted
  `terminal-news` destination. For a specific article, remember the return path
  in session storage rather than accepting an arbitrary `next` URL.
- No public/anonymous sentiment display in the first release.

## Operations

- New module: `dashboard/news_sentiment.py` (or `news_classifier.py`).
- CLI: `--once`, `--limit`, `--dry-run`, `--model`, `--since`, `--max-usd`.
- Queue query: items with no current `(model, prompt_version)` result, oldest
  first, capped per run.
- Windows task: `NQC-News-Sentiment`, every 10 minutes, limited privilege,
  interactive login, file lock, five-minute recovery trigger, logs under
  ignored `data/`.
- Retries: exponential backoff on 429/5xx; one schema-validation retry; mark
  permanent failures after N attempts.
- Health: write classifier status separately from source health; do not mark a
  source down because the model failed.
- Security: API key only in local `.env` or Windows credentials; never commit,
  log or return it. Service-role writes; member-only reads.
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

### Phase 0 - pilot (no UI)

- Add FT, Yahoo per-symbol and Reuters-discovery sources in dry-run mode.
- Add the sentiment schema and a mock-model test harness.
- Classify 20-50 real items with the pilot prompt; human-review the output.
- Measure cost, latency and schema validity.

### Phase 1 - member news + market scores

- Production classifier for articles, market buckets only.
- Add article detail route and Home sentiment strip.
- RLS, retention, cost cap, source health and tests.
- Member-only release; no public data.

### Phase 2 - strategies

- Seed accepted strategy profiles and normalize display names.
- Add strategy scores, strategy cards and the market/strategy heatmap.
- Add admin review/override workflow.

### Phase 3 - podcasts

- Curate shows and transcript sources.
- Add episode ingestion, chunked classification and podcast badges.
- Keep full transcripts out of the database.

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

## Open decisions

1. Which strategies are official for scoring, and which stay test/demo?
2. Should market scores cover regions, asset classes, or both? Default: both.
3. What horizon should the headline score represent: intraday, days, weeks or
   months? Default: weeks, with intraday optional.
4. Article access: inline drawer only, or dedicated `/terminal/news/[id]`
   pages? Recommendation: dedicated member pages with source links.
5. Podcast shows and transcript budget: which shows, and is a paid transcript
   API acceptable?
6. Model route: OpenCode Go (confirm workload terms) or direct DeepSeek API.
7. Who reviews/overrides AI scores, and how often?
8. Should any sentiment aggregate ever be public, or stay member-only?
