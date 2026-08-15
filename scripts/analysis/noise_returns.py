"""
The existing noise floor measured accuracy and IC. Nobody measured the error
bar on the thing that actually decides the project: the equity curve.

Two estimates, from different directions.
"""
import numpy as np, pandas as pd
rng = np.random.default_rng(0)

bk = pd.read_csv('data/bakeoff_curves.csv', index_col=0, parse_dates=True)
v4 = pd.read_csv('data/equity_curves_v4.csv', index_col=0, parse_dates=True)

def ann(x): return (1+x).prod()**(252/len(x)) - 1
def shp(x): return ann(x)/(x.std(ddof=1)*np.sqrt(252))

print("="*78)
print("A. BLOCK BOOTSTRAP ON THE EQUITY CURVE  (10-day blocks, 2000 resamples)")
print("="*78)
print("How much would the headline move if 2022-24 had gone slightly differently?")
print()
print(f"{'strategy':26} {'ann ret':>9} {'+/- SE':>8} {'Sharpe':>8} {'+/- SE':>8}")
BL = 10
for src, cols in [(bk, ['v1  CLS price', 'v4  CLS price+insider', 'SPY']),
                  (v4, ['v1 (price only)', 'v4 (price+insider)'])]:
    r = src.pct_change().dropna()
    for c in cols:
        x = r[c].values; n = len(x)
        nb = n // BL
        rets, shps = [], []
        for _ in range(2000):
            starts = rng.integers(0, n - BL, nb)
            s = np.concatenate([x[i:i+BL] for i in starts])
            rets.append(ann(pd.Series(s))); shps.append(shp(pd.Series(s)))
        print(f"{c[:26]:26} {ann(pd.Series(x)):>8.2%} {np.std(rets):>7.2%} "
              f"{shp(pd.Series(x)):>8.2f} {np.std(shps):>8.2f}")

print()
print("="*78)
print("B. THE NATURAL EXPERIMENT ALREADY IN THE REPO")
print("="*78)
a = pd.read_parquet('data/preds_v1_window.parquet').set_index(['date','symbol'])['score']
b = pd.read_parquet('data/preds_CLS_price.parquet').set_index(['date','symbol'])['score']
j = pd.concat([a.rename('A'), b.rename('B')], axis=1, join='inner')
daily = j.groupby(level=0).apply(lambda g: g['A'].corr(g['B'], method='spearman'))
ov = j.groupby(level=0).apply(
    lambda g: len(set(g['A'].nlargest(25).index.get_level_values(1)) &
                  set(g['B'].nlargest(25).index.get_level_values(1))))
rA = v4['v1 (price only)'].pct_change().dropna()
rB = bk['v1  CLS price'].pct_change().dropna()
print("preds_v1_window.parquet vs preds_CLS_price.parquet")
print("Both are 'v1 = CLS architecture, price features only'. Same engine,")
print("same 25 positions, same 8/12/10 barriers, same 3.5bps, same dates.")
print()
print(f"  agreement between the two models : rank corr {daily.median():.3f}, "
      f"{ov.mean():.0f} of 25 names identical each day")
print(f"  backtested annual return         : {ann(rA):.2%}  vs  {ann(rB):.2%}")
print(f"  gap from a ~5-name-a-day difference: {ann(rA)-ann(rB):+.2%} per year")
print(f"  backtested Sharpe                : {shp(rA):.2f}  vs  {shp(rB):.2f}")
print()
print("  The eight-cell bakeoff spans -1.58% to +17.65% per year.")
print(f"  Re-running the SAME cell moves it by {abs(ann(rA)-ann(rB)):.1%}.")
print("  The table is not ranking models. It is ranking coin flips.")
