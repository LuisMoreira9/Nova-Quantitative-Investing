# Second-device handoff — Nova terminal, news sentiment and OpenCode skills

This is the complete setup for continuing the Nova terminal/news work on another
machine with OpenCode. Commands are Windows PowerShell first; macOS/Linux
differences are noted where they matter.

The goal is a fresh clone on the new device. Do not copy the old working folders,
`node_modules`, `.svelte-kit`, `.wrangler`, `.venv` or `__pycache__`.

## Baseline

| Repo | Branch | Baseline head | PR |
| --- | --- | --- | --- |
| `novaquantclub/IBKR-Paper-Trading` | `codex/terminal-public-summary` | `6c429ac` | #4 |
| `novaquantclub/website` | `codex/terminal-restore` | `68eb64a` | #4 |

The commit that adds this document is newer than the baseline. Always confirm the
current head on the new device:

```powershell
git log -1 --oneline
```

Key documents:

- `docs/NEWS_SENTIMENT_PLAN.md` — source, taxonomy, schema, cost and rollout plan.
- `docs/NEWS_SENTIMENT_PILOT.md` — six-item pilot results and token usage.
- `docs/TERMINAL_RESTORE.md` — website restore checkpoint and production blockers.
- `docs/PERFORMANCE_ACCOUNTING.md` — accounting importer and reconciliation.

## Where we are

### Objective

Restore the member terminal on current website `main`, recover paper reporting
when TWS is available, finish accounting reconciliation, and add source-linked
news with AI bullish/bearish scores per market and strategy.

### Completed

- Website terminal restored on `codex/terminal-restore` from `origin/main` at
  `37254ed`; website PR #4 supersedes PR #2.
- Current homepage, team, partners and publications kept; retired `/research`
  remains HTTP 410.
- `/terminal/sign-in` with allowlisted destinations; public `/portfolio` reads
  `terminal_public_summary`; member-only RLS; no public monetary data.
- Development-only **Terminal** header link, gated by `$app/environment` `dev`;
  production builds omit it.
- Preview Worker `e0ef9802-5328-488a-a7a7-a4c51efeeff9` at
  `https://terminal-restore-website.quant-club.workers.dev`; public, signed-out,
  member, mobile and magic-link smokes passed.
- Supabase Auth allow-list contains exactly the three terminal callbacks; hosted
  magic-link round trip passed; logout returned 204.
- Accounting staging ledger: real export staged 8,343 executions, 1,474 matched,
  6,869 statement-only; cash/position/corporate-action reconciliation and 38
  offline tests pass.
- News feasibility checked: FT RSS, Reuters via Google News/GDELT, Yahoo
  per-symbol RSS, and the three selected podcast transcripts.
- OpenCode Go API key route confirmed; six-item pilot passed 6/6 after raising
  `max_tokens` to 32,000; flat `scores` schema validated; short-strategy
  inversion was correct.
- Project-level OpenCode skills added to both repos.

### Decisions already made

- Keep the current public site; do not restore the retired Research section.
- Keep `verified_return_pct` null until the accounting baselines are accepted.
- Test all seven current sleeves in the news pilot: four scoreable, three
  flagged as test/demo or profile-incomplete.
- Market taxonomy: 9 regions, 7 asset classes, 7 equity styles and 11 sectors.
- Score horizon is `weeks`.
- No dedicated article pages; reuse the inline Home drawer and source link.
- First podcast batch: The Macro Trading Floor, Morgan Stanley Thoughts on the
  Market and Goldman Sachs The Markets.
- Use OpenCode Go for the pilot; the production workload terms still need
  confirmation.

### Open blockers

- Production promotion awaits approval: merge website PR #4, rebuild from
  `main`, deploy, smoke-test and record the SHA and Worker version.
- TWS Simulated Trading is not listening on 7497; the last public observation is
  still 26 October 2026 18:32 UTC.
- Accounting opening baseline and strategy-capital/transfer rules need decisions.
- The invite flow shares the callback/page but has not been tested with a new
  user.
- Two positions share symbol/venue/currency with different marks; identity is
  unresolved.
- News production needs decisions on the ambiguous strategy profiles, OpenCode
  Go terms or a direct DeepSeek key, the source list, podcast display rights, a
  reviewer and a daily cost cap.

### Next steps in order

1. Review and merge website PR #4, rebuild from `main`, promote the Worker,
   smoke-test production and record the SHA/version.
2. Open TWS, verify the paper handshake and two advancing observations.
3. Accept the accounting opening and strategy-capital baselines before any
   return calculation.
4. Build the 50-100 item human-labelled news set.
5. Confirm the direction, universe and mandate of `europe_order_flow_stress` and
   `europe_short_hold`.
6. Decide the production model route: confirm OpenCode Go workload terms, or use
   a direct DeepSeek API key.
7. Implement the production news worker and member-only UI.
8. Close website PR #2 after PR #4 merges.

## 1. Before leaving the old device

1. Confirm both repos are clean and pushed:

   ```powershell
   cd C:\Users\guiro\Documents\Nova-Quantitative-Investing-terminal
   git status --short
   git log -1 --oneline

   cd C:\Users\guiro\Documents\Nova-Quant-Club-Website-restore
   git status --short
   git log -1 --oneline
   ```

2. Optional: export this OpenCode session if you want the exact conversation:

   ```powershell
   opencode session export $env:OPENCODE_SESSION_ID --sanitize |
     Out-File -Encoding utf8 "$env:USERPROFILE\Desktop\nova-session.json"
   ```

   `--sanitize` redacts sensitive transcript and file data. Review the JSON
   before transferring it.

3. Copy secrets through a password manager or encrypted transfer — never Git,
   chat, or a normal shared folder:

   - OpenCode Go API key (`oc_sk_...`).
   - Supabase service-role key, only if the new device will publish.
   - Any local `.env` values that are not public.
   - Private accounting files, only if continuing that work.

4. Do not copy:

   - `node_modules`, `.svelte-kit`, `.wrangler`.
   - `.venv`, `venv`, `__pycache__`, `.pytest_cache`.
   - `.env`, `.dev.vars`.
   - `Nova-Private-Accounting\nqc_execution_staging.sqlite`.
   - `Downloads\NQC_Accounting_XML.xml`.
   - TWS credentials.

## 2. Install the tools

Install:

- Git.
- GitHub CLI (`gh`).
- Node.js 24.
- Python 3.12 or newer.
- OpenCode V2.

```powershell
git --version
gh --version
node --version
python --version

npm install -g @opencode/cli@latest
opencode --version
```

macOS/Linux OpenCode install alternative:

```bash
curl -fsSL https://opencode.ai/v2/install | bash
```

Sign in:

```powershell
gh auth login
gh auth setup-git

opencode auth login
# choose OpenCode Go and paste your Go API key
# or start `opencode` and use /connect
```

Verify the model is available:

```powershell
opencode models | Select-String "deepseek-v4.1-flash"
```

On macOS/Linux use `grep` instead of `Select-String`.

## 3. Clone both repos

The local folder names do not matter; use the canonical GitHub repos.

```powershell
mkdir C:\dev
cd C:\dev

git clone https://github.com/novaquantclub/IBKR-Paper-Trading.git
cd IBKR-Paper-Trading
git checkout codex/terminal-public-summary
git log -1 --oneline

cd C:\dev
git clone https://github.com/novaquantclub/website.git
cd website
git checkout codex/terminal-restore
git log -1 --oneline
```

If you still push to the personal backend remote, add it explicitly:

```powershell
cd C:\dev\IBKR-Paper-Trading
git remote add personal https://github.com/LuisMoreira9/Nova-Quantitative-Investing.git
```

## 4. Project-level skills — already committed

These are in the repos and are discovered automatically when OpenCode starts
from the repo root. No install command is needed.

| Repo | Project skills |
| --- | --- |
| Website | `sveltekit-layerchart`, `playwright-verification`, `portfolio-var` |
| Backend | `python-quant-var`, `portfolio-var` |

They live at:

```text
.opencode/skills/<skill-id>/SKILL.md
```

`portfolio-var` is intentionally duplicated because it spans the backend,
Supabase and website. The project copies are portable and no longer contain the
old absolute Windows checkout paths.

If a skill does not appear:

1. Start OpenCode from the repo root.
2. Confirm the file is exactly `.opencode/skills/<id>/SKILL.md`.
3. Confirm the frontmatter has a `description`.
4. Confirm you are on OpenCode V2.

To add another project skill:

```powershell
mkdir .opencode\skills\<skill-id>
# create .opencode\skills\<skill-id>\SKILL.md
git add .opencode/skills
```

## 5. Global skills to install on the new device

The project skills reference some general skills. Copy these from the old
machine's `C:\Users\guiro\.agents\skills` into the new machine's:

```text
~/.config/opencode/skills/<skill-id>/
```

OpenCode also discovers `~/.agents/skills`, so copying the whole folder there
works too.

### Required

| Skill | Why |
| --- | --- |
| `supabase` | Auth, RLS, database and client work |
| `supabase-postgres-best-practices` | Schema, migrations, RLS and indexes |
| `wrangler` | Cloudflare Worker config and deployment |
| `workers-best-practices` | Worker code review and production patterns |
| `web-perf` | Core Web Vitals and loading audits |

### Recommended

| Skill | Why |
| --- | --- |
| `cloudflare` | Product selection and architecture |
| `gh-attach` | Screenshots and files in PRs/issues |
| `git-commit` | Conventional commit workflow |
| `opencode` | OpenCode config, CLI and troubleshooting |

### Conditional

| Skill | When |
| --- | --- |
| `turnstile-spin` | Contact form / Turnstile work |
| `cloudflare-email-service` | Resend / Email Routing work |
| `github-release` | Release and changelog work |

### Optional

- `ponytail`, `ponytail-review` — minimalism/code-review passes.
- `browser` — built in; no install needed.

### Not needed for this project

`agents-sdk`, `durable-objects`, `cloudflare-one*`, `nextjs-on-cloudflare`,
`sandbox-*`.

### Copy command

On the old device:

```powershell
Compress-Archive -Path "$env:USERPROFILE\.agents\skills\*" `
  -DestinationPath "$env:USERPROFILE\Desktop\opencode-skills.zip"
```

On the new device:

```powershell
New-Item -ItemType Directory -Force "$env:USERPROFILE\.config\opencode\skills" | Out-Null
Expand-Archive "$env:USERPROFILE\Downloads\opencode-skills.zip" `
  -DestinationPath "$env:USERPROFILE\.config\opencode\skills"
```

Project-level copies take precedence over global copies.

## 6. Svelte MCP for website work

The website `AGENTS.md` expects the Svelte MCP tools. The VS Code config already
has the server, but OpenCode needs its own project config:

```powershell
cd C:\dev\website
opencode mcp add svelte -- npx -y @sveltejs/mcp
```

This is project-level. Commit the generated config if you want it shared with the
repo. If the MCP tools are not exposed, the agent should report the gap rather
than claim it used them.

## 7. Backend setup

```powershell
cd C:\dev\IBKR-Paper-Trading

py -3.14 -m venv .venv
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1

python -m pip install --upgrade pip
pip install -r requirements.txt

Copy-Item .env.example .env
# edit .env:
# OPENCODE_API_KEY=oc_sk_...
# plus IBKR_* and SUPABASE_* only if running the paper host/publisher
```

If `py -3.14` is unavailable, use:

```powershell
python -m venv .venv
```

`requirements.txt` includes the news dependencies: `requests`,
`beautifulsoup4` and `pypdf`.

Run the offline tests:

```powershell
python -m unittest discover -s tests
```

If `ibapi` fails to build, use Python 3.12 or 3.13 for the backend environment.

### TWS paper host

Only if this device will run the paper executor:

1. Install Trader Workstation.
2. Log into the paper account.
3. Enable ActiveX and Socket Clients.
4. Use `127.0.0.1:7497`.
5. Keep `IBKR_CLIENT_ID=101` unless another machine is also running an executor.

Only one host should trade the paper account at a time. If two hosts must run,
give them different client IDs.

### News classifier key

The backend `.env.example` now documents:

```text
OPENCODE_API_KEY=
NOVA_NEWS_REASONING_EFFORT=high
NOVA_NEWS_AI_DAILY_USD=5
```

Use a dedicated OpenCode Go service-account key restricted to
**Models: Request**. Never commit the real value.

## 8. Website setup

```powershell
cd C:\dev\website

corepack enable
corepack prepare pnpm@latest --activate
pnpm install

Copy-Item .env.example .env
# fill:
# PUBLIC_SUPABASE_URL
# PUBLIC_SUPABASE_PUBLISHABLE_KEY
# PUBLIC_TURNSTILE_SITE_KEY
# These are public values already in wrangler.jsonc.
```

Start the dev server:

```powershell
pnpm dev
# http://127.0.0.1:5173
```

Verify:

```powershell
pnpm check
pnpm lint
node --test tests/*.test.mjs
pnpm build
```

`pnpm check` is the main gate. `pnpm lint` may report existing warnings in
contact/publications; record them separately from new regressions.

### Browser fixture suite

```powershell
$env:PUBLIC_SUPABASE_URL='https://terminal-fixture.invalid'
$env:PUBLIC_SUPABASE_PUBLISHABLE_KEY='fixture-publishable'
pnpm dev -- --host 127.0.0.1 --port 5173
```

In another terminal:

```powershell
$env:TERMINAL_TEST_URL='http://127.0.0.1:5173'
node tests/terminal-workspaces.test.mjs
```

If Chrome is not found, set `PLAYWRIGHT_EXECUTABLE_PATH` or
`PLAYWRIGHT_CHANNEL`.

### Preview and production

The current preview is:

```text
https://terminal-restore-website.quant-club.workers.dev
```

Production promotion is still pending approval. Do not deploy from the new
device unless that is the agreed next step. `wrangler.jsonc` contains the public
Supabase and Turnstile values; deployment still needs Cloudflare authentication.

## 9. OpenCode session continuity

OpenCode sessions are local and do not sync automatically. Three options:

### Recommended — start fresh

Start OpenCode in the backend or website repo and say:

> Read `docs/NEWS_SENTIMENT_PLAN.md` and `docs/NEWS_SENTIMENT_PILOT.md`.
> Current backend head is `6c429ac`, website head is `68eb64a`.
> Continue with the human-labelled 50-100 item pilot.

This is cleaner than transferring a long session.

### Quick live handoff — `opencode pair`

On the old machine:

```powershell
opencode pair
```

Open the one-time link on the new device. Links expire in 5 minutes.

`opencode pair --remote` exposes the server to the internet. Use it only
temporarily and only on a trusted network.

### Move the session — export/import

On the old machine:

```powershell
opencode session export $env:OPENCODE_SESSION_ID --sanitize |
  Out-File -Encoding utf8 "$env:USERPROFILE\Desktop\nova-session.json"
```

Transfer it securely, then on the new device:

```powershell
opencode session import .\nova-session.json --directory C:\dev\IBKR-Paper-Trading
```

Review the JSON before sharing it. The session may contain file contents and
tool output even after sanitization.

## 10. Secrets and private data

| Item | Where it belongs | Notes |
| --- | --- | --- |
| OpenCode Go API key | Backend `.env` as `OPENCODE_API_KEY` | Create a service-account key; never commit |
| Supabase service-role key | Backend `.env` only | Never in the website or repo |
| Supabase publishable key | Website `.env` / `wrangler.jsonc` | Public, safe to expose |
| Turnstile site key | Website `.env` / `wrangler.jsonc` | Public |
| Turnstile secret / Resend key | `.dev.vars` or Cloudflare secrets | Never in Git |
| IBKR/TWS credentials | TWS itself | Never in `.env`, chat or Git |
| Accounting SQLite ledger | Private encrypted storage | Not in Git |
| Flex XML export | Private encrypted storage | Re-download from IBKR if possible |

The website `.env` needs only public values. The backend `.env` is gitignored.
Private accounting files are outside Git and should be transferred with an
encrypted drive/password manager, or recreated on the new device.

## 11. Verification checklist

```powershell
cd C:\dev\IBKR-Paper-Trading
git log -1 --oneline          # baseline 6c429ac
python -m unittest discover -s tests

cd C:\dev\website
git log -1 --oneline          # baseline 68eb64a
pnpm check
node --test tests/*.test.mjs
pnpm build
pnpm dev
```

Then open:

```text
http://127.0.0.1:5173/terminal
```

Confirm the member terminal still loads. Signed-out access should reach
`/terminal/sign-in?next=terminal`.

## 12. Immediate next action

The ordered list is in **Where we are → Next steps in order**. Start with the
production promotion checklist unless the agreed priority is the news work; in
that case start with the 50-100 item human-labelled set.

Do not implement the production news worker until the ambiguous strategy
profiles and the production model route are decided.

No schema, classifier, publisher, UI, order or verified-return change is pending
from this handoff.

## 13. Troubleshooting

| Problem | Fix |
| --- | --- |
| `pnpm` not found | `corepack enable` then `corepack prepare pnpm@latest --activate`; or `npm install -g pnpm` |
| `ibapi` build fails | Use Python 3.12/3.13 for the backend venv |
| OpenCode skill missing | Check `.opencode/skills/<id>/SKILL.md`, description and V2 |
| OpenCode Go auth fails | Run `opencode auth login` or `/connect`; confirm the Go subscription |
| TWS connection refused | Confirm TWS is running, paper account is logged in, API is enabled and port is 7497 |
| Supabase member reads return zero rows | Confirm the session is active and RLS policies are unchanged |
| Local website has no Supabase data | Fill the public `.env` values from `wrangler.jsonc` |
| Browser test cannot find Chrome | Set `PLAYWRIGHT_EXECUTABLE_PATH` or `PLAYWRIGHT_CHANNEL` |
