"""
Triple-barrier labelling (C1 in PREREGISTRATION.md).

v1 asked the model "will this stock beat the median over the next 5 days?" while
the strategy actually traded a bracket order: 8% stop, 12% target, 10-day limit.
Those are different questions, and a model answering the wrong one is unhelpful
even when it answers well.

This module labels each example by what the bracket would really have produced —
walk forward day by day, and record which barrier was touched first:

    stop hit    -> roughly -8%
    target hit  -> roughly +12%
    neither     -> exit at the close of day N

Ties within a bar resolve to the stop, matching the backtest engine's
pessimistic convention. Daily bars cannot order intraday events, and assuming
the favourable one manufactures returns.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def triple_barrier(bars: pd.DataFrame, stop_pct: float = 0.08,
                   target_pct: float = 0.12, max_days: int = 10) -> pd.DataFrame:
    """
    Returns the input with three columns added:

        bt_return   realised return of the bracket trade
        bt_exit     which barrier fired: stop / target / time
        bt_days     how long it was held
    """
    df = bars.sort_values(["symbol", "date"]).reset_index(drop=True).copy()
    g = df.groupby("symbol", observed=True)

    entry = g["open"].shift(-1)          # bracket fills at the NEXT open
    stop_px = entry * (1 - stop_pct)
    target_px = entry * (1 + target_pct)

    n = len(df)
    ret = np.full(n, np.nan)
    exit_kind = np.empty(n, dtype=object)
    held = np.full(n, np.nan)

    # Future highs, lows and closes, shifted so day h is h sessions ahead of entry.
    highs = {h: g["high"].shift(-(h + 1)).to_numpy() for h in range(max_days)}
    lows = {h: g["low"].shift(-(h + 1)).to_numpy() for h in range(max_days)}
    closes = {h: g["close"].shift(-(h + 1)).to_numpy() for h in range(max_days)}

    e = entry.to_numpy()
    sp = stop_px.to_numpy()
    tp = target_px.to_numpy()
    open_ = np.ones(n, dtype=bool) & ~np.isnan(e)

    for h in range(max_days):
        lo, hi, cl = lows[h], highs[h], closes[h]
        valid = open_ & ~np.isnan(lo)

        hit_stop = valid & (lo <= sp)
        hit_target = valid & ~hit_stop & (hi >= tp)   # stop wins ties

        ret[hit_stop] = -stop_pct
        exit_kind[hit_stop] = "stop"
        held[hit_stop] = h + 1

        ret[hit_target] = target_pct
        exit_kind[hit_target] = "target"
        held[hit_target] = h + 1

        open_ = open_ & ~hit_stop & ~hit_target

        if h == max_days - 1:
            timeout = open_ & ~np.isnan(cl)
            ret[timeout] = cl[timeout] / e[timeout] - 1
            exit_kind[timeout] = "time"
            held[timeout] = max_days
            open_ = open_ & ~timeout

    df["bt_return"] = ret
    df["bt_exit"] = exit_kind
    df["bt_days"] = held
    return df


def cross_sectional_target(df: pd.DataFrame) -> pd.Series:
    """
    Demean the bracket return within each day (C3).

    Predicting a stock's raw return is mostly predicting the market, which is
    not the question. Demeaning per day leaves only relative performance.
    """
    med = df.groupby("date", observed=True)["bt_return"].transform("median")
    return df["bt_return"] - med


def non_overlapping_mask(df: pd.DataFrame, every: int = 10) -> pd.Series:
    """
    Keep one observation per symbol per holding period (C2).

    With a 10-day bracket, consecutive daily rows share almost all of their
    outcome. Training on all of them shows the model the same evidence ten times
    and inflates its confidence without adding information.
    """
    return df.groupby("symbol", observed=True).cumcount() % every == 0
