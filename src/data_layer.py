"""
Data layer for the trading bot.

Every trap found during the 13 Aug 2026 audit is blocked here in code, because
none of them raise errors on their own. See DECISIONS.md D6, D7, D10, D12 and
DATA_AUDIT.md for why each one matters.

Design rule: no silent defaults. If a caller forgets to say which feed or which
adjustment it wants, this module refuses rather than guessing. A guess would
produce data that looks fine and is wrong.
"""

from __future__ import annotations

import json
import os
import re
import time
from bisect import bisect_right
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
BARS = DATA / "bars"
DATA_URL = "https://data.alpaca.markets"

VALID_FEEDS = {"sip", "iex"}
VALID_ADJUSTMENTS = {"raw", "split", "dividend", "all"}
RFC3339 = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})$")


# ---------------------------------------------------------------------------
# credentials
# ---------------------------------------------------------------------------

def load_credentials(env_path: Path | None = None) -> dict[str, str]:
    """Read keys from .env. Aborts on a live key — see D11."""
    env_path = env_path or ROOT / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip("'\""))

    key = os.environ.get("APCA_API_KEY_ID")
    secret = os.environ.get("APCA_API_SECRET_KEY")
    if not key or not secret:
        raise RuntimeError("No Alpaca credentials. See SETUP.md Part 2.")
    if key.startswith("AK"):
        raise RuntimeError(
            f"Key ID starts with 'AK', which is a LIVE trading key. Refusing to "
            f"run. This project is paper-only (D1). Generate paper keys (PK...)."
        )
    if not key.startswith("PK"):
        raise RuntimeError(f"Unrecognised key prefix '{key[:2]}'. Expected 'PK'.")
    return {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}


# ---------------------------------------------------------------------------
# time handling — D10
# ---------------------------------------------------------------------------

def utc(dt: datetime) -> str:
    """Format a datetime as an explicit RFC3339 UTC instant."""
    if dt.tzinfo is None:
        raise ValueError("Naive datetime. Timezone must be explicit — see D10.")
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def utc_now(minutes_ago: int = 30) -> str:
    return utc(datetime.now(timezone.utc) - timedelta(minutes=minutes_ago))


def day_start(date_str: str) -> str:
    """'2016-01-04' -> '2016-01-04T00:00:00Z'. Never pass a bare date to Alpaca."""
    return datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc) \
        .isoformat(timespec="seconds").replace("+00:00", "Z")


def _check_instant(value: str, name: str) -> str:
    if not RFC3339.match(value):
        raise ValueError(
            f"{name}={value!r} is not an RFC3339 instant. Alpaca resolves bare "
            f"dates against US Eastern, which from Sydney is often in the future "
            f"and returns a misleading 403. Use day_start() or utc(). See D10."
        )
    return value


# ---------------------------------------------------------------------------
# client
# ---------------------------------------------------------------------------

class AlpacaBars:
    """Thin bars client. feed and adjustment are mandatory — see D6 and D7."""

    def __init__(self, headers: dict[str, str] | None = None, pause: float = 0.30):
        self.session = requests.Session()
        self.session.headers.update(headers or load_credentials())
        self.pause = pause

    def daily(self, symbol: str, start: str, end: str, *, feed: str, adjustment: str,
              limit: int = 10000, max_retries: int = 4) -> list[dict]:
        if feed not in VALID_FEEDS:
            raise ValueError(f"feed must be one of {VALID_FEEDS}, got {feed!r}")
        if adjustment not in VALID_ADJUSTMENTS:
            raise ValueError(f"adjustment must be one of {VALID_ADJUSTMENTS}, got {adjustment!r}")
        if feed != "sip":
            raise ValueError(
                "feed='iex' returns bars built from ~3.5% of US volume. Closes and "
                "volumes will be wrong with no error raised. See D6."
            )
        if adjustment != "all":
            raise ValueError(
                "adjustment must be 'all'. Anything else leaves split and dividend "
                "discontinuities in the series — NVDA shows a fake -89.9% day. See D7."
            )
        _check_instant(start, "start")
        _check_instant(end, "end")

        out, token = [], None
        while True:
            params = {"timeframe": "1Day", "start": start, "end": end, "feed": feed,
                      "adjustment": adjustment, "limit": limit, "sort": "asc"}
            if token:
                params["page_token"] = token
            payload = self._get(f"{DATA_URL}/v2/stocks/{symbol}/bars", params, max_retries)
            out.extend(payload.get("bars") or [])
            token = payload.get("next_page_token")
            if not token:
                return out

    def _get(self, url: str, params: dict, max_retries: int) -> dict:
        for attempt in range(max_retries):
            r = self.session.get(url, params=params, timeout=60)
            if r.status_code == 429:
                time.sleep(3 * (attempt + 1))
                continue
            if r.status_code == 403 and "SIP" in r.text:
                raise RuntimeError(
                    f"403 on SIP data. Almost always a timezone bug, not a "
                    f"subscription limit — check 'end' is not in the future in US "
                    f"Eastern terms. See D10. Raw: {r.text[:200]}"
                )
            if r.status_code != 200:
                raise RuntimeError(f"HTTP {r.status_code} for {params.get('start')}: {r.text[:200]}")
            time.sleep(self.pause)
            return r.json()
        raise RuntimeError("Rate limited after retries.")


# ---------------------------------------------------------------------------
# universe — D12
# ---------------------------------------------------------------------------

SUFFIX = re.compile(r"^(.*?)-(\d{6})$")

# Tickers reused by an unrelated company after the original left the index.
# Alpaca serves plenty of bars for these; the bars belong to a different
# business. See DATA_AUDIT.md "Trap 1".
REUSED_TICKERS = {"STI", "TE", "WYND", "AABA"}


class Universe:
    """Point-in-time S&P 500 membership, loaded from the dated components file."""

    def __init__(self, csv_path: Path | None = None, start: str = "2016-01-01"):
        import csv as _csv
        import io as _io

        csv_path = csv_path or DATA / "sp500_history.csv"
        text = csv_path.read_text()
        rows = [r for r in _csv.DictReader(_io.StringIO(text)) if r["date"] >= start]
        if not rows:
            raise RuntimeError(f"No rows at or after {start} in {csv_path.name}.")

        self.snapshot_dates = [r["date"] for r in rows]
        self._members: dict[str, set[str]] = {}
        present: dict[str, set[str]] = {}
        for r in rows:
            syms = set()
            for tok in r["tickers"].split(","):
                if not tok:
                    continue
                m = SUFFIX.match(tok)
                base = m.group(1) if m else tok
                syms.add(base)
                present.setdefault(base, set()).add(r["date"])
            self._members[r["date"]] = syms

        idx = {d: i for i, d in enumerate(self.snapshot_dates)}
        self.intervals: dict[str, list[tuple[str, str]]] = {}
        for sym, dates in present.items():
            ii = sorted(idx[d] for d in dates)
            runs, s, p = [], ii[0], ii[0]
            for i in ii[1:]:
                if i == p + 1:
                    p = i
                else:
                    runs.append((self.snapshot_dates[s], self.snapshot_dates[p]))
                    s = p = i
            runs.append((self.snapshot_dates[s], self.snapshot_dates[p]))
            self.intervals[sym] = runs

        self._assert_coverage()

    def _assert_coverage(self) -> None:
        """D12 rule 3 — the obvious file in that repo is stale by seven years."""
        last = self.snapshot_dates[-1]
        if last < "2025-01-01":
            raise RuntimeError(
                f"Constituent data ends {last}, which is far older than expected. "
                f"You have probably loaded the undated file, which stops at "
                f"2019-01-11. Use the dated one. See DATA_AUDIT.md."
            )
        sizes = [len(v) for v in self._members.values()]
        if not (480 <= min(sizes) and max(sizes) <= 525):
            raise RuntimeError(f"Membership size out of range: {min(sizes)}-{max(sizes)}.")

    def members_on(self, date: str) -> set[str]:
        """Index membership as of `date`, forward-filled from the last change."""
        i = bisect_right(self.snapshot_dates, date) - 1
        if i < 0:
            raise ValueError(f"{date} predates the first snapshot "
                             f"({self.snapshot_dates[0]}).")
        return self._members[self.snapshot_dates[i]]

    def was_member(self, symbol: str, date: str) -> bool:
        """D12 rule 2 — membership is a set of intervals, not a span."""
        return any(a <= date <= b for a, b in self.intervals.get(symbol, []))

    def all_tickers(self, exclude_reused: bool = True) -> list[str]:
        syms = set(self.intervals)
        if exclude_reused:
            syms -= REUSED_TICKERS
        return sorted(syms)

    def window(self, symbol: str) -> tuple[str, str]:
        """Earliest and latest date this symbol was ever a member."""
        iv = self.intervals[symbol]
        return iv[0][0], iv[-1][1]


# ---------------------------------------------------------------------------
# fetch + filter
# ---------------------------------------------------------------------------

def fetch_symbol(client: AlpacaBars, universe: Universe, symbol: str,
                 pad_days: int = 400, trail_days: int = 45) -> list[dict]:
    """
    Fetch bars for one symbol, keeping only those inside a membership interval.

    D12 rule 1: bars outside the membership window may belong to an unrelated
    company that later took the ticker. `pad_days` of lead-in is kept before the
    first membership date so that trailing indicators (60-day dollar volume,
    200-day averages) have history to work with on day one.

    `trail_days` keeps bars *after* membership ends. Without it, a position
    still open when a name leaves the index has no prices to exit against, and
    the engine is forced to liquidate at a stale close. That matters because
    the index drops failing companies mid-collapse: SIVB left the list on
    2023-01-04 at $240 and kept trading down to $106, FRC left at $12.18 and
    fell to $3.51. 45 calendar days comfortably covers the 10-day maximum hold.
    Acquisitions are harmless here (price freezes near the deal), failures are
    not. See D20.
    """
    if symbol in REUSED_TICKERS:
        raise ValueError(f"{symbol} is a reused ticker and must not be fetched. See D12.")

    first, last = universe.window(symbol)
    lead = (datetime.strptime(first, "%Y-%m-%d") - timedelta(days=pad_days)).strftime("%Y-%m-%d")
    tail = (datetime.strptime(last, "%Y-%m-%d") + timedelta(days=trail_days)).strftime("%Y-%m-%d")
    raw = client.daily(symbol, day_start(max(lead, "2016-01-01")),
                       day_start(tail) if last < "2026-01-14" else utc_now(),
                       feed="sip", adjustment="all")

    kept = []
    for b in raw:
        d = b["t"][:10]
        in_window = any(
            (datetime.strptime(a, "%Y-%m-%d") - timedelta(days=pad_days)).strftime("%Y-%m-%d")
            <= d <=
            (datetime.strptime(z, "%Y-%m-%d") + timedelta(days=trail_days)).strftime("%Y-%m-%d")
            for a, z in universe.intervals[symbol]
        )
        if in_window:
            kept.append({"symbol": symbol, "date": d, "open": b["o"], "high": b["h"],
                         "low": b["l"], "close": b["c"], "volume": b["v"],
                         "trades": b.get("n"), "vwap": b.get("vw"),
                         "is_member": universe.was_member(symbol, d)})
    return kept
