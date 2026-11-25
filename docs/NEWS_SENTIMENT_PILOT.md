# News sentiment pilot — 25 November 2026

Status: completed, no schema, publisher, UI or production change. This is a
formatting and reasoning smoke test, not an accuracy benchmark.

## Setup

- Model: `deepseek-v4.1-flash` (DeepSeek V4.1 Flash 1017) through OpenCode Go.
- Endpoint: `https://opencode.ai/inference/go/openai/v1/chat/completions`.
- Settings: `temperature=0`, `response_format=json_object`,
  `reasoning_effort=max`, `max_tokens=32000`, horizon `weeks`.
- Six real items: FT, Yahoo Finance/ASML, Reuters via Google News, The Macro
  Trading Floor, Morgan Stanley Thoughts on the Market and Goldman Sachs The
  Markets.
- Full article text and full transcripts were not stored in the repository.
  Only headlines, short summaries and scores are reproduced here.

## Result

All six items passed validation after the output cap was raised from 8,000 to
32,000 tokens. The first run passed three and failed three; every failure was an
empty or truncated response caused by the cap, not by source access or invalid
keys.

| Item | Kind | Scores | Finish | Seconds | Allowance |
| --- | --- | ---: | --- | ---: | ---: |
| FT — US stocks sell-off deepens as AI trade cracks | article | 13 | stop | 70.4 | $0.00288 |
| Yahoo — ASML drops 6.1% on US chip-curb bill | article | 11 | stop | 111.5 | $0.00431 |
| Reuters — Boehly/Lukoil share-swap deal | article | 7 | stop | 37.1 | $0.00143 |
| Macro Trading Floor — Macro Doomerism | podcast | 22 | stop | 150.5 | $0.00623 |
| Morgan Stanley — 2027 Equities Outlook | podcast | 25 | stop | 107.4 | $0.00485 |
| Goldman Sachs — Why Investors Are Reducing AI Exposure | podcast | 23 | stop | 60.6 | $0.00268 |

Clean `max` run total: about **$0.022** of Go allowance for six items.

### `high` comparison

Two items were rerun at `reasoning_effort=high`:

| Item | Effort | Completion | Reasoning | Seconds | Allowance |
| --- | --- | ---: | ---: | ---: | ---: |
| Yahoo/ASML | max | 11,963 | 11,342 | 111.5 | $0.00431 |
| Yahoo/ASML | high | 4,650 | 4,039 | 42.0 | $0.00176 |
| Macro Trading Floor | max | 17,244 | 16,007 | 150.5 | $0.00623 |
| Macro Trading Floor | high | 7,902 | 6,763 | 68.9 | $0.00358 |

`high` was roughly 2.4-3x cheaper and faster. Scores were materially similar on
these two items. The plan recommends `high` for routine headlines and `max` for
podcasts and ambiguous or market-moving items.

## Selected scores

### FT — US stocks sell-off deepens as AI trade cracks

| Scope | Relevance | Direction | Confidence |
| --- | ---: | ---: | ---: |
| `us` | 100 | -85 | 85 |
| `sector_tech` | 100 | -90 | 90 |
| `equities` | 95 | -80 | 80 |
| `equity_growth` | 80 | -70 | 70 |
| `equity_momentum` | 70 | -60 | 60 |
| `sp500_momentum` | 85 | -40 | 55 |
| `sp500_short_momentum` | 85 | +60 | 65 |
| `europe_momentum` | 30 | -25 | 40 |
| `europe_short_hold` | 30 | +25 | 40 |

### Yahoo Finance — ASML drops 6.1% on US chip-curb bill

| Scope | Relevance | Direction | Confidence |
| --- | ---: | ---: | ---: |
| `sector_tech` | 85 | -50 | 70 |
| `euro_area` | 85 | -45 | 70 |
| `europe_ex_uk` | 80 | -40 | 65 |
| `equities` | 30 | -10 | 45 |
| `europe_momentum` | 70 | -20 | 45 |
| `europe_short_hold` | 60 | +20 | 35 |

### Reuters — Boehly signs US-backed share swap to buy Lukoil assets

| Scope | Relevance | Direction | Confidence |
| --- | ---: | ---: | ---: |
| `sector_energy` | 85 | +20 | 30 |
| `global` | 40 | +10 | 25 |
| `us` | 35 | +10 | 25 |
| `europe_ex_uk` | 30 | +5 | 20 |
| `emerging_markets` | 30 | 0 | 20 |
| `equities` | 30 | +10 | 20 |
| `commodities` | 25 | 0 | 20 |

### Macro Trading Floor — Macro Doomerism - Right or Wrong?

| Scope | Relevance | Direction | Confidence |
| --- | ---: | ---: | ---: |
| `equities` | 95 | -35 | 60 |
| `us` | 95 | -30 | 65 |
| `global` | 90 | -30 | 55 |
| `credit` | 75 | -50 | 60 |
| `commodities` | 80 | -50 | 70 |
| `sector_tech` | 85 | -50 | 65 |
| `sp500_momentum` | 90 | -20 | 55 |
| `sp500_short_momentum` | 90 | +35 | 60 |

### Morgan Stanley — 2027 Equities Outlook

| Scope | Relevance | Direction | Confidence |
| --- | ---: | ---: | ---: |
| `equities` | 95 | +45 | 75 |
| `us` | 95 | +35 | 70 |
| `equity_quality` | 85 | +60 | 75 |
| `sector_industrials` | 80 | +60 | 75 |
| `sector_tech` | 80 | +30 | 65 |
| `sp500_momentum` | 75 | +45 | 65 |
| `sp500_short_momentum` | 65 | -35 | 60 |

### Goldman Sachs — Why Investors Are Reducing AI Exposure

| Scope | Relevance | Direction | Confidence |
| --- | ---: | ---: | ---: |
| `sector_tech` | 95 | -55 | 65 |
| `credit` | 85 | -55 | 60 |
| `equities` | 85 | -30 | 55 |
| `govt_bonds` | 70 | +35 | 55 |
| `global` | 80 | -25 | 50 |
| `sector_industrials` | 65 | +40 | 45 |
| `sp500_momentum` | 70 | +30 | 40 |
| `sp500_short_momentum` | 65 | -25 | 35 |

## Findings

- The flat `scores` array is reliable. A nested `markets`/`strategies` schema
  with long enum lists produced `confidence=0` and, in one run, truncated JSON.
- `max_tokens=8000` is too small for `reasoning_effort=max`. The model spent
  all 8,000 tokens on reasoning and returned empty or incomplete content.
  `max_tokens=32000` fixed all three failures.
- Confidence must be stated explicitly in the prompt and constrained to `1-100`.
  The earlier schema allowed `0` and the model used it for every scope.
- The model correctly inverted a short strategy: bearish US equity news was
  negative for `sp500_momentum` and positive for `sp500_short_momentum`.
- Test/demo sleeves were flagged instead of scored. `europe_short_hold` was also
  flagged in two podcast runs; the production prompt must explicitly make it
  scoreable and omit it below the relevance threshold.
- Reuters omitted the strategy rows entirely because none reached the relevance
  threshold. The production prompt should always emit all seven strategy keys,
  using the not-scored row for the three flagged sleeves.

## Limitations

- One run per item, no human labels, no inter-rater agreement.
- The sample is six items, not a representative distribution.
- The scores are contemporaneous analysis, not forecasts or trading signals.
- No claim is made about predictive accuracy.
- Podcast transcripts came from public publisher pages; display and linking
  terms still need review.

## Next

1. Build the 50-100 item human-labelled set.
2. Confirm the `europe_order_flow_stress` and `europe_short_hold` profiles.
3. Decide `high` vs `max` as the production default.
4. Confirm the OpenCode Go workload terms or create a direct DeepSeek API key.
5. Implement the production worker and member-only UI after those decisions.
