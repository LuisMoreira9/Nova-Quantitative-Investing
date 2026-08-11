"""Run with ``streamlit run dashboard/app.py`` to inspect Nova paper trading."""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.main_executor import load_local_env
from dashboard.alpaca_account import PaperAccountDataError, load_paper_account_data


st.set_page_config(page_title="Nova Paper Trading", page_icon="📈", layout="wide")
load_local_env()
st.title("Nova Paper Trading Dashboard")
st.caption("Read-only view of the connected Alpaca paper account — not a historical snapshot backtest.")

period = st.sidebar.selectbox("Equity-history period", ["1D", "1W", "1M", "3M", "1A", "all"], index=4)
if st.sidebar.button("Refresh account data"):
    st.cache_data.clear()


@st.cache_data(ttl=60, show_spinner="Loading Alpaca paper account…")
def load_data(selected_period: str) -> dict:
    return load_paper_account_data(selected_period)


try:
    data = load_data(period)
except Exception as exc:
    st.error(f"Could not load Alpaca paper-account data: {exc}")
    st.stop()

account = data["account"]
history: pd.DataFrame = data["history"]
positions: pd.DataFrame = data["positions"]
orders: pd.DataFrame = data["orders"]

equity = float(getattr(account, "equity", 0) or 0)
cash = float(getattr(account, "cash", 0) or 0)
buying_power = float(getattr(account, "buying_power", 0) or 0)
portfolio_change = float(getattr(account, "equity", 0) or 0) - float(getattr(account, "last_equity", 0) or 0)
cards = st.columns(4)
cards[0].metric("Paper equity", f"${equity:,.2f}", f"${portfolio_change:,.2f} today")
cards[1].metric("Cash", f"${cash:,.2f}")
cards[2].metric("Buying power", f"${buying_power:,.2f}")
cards[3].metric("Open positions", len(positions))
st.caption(f"Last refreshed: {data['loaded_at'].strftime('%Y-%m-%d %H:%M UTC')}")

overview, position_view, order_view = st.tabs(["Performance", "Positions", "Orders & strategy activity"])

with overview:
    if history.empty:
        st.info("Alpaca has not returned portfolio-history points for this period yet.")
    else:
        chart = px.line(history, x="timestamp", y="equity", title="Paper account equity", labels={"timestamp": "Time", "equity": "Equity (USD)"})
        chart.update_layout(hovermode="x unified")
        st.plotly_chart(chart, width="stretch", config={"scrollZoom": True, "displaylogo": False})
        returns = history["equity"].pct_change().dropna()
        if not returns.empty:
            performance = st.columns(3)
            performance[0].metric("Period return", f"{history['equity'].iloc[-1] / history['equity'].iloc[0] - 1:.2%}")
            performance[1].metric("Period high", f"${history['equity'].max():,.2f}")
            drawdown = history["equity"] / history["equity"].cummax() - 1
            performance[2].metric("Max drawdown", f"{drawdown.min():.2%}")

with position_view:
    if positions.empty:
        st.info("No open positions in the connected paper account.")
    else:
        display = positions.copy()
        display["weight"] = display["market_value"] / equity if equity else float("nan")
        st.dataframe(display.style.format({"quantity": "{:,.4f}", "market_value": "${:,.2f}", "cost_basis": "${:,.2f}", "average_entry_price": "${:,.2f}", "current_price": "${:,.2f}", "unrealized_pl": "${:,.2f}", "unrealized_plpc": "{:.2%}", "weight": "{:.2%}"}), width="stretch", hide_index=True)
        exposure = px.bar(display.sort_values("market_value"), x="symbol", y="market_value", color="side", title="Open-position market value")
        st.plotly_chart(exposure, width="stretch", config={"displaylogo": False})

with order_view:
    st.caption("Orders with a `nova-…` client order ID are attributed to the strategy class that emitted the signal. Existing Alpaca orders remain untagged.")
    if orders.empty:
        st.info("No orders returned by Alpaca for this account.")
    else:
        strategy_counts = orders.groupby(["strategy", "status"], dropna=False).size().reset_index(name="orders")
        st.plotly_chart(px.bar(strategy_counts, x="strategy", y="orders", color="status", barmode="stack", title="Orders by strategy and status"), width="stretch", config={"displaylogo": False})
        st.dataframe(orders.style.format({"quantity": "{:,.4f}", "filled_quantity": "{:,.4f}", "filled_avg_price": "${:,.4f}"}), width="stretch", hide_index=True)
