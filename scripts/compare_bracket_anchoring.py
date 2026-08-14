#!/usr/bin/env python3
"""
Which entry scheme makes the live bot and the backtest the SAME strategy?

The problem in one line: the live bot must name dollar prices for its stop and
target the night before, when only yesterday's close is known, but the backtest
places them as percentages of the actual fill. If a stock closes at $100 and
opens at $102, the live order carries a $92 stop and a $112 target — 9.8% away
in both directions, when the design called for 8% down and 12% up.

Four schemes, same model, same window, same costs, same universe:

  BASELINE   what the backtest has always assumed. Stop and target are
             percentages of the fill. NOT ACHIEVABLE LIVE — included only as
             the reference the old numbers were computed against.

  A          anchor the barriers to the prior close, as the live bot does.
             This is what live actually trades today.

  C          limit entry at the prior close: never pay more than the price the
             barriers were computed from. Skips gap-ups entirely.

  C_WIDE     the same limit idea with 0.5% of slack, since an exact-close limit
             may be too strict to fill often enough to matter.

Scheme B — submit the entry alone, attach barriers after the fill — is not
simulated because a backtest cannot represent its actual cost, which is the
position sitting unprotected through the open. That is a live risk, not a
historical return, and pretending to measure it would be worse than saying so.

THE CRITERION IS NOT THE HIGHEST SHARPE. It is which scheme the live bot can
actually reproduce. Sharpe is reported so the cost of that choice is visible,
not so the winner is picked on it. Picking on Sharpe here would be exactly the
metric-shopping this project has rejected four times.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from scipy import stats  # noqa: E402

from src.backtest import Backtest, Order, buy_and_hold  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
TEST_START, TEST_END = "2022-01-01", "2024-12-31"
N_POS, STOP, TARGET, MAX_HOLD, SLIP = 25, 0.08, 0.12, 10, 3.5


def make_strategy(preds: pd.DataFrame, scheme: str):
    table = {d: g.set_index("symbol")[["score", "close"]]
             for d, g in preds.groupby(preds.date.dt.date)}

    def strategy(view, positions, cash, equity):
        s = table.get(view.as_of)
        if s is None:
            return []
        pool = s[s.index.isin(view.tradable) & ~s.index.isin(positions)]
        if pool.empty:
            return []
        picks = pool.nlargest(N_POS - len(positions), "score")
        if picks.empty:
            return []
        w = 0.95 / N_POS
        out = []
        for sym, r in picks.iterrows():
            ref = float(r["close"])            # the prior close, all we know
            if scheme == "baseline":
                out.append(Order(sym, weight=w, stop_pct=STOP,
                                 target_pct=TARGET, max_hold_days=MAX_HOLD))
            elif scheme == "A":
                out.append(Order(sym, weight=w, max_hold_days=MAX_HOLD,
                                 stop_abs=ref * (1 - STOP),
                                 target_abs=ref * (1 + TARGET)))
            elif scheme in ("C", "C_WIDE"):
                slack = 1.005 if scheme == "C_WIDE" else 1.0
                out.append(Order(sym, weight=w, max_hold_days=MAX_HOLD,
                                 stop_abs=ref * (1 - STOP),
                                 target_abs=ref * (1 + TARGET),
                                 limit_price=ref * slack))
        return out
    return strategy


def se(sharpe, years):
    return float(np.sqrt((1 + 0.5 * sharpe ** 2) / years))


def main() -> int:
    bars = pd.read_parquet(DATA / "bars" / "daily.parquet")
    bars["date"] = pd.to_datetime(bars["date"])
    win = bars[(bars.date >= "2021-06-01") & (bars.date <= "2025-02-15")]

    spy = pd.read_parquet(DATA / "spy.parquet")
    spy["date"] = pd.to_datetime(spy["date"])
    spy_w = spy[(spy.date >= TEST_START) & (spy.date <= TEST_END)]

    preds = pd.read_parquet(DATA / "preds_CLS_price.parquet")
    preds["date"] = pd.to_datetime(preds["date"])
    # the strategy needs the decision-day close to place absolute barriers
    closes = bars[["symbol", "date", "close"]]
    preds = preds.merge(closes, on=["symbol", "date"], how="left").dropna(subset=["close"])

    print(f"model v1, {N_POS} positions, {SLIP} bps/side, "
          f"{TEST_START} -> {TEST_END}\n")

    rows, rets = [], {}
    for scheme, label in (("baseline", "BASELINE  pct of fill (not live-able)"),
                          ("A", "A         anchored to prior close"),
                          ("C", "C         limit at prior close"),
                          ("C_WIDE", "C_WIDE    limit at close +0.5%")):
        bt = Backtest(win, slippage_bps=SLIP, members_only=True)
        r = bt.run(make_strategy(preds, scheme), warmup=120)
        eq = r.equity[(r.equity.index >= TEST_START) & (r.equity.index <= TEST_END)]
        rr = eq.pct_change().dropna()
        yrs = (eq.index[-1] - eq.index[0]).days / 365.25
        sh = rr.mean() / rr.std() * np.sqrt(252)
        trades = [t for t in r.trades
                  if pd.Timestamp(t.entry_date) >= pd.Timestamp(TEST_START)]
        reasons = pd.Series([t.reason for t in trades]).value_counts()
        rows.append({
            "label": label, "scheme": scheme,
            "cagr": (eq.iloc[-1] / eq.iloc[0]) ** (1 / yrs) - 1,
            "sharpe": sh, "se": se(sh, yrs),
            "dd": float((eq / eq.cummax() - 1).min()),
            "n": len(trades),
            "stop": int(reasons.get("stop", 0) + reasons.get("stop_gap", 0)
                        + reasons.get("stop_same_day", 0)),
            "target": int(reasons.get("target", 0) + reasons.get("target_gap", 0)),
            "time": int(reasons.get("time", 0)),
        })
        rets[scheme] = rr

    bt = Backtest(spy_w, slippage_bps=SLIP, members_only=False)
    rs = bt.run(buy_and_hold("SPY"), warmup=0)
    spy_r = rs.equity.pct_change().dropna()
    spy_sh = spy_r.mean() / spy_r.std() * np.sqrt(252)

    print(f"{'scheme':<38}{'CAGR':>8}{'Sharpe':>8}{'+/-SE':>7}{'maxDD':>8}"
          f"{'trades':>8}{'stop':>7}{'target':>8}{'time':>7}")
    print("-" * 99)
    for r in rows:
        print(f"{r['label']:<38}{r['cagr']:>8.2%}{r['sharpe']:>8.2f}{r['se']:>7.2f}"
              f"{r['dd']:>8.1%}{r['n']:>8,}{r['stop']:>7,}{r['target']:>8,}"
              f"{r['time']:>7,}")
    print("-" * 99)
    print(f"{'SPY buy & hold':<38}{'':>8}{spy_sh:>8.2f}")

    base = rows[0]
    print(f"\nEvery scheme vs BASELINE (the number the old results assumed):")
    for r in rows[1:]:
        a, b = rets[r["scheme"]].align(rets["baseline"], join="inner")
        _, p = stats.ttest_rel(a, b)
        print(f"  {r['label']:<38} Sharpe {r['sharpe']-base['sharpe']:+.2f}  "
              f"trades {r['n']-base['n']:+,}  p={p:.3f}")

    print(f"\nStop-to-target ratio (the design is {STOP:.0%} risk / {TARGET:.0%} reward,")
    print(f"so a healthy run should show more targets than stops if the model has skill):")
    for r in rows:
        tot = r["stop"] + r["target"] + r["time"]
        if tot:
            print(f"  {r['label']:<38} stop {r['stop']/tot:>5.1%}  "
                  f"target {r['target']/tot:>5.1%}  time {r['time']/tot:>5.1%}")

    print("\nREAD THIS BEFORE THE NUMBERS ABOVE:")
    print("BASELINE is not achievable live. The choice is between A and C.")
    print("The criterion is which one the bot can actually reproduce, not which")
    print("one scores best — and with a 3-year error bar near +/-0.6, none of")
    print("these Sharpe differences is likely to be distinguishable anyway.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
