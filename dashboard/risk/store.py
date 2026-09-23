"""Local SQLite cache for VaR: prices, audit inputs, pending publications. Stdlib only."""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path


DEFAULT_DB_PATH = Path(__file__).resolve().parents[2] / "data" / "risk.db"
AUDIT_RETENTION_SECONDS = 30 * 24 * 3600
PRICE_MAX_ROWS = 200_000


def connect(path: Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS price_cache (
            ticker TEXT NOT NULL,
            date TEXT NOT NULL,
            close REAL NOT NULL,
            fetched_at REAL NOT NULL,
            PRIMARY KEY (ticker, date)
        );
        CREATE TABLE IF NOT EXISTS audit_inputs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            snapshot_hash TEXT NOT NULL,
            inputs_json TEXT NOT NULL,
            created_at REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS audit_inputs_created_at_idx ON audit_inputs (created_at);
        CREATE TABLE IF NOT EXISTS pending_publications (
            id TEXT PRIMARY KEY,
            payload_json TEXT NOT NULL,
            created_at REAL NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS risk_state (
            key TEXT PRIMARY KEY,
            value_json TEXT NOT NULL,
            updated_at REAL NOT NULL
        );
        """
    )
    return conn


def save_prices(conn: sqlite3.Connection, ticker: str, rows: list[tuple[str, float]], fetched_at: float | None = None) -> None:
    ts = fetched_at if fetched_at is not None else time.time()
    conn.executemany(
        "INSERT OR REPLACE INTO price_cache (ticker, date, close, fetched_at) VALUES (?,?,?,?)",
        [(ticker.upper(), d, float(c), ts) for d, c in rows],
    )
    # Bounded reusable cache: keep newest rows only.
    conn.execute(
        "DELETE FROM price_cache WHERE rowid NOT IN (SELECT rowid FROM price_cache ORDER BY fetched_at DESC, date DESC LIMIT ?)",
        (PRICE_MAX_ROWS,),
    )
    conn.commit()


def load_prices(conn: sqlite3.Connection, ticker: str) -> list[tuple[str, float]]:
    cur = conn.execute(
        "SELECT date, close FROM price_cache WHERE ticker=? ORDER BY date ASC", (ticker.upper(),)
    )
    return [(d, float(c)) for d, c in cur.fetchall()]


def replace_price_window(conn: sqlite3.Connection, data: dict[str, list[tuple[str, float]]]) -> None:
    """Refresh the complete cached window (never mix adjustment bases)."""
    ts = time.time()
    tickers = [t.upper() for t in data]
    if tickers:
        conn.execute(
            f"DELETE FROM price_cache WHERE ticker IN ({','.join('?' for _ in tickers)})",
            tickers,
        )
    for ticker, rows in data.items():
        conn.executemany(
            "INSERT OR REPLACE INTO price_cache (ticker, date, close, fetched_at) VALUES (?,?,?,?)",
            [(ticker.upper(), d, float(c), ts) for d, c in rows],
        )
    conn.execute(
        "DELETE FROM price_cache WHERE rowid NOT IN (SELECT rowid FROM price_cache ORDER BY fetched_at DESC, date DESC LIMIT ?)",
        (PRICE_MAX_ROWS,),
    )
    conn.commit()


def save_audit(conn: sqlite3.Connection, snapshot_hash: str, inputs: dict) -> None:
    conn.execute(
        "INSERT INTO audit_inputs (snapshot_hash, inputs_json, created_at) VALUES (?,?,?)",
        (snapshot_hash, json.dumps(inputs, default=str), time.time()),
    )
    conn.execute(
        "DELETE FROM audit_inputs WHERE created_at < ?", (time.time() - AUDIT_RETENTION_SECONDS,)
    )
    conn.commit()


def _payload_time(payload: dict) -> str:
    """Return the newest observation timestamp carried by a queued payload."""

    for key in ("observed_at", "calculated_at"):
        value = payload.get(key)
        if value:
            return str(value)
    return ""


def queue_pending(conn: sqlite3.Connection, key: str, payload: dict) -> None:
    """Stage a risk observation for retry; newest observation wins per key.

    Minute keys are unique per UTC minute so retries keep the original
    timestamps. Daily keys cover a whole UTC date, so a newer observation for
    the same date replaces the staged one — the daily table must hold the
    final observed state, and collapsing here prevents a delayed retry from
    overwriting a newer server record after the drain.
    """
    existing = conn.execute(
        "SELECT payload_json FROM pending_publications WHERE id=?", (key,)
    ).fetchone()
    if existing is not None:
        try:
            old_payload = json.loads(existing[0])
        except Exception:
            old_payload = {}
        if _payload_time(payload) <= _payload_time(old_payload):
            return
        conn.execute(
            "UPDATE pending_publications SET payload_json=? WHERE id=?",
            (json.dumps(payload, default=str), key),
        )
    else:
        conn.execute(
            "INSERT INTO pending_publications (id, payload_json, created_at, attempts) VALUES (?,?,?,0)",
            (key, json.dumps(payload, default=str), time.time()),
        )
    conn.commit()


def load_all_prices(conn: sqlite3.Connection) -> dict[str, list[tuple[str, float]]]:
    """Return the full persisted price cache (ticker -> ascending rows)."""

    cur = conn.execute("SELECT ticker, date, close FROM price_cache ORDER BY ticker ASC, date ASC")
    out: dict[str, list[tuple[str, float]]] = {}
    for ticker, d, c in cur.fetchall():
        out.setdefault(str(ticker).upper(), []).append((str(d), float(c)))
    return out


def load_latest_valid(conn: sqlite3.Connection) -> dict | None:
    """Return the most recent audit entry holding a ready VaR result, if any."""

    cur = conn.execute(
        "SELECT inputs_json FROM audit_inputs ORDER BY created_at DESC LIMIT 50"
    )
    for (raw,) in cur.fetchall():
        try:
            entry = json.loads(raw)
        except Exception:
            continue
        if isinstance(entry, dict) and entry.get("status") == "ready" and entry.get("last_valid"):
            return entry["last_valid"]
    return None


def save_last_valid(conn: sqlite3.Connection, last_valid: dict) -> None:
    """Persist the last valid estimate separately from the audit trail.

    The audit table is append-only and pruned by age, so after a long run of
    unavailable observations the latest valid record would fall out of any
    bounded recent scan. This key-value slot always holds the newest one.
    """

    conn.execute(
        "INSERT INTO risk_state (key, value_json, updated_at) VALUES ('last_valid', ?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json, updated_at=excluded.updated_at",
        (json.dumps(last_valid, default=str), time.time()),
    )
    conn.commit()


def load_last_valid(conn: sqlite3.Connection) -> dict | None:
    """Return the separately persisted last valid estimate, if any."""

    cur = conn.execute("SELECT value_json FROM risk_state WHERE key='last_valid'")
    row = cur.fetchone()
    if row is None:
        return None
    try:
        value = json.loads(row[0])
    except Exception:
        return None
    return value if isinstance(value, dict) else None


def list_pending(conn: sqlite3.Connection, limit: int = 50) -> list[tuple[str, dict, int]]:
    cur = conn.execute(
        "SELECT id, payload_json, attempts FROM pending_publications ORDER BY created_at ASC LIMIT ?", (limit,)
    )
    return [(row[0], json.loads(row[1]), int(row[2])) for row in cur.fetchall()]


def mark_pending_result(conn: sqlite3.Connection, key: str, ok: bool) -> None:
    if ok:
        conn.execute("DELETE FROM pending_publications WHERE id=?", (key,))
    else:
        conn.execute("UPDATE pending_publications SET attempts=attempts+1 WHERE id=?", (key,))
    conn.commit()
