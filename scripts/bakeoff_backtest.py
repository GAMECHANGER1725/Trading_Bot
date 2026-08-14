#!/usr/bin/env python3
"""
Backtest all eight cells through the identical engine, window and costs.

Only the ranking differs. Same universe, same 2022-01-01 to 2024-12-31, same
10 equal-weighted positions, same 8% stop / 12% target / 10-day limit, same
3.5 bps per side. Momentum and SPY run on the same window as reference points.

Equal weighting is used for every cell, including the REG ones. v2's
pre-registered C4 volatility-scaled sizing is a separate lever; applying it to
only some cells would make position sizing a third uncontrolled axis, which is
the mistake this whole rebuild exists to correct.
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

CELLS = [
    ("v1", "CLS", "price"), ("—", "CLS", "price_v3"),
    ("v4", "CLS", "price_insider"), ("—", "CLS", "price_v3_insider"),
    ("v2", "REG", "price"), ("v3", "REG", "price_v3"),
    ("—", "REG", "price_insider"), ("—", "REG", "price_v3_insider"),
]


def score_strategy(preds: pd.DataFrame):
    table = {d: g.set_index("symbol")["score"]
             for d, g in preds.groupby(preds.date.dt.date)}

    def strategy(view, positions, cash, equity):
        s = table.get(view.as_of)
        if s is None:
            return []
        pool = s[s.index.isin(view.tradable)]
        pool = pool[~pool.index.isin(positions)]
        picks = pool.nlargest(N_POS - len(positions)) if not pool.empty else pool
        if picks.empty:
            return []
        return [Order(sym, weight=0.95 / N_POS, stop_pct=STOP,
                      target_pct=TARGET, max_hold_days=MAX_HOLD)
                for sym in picks.index]
    return strategy


def momentum_strategy(lookback=20):
    def strategy(view, positions, cash, equity):
        c = view.closes(lookback + 5)
        if len(c) < lookback + 1:
            return []
        mom = (c.iloc[-1] / c.iloc[-(lookback + 1)] - 1).dropna()
        mom = mom[mom.index.isin(view.tradable) & ~mom.index.isin(positions)]
        picks = mom.nlargest(N_POS - len(positions))
        if picks.empty:
            return []
        return [Order(s, weight=0.95 / N_POS, stop_pct=STOP,
                      target_pct=TARGET, max_hold_days=MAX_HOLD)
                for s in picks.index]
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

    out, curves = [], {}
    for name, arch, fset in CELLS:
        p = pd.read_parquet(DATA / f"preds_{arch}_{fset}.parquet")
        p["date"] = pd.to_datetime(p["date"])
        bt = Backtest(win, slippage_bps=SLIP, members_only=True)
        r = bt.run(score_strategy(p), warmup=120)
        eq = r.equity[(r.equity.index >= TEST_START) & (r.equity.index <= TEST_END)]
        label = f"{name:<3} {arch} {fset.replace('_', '+')}"
        curves[label] = eq
        out.append((label, r, eq))

    for label, strat in (("mom 20d", momentum_strategy()),):
        bt = Backtest(win, slippage_bps=SLIP, members_only=True)
        r = bt.run(strat, warmup=120)
        eq = r.equity[(r.equity.index >= TEST_START) & (r.equity.index <= TEST_END)]
        curves[label] = eq
        out.append((label, r, eq))

    bt = Backtest(spy_w, slippage_bps=SLIP, members_only=False)
    r = bt.run(buy_and_hold("SPY"), warmup=0)
    curves["SPY"] = r.equity
    out.append(("SPY", r, r.equity))

    print("=" * 88)
    print(f"IDENTICAL ENGINE, WINDOW {TEST_START} -> {TEST_END}, "
          f"{SLIP} bps/side, {N_POS} equal-weight positions")
    print("=" * 88)
    print(f"{'cell':<28}{'CAGR':>9}{'Sharpe':>9}{'+/-SE':>8}{'95% interval':>18}"
          f"{'maxDD':>9}{'trades':>8}")
    print("-" * 88)
    rets = {}
    for label, res, eq in out:
        rr = eq.pct_change().dropna()
        yrs = (eq.index[-1] - eq.index[0]).days / 365.25
        cagr = (eq.iloc[-1] / eq.iloc[0]) ** (1 / yrs) - 1
        sh = rr.mean() / rr.std() * np.sqrt(252)
        s = se(sh, yrs)
        dd = float((eq / eq.cummax() - 1).min())
        n = len([t for t in res.trades
                 if pd.Timestamp(t.entry_date) >= pd.Timestamp(TEST_START)])
        rets[label] = rr
        print(f"{label:<28}{cagr:>9.2%}{sh:>9.2f}{s:>8.2f}"
              f"{f'[{sh-1.96*s:+.2f}, {sh+1.96*s:+.2f}]':>18}{dd:>9.1%}{n:>8,}")
    print("-" * 88)

    print("\nEvery cell vs SPY — paired t-test on daily returns:")
    spy_r = rets["SPY"]
    for label in rets:
        if label == "SPY":
            continue
        a, b = rets[label].align(spy_r, join="inner")
        t, p = stats.ttest_rel(a, b)
        print(f"  {label:<28} p={p:.3f}  "
              f"{'DIFFERENT' if p < 0.05 else 'not distinguishable'}")

    pd.DataFrame(curves).to_csv(DATA / "bakeoff_curves.csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
