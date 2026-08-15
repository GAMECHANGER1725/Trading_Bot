#!/usr/bin/env python3
"""Backtest the 3-fold and 6-fold predictions through the identical engine."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
import numpy as np, pandas as pd
from src.backtest import Backtest, Order, buy_and_hold

ROOT = Path(__file__).resolve().parent.parent.parent; DATA = ROOT/"data"
N_POS, STOP, TARGET, MAX_HOLD, SLIP = 25, 0.08, 0.12, 10, 3.5
rng = np.random.default_rng(7)

def ann(x): return (1+x).prod()**(252/len(x))-1
def vol(x): return x.std(ddof=1)*np.sqrt(252)
def shp(x): return ann(x)/vol(x)
def mdd(x):
    c=(1+x).cumprod(); return (c/c.cummax()-1).min()

bars = pd.read_parquet(DATA/"bars"/"daily.parquet")
bars["date"] = pd.to_datetime(bars["date"])
spy = pd.read_parquet(DATA/"spy.parquet"); spy["date"] = pd.to_datetime(spy["date"])

def strat(preds):
    table = {d: g.set_index("symbol")["score"]
             for d, g in preds.groupby(preds.date.dt.date)}
    def strategy(view, positions, cash, equity):
        s = table.get(view.as_of)
        if s is None: return []
        pool = s[s.index.isin(view.tradable)]
        pool = pool[~pool.index.isin(positions)]
        if pool.empty or len(positions) >= N_POS: return []
        picks = pool.nlargest(N_POS - len(positions))
        if picks.empty: return []
        return [Order(sym, weight=0.95/N_POS, stop_pct=STOP,
                      target_pct=TARGET, max_hold_days=MAX_HOLD)
                for sym in picks.index]
    return strategy

curves = {}
for tag, f in [("3 folds (2022-24, as shipped)", "preds_long_short.parquet"),
               ("6 folds (2019-24, extended)",   "preds_long_long.parquet")]:
    p = pd.read_parquet(DATA/f); p["date"] = pd.to_datetime(p["date"])
    sub = bars[bars.date.isin(p.date.unique())]
    bt = Backtest(sub, slippage_bps=SLIP)
    curves[tag] = bt.run(strat(p), warmup=0).equity

for tag, span in [("SPY over 2022-24", ("2022-01-01","2024-12-31")),
                  ("SPY over 2019-24", ("2019-01-01","2024-12-31"))]:
    s = spy[(spy.date>=span[0])&(spy.date<=span[1])]
    curves[tag] = Backtest(s, slippage_bps=SLIP).run(buy_and_hold("SPY"), warmup=0).equity

pd.DataFrame(curves).to_csv(DATA/"long_window_curves.csv")
print("="*84)
print("SAME MODEL, SAME SEED, SAME ENGINE — ONLY THE TEST WINDOW CHANGES")
print("="*84)
print(f"{'':34} {'ret':>7} {'vol':>7} {'Sharpe':>7} {'+/-SE':>6} {'maxDD':>8}")
BL = 10
for tag, eq in curves.items():
    r = pd.Series(eq).pct_change().dropna()
    x = r.values; n = len(x); nb = n//BL
    ss = [shp(pd.Series(np.concatenate([x[i:i+BL] for i in
          rng.integers(0, n-BL, nb)]))) for _ in range(1500)]
    print(f"{tag:34} {ann(r):>6.2%} {vol(r):>6.2%} {shp(r):>7.2f} "
          f"{np.std(ss):>6.2f} {mdd(r):>7.1%}")

print()
print("="*84)
print("DID THE ERROR BAR ACTUALLY SHRINK?")
print("="*84)
for tag in ["3 folds (2022-24, as shipped)", "6 folds (2019-24, extended)"]:
    r = pd.Series(curves[tag]).pct_change().dropna()
    x = r.values; n=len(x); nb=n//BL
    aa = [ann(pd.Series(np.concatenate([x[i:i+BL] for i in
          rng.integers(0,n-BL,nb)]))) for _ in range(1500)]
    print(f"  {tag:34} {len(r)/252:>4.1f} test years, "
          f"SE on annual return +/-{np.std(aa):.2%}")
print()
print("  Predicted shrinkage from sqrt(T): "
      f"{np.sqrt(6/3):.2f}x. This is the whole point of Phase 0.")
