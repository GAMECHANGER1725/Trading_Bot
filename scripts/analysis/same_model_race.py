#!/usr/bin/env python3
"""
The decisive test on "which model is best".

rank_models.py bootstraps TIME: "if 2022-24 had gone differently, would v4
still win?" It answers 42.4% of the time. But it holds the trained model
fixed, so it cannot see the larger source of variation — D38 measured an
8.1pp/yr spread from RE-RUNNING the same specification.

So enter the same specification twice, as two separate horses. If a model's
two realisations land far apart in the ranking, the ranking is scoring the
realisation, not the model, and "which model is best" has no answer.

Every entrant below is CLS/REG XGBoost, seed 42, identical hyperparameters,
25 positions, 8%/12%/10 days, 3.5 bps, 2022-2024.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
import numpy as np, pandas as pd
rng = np.random.default_rng(13)
ROOT = Path(__file__).resolve().parent.parent.parent; DATA = ROOT/"data"

def ann(x): return (1+x).prod()**(252/len(x))-1
def shp(x): return ann(x)/(x.std(ddof=1)*np.sqrt(252))

bk = pd.read_csv(DATA/"bakeoff_curves.csv", index_col=0, parse_dates=True)
v4 = pd.read_csv(DATA/"equity_curves_v4.csv", index_col=0, parse_dates=True)
lw = pd.read_csv(DATA/"long_window_curves.csv", index_col=0, parse_dates=True)

field = pd.DataFrame({
    'v1  run A (panel_all, 601 sym)' : bk['v1  CLS price'],
    'v1  run B (panel_v4,  601 sym)' : v4['v1 (price only)'],
    'v1  run C (price-only, 673 sym)': lw['3 folds (2022-24, as shipped)'],
    'v4  run A (panel_all)'          : bk['v4  CLS price+insider'],
    'v4  run B (panel_v4)'           : v4['v4 (price+insider)'],
    'best-of-8 (REG p+v3+ins)'       : bk['—   REG price+v3+insider'],
    'mom 20d, no ML'                 : bk['mom 20d'],
    'SPY buy & hold'                 : bk['SPY'],
}).dropna()
r = field.pct_change().dropna()

print("="*88)
print("THE SAME SPECIFICATION, ENTERED TWICE — 2022-2024, identical settings")
print("="*88)
print(f"{'entrant':34} {'return':>8} {'Sharpe':>8}")
for c in r.columns:
    print(f"{c:34} {ann(r[c]):>7.2%} {shp(r[c]):>8.2f}")
print()
for pair, lab in [(['v1  run A (panel_all, 601 sym)','v1  run B (panel_v4,  601 sym)',
                    'v1  run C (price-only, 673 sym)'], 'v1'),
                  (['v4  run A (panel_all)','v4  run B (panel_v4)'], 'v4')]:
    v = [ann(r[c]) for c in pair]
    print(f"  {lab}: same spec, {len(pair)} runs -> "
          f"{', '.join(f'{x:.2%}' for x in v)}   spread {max(v)-min(v):.2%}/yr")

print()
print("="*88)
print("HORSE RACE   block bootstrap, 4000 resamples, 10-day blocks")
print("="*88)
X = r.values; n, BL = len(r), 10; nb = n//BL
cols = list(r.columns); ns = len(cols)
win = np.zeros(ns); beat = np.zeros(ns)
si = cols.index('SPY buy & hold')
for _ in range(4000):
    ii = np.concatenate([np.arange(i,i+BL) for i in rng.integers(0,n-BL,nb)])
    sub = X[ii]
    sh = np.array([shp(pd.Series(sub[:,j])) for j in range(ns)])
    win[np.nanargmax(sh)] += 1
    beat += (sh > sh[si]).astype(float)
print(f"{'entrant':34} {'#1 by Sharpe':>14} {'beats SPY':>12}")
for j in np.argsort(-win):
    print(f"{cols[j]:34} {win[j]/40:>13.1f}% {beat[j]/40:>11.1f}%")

print()
print("="*88)
print("VERDICT")
print("="*88)
a = win[cols.index('v4  run A (panel_all)')]/40
b = win[cols.index('v4  run B (panel_v4)')]/40
c1 = win[cols.index('v1  run A (panel_all, 601 sym)')]/40
c2 = win[cols.index('v1  run B (panel_v4,  601 sym)')]/40
print(f"  v4 run A takes #1 {a:.1f}% of the time. v4 run B takes it {b:.1f}%.")
print(f"  v1 run A takes #1 {c1:.1f}% of the time. v1 run B takes it {c2:.1f}%.")
print()
print(f"  Gap between the two v4 runs : {abs(a-b):.1f} points")
print(f"  Gap between v4 run B and v1 run B: "
      f"{abs(b-c2):.1f} points")
print()
print("  If a specification cannot beat ITSELF consistently, the ranking is not")
print("  measuring the specification. There is no best model in this field.")
