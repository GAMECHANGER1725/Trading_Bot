"""Head to head: six weeks of ML vs four lines of arithmetic. Same window."""
import numpy as np, pandas as pd
rng = np.random.default_rng(5)
def ann(x): return (1+x).prod()**(252/len(x))-1
def vol(x): return x.std(ddof=1)*np.sqrt(252)
def shp(x): return ann(x)/vol(x)
def mdd(x):
    c=(1+x).cumprod(); return (c/c.cummax()-1).min()

d = pd.read_parquet('data/etfs.parquet')
px = d.pivot(index='date', columns='symbol', values='close').sort_index()
r = px.pct_change().dropna()
iv = 1/(r.rolling(20).std().shift(1))
rp = (iv.div(iv.sum(axis=1),axis=0)*r).sum(axis=1)
fv = rp.rolling(20).std().shift(1)*np.sqrt(252)
rpvt = ((0.15/fv).clip(upper=2.5)*rp).dropna()

bk = pd.read_csv('data/bakeoff_curves.csv', index_col=0, parse_dates=True).pct_change().dropna()
v4 = pd.read_csv('data/equity_curves_v4.csv', index_col=0, parse_dates=True).pct_change().dropna()
win = bk.index                                    # 2022-2024, the bot's window
rows = [
 ('bot, v1 (bakeoff run)',      bk['v1  CLS price']),
 ('bot, v1 (v4 run, same spec)',v4['v1 (price only)']),
 ('bot, best of 8 configs',     bk['—   REG price+v3+insider']),
 ('20d momentum, no ML',        bk['mom 20d']),
 ('SPY buy & hold',             bk['SPY']),
 ('risk parity + vol target',   rpvt.reindex(win).dropna()),
]
print("="*80)
print("HEAD TO HEAD ON THE BOT'S OWN WINDOW  (2022-01 to 2024-12)")
print("="*80)
print(f"{'':30} {'ret':>7} {'vol':>7} {'Sharpe':>7} {'maxDD':>8}")
for k,v in rows:
    v=v.dropna()
    print(f"{k:30} {ann(v):>6.2%} {vol(v):>6.2%} {shp(v):>7.2f} {mdd(v):>7.1%}")

print()
print("="*80)
print("AND ON THE FULL TEN YEARS THE BOT NEVER USED  (2016-2026)")
print("="*80)
full = rpvt.index
spy_full = r['SPY'].reindex(full).dropna()
rp_full  = rpvt.reindex(full).dropna()
print(f"{'':30} {'ret':>7} {'vol':>7} {'Sharpe':>7} {'maxDD':>8}")
print(f"{'SPY buy & hold':30} {ann(spy_full):>6.2%} {vol(spy_full):>6.2%} "
      f"{shp(spy_full):>7.2f} {mdd(spy_full):>7.1%}")
print(f"{'risk parity + vol target':30} {ann(rp_full):>6.2%} {vol(rp_full):>6.2%} "
      f"{shp(rp_full):>7.2f} {mdd(rp_full):>7.1%}")
print()
print(f"  return   : {ann(rp_full)-ann(spy_full):+.2%}  (NOT measurable - SE is ~5pp)")
print(f"  volatility: {vol(rp_full)/vol(spy_full)-1:+.1%}  (measurable - SE is ~1.4% relative)")
print(f"  drawdown : {mdd(rp_full)/mdd(spy_full)-1:+.1%}")
print()
print("  The return claim is unprovable. The risk claim is not.")
print("  Build the pitch on the half you can actually defend.")
