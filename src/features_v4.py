"""
v4 features: SEC Form 4 insider transactions + earnings-cycle position.

The point of v4 is NOT more price history. D26/D28 established that
price-derived features on S&P large caps at 5-10 day horizons contain no
tradeable signal, and adding more rows of the same thing cannot fix that.
This module adds a genuinely different information category: what corporate
insiders actually did with their own money, and where in the earnings cycle
the stock sits.

-----------------------------------------------------------------------------
LOOKAHEAD CONTROL — the only thing that matters here
-----------------------------------------------------------------------------
A Form 4 has two dates. `earliest_execution_date` is when the insider traded.
`filing_date` is when the SEC published it. The market cannot react to the
first one. Joining on execution date is the classic way this dataset produces
a beautiful, worthless backtest: the median filing lag in this data is 2 days
and the 75th percentile is 4, so an execution-date join hands the model up to
a week of future knowledge on exactly the events that move prices.

Rules enforced below, in code rather than in comments:

  R1. Only `filing_date` is ever read. `earliest_execution_date` is dropped at
      load time so it cannot be used by accident.
  R2. A filing counts toward decision date D only if filing_date <= D - 1.
      Form 4s are accepted by EDGAR until 22:00 ET, which is *after* this
      bot's 06:30 Sydney run. Same-day filings are therefore not reliably
      knowable and are excluded.
  R3. Earnings dates count only if strictly before D, for the same reason:
      an after-close release at 17:00 ET lands 30 minutes after the bot runs.
  R4. No forward-looking earnings feature. "Days until next earnings" needs
      the next date, which is only sometimes pre-announced. Replaced with
      `earn_phase` — days since the last release divided by that company's
      median gap between its *prior* releases. Same information, zero leak.

-----------------------------------------------------------------------------
COLUMNS DELIBERATELY NOT USED
-----------------------------------------------------------------------------
`aggregated_value_usd`   max 7.17e15, min -1.50e15. A $7 quadrillion insider
                         trade is not a thing. The column is corrupt.
`aggregated_percent_of_shares`  max 2,857,143%, min -4,226%. Same.

Both would have been the most natural features to build, and both would have
handed the model a handful of absurd outliers to fit. Tree models are
particularly happy to carve those out. Counts and signs are used instead.

-----------------------------------------------------------------------------
WHY THESE FEATURES AND NOT OTHERS
-----------------------------------------------------------------------------
Cohen, Malloy & Pomorski (2012), "Decoding Inside Information", find that
insider trading's predictive content sits entirely in *opportunistic* trades;
routine, pre-scheduled trades carry none. This dataset ships the discriminator
directly as `under_schedule` (the Rule 10b5-1 flag), so the split is measured
rather than guessed.

The honest caveat, recorded before any result is seen: that literature works
on long horizons (months to a year) and finds most of the effect in small and
mid caps. This project trades S&P large caps on a 2-10 day bracket. There is
no strong prior reason the effect survives either translation. Expect nothing.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

INSIDER_FEATURES = [
    "ins_net_60",        # buys minus sells, 60 calendar days, all filings
    "ins_opp_net_60",    # same, opportunistic only (not 10b5-1 scheduled)
    "ins_buy_60",        # buy filings, 60d — buys are rarer and better studied
    "ins_sell_60",       # sell filings, 60d
    "ins_exec_net_60",   # buys minus sells by C-suite roles only, 60d
    "ins_net_20",        # shorter window, closer to the holding period
    "ins_days_since_buy",  # recency of the last insider purchase
    "ins_activity_20",   # directional filings in 20d, an attention proxy
]

EARNINGS_FEATURES = [
    "earn_days_since",   # calendar days since the last release
    "earn_phase",        # that, over the company's own median prior gap
]

V4_FEATURES = INSIDER_FEATURES + EARNINGS_FEATURES

# Roles treated as C-suite. Matched case-insensitively as substrings because
# the raw field is free text ("President & CEO", "Senior Vice President, CFO").
EXEC_PATTERNS = (
    "chief executive", "ceo", "chief financial", "cfo", "chief operating",
    "coo", "president", "chairman",
)


# ---------------------------------------------------------------------------
# loading
# ---------------------------------------------------------------------------

def load_form4(path: str, universe: set[str] | None = None) -> pd.DataFrame:
    """
    Load the Form 4 panel, keyed on filing_date only (R1).

    Returns one row per filing with: symbol, filing_date, side (+1/-1),
    opportunistic (bool), is_exec (bool).
    """
    cols = ["ticker_symbol", "filing_date", "insider_role", "under_schedule",
            "aggregated_signal"]
    f = pd.read_csv(path, usecols=cols, parse_dates=["filing_date"])

    # R1: execution date was never read, so it cannot leak.
    f = f.rename(columns={"ticker_symbol": "symbol"})
    f = f[f["aggregated_signal"].isin(["buy", "sell"])].copy()
    if universe is not None:
        f = f[f["symbol"].isin(universe)]

    f["side"] = np.where(f["aggregated_signal"] == "buy", 1, -1)
    f["opportunistic"] = ~f["under_schedule"].astype(bool)
    role = f["insider_role"].fillna("").str.lower()
    f["is_exec"] = role.apply(lambda r: any(p in r for p in EXEC_PATTERNS))

    return f[["symbol", "filing_date", "side", "opportunistic", "is_exec"]] \
        .sort_values(["symbol", "filing_date"]).reset_index(drop=True)


def load_earnings(path: str, universe: set[str] | None = None) -> pd.DataFrame:
    """
    Load earnings announcement dates only.

    The source file also carries `beat`, `beat_streak`, `historical_beat_rate`,
    `avg_surprise_4q` and `estimate_trend`. None are used. `beat` is the
    outcome of the announcement — using it as a feature on the announcement
    date is straight lookahead — and the derived columns are of unverifiable
    construction, so it cannot be established whether they exclude the current
    quarter. Dates are verifiable; the rest is not.
    """
    e = pd.read_csv(path, usecols=["earnings_date", "ticker"])
    e["date"] = pd.to_datetime(e["earnings_date"], utc=True).dt.tz_localize(None).dt.normalize()
    e = e.rename(columns={"ticker": "symbol"})[["symbol", "date"]]
    if universe is not None:
        e = e[e["symbol"].isin(universe)]
    return e.drop_duplicates().sort_values(["symbol", "date"]).reset_index(drop=True)


# ---------------------------------------------------------------------------
# feature construction
# ---------------------------------------------------------------------------

def _rolling_event_counts(panel: pd.DataFrame, events: pd.DataFrame,
                          value_col: str, window_days: int,
                          out_col: str) -> pd.Series:
    """
    For each (symbol, date) in `panel`, sum `value_col` over events whose
    filing_date lies in [date - window_days, date - 1].

    Implemented as a merge_asof-free cumulative-sum difference so that the
    upper bound is strictly `date - 1` (R2) and cannot drift.
    """
    ev = events[["symbol", "filing_date", value_col]].copy()
    ev = ev.rename(columns={"filing_date": "date"})
    daily = ev.groupby(["symbol", "date"], as_index=False)[value_col].sum()

    # Cumulative sum per symbol over a dense daily calendar, then difference
    # the two lagged endpoints. Dense reindexing keeps the window in calendar
    # days, which is what "60 days of filings" means.
    out = []
    for sym, grp in daily.groupby("symbol", sort=False):
        s = grp.set_index("date")[value_col].sort_index()
        idx = pd.date_range(s.index.min(), max(s.index.max(),
                                               panel["date"].max()), freq="D")
        s = s.reindex(idx, fill_value=0.0).cumsum()
        # value known as of D-1 minus value known as of D-1-window
        upper = s.shift(1)
        lower = s.shift(1 + window_days)
        w = (upper - lower.fillna(0.0)).rename(out_col)
        w.index.name = "date"
        w = w.reset_index()
        w["symbol"] = sym
        out.append(w)

    if not out:
        return pd.Series(np.nan, index=panel.index, name=out_col)

    wide = pd.concat(out, ignore_index=True)
    merged = panel[["symbol", "date"]].merge(wide, on=["symbol", "date"], how="left")
    return merged[out_col].fillna(0.0).to_numpy()


def build_insider(panel: pd.DataFrame, f4: pd.DataFrame) -> pd.DataFrame:
    """Attach insider features to a (symbol, date) panel. Raw, unranked."""
    out = panel[["symbol", "date"]].copy()

    f4 = f4.copy()
    f4["one"] = 1.0
    f4["side_opp"] = f4["side"] * f4["opportunistic"].astype(float)
    f4["side_exec"] = f4["side"] * f4["is_exec"].astype(float)
    f4["is_buy"] = (f4["side"] > 0).astype(float)
    f4["is_sell"] = (f4["side"] < 0).astype(float)

    out["ins_net_60"] = _rolling_event_counts(out, f4, "side", 60, "ins_net_60")
    out["ins_opp_net_60"] = _rolling_event_counts(out, f4, "side_opp", 60, "ins_opp_net_60")
    out["ins_buy_60"] = _rolling_event_counts(out, f4, "is_buy", 60, "ins_buy_60")
    out["ins_sell_60"] = _rolling_event_counts(out, f4, "is_sell", 60, "ins_sell_60")
    out["ins_exec_net_60"] = _rolling_event_counts(out, f4, "side_exec", 60, "ins_exec_net_60")
    out["ins_net_20"] = _rolling_event_counts(out, f4, "side", 20, "ins_net_20")
    out["ins_activity_20"] = _rolling_event_counts(out, f4, "one", 20, "ins_activity_20")

    # days since the last insider BUY, using filing dates lagged one day (R2)
    buys = f4[f4["side"] > 0][["symbol", "filing_date"]].copy()
    buys["known_from"] = buys["filing_date"] + pd.Timedelta(days=1)
    buys = buys.sort_values("known_from")
    left = out.sort_values("date")
    merged = pd.merge_asof(left, buys[["symbol", "known_from"]].rename(
        columns={"known_from": "last_buy"}), left_on="date", right_on="last_buy",
        by="symbol", direction="backward")
    gap = (merged["date"] - merged["last_buy"]).dt.days
    merged["ins_days_since_buy"] = gap.fillna(999.0).clip(upper=999.0)
    return merged.sort_index()[["symbol", "date"] + INSIDER_FEATURES]


def build_earnings(panel: pd.DataFrame, earn: pd.DataFrame) -> pd.DataFrame:
    """
    Attach earnings-cycle features. Backward-looking only (R3, R4).

    `earn_phase` uses each company's expanding median gap between *prior*
    releases, so a value near 1.0 means "about due" without ever consulting
    the next release date.
    """
    e = earn.sort_values(["symbol", "date"]).copy()
    e["gap"] = e.groupby("symbol")["date"].diff().dt.days
    # expanding median of gaps strictly before the current release
    e["median_gap"] = (e.groupby("symbol")["gap"]
                        .transform(lambda s: s.shift(1).expanding().median()))
    e["median_gap"] = e["median_gap"].fillna(91.0)  # quarterly prior
    # R3: known only from the day AFTER the release
    e["known_from"] = e["date"] + pd.Timedelta(days=1)

    left = panel[["symbol", "date"]].sort_values("date")
    right = e[["symbol", "known_from", "median_gap"]].sort_values("known_from")
    m = pd.merge_asof(left, right, left_on="date", right_on="known_from",
                      by="symbol", direction="backward")

    m["earn_days_since"] = (m["date"] - m["known_from"]).dt.days + 1
    m["earn_phase"] = m["earn_days_since"] / m["median_gap"]
    m["earn_days_since"] = m["earn_days_since"].fillna(999.0).clip(upper=400.0)
    m["earn_phase"] = m["earn_phase"].fillna(-1.0).clip(upper=5.0)
    return m.sort_index()[["symbol", "date"] + EARNINGS_FEATURES]


def rank_cross_sectionally(df: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    """
    Percentile-rank each feature within each day, matching features.py.

    Note on ties: on any given day most symbols have zero insider filings in
    the window, so those rows all receive the same averaged mid-rank. That is
    the correct representation — "no filings" genuinely is one state, not a
    spread — but it does mean these features are informative on a minority of
    rows and neutral on the rest.
    """
    out = df.copy()
    by_day = out.groupby("date", observed=True)
    for c in cols:
        out[c] = by_day[c].rank(pct=True)
    return out
