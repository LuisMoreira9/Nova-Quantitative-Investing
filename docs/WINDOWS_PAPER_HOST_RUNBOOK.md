# Windows paper-trading host runbook

This runbook is for a dedicated **Windows** machine running Nova's IBKR
**simulated/paper** account. It intentionally uses a Windows virtual
environment for broker-connected services: TWS is on Windows loopback, whereas
WSL may be able to open port 7497 without completing the IBKR API handshake.

## What starts

The maintained paper strategy set on `main` is:

- `core.sp500_yfinance_executor` — S&P 500 long sleeve;
- `core.europe_yfinance_executor` — approved Europe registry sleeve;
- `core.sp500_short_yfinance_executor` — explicitly opt-in, capped short demo.

The FX hedge executor is **not** started by default. It requires
`NOVA_FX_HEDGE_ENABLED=CONFIRM` and submits FX orders, so enable it only after a
separate review. The STOXX 600 stress executor is on its own experimental
branch and is not a normal host service.

## One-time setup

Open PowerShell in the repository root, then fetch the approved production
branch and create the Windows environment:

```powershell
git fetch nova-quant-club --prune
git switch main
git pull --ff-only nova-quant-club main
py -3 -m venv .venv-windows
& .\.venv-windows\Scripts\python.exe -m pip install --upgrade pip
& .\.venv-windows\Scripts\python.exe -m pip install -r requirements.txt
```

Install the website dependencies once:

```powershell
$env:CI = 'true'
corepack pnpm --dir website install --frozen-lockfile
```

If pnpm reports ignored build scripts, use `pnpm approve-builds` interactively
and approve only the expected packages (`esbuild`, `sharp`, and `workerd`), then
repeat the install. Do not delete `node_modules` manually.

Copy `.env.example` to `.env` if `.env` does not exist. It must remain ignored
by Git. For paper TWS on the Windows host, use:

```text
IBKR_HOST=127.0.0.1
IBKR_PORT=7497
IBKR_CLIENT_ID=101
```

The short demo additionally requires the deliberate confirmation already used
in this project:

```text
NOVA_SHORT_SELLING_PAPER_TEST=CONFIRM
```

## TWS paper session

1. Start Trader Workstation and select **Simulated Trading**.
2. Log in.
3. In `Global Configuration -> API -> Settings`, enable **ActiveX and Socket
   Clients**, disable **Read-Only API**, and confirm paper TWS port `7497`.
4. Before starting any executor, verify the socket:

```powershell
Test-NetConnection 127.0.0.1 -Port 7497
```

`TcpTestSucceeded : True` is required. The strategy executors submit only paper
orders, but they can still create many orders under the deliberately loose test
thresholds, so keep TWS visible during testing.

## Start services

Set the Python command once in each PowerShell session:

```powershell
$python = .\.venv-windows\Scripts\python.exe
```

Run these in **separate PowerShell windows** so each process can be observed and
stopped with `Ctrl+C`:

```powershell
# Read-only broker bridge for the website
& $python -m dashboard.portfolio_api

# Streamlit account dashboard
& $python -m streamlit run dashboard/app.py --server.address 127.0.0.1 --server.port 8501

# Local website portfolio page
cd website
.\node_modules\.bin\vite.cmd --host 127.0.0.1 --port 5173
```

Open:

- Website portfolio page: `http://127.0.0.1:5173/portfolio`
- Streamlit dashboard: `http://127.0.0.1:8501`
- Portfolio bridge health: `http://127.0.0.1:8765/health`

Confirm the bridge can fetch an account snapshot before enabling orders:

```powershell
(Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8765/api/portfolio).StatusCode
```

It should return `200`.

By default Streamlit refreshes only its account fragment every 10 seconds and
the website refreshes its local snapshot every 5 seconds. These values can be
increased on a constrained host with `NOVA_DASHBOARD_REFRESH_SECONDS` and
`NOVA_PORTFOLIO_API_CACHE_SECONDS` in `.env`; do not set either below 5 seconds.

Then, in three further PowerShell windows from the repository root:

```powershell
& $python -m core.sp500_yfinance_executor
& $python -m core.europe_yfinance_executor
& $python -m core.sp500_short_yfinance_executor
```

Logs are written below `data/`, notably:

- `sp500_yfinance_executor_error.log`
- `europe_yfinance_executor_error.log`
- `sp500_short_yfinance_executor_error.log`
- `streamlit_dashboard_error.log`
- `portfolio_api_error.log`

Use PowerShell (not Git Bash) for `Get-Content`:

```powershell
Get-Content data\sp500_yfinance_executor_error.log -Tail 50
```

## Stopping safely

Use `Ctrl+C` in the individual service/executor windows. Do not terminate TWS
before the executors: stop the order processes first, then the dashboards, then
log out of TWS. If a process is detached, identify it before stopping it:

```powershell
Get-CimInstance Win32_Process | Where-Object {
  $_.CommandLine -match 'core\\.(sp500_yfinance_executor|europe_yfinance_executor|sp500_short_yfinance_executor)'
} | Select-Object ProcessId, CommandLine
```

## Public website and Supabase

The local Vite website reads from the local bridge. The public portfolio page
requires the separately reviewed Supabase publisher/client deployment. The
service-role key belongs **only** in the host's ignored `.env`; it must never be
placed in website code, Git, logs, or chat. The public website receives only the
publishable key and reads the latest sanitized snapshot.
