#!/usr/bin/env python3
"""Fetch SPY separately (D16). It is an ETF, so it never appears in the
constituent file, and the entire success bar is stated relative to it."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402

from src.data_layer import AlpacaBars, day_start, load_credentials, utc_now  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

bars = AlpacaBars(headers=load_credentials(ROOT / ".env"))
raw = bars.daily("SPY", day_start("2016-01-01"), utc_now(), feed="sip", adjustment="all")
df = pd.DataFrame([{"symbol": "SPY", "date": b["t"][:10], "open": b["o"], "high": b["h"],
                    "low": b["l"], "close": b["c"], "volume": b["v"],
                    "is_member": True} for b in raw])
df["date"] = pd.to_datetime(df["date"])
df.to_parquet(ROOT / "data" / "spy.parquet", index=False)
print(f"SPY {len(df):,} bars {df.date.min().date()} -> {df.date.max().date()}")
assert len(df) > 2500, "SPY fetch looks short — benchmark must not be trusted"
