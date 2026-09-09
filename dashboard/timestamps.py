"""Timezone-safe conversion for timestamps supplied by IBKR/TWS."""

from __future__ import annotations

from datetime import datetime, timezone
import os
from zoneinfo import ZoneInfo


DEFAULT_TWS_TIMEZONE = "Europe/Lisbon"


def ibkr_execution_timestamp(value: object) -> datetime | None:
    """Return an IBKR execution time as an aware UTC datetime.

    TWS delivers executions in its configured local wall-clock time as
    ``YYYYMMDD  HH:MM:SS`` without an offset.  Treating that string as UTC
    shifts every Portuguese summer-time fill by one hour on public dashboards.
    ISO-8601 values that already include an offset preserve their instant
    and are normalized to UTC.
    """

    text = str(value or "").strip()
    if not text:
        return None
    compact = " ".join(text.split())
    try:
        local_time = datetime.strptime(compact, "%Y%m%d %H:%M:%S")
    except ValueError:
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=ZoneInfo(os.getenv("IBKR_TWS_TIMEZONE", DEFAULT_TWS_TIMEZONE)))
        return parsed.astimezone(timezone.utc)
    return local_time.replace(
        tzinfo=ZoneInfo(os.getenv("IBKR_TWS_TIMEZONE", DEFAULT_TWS_TIMEZONE))
    ).astimezone(timezone.utc)
