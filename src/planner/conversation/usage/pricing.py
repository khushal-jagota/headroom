"""Persist the full price snapshot once; all summaries price from owned state."""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from typing import cast

import httpx

from planner.conversation.usage.adapters.prices import (
    LITELLM_RATES_URL,
    MAX_RATES_BYTES,
    RATES_REFRESH_FLOOR_SECONDS,
    RATES_TTL_SECONDS,
    fetch_rate_document,
)
from planner.conversation.usage.contracts import UsagePriceSnapshot, UsagePricingSummary
from planner.conversation.usage.logic.pricing import parse_rate_table


def read_price_snapshot(conn: sqlite3.Connection) -> UsagePriceSnapshot | None:
    row = conn.execute(
        "SELECT source,fetched_at,document FROM native_usage_prices WHERE singleton=1"
    ).fetchone()
    if row is None:
        return None
    return UsagePriceSnapshot(
        row["source"], datetime.fromisoformat(row["fetched_at"]),
        cast(dict[str, object], json.loads(row["document"])),
    )


def pricing_summary(
    snapshot: UsagePriceSnapshot | None, *, now: datetime
) -> UsagePricingSummary:
    if snapshot is None:
        return {
            "status": "unavailable", "source": LITELLM_RATES_URL,
            "fetched_at": None, "known_models": 0,
        }
    return {
        "status": "fresh" if 0 <= (now - snapshot.fetched_at).total_seconds()
        < RATES_TTL_SECONDS else "cached",
        "source": snapshot.source,
        "fetched_at": snapshot.fetched_at.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        "known_models": len(parse_rate_table(snapshot.document)),
    }


class UsagePricingService:
    def __init__(
        self, connect: Callable[[], sqlite3.Connection], *,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        fetch: Callable[[], dict[str, object]] = fetch_rate_document,
    ) -> None:
        self._connect = connect
        self._now = now
        self._fetch = fetch
        self._lock = threading.Lock()
        self._last_attempt: datetime | None = None

    def refresh(self, *, force: bool = False) -> UsagePricingSummary:
        """Only explicit refresh calls enter here; failures retain the old snapshot."""
        with self._lock:
            conn = self._connect()
            try:
                previous = read_price_snapshot(conn)
                now = self._now()
                max_age = RATES_REFRESH_FLOOR_SECONDS if force else RATES_TTL_SECONDS
                if previous is not None and (
                    0 <= (now - previous.fetched_at).total_seconds() < max_age
                ):
                    return pricing_summary(previous, now=now)
                # A failed source never creates a request storm on repeated refresh.
                if self._last_attempt is not None and (
                    0 <= (now - self._last_attempt).total_seconds()
                    < RATES_REFRESH_FLOOR_SECONDS
                ):
                    return pricing_summary(previous, now=now)
                self._last_attempt = now
                try:
                    document = self._fetch()
                    raw = json.dumps(document, allow_nan=False, separators=(",", ":"))
                    if len(raw.encode()) > MAX_RATES_BYTES or not parse_rate_table(document):
                        return pricing_summary(previous, now=now)
                except (OSError, ValueError, TimeoutError, httpx.HTTPError, RecursionError):
                    return pricing_summary(previous, now=now)
                conn.execute(
                    "INSERT INTO native_usage_prices(singleton,source,fetched_at,document) "
                    "VALUES(1,?,?,?) ON CONFLICT(singleton) DO UPDATE SET "
                    "source=excluded.source,fetched_at=excluded.fetched_at,"
                    "document=excluded.document",
                    (LITELLM_RATES_URL, now.isoformat(), raw),
                )
                conn.commit()
                return pricing_summary(
                    UsagePriceSnapshot(LITELLM_RATES_URL, now, document), now=now
                )
            finally:
                conn.close()
