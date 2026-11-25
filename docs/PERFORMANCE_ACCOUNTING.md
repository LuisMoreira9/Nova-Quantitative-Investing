# Performance accounting checkpoint — 22 October 2026

## Implemented: offline input audit

`dashboard.performance_readiness` reads the existing equity history and Nova fill ledger without importing the TWS client, publisher, pandas or price providers. It writes no files, calls no network services, and reports only counts, dates and blockers, never balances or individual fills. It is not connected to the publisher or website yet.

From this worktree, using the existing Windows environment:

```powershell
cd C:\Users\guiro\Documents\Nova-Quantitative-Investing-terminal
& ..\Nova-Quantitative-Investing\.venv-windows\Scripts\python.exe -m dashboard.performance_readiness --data-dir ..\Nova-Quantitative-Investing\data
& ..\Nova-Quantitative-Investing\.venv-windows\Scripts\python.exe -m unittest tests.test_performance_readiness
```

`status: observed` on a file means its inspected rows passed structural checks. It does **not** mean current, continuously collected, broker-reconciled, or sufficient for returns. Missing/empty/unreadable files stay unavailable. Malformed rows, naive/future timestamps, nonfinite/nonpositive numbers and conflicting duplicate identities cannot silently become valid inputs. Equivalent duplicates count once; conflicting execution IDs or UTC equity timestamps are flagged rather than resolved by taking the latest row.

The top-level readiness result intentionally stays `blocked`, with `verified_return_pct: null`, even for clean files. This audit understands only today's legacy contract; adding accounting columns or files does not promote it to ready. A future importer/reconciler must explicitly establish completeness. CLI exit zero means the audit ran, not that returns are verified; consume its JSON status.

Observed on 22 October: 2,352 unique equity observations (11 August–7 October), 1,474 unique Nova fills (11 August–6 October), zero malformed rows or conflicting duplicates. Latest portfolio observation is `2026-10-07T18:26:32.052289+00:00`. These counts are a local file checkpoint, not a hosted database acceptance or complete broker history.

## Why returns remain unavailable

- Equity history stores time and equity without a durable account/base-currency identity. Do not silently assume every historic observation belongs to today's account/currency or spans no paper-account resets.
- No reconciled funding history: an equity change cannot establish whether a deposit, withdrawal, transfer or paper reset occurred. Missing records are not proof of zero flows.
- The fill ledger stores Nova-observed executions without commission/fee records. It excludes manual/untagged activity and can miss trades during collector outages. Do not infer missing fees as zero.
- Opening holdings/cash and statement-period coverage are not reconciled. Gross externally marked strategy P&L is not strategy NAV.
- Strategy capital assignments, transfers, financing/corporate-action treatment and individual mark timestamps are not established. Test/demo sleeves must remain distinct from accepted strategy baselines.

Net account equity already reflects account expenses: importing commission records is needed for coverage and strategy attribution, not permission to subtract the same costs a second time from account equity.

## Initial importer requirements (22 October)

Build a local, read-only paper **Activity Flex XML** importer using a real export as the format contract. IBKR documents paper-account statements and Flex queries in [Client Portal for a Paper Trading Account](https://www.ibkrguides.com/clientportal/accountmanagementforpapertradingaccount.htm). Use an export from the **paper** account; no password or Flex web-service token is required for the first local importer.

The export must identify account, base currency, report period and time-zone convention and include:

1. Opening/closing NAV, positions and cash, with dates and relevant currencies.
2. Cash transactions including deposits/withdrawals/transfers and other cash activity, retaining classification, transaction ID, date, amount/currency and conversion provenance. Deposits/withdrawals are external funding; dividends/interest/fees are performance components, not external contributions. Do not classify every cash transaction as funding.
3. Execution-level trades: broker execution ID, timestamp, contract identity, side, quantity, price, currency, commissions/taxes and fee currency. Preserve corrections/cancellations, not both as independent fills. Request order references where available; IBKR documents these as available on daily single-day Activity Flex queries, so multi-day exports alone may not supply strategy attribution.
4. Coverage for missing dates and opening balances; reconcile with the observed ledger by durable execution ID before accepting a baseline. Account identity and source-file digest stay private/local.

IBKR's [execution-level Flex fields](https://www.ibkrguides.com/reportingreference/reportguide/financialinformation_fq.htm) include execution IDs, fees, currencies, taxes and corrections. [Cash Transactions](https://www.ibkrguides.com/reportingreference/reportguide/cash%20transactionsfq.htm) provides transaction-level cash details. Prospective callback capture can supplement statements: [commission-and-fee reports](https://www.interactivebrokers.com/docs/tws-api/ref/commission-and-fees-report) link costs to execution IDs and explicitly provide a fee currency. Check the installed `ibapi` callback version before changing the shared trading adapter; this slice does not alter it.

First import should only validate and produce a local reconciliation report. Then establish an explicit account baseline and accepted strategy capital ledger. Only after tested reconciliation should return calculations and member UI be connected. No guessed historical funding or backfilled percentage returns.

## Operational observation

At the 22 October checkpoint, both native reporting tasks were running, but port 7497 was closed and portfolio logs showed IBKR 502 connection failures with retries. News recovered from an earlier DNS failure to 35 items, four ranked, two sources OK and one failed at 14:16 Lisbon. User sign-in to TWS Simulated Trading is needed to restore portfolio collection. No executors were restarted and no orders were submitted.

Recovery verified later on 22 October: after the user signed in, paper API 7497 was listening and the hosted public summary advanced to observation `2026-10-22T13:30:13.503591+00:00` (14:30:13 Lisbon), status `ready`. The existing reporter recovered without a restart. This supersedes the disconnected status above; it does not prove trading executors are active. No Flex XML export was found in Downloads or the running checkout's data folder. User was asked for a paper Activity Flex XML export, stored outside Git, to establish the actual importer format. The importer is not implemented yet.

Tests cover read-only/privacy properties, clean-but-blocked input, equivalent and conflicting duplicates, UTC identity, missing/empty/unreadable/malformed CSVs, invalid attribution/currencies/money/timestamps. The existing public-summary regression continues to require null verified returns.

## Activity Flex import checkpoint — 26 October 2026

The paper Client Portal now has a reusable **NQC Accounting XML** Activity Flex Query. Its default is Last Business Day; the first run used a custom range of **11 August–23 October 2026** with daily breakouts, execution/detail records, ISO dates, timezone-tagged times, audit-trail fields, currency rates and offsetting trade/cancel pairs. Selected sections: account ID/currency/opening dates (no names or addresses), cash report and transactions, Change in NAV, corporate actions, interest accruals, NAV, open/prior-period positions, statement of funds, trades and transfers. No web-service token or API key was created. No automatic delivery was enabled.

The original 36.9 MB export is local at `C:\Users\guiro\Downloads\NQC_Accounting_XML.xml`, outside both repositories. It is not committed or uploaded. The earlier PDF remains a manual reference. Client Portal required renewed paper sign-in; the user completed authentication. Plugin searches for IBKR/Interactive Brokers and local catalog checks found no matching installable plugin; nothing was installed.

### Implemented

`dashboard/flex_import.py` parses this actual export contract locally with the standard library and Decimal money, returning private in-memory trade/cash records and a redacted JSON report. It performs no writes, broker calls, public publishing or return calculation. The source digest ties the report to the exact file. Account identity is checked for consistency inside XML, but the XML alone does not authenticate the account or certify simulated/live status.

```powershell
cd C:\Users\guiro\Documents\Nova-Quantitative-Investing-terminal
& ..\Nova-Quantitative-Investing\.venv-windows\Scripts\python.exe -m dashboard.flex_import C:\Users\guiro\Downloads\NQC_Accounting_XML.xml --ledger ..\Nova-Quantitative-Investing\data\nova_strategy_execution_ledger.csv
& ..\Nova-Quantitative-Investing\.venv-windows\Scripts\python.exe -m unittest tests.test_flex_import tests.test_performance_readiness tests.test_terminal_public_summary
```

- Rejects oversized (>64 MiB), non-UTF-8/NUL, DTD/entity, malformed/error-response XML; mixed accounts/currencies; missing required fields/fees; unsupported detail levels/children; conflicting duplicate identities and malformed local ledgers. CLI errors contain reason codes rather than financial values or paths.
- Accepts this query's daily AF statements, aware EDT/EST/UTC/GMT times and ISO dates. EDT/EST labels must agree with New York timezone rules, including DST folds/gaps. Date-only cash activity stays a date with no invented UTC instant. Commission currency is retained separately; cross-currency fees require further reconciliation.
- Checks the additive mark-to-market NAV bridge, closing NAV and successive opening/closing totals within 0.02 base-currency units. Realized/unrealized alternate views and TWR percentages are not summed into the bridge. Cash funding types are distinguished from performance income; deposits/withdrawals are compared with the daily NAV bridge. Transfers, unknown cash types and corrections require review.
- Equivalent repeated records count once; conflicts stop import. Local ledger matching uses durable execution IDs and compares symbol, currency, side, quantity, price, order reference and timestamp without changing either source.
- Validation currently covers NAV bridges, execution records and cash transactions. Cash report/open-position sections are required but their balances/positions are not yet normalized or reconciled. Corporate actions/transfers are counted and flagged, not replayed. Calendar completeness, opening baseline acceptance and strategy capital are unresolved. `validated_core_records` does not certify full-account reconciliation; the return field always stays null.

### Real-file result

Parsed **54 daily statements**, **55 distinct NAV dates**, **8,343 executions** and **53 cash transactions**. Fifty-three repeated NAV rows across daily statements deduplicated consistently. NAV bridges and the chain passed; one corporate action requires reconciliation. Cash records were dividends in lieu and broker interest, classified as performance rather than contributions. Zero external funding rows in this export is not a claim about the account's entire funding history.

All **1,474** local fill IDs matched the statement. Symbols, currencies, sides, quantities, prices and order references matched for every local fill. The statement has **6,869 additional execution IDs**, so the legacy observed ledger is incomplete. Timestamp differences are explicit: 885 rows at local-minus-statement +3600 seconds, seven at +3601, one at +3610, and eight at +1. These are discrepancies, not automatically corrected timestamps or proof of their cause. Report status remains `needs_review`, with verified return null.

Verification: **25 offline tests passed** (11 importer/reconciliation, seven readiness and seven existing public-summary). Includes unsafe input/redaction/read-only properties, exact money/signed sells, date-only cash, mixed-account/currency guards, fee currency, duplicate conflicts, funding/income separation, NAV mismatches, corrections/corporate actions, DST ambiguity and ledger discrepancies. Test data is synthetic; no real account rows were added to tests.

### Next bounded slice

Normalize and reconcile daily cash balances, holdings and the corporate action, then stage the missing broker fills and timestamp provenance in a separate private accounting ledger. Preserve the running legacy ledger until reconciliation is complete; no blind overwrite or assumed zero fees. Only then accept an opening account baseline and strategy capital/transfer rules and connect verified return calculations. The current parser is worktree-only; no running-checkout cherry-pick, reporter/executor restart, database migration or frontend deployment occurred.

## Balance reconciliation checkpoint — 25 November 2026

`dashboard.flex_import` now normalizes and reconciles the Cash Report, open positions and corporate actions in addition to the existing NAV, execution and cash-transaction checks. The report remains local and redacted: it prints counts, report dates, currency codes, tolerances and reason codes, never balances, marks, conids, symbols or private identifiers.

Implemented:

- Parses the base-currency and native-currency Cash Report rows with exact Decimal money. Checks daily `startingCash` continuity, ties base `endingCash` to `EquitySummaryInBase.cash`, and compares native trade proceeds, commissions, income and external funding with the report components.
- Parses daily `OpenPositions` at summary detail. Validates signed quantities, multiplier, mark, FX and position value, then replays each next day as prior close + signed executions + corporate-action quantity. The first statement is an explicit baseline, not a claimed opening reconciliation.
- Reconciles corporate-action quantity and cash effects. The current export contains one spinoff: its one-warrant quantity is present in the next close and the zero cash effect requires no separate cash row.
- Ties base position value (`positionValue * fxRateToBase`) to the broker's `stock`, `options`, `funds` and `bonds` NAV components. Documented tolerances: EUR 0.02 for money, 0.1% for cash/NAV and 1% for position-value/NAV.
- Adds `balance_reconciliation` to the redacted JSON report with per-area counts, mismatch dates, statement gaps and tolerances. `verified_return_pct` stays null.

Real export result with `--ledger`:

- 54 daily statements; 108 native-currency Cash Report rows; zero continuity breaks and zero statement gaps.
- 54/54 base cash balances exactly match NAV cash; zero trade, commission, income, funding or movement-component residuals.
- 53/53 position transitions match; zero quantity mismatches, zero empty snapshots and zero mark/multiplier mismatches.
- 216/216 position-value/NAV component checks matched across 54 days.
- One corporate action: quantity reconciled (1/1), zero quantity mismatches, zero unmatched cash effects.
- The ledger comparison is unchanged: all 1,474 local fill IDs matched; symbol, currency, side, quantity, price and order-reference mismatches are zero; 901 timestamps differ and 6,869 statement-only execution IDs remain outside the legacy ledger.

The top-level status remains `needs_review` because the ledger timestamp discrepancies and the missing opening/strategy-capital baselines are unresolved. This checkpoint reconciles daily broker balances and holdings; it does not certify the account opening baseline, complete calendar coverage or strategy returns.

Commands:

```powershell
cd C:\Users\guiro\Documents\Nova-Quantitative-Investing-terminal
& ..\Nova-Quantitative-Investing\.venv-windows\Scripts\python.exe -m dashboard.flex_import C:\Users\guiro\Downloads\NQC_Accounting_XML.xml --ledger ..\Nova-Quantitative-Investing\data\nova_strategy_execution_ledger.csv
& ..\Nova-Quantitative-Investing\.venv-windows\Scripts\python.exe -m unittest tests.test_flex_import tests.test_balance_reconciliation tests.test_performance_readiness tests.test_terminal_public_summary
```

Verification: 32 offline tests passed (12 importer/parsing, six balance/position/corporate-action, seven readiness and seven public-summary). Synthetic fixtures cover a flat start, shorts, multi-currency marks, corporate-action quantity and cash effects, continuity breaks, component mismatches and report redaction.

### Next bounded slice

Stage the missing broker fills and timestamp provenance in a separate private accounting ledger, then accept an explicit opening account and strategy-capital baseline. Preserve the legacy ledger until the staged ledger is verified; no automatic return calculation, publisher change or public UI exposure is included here.

## Execution staging checkpoint — 25 November 2026

`dashboard.accounting_ledger` builds an atomic, private SQLite staging ledger from the Activity Flex XML and the legacy observed CSV. It keeps the broker statement fills as the primary record, preserves the legacy timestamp and all matched legacy fields, and labels every discrepancy instead of correcting it silently. The XML and CSV remain read-only.

Implemented:

- One row per execution ID, with `record_origin` of `statement_and_ledger`, `statement` or `ledger`. All 6,869 statement-only fills are staged, not discarded because the legacy ledger missed them.
- Broker `dateTime` text and normalized UTC are stored alongside the legacy timestamp text, normalized UTC and strategy. `time_delta_seconds` is legacy minus statement; offset counts are reported, not explained or applied.
- Symbol, currency, side, quantity, price and order-reference mismatches are preserved in both forms and listed in `field_mismatches`. `review_reason` distinguishes `matched`, `timestamp_offset`, `field_mismatch`, `statement_only`, `ledger_only_in_period` and `ledger_only_outside_period`.
- Metadata records the source and legacy SHA-256 digests, statement period, base currency, counts, offset/mismatch counts and explicit `calendar_coverage_verified=false`, `opening_baseline_accepted=false`, `strategy_capital_accepted=false`.
- The database is written to a temporary file and atomically replaced, so repeated runs are deterministic and cannot leave a partial ledger. Output must be outside Git; `/data/accounting/` and `/data/*.sqlite` are ignored as a safety net.
- CLI stdout contains only status, counts, dates, digests and reason codes. It never prints symbols, conids, amounts, account identity or execution IDs.

Real export result with the legacy ledger:

- **8,343** statement executions staged: **1,474** matched, **6,869** statement-only, **0** ledger-only.
- **573** matched fills have exact timestamps; **901** differ: 885 at +3,600 seconds, seven at +3,601, one at +3,610 and eight at +1. The large group is consistent with legacy Lisbon wall-clock time stored as UTC, but the ledger records the offsets rather than asserting the cause. The small offsets remain unresolved.
- Zero symbol, currency, side, quantity, price or order-reference mismatches. Zero legacy duplicate rows and **106** equivalent statement duplicate rows were collapsed by the existing parser.

```powershell
cd C:\Users\guiro\Documents\Nova-Quantitative-Investing-terminal
& ..\Nova-Quantitative-Investing\.venv-windows\Scripts\python.exe -m dashboard.accounting_ledger C:\Users\guiro\Downloads\NQC_Accounting_XML.xml --ledger ..\Nova-Quantitative-Investing\data\nova_strategy_execution_ledger.csv --output C:\Users\guiro\Documents\Nova-Private-Accounting\nqc_execution_staging.sqlite
& ..\Nova-Quantitative-Investing\.venv-windows\Scripts\python.exe -m unittest tests.test_flex_import tests.test_balance_reconciliation tests.test_accounting_ledger tests.test_performance_readiness tests.test_terminal_public_summary
```

Verification: **38 offline tests passed** (12 importer/parsing, six balance/position/corporate-action, six staging-ledger, seven readiness and seven public-summary). Synthetic fixtures cover timestamp offsets, statement-only and ledger-only fills, field mismatches, deterministic rebuilds, read-only inputs, directory-output rejection and CLI redaction.

This checkpoint makes the missing fills reviewable; it does not accept the opening account, strategy capital or timestamp corrections. `verified_return_pct` stays null, and no publisher, reporter or website change is included.

### Next bounded slice

Accept an explicit account opening baseline and strategy-capital/transfer rules against the staged ledger. Only after those decisions are recorded should a return calculation consume the ledger; no automatic timestamp correction or public exposure is implied.
