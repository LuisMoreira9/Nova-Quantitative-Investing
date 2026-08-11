"""Run with ``streamlit run dashboard/app.py`` to inspect the IBKR paper account."""

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
from dashboard.ibkr_account import load_paper_account_data


st.set_page_config(page_title="Nova IBKR Paper Trading", page_icon="📈", layout="wide")
load_local_env()
st.title("Nova IBKR Paper Trading Dashboard")
st.caption("Read-only view of the local TWS simulated account. It cannot submit, modify, or cancel orders.")
if st.sidebar.button("Refresh account data"):
    st.rerun()
st.sidebar.caption("Account data auto-refreshes every 60 seconds.")


def load_data() -> dict:
    """Use a fresh TWS snapshot on each scheduled dashboard refresh.

    Caching this response can retain an older DataFrame schema after the
    adapter gains fields such as ``market``. The dashboard refresh cadence is
    already one minute, so a cache provides no meaningful protection here.
    """
    return load_paper_account_data()


@st.fragment(run_every=60)
def render_account() -> None:
    """Render a fragment that polls TWS once per minute."""
    try:
        data = load_data()
    except Exception as exc:
        st.error(f"Could not load IBKR paper-account data: {exc}")
        return

    account = data["account"]
    history: pd.DataFrame = data["history"]
    positions: pd.DataFrame = data["positions"]
    orders: pd.DataFrame = data["orders"]
    fx_hedges: pd.DataFrame = data["fx_hedges"]
    base_currency = data["base_currency"]
    equity, cash, buying_power = float(account["equity"]), float(account["cash"]), float(account["buying_power"])
    cards = st.columns(4)
    cards[0].metric("Paper equity", f"{base_currency} {equity:,.2f}")
    cards[1].metric("Cash", f"{base_currency} {cash:,.2f}")
    cards[2].metric("Buying power", f"{base_currency} {buying_power:,.2f}")
    cards[3].metric("Open positions", len(positions))
    st.caption(f"Last refreshed: {data['loaded_at'].strftime('%Y-%m-%d %H:%M UTC')}")

    overview, position_view, hedge_view, order_view = st.tabs(["Performance", "Positions", "Currency hedge", "Orders & strategy activity"])
    with overview:
        if len(history) < 2:
            st.info("IBKR performance history begins accumulating as the dashboard refreshes.")
        else:
            chart = px.line(history, x="timestamp", y="equity", title="Paper account equity", labels={"timestamp": "Time", "equity": f"Equity ({base_currency})"})
            chart.update_layout(hovermode="x unified")
            st.plotly_chart(chart, width="stretch", config={"scrollZoom": True, "displaylogo": False})
    with position_view:
        if positions.empty:
            st.info("No open positions in the connected IBKR paper account.")
        else:
            display = positions.copy()
            display["weight"] = display["market_value"] / equity if equity else float("nan")
            formats = {"quantity": "{:,.4f}", "market_value": "{:,.2f}", "native_market_value": "{:,.2f}", "native_market_value_base": "{:,.2f}", "cost_basis": "{:,.2f}", "average_entry_price": "{:,.2f}", "current_price": "{:,.2f}", "unrealized_pl": "{:,.2f}", "unrealized_plpc": "{:.2%}", "weight": "{:.2%}"}
            st.caption("Click a position row to open that stock's order history in the Orders tab.")
            position_selection = st.dataframe(
                display.style.format({key: value for key, value in formats.items() if key in display}),
                width="stretch",
                hide_index=True,
                key="position_table",
                on_select="rerun",
                selection_mode="single-row",
            )
            if position_selection.selection.rows:
                st.session_state["ticker_filter"] = str(display.iloc[position_selection.selection.rows[0]]["symbol"])
            st.plotly_chart(px.bar(display.sort_values("market_value"), x="symbol", y="market_value", color="side", title="Open-position market value"), width="stretch", config={"displaylogo": False})
    with hedge_view:
        st.caption(f"Reporting base: {base_currency}. Currency exposure is calculated as foreign stock value plus the corresponding native cash balance.")
        st.info("This is a net-exposure report only; it does not assume or place an FX hedge.")
        if fx_hedges.empty:
            st.info("No foreign-currency positions are currently held.")
        else:
            formats = {"stock_native_exposure": "{:,.2f}", "cash_native_balance": "{:,.2f}", "net_native_exposure": "{:,.2f}", "net_base_exposure": "{:,.2f}"}
            st.dataframe(fx_hedges.style.format({key: value for key, value in formats.items() if key in fx_hedges}), width="stretch", hide_index=True)
    with order_view:
        st.caption("Nova orders carry a `nova-…` order reference.")
        if orders.empty:
            st.info("No open orders or session executions returned by TWS.")
        else:
            # Prefer friendly market labels, but accept raw broker exchange
            # values from older callback/session rows as a reliable fallback.
            market_column = "market" if "market" in orders.columns else "exchange"
            exchange_options = ["All exchanges"]
            if market_column in orders.columns:
                exchange_options.extend(sorted({value for value in orders[market_column].dropna().astype(str) if value.strip()}))
            selector_left, selector_right = st.columns(2)
            selected_stock = selector_left.text_input(
                "Find orders by ticker",
                key="ticker_filter",
                placeholder="Type a ticker, e.g. MC, ASML, AAPL",
                help="Matches the ticker exactly, ignoring upper/lower case. Click a Positions-table row to fill this automatically.",
            ).strip().upper()
            selected_exchange = selector_right.selectbox("Filter exchange", exchange_options)
            filtered_orders = orders.copy()
            if selected_stock:
                filtered_orders = filtered_orders[filtered_orders["symbol"].astype(str) == selected_stock]
            if selected_exchange != "All exchanges" and market_column in filtered_orders.columns:
                filtered_orders = filtered_orders[filtered_orders[market_column] == selected_exchange]
            st.caption(f"Showing {len(filtered_orders)} order/execution record(s).")
            counts = orders.groupby(["strategy", "status"], dropna=False).size().reset_index(name="orders")
            st.plotly_chart(px.bar(counts, x="strategy", y="orders", color="status", barmode="stack", title="Orders by strategy and status"), width="stretch", config={"displaylogo": False})
            if "submitted_at" in filtered_orders:
                filtered_orders = filtered_orders.sort_values("submitted_at", ascending=False, na_position="last")
            st.dataframe(filtered_orders.style.format({"quantity": "{:,.4f}", "filled_quantity": "{:,.4f}", "filled_avg_price": "{:,.4f}"}), width="stretch", hide_index=True)


render_account()
