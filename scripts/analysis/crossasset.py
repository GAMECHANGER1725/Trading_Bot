"""
Does breadth across GENUINELY different markets buy the statistical power
that 402 correlated large caps did not?

Everything below is pre-specified: 15 liquid ETFs, inverse-vol weighting,
12-1 month time-series momentum, 20-day vol lookback, 15% vol target.
No parameter was searched. These are the textbook values.
"""
import numpy as np, pandas as pd
rng = np.random.default_rng(4)

d = pd.read_parquet('data/etfs.parquet')
px = d.pivot(index='date', columns='symbol', values='close').sort_index()
r = px.pct_change().dropna()
print(f"{px.shape[1]} ETFs, {r.index.min().date()} -> {r.index.max().date()} "
      f"({len(r)/252:.1f} years)\n")

print("="*78)
print("STEP 1. IS THIS ACTUALLY DIVERSIFIED, OR 402 LARGE CAPS IN A HAT?")
print("="*78)
c = r.corr().values; m = len(c)
rho = (c.sum()-m)/(m*(m-1))
n_eff = m/(1+(m-1)*rho)
print(f"  average pairwise correlation     {rho:.3f}   "
      f"(S&P 500 single names: 0.414)")
print(f"  effective independent bets       {n_eff:.1f}     "
      f"(402 large caps gave 2.4)")
print()
print("  correlation to SPY:")
for s in sorted(r.columns, key=lambda s: r[s].corr(r['SPY'])):
    print(f"    {s:5} {r[s].corr(r['SPY']):+.2f}")

def ann(x): return (1+x).prod()**(252/len(x))-1
def vol(x): return x.std(ddof=1)*np.sqrt(252)
def shp(x): return ann(x)/vol(x)
def mdd(x):
    cc=(1+x).cumprod(); return (cc/cc.cummax()-1).min()

TARGET, LOOK = 0.15, 20
iv = 1/(r.rolling(LOOK).std().shift(1))                 # inverse vol
rp = (iv.div(iv.sum(axis=1), axis=0) * r).sum(axis=1)   # risk parity
mom = px.pct_change(252).shift(1).reindex(r.index)
sig = (mom > 0).astype(float)
rp_tr = (iv.mul(sig).div(iv.mul(sig).sum(axis=1).replace(0,np.nan), axis=0)*r
         ).sum(axis=1).fillna(0)
ew = r.mean(axis=1)

def voltarget(x, cap):
    fv = x.rolling(LOOK).std().shift(1)*np.sqrt(252)
    return ((TARGET/fv).clip(upper=cap)*x).dropna()

cand = {
    'SPY buy & hold'            : r['SPY'],
    '60/40 SPY/IEF'             : 0.6*r['SPY'] + 0.4*r['IEF'],
    'equal weight 15 ETFs'      : ew,
    'risk parity'               : rp,
    'risk parity + 12m trend'   : rp_tr,
    'risk parity, vol-tgt 2.5x' : voltarget(rp, 2.5),
    'RP + trend, vol-tgt 2.5x'  : voltarget(rp_tr, 2.5),
}
idx = cand['RP + trend, vol-tgt 2.5x'].index
print()
print("="*78)
print(f"STEP 2. RESULTS  ({idx.min().date()} -> {idx.max().date()}, "
      f"{len(idx)/252:.1f}y, no costs yet)")
print("="*78)
print(f"{'strategy':28} {'ret':>7} {'vol':>7} {'Sharpe':>7} {'maxDD':>8} {'corr SPY':>9}")
for k,v in cand.items():
    v = v.reindex(idx).dropna()
    print(f"{k:28} {ann(v):>6.2%} {vol(v):>6.2%} {shp(v):>7.2f} {mdd(v):>7.1%} "
          f"{v.corr(r['SPY'].reindex(idx)):>9.2f}")

print()
print("="*78)
print("STEP 3. PAIRED BOOTSTRAP vs SPY — the only line that decides anything")
print("="*78)
base = cand['SPY buy & hold'].reindex(idx).dropna().values
BL, n = 10, len(idx); nb = n//BL
print(f"{'strategy':28} {'dSharpe':>9} {'95% CI':>19} {'p':>7}  verdict")
for k,v in cand.items():
    if k=='SPY buy & hold': continue
    xv = v.reindex(idx).dropna().values
    dd=[]
    for _ in range(4000):
        st = rng.integers(0, n-BL, nb)
        ii = np.concatenate([np.arange(i,i+BL) for i in st])
        dd.append(shp(pd.Series(xv[ii]))-shp(pd.Series(base[ii])))
    dd=np.array(dd); lo,hi=np.percentile(dd,[2.5,97.5])
    p=2*min((dd<=0).mean(),(dd>=0).mean())
    print(f"{k:28} {shp(pd.Series(xv))-shp(pd.Series(base)):>+9.2f} "
          f"[{lo:>+6.2f},{hi:>+6.2f}] {p:>7.3f}  "
          f"{'REAL - CI excludes 0' if lo>0 else 'not proven'}")

print()
print("="*78)
print("STEP 4. COST REALITY CHECK — how much turnover would eat this?")
print("="*78)
wts = iv.div(iv.sum(axis=1), axis=0)
to = wts.diff().abs().sum(axis=1).mean()
print(f"  risk parity daily turnover       {to:.2%} of book")
print(f"  annualised                       {to*252:.0f}x book per year")
for bps in [1, 3.5, 10]:
    drag = to*252*bps/10000
    print(f"    at {bps:>4.1f} bps/side -> {drag:.2%} per year of drag")
print("  (rebalancing monthly instead of daily cuts this by roughly 20x)")
