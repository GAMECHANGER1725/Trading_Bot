#!/usr/bin/env python3
"""
"Which model is best?" — answered by asking whether the question has an answer.

Rank all eight cells plus the baselines on every metric the project has used,
then test whether the ranking is stable. A ranking that reshuffles when you
resample the same data is not a ranking.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
import numpy as np, pandas as pd
rng = np.random.default_rng(11)

ROOT = Path(__file__).resolve().parent.parent.parent; DATA = ROOT/"data"
def ann(x): return (1+x).prod()**(252/len(x))-1
def vol(x): return x.std(ddof=1)*np.sqrt(252)
def shp(x): return ann(x)/vol(x)
def mdd(x):
    c=(1+x).cumprod(); return (c/c.cummax()-1).min()

bk = pd.read_csv(DATA/"bakeoff_curves.csv", index_col=0, parse_dates=True)
tbl = pd.read_csv(DATA/"bakeoff.csv")
r = bk.pct_change().dropna()
spy = r['SPY']
CELLS = [c for c in bk.columns if c != 'SPY']
lut = {'v1  CLS price':0, '—   CLS price+v3':1, 'v4  CLS price+insider':2,
       '—   CLS price+v3+insider':3, 'v2  REG price':4, 'v3  REG price+v3':5,
       '—   REG price+insider':6, '—   REG price+v3+insider':7}

rows = []
for c in CELLS:
    y = r[c].values
    X = np.column_stack([np.ones(len(y)), spy.values])
    coef, *_ = np.linalg.lstsq(X, y, rcond=None)
    res = y - X@coef
    se = np.sqrt(np.diag((res@res/(len(y)-2))*np.linalg.inv(X.T@X)))
    rec = dict(cell=c, ret=ann(r[c]), vol=vol(r[c]), sharpe=shp(r[c]),
               alpha=coef[0]*252, t_alpha=coef[0]/se[0], beta=coef[1],
               mdd=mdd(r[c]), out_acc=np.nan, ic_fwd=np.nan, t_fwd=np.nan)
    if c in lut:
        i = lut[c]
        rec.update(out_acc=tbl.out_acc.iloc[i], ic_fwd=tbl.ic_fwd.iloc[i],
                   t_fwd=tbl.t_fwd.iloc[i])
    rows.append(rec)
d = pd.DataFrame(rows).set_index('cell')

print("="*102)
print("EVERY MODEL, EVERY METRIC   (2022-2024, 25 positions, 3.5 bps)")
print("="*102)
print(f"{'model':26} {'acc':>7} {'IC':>9} {'t(IC)':>7} {'return':>8} {'Sharpe':>7} "
      f"{'alpha':>8} {'t(a)':>6} {'beta':>6} {'maxDD':>7}")
for c in CELLS:
    x = d.loc[c]
    a = f"{x.out_acc:.4f}" if not pd.isna(x.out_acc) else "   —   "
    i = f"{x.ic_fwd:+.4f}" if not pd.isna(x.ic_fwd) else "    —    "
    t = f"{x.t_fwd:+.2f}"  if not pd.isna(x.t_fwd)  else "   —   "
    print(f"{c[:26]:26} {a:>7} {i:>9} {t:>7} {x.ret:>7.2%} {x.sharpe:>7.2f} "
          f"{x.alpha:>+7.2%} {x.t_alpha:>6.2f} {x.beta:>6.2f} {x.mdd:>6.1%}")
print(f"{'SPY buy & hold':26} {'   —   ':>7} {'    —    ':>9} {'   —   ':>7} "
      f"{ann(spy):>7.2%} {shp(spy):>7.2f} {'   —':>8} {'   —':>6} {1.00:>6.2f} "
      f"{mdd(spy):>6.1%}")

print()
print("="*102)
print("DO THE METRICS AGREE ON WHO IS BEST?   Spearman rank correlation, 8 cells")
print("="*102)
sub = d.loc[[c for c in CELLS if c in lut], ['out_acc','ic_fwd','ret','sharpe','alpha']]
cm = sub.corr(method='spearman')
print(cm.round(2).to_string())
print()
print(f"  accuracy (what it is TRAINED on) vs return (what you get PAID on): "
      f"{cm.loc['out_acc','ret']:+.2f}")

print()
print("="*102)
print("HOW OFTEN IS EACH MODEL #1?   block bootstrap, 4000 resamples, 10-day blocks")
print("="*102)
X = r[CELLS].values; S = spy.values
n, BL = len(r), 10; nb = n//BL
win_s = np.zeros(len(CELLS)); win_r = np.zeros(len(CELLS)); beat = np.zeros(len(CELLS))
for _ in range(4000):
    ii = np.concatenate([np.arange(i,i+BL) for i in rng.integers(0,n-BL,nb)])
    sub_ = X[ii]
    sh = np.array([shp(pd.Series(sub_[:,j])) for j in range(len(CELLS))])
    rt = np.array([ann(pd.Series(sub_[:,j])) for j in range(len(CELLS))])
    win_s[np.nanargmax(sh)] += 1; win_r[np.nanargmax(rt)] += 1
    beat += (sh > shp(pd.Series(S[ii]))).astype(float)
print(f"{'model':26} {'#1 by Sharpe':>14} {'#1 by return':>14} {'beats SPY':>12}")
order = np.argsort(-win_s)
for j in order:
    print(f"{CELLS[j][:26]:26} {win_s[j]/40:>13.1f}% {win_r[j]/40:>13.1f}% "
          f"{beat[j]/40:>11.1f}%")
print()
print("  A real winner takes the top spot near 100% of the time. Nothing here does.")

print()
print("="*102)
print("WHAT TO PICK WHEN NOTHING IS DISTINGUISHABLE")
print("="*102)
print(f"{'candidate':32} {'parameters fitted':>20} {'selected from':>32}")
for nm, p, sel in [
    ("SPY buy & hold",                "0",            "nothing"),
    ("20d momentum, no ML",           "0",            "nothing"),
    ("risk parity + vol target",      "0 (textbook)", "nothing"),
    ("v1  CLS price",                 "300 trees x3", "8 cells, chosen before results"),
    ("best-looking of the 8",         "300 trees x3", "8 cells, chosen AFTER results"),
]:
    print(f"{nm:32} {p:>20} {sel:>32}")
print()
print("  When effects are indistinguishable the tie-break is not performance.")
print("  It is how much opportunity each candidate had to fit noise.")
