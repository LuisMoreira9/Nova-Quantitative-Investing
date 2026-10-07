# Reporting tasks on Windows

These tasks manage outbound reporting only. They do not start, stop or supervise trading executors, and they never submit orders. TWS must already be authenticated to a paper account for fresh portfolio observations.

## Install on the running checkout

Install the committed files in the host checkout first, preserving `.env`, data and unrelated changes. From that checkout:

```powershell
.\.venv-windows\Scripts\python.exe -m unittest tests.test_reporting_service tests.test_terminal_news tests.test_terminal_public_summary
.\scripts\reporting-tasks.ps1 -Action Install
.\scripts\reporting-tasks.ps1 -Action Status
```

The script registers `NQC-Portfolio-Reporting` and `NQC-News-Reporting` for the current Windows user, at limited privilege with an interactive login. No Windows/TWS password is stored. Tasks start at login, have a five-minute recovery trigger and three one-minute retries on unexpected failure. Multiple task instances are ignored. The reporter entry points also acquire independent OS file locks, so manual launches of the updated modules cannot create duplicate publishers. Existing older code must be stopped before installing updated tasks.

`pythonw.exe` keeps background tasks hidden. Each task uses the checkout's existing Windows venv and local `.env`. The runner preserves `SSL_CERT_FILE` if set and otherwise uses the installed certifi bundle. Standard output/errors and Python logging go to ignored `data/reporting-portfolio.log` and `data/reporting-news.log`. Each rotates at 5 MiB with three backups (about 20 MiB per service). Logs can contain private broker details; never commit or publicly upload them. OS locks release on process exit/crash; leave lock files in place.

## Verify and recover

Task state `Running` or a listening TWS port does not prove successful collection. Check advancing timestamps in `terminal_public_summary` and `terminal_portfolio_latest`, source-health updates and successive successful log lines. If TWS, DNS or Supabase fails, the existing watch loops retry; stale observations remain stale on the website. Task Scheduler restarts processes that exit, not processes that hang. Hangs require a separate health investigation before restarting; do not blindly start another reporter.

The lock regression test uses a real disposable subprocess: duplicate acquisition fails, the other service can run, and terminating the test process releases its lock. It does not terminate any real publisher or broker process.

```powershell
# Disable recovery triggers and stop only these reporting tasks.
.\scripts\reporting-tasks.ps1 -Action Stop
# Remove these task registrations; local logs and reporting history remain.
.\scripts\reporting-tasks.ps1 -Action Uninstall
```

Re-running Install re-enables the two task definitions. Recheck process identities before stopping a manually launched reporter; a Windows venv launcher/worker pair is one service.

## Availability limits

This PC cannot collect while asleep or powered off. Tasks resume after Windows login while the host is awake. Closing Codex or a browser does not stop Task Scheduler. Windows sign-out ends this interactive task context, and TWS authentication is still required after a reboot. Continuous coverage requires an always-on host and a supported broker gateway/login arrangement; no power settings or TWS credentials were changed here.

Native scheduling references: [Microsoft task settings](https://learn.microsoft.com/en-us/powershell/module/scheduledtasks/new-scheduledtasksettingsset), [Windows logon types](https://learn.microsoft.com/en-us/windows/win32/api/taskschd/ne-taskschd-task_logon_type). Broker dependency: [IB Gateway/TWS architecture](https://www.interactivebrokers.com/docs/tws-api/doc/architecture/the-trader-workstation/the-ib-gateway).

## News ranking revision 4

Routine Fed/ECB institution names no longer establish macro or regional relevance. Actual rates, inflation and monetary-policy topics can earn the existing capped broad-market score when holdings exist; held symbols and verified factor matches still count. Gross exposure includes absolute short values, preserves actual zero base values and skips malformed/nonfinite values. This is a relevance heuristic, not a return-impact prediction. Strategy-specific matching remains unimplemented.

Geography is a catalogue instrument mandate or an official central-bank policy mandate, never a newspaper's headquarters or an extracted article event location. Policy stories from the reviewed Fed source map to US; the reviewed ECB source maps to the 21 euro-area members as of 7 October 2026, including Bulgaria. Unknown geography remains Global. The UI evidence drawer identifies the mandate kind; this does not model cross-border spillovers. References are recorded next to `POLICY_COUNTRIES` in the collector.

Deploy the frontend's `score=gt.0` Ranked filter before updating the collector: revision 4 upserts zero scores so reclassified headlines lose obsolete positive relevance. Zero rows remain inspectable and headlines remain in Latest. Only stories still returned by current feeds are recomputed; older retained stories can retain an earlier ranking version. No migration or backfill is needed.

For a news-only update, disable and stop `NQC-News-Reporting`, cherry-pick the tested code, run tests and `python -m dashboard.terminal_news --once` (read-only), then enable and start that exact task. Confirm a successful cycle and revision 4 rows. Leave `NQC-Portfolio-Reporting` running. Never launch a second watcher as a recovery workaround.
