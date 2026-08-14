"""
Feature engineering.

Two rules govern everything here.

**1. Nothing may look forward.** Every feature for date D is computed only from
bars dated D or earlier. Rolling windows use `.shift()` where needed and are
never centred. This is verified by test, not by inspection — see
`scripts/test_features.py`, which recomputes a date's features with a decade of
future data appended and asserts the values are byte-identical.

**2. Features are ranked cross-sectionally, per day.** A raw 20-day return of
+8% means something different in March 2020 than in a calm month. Worse, if the
model sees raw values it can learn "everything was rising in 2021", which is a
fact about the calendar rather than about a stock, and the calendar does not
repeat. Converting each feature to a percentile rank *within that day's
universe* removes the market-wide component and leaves the question that
actually matters: is this stock better placed than its peers today?

The label follows the same logic. We do not predict a stock's return — that is
mostly predicting the market. We predict whether it will beat the median stock
over the holding period.

Feature count is deliberately small. With ~2,500 trading days of genuinely
independent observations, every extra feature is another chance to fit noise.
Each one below has a one-line reason for existing.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

# window -> what it is meant to capture
MOMENTUM_WINDOWS = {
    "mom_5": 5,      # short-term drift
    "mom_20": 20,    # the classic 1-month momentum effect
    "mom_60": 60,    # 3-month, the horizon most momentum research uses
    "mom_120": 120,  # 6-month trend
}

FEATURE_NAMES = [
    "mom_5", "mom_20", "mom_60", "mom_120",
    "reversal_1",      # yesterday's move; short-term reversal is well documented
    "vol_20",          # realised volatility, a risk and regime proxy
    "vol_ratio",       # volatility now vs its own recent norm
    "volume_ratio",    # today's volume against its 20-day average
    "dollar_volume",   # liquidity; also proxies size
    "px_sma20",        # stretch above/below the 20-day average
    "px_sma50",
    "px_sma200",       # long-trend filter
    "range_pos_252",   # position within the 52-week high/low band
    "gap_20",          # average overnight gap, a microstructure tell
]


def build(bars: pd.DataFrame, forward_days: int = 5,
          min_history: int = 252) -> pd.DataFrame:
    """
    Turn raw bars into a model-ready table.

    Returns one row per (symbol, date) with ranked features in [0, 1] and a
    binary label: did this stock beat the day's median forward return.
    """
    df = bars.copy()
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values(["symbol", "date"]).reset_index(drop=True)
    g = df.groupby("symbol", observed=True)

    close, high, low, vol = df["close"], df["high"], df["low"], df["volume"]

    # ---- raw features, all strictly backward-looking ----
    for name, w in MOMENTUM_WINDOWS.items():
        df[name] = g["close"].pct_change(w)

    df["reversal_1"] = g["close"].pct_change(1)

    daily_ret = g["close"].pct_change(1)
    df["_ret"] = daily_ret
    df["vol_20"] = g["_ret"].transform(lambda s: s.rolling(20, min_periods=20).std())
    vol_60 = g["_ret"].transform(lambda s: s.rolling(60, min_periods=60).std())
    df["vol_ratio"] = df["vol_20"] / vol_60.replace(0, np.nan)

    avg_vol = g["volume"].transform(lambda s: s.rolling(20, min_periods=20).mean())
    df["volume_ratio"] = vol / avg_vol.replace(0, np.nan)
    df["dollar_volume"] = (close * vol).rolling(1).mean()

    for w in (20, 50, 200):
        sma = g["close"].transform(lambda s, w=w: s.rolling(w, min_periods=w).mean())
        df[f"px_sma{w}"] = close / sma.replace(0, np.nan) - 1

    hi252 = g["high"].transform(lambda s: s.rolling(252, min_periods=252).max())
    lo252 = g["low"].transform(lambda s: s.rolling(252, min_periods=252).min())
    df["range_pos_252"] = (close - lo252) / (hi252 - lo252).replace(0, np.nan)

    prev_close = g["close"].shift(1)
    gap = (df["open"] - prev_close) / prev_close.replace(0, np.nan)
    df["_gap"] = gap
    df["gap_20"] = g["_gap"].transform(lambda s: s.rolling(20, min_periods=20).mean())

    # ---- label: does it beat the median stock over the next `forward_days` ----
    fwd = g["close"].shift(-forward_days) / close - 1
    df["fwd_return"] = fwd

    # ---- restrict to tradable rows before ranking ----
    df["has_history"] = g.cumcount() >= min_history
    mask = df["has_history"]
    if "is_member" in df.columns:
        mask &= df["is_member"]
    df = df[mask].copy()

    # ---- cross-sectional ranking, per day ----
    by_day = df.groupby("date", observed=True)
    for f in FEATURE_NAMES:
        df[f] = by_day[f].rank(pct=True)

    median_fwd = by_day["fwd_return"].transform("median")
    df["label"] = (df["fwd_return"] > median_fwd).astype(float)
    df.loc[df["fwd_return"].isna(), "label"] = np.nan

    keep = ["symbol", "date"] + FEATURE_NAMES + ["fwd_return", "label", "close"]
    out = df[keep].dropna(subset=FEATURE_NAMES).reset_index(drop=True)
    return out


def daily_universe_size(features: pd.DataFrame) -> pd.Series:
    return features.groupby("date", observed=True).size()
