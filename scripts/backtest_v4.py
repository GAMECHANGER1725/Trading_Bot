#!/usr/bin/env python3
"""
Backtest v1 vs v4 vs momentum vs SPY over the identical out-of-sample window.

All four run through the same engine, the same costs, the same universe and
the same dates. The only thing that differs is how symbols get ranked.

Error bars are computed with Lo (2002): SE(annual Sharpe) ~= sqrt((1+S^2/2)/T)
with T in years. That formula reproduces D23's +/-0.46 over 6.1 years exactly,
which is why it is used rather than something invented here. Over the three
years available it gives roughly +/-0.66 — so a difference of less than about
1.3 Sharpe points between any two of these is not evidence of anything.

Reported before the numbers, per the project rule that the metric is never
chosen after seeing the result: the comparison that counts is Sharpe, because
that is what the success bar names.
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
WARMUP_FROM = "2021-06-01"     # engine warmup + 200-day features already ranked
N_POS, STOP, TARGET, MAX_HOLD = 25, 0.08, 0.12, 10
SLIP_BPS = 3.5


def score_strategy(preds: pd.DataFrame):
    """Each day, buy the top N by model score. Bracket order, filled next open."""
    table = {d: g.set_index("symbol")["score"]
             for d, g in preds.groupby(preds.date.dt.date)}

    def strategy(view, positions, cash, equity):
        s = table.get(view.as_of)
        if s is None:
            return []
        pool = s[s.index.isin(view.tradable)]
        pool = pool[~pool.index.isin(positions)]
        if pool.empty:
            return []
        picks = pool.nlargest(N_POS - len(positions))
        if picks.empty:
            return []
        w = 0.95 / N_POS
        return [Order(sym, weight=w, stop_pct=STOP, target_pct=TARGET,
                      max_hold_days=MAX_HOLD) for sym in picks.index]

    return strategy


def momentum_strategy(lookback: int = 20):
    """D21's baseline: top N by `lookback`-day return. No model involved."""
    def strategy(view, positions, cash, equity):
        closes = view.closes(lookback + 5)
        if len(closes) < lookback + 1:
            return []
        mom = (closes.iloc[-1] / closes.iloc[-(lookback + 1)] - 1).dropna()
        mom = mom[mom.index.isin(view.tradable)]
        mom = mom[~mom.index.isin(positions)]
        picks = mom.nlargest(N_POS - len(positions))
        if picks.empty:
            return []
        w = 0.95 / N_POS
        return [Order(sym, weight=w, stop_pct=STOP, target_pct=TARGET,
                      max_hold_days=MAX_HOLD) for sym in picks.index]
    return strategy


def sharpe_se(sharpe: float, years: float) -> float:
    """Lo (2002). Reproduces D23's +/-0.46 at S=0.77, T=6.1."""
    return float(np.sqrt((1 + 0.5 * sharpe ** 2) / years))


def main() -> int:
    bars = pd.read_parquet(DATA / "bars" / "daily.parquet")
    bars["date"] = pd.to_datetime(bars["date"])
    win = bars[(bars.date >= WARMUP_FROM) & (bars.date <= "2025-02-15")].copy()

    spy = pd.read_parquet(DATA / "spy.parquet")
    spy["date"] = pd.to_datetime(spy["date"])
    spy_win = spy[(spy.date >= TEST_START) & (spy.date <= TEST_END)].copy()

    p1 = pd.read_parquet(DATA / "preds_v1_window.parquet")
    p4 = pd.read_parquet(DATA / "preds_v4.parquet")
    for p in (p1, p4):
        p["date"] = pd.to_datetime(p["date"])

    print(f"universe bars : {len(win):,} rows, {win.symbol.nunique()} symbols")
    print(f"window        : {TEST_START} -> {TEST_END}")
    print(f"costs         : {SLIP_BPS} bps per side, {N_POS} positions, "
          f"{STOP:.0%} stop / {TARGET:.0%} target / {MAX_HOLD}d limit\n")

    runs = {}
    for name, strat in (("v1 (price only)", score_strategy(p1)),
                        ("v4 (price+insider)", score_strategy(p4)),
                        ("momentum 20d", momentum_strategy())):
        bt = Backtest(win, starting_cash=100_000.0, slippage_bps=SLIP_BPS,
                      members_only=True)
        res = bt.run(strat, warmup=120)
        eq = res.equity
        eq = eq[(eq.index >= TEST_START) & (eq.index <= TEST_END)]
        runs[name] = (res, eq)

    bt_spy = Backtest(spy_win, starting_cash=100_000.0, slippage_bps=SLIP_BPS,
                      members_only=False)
    res_spy = bt_spy.run(buy_and_hold("SPY"), warmup=0)
    runs["SPY buy & hold"] = (res_spy, res_spy.equity)

    print(f"{'strategy':<22}{'CAGR':>9}{'Sharpe':>9}{'+/- SE':>9}"
          f"{'max DD':>9}{'trades':>9}{'win%':>8}")
    print("-" * 75)
    rows = {}
    for name, (res, eq) in runs.items():
        r = eq.pct_change().dropna()
        years = (eq.index[-1] - eq.index[0]).days / 365.25
        cagr = (eq.iloc[-1] / eq.iloc[0]) ** (1 / years) - 1
        sh = r.mean() / r.std() * np.sqrt(252)
        dd = float((eq / eq.cummax() - 1).min())
        se = sharpe_se(sh, years)
        n_tr = len([t for t in res.trades
                    if pd.Timestamp(t.entry_date) >= pd.Timestamp(TEST_START)])
        wins = [t for t in res.trades
                if pd.Timestamp(t.entry_date) >= pd.Timestamp(TEST_START) and t.pnl > 0]
        wr = len(wins) / n_tr if n_tr else np.nan
        rows[name] = r
        print(f"{name:<22}{cagr:>9.2%}{sh:>9.2f}{se:>9.2f}{dd:>9.1%}"
              f"{n_tr:>9,}{wr:>8.1%}")

    print("-" * 75)
    print("\n95% Sharpe intervals (point estimate +/- 1.96 SE):")
    for name, (res, eq) in runs.items():
        r = eq.pct_change().dropna()
        years = (eq.index[-1] - eq.index[0]).days / 365.25
        sh = r.mean() / r.std() * np.sqrt(252)
        se = sharpe_se(sh, years)
        print(f"  {name:<22}[{sh-1.96*se:>6.2f}, {sh+1.96*se:>6.2f}]")

    print("\nPaired t-tests on daily returns (are any two of these different?):")
    names = list(rows)
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            a, b = rows[names[i]].align(rows[names[j]], join="inner")
            t, p = stats.ttest_rel(a, b)
            verdict = "different" if p < 0.05 else "not distinguishable"
            print(f"  {names[i]:<22} vs {names[j]:<22} p={p:.3f}  {verdict}")

    print("\nTrade count check against the success bar (200+ trades):")
    for name, (res, eq) in runs.items():
        n = len([t for t in res.trades
                 if pd.Timestamp(t.entry_date) >= pd.Timestamp(TEST_START)])
        print(f"  {name:<22}{n:>6,} trades")

    out = pd.DataFrame({k: v[1] for k, v in runs.items()})
    out.to_csv(DATA / "equity_curves_v4.csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
