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

## Next implementation slice

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

Tests cover read-only/privacy properties, clean-but-blocked input, equivalent and conflicting duplicates, UTC identity, missing/empty/unreadable/malformed CSVs, invalid attribution/currencies/money/timestamps. The existing public-summary regression continues to require null verified returns.
