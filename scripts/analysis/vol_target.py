"""
The project has been trying to raise the numerator of the Sharpe ratio.
Test the other option: act on the denominator.

Rationale, stated before running anything:
  Returns have almost no autocorrelation. Volatility has a great deal.
  Forecasting next month's volatility from last month's is one of the most
  reliable relationships in finance. Forecasting next month's return is not.
  So a rule that sizes exposure by forecast volatility is betting on the
  quantity that is actually forecastable.

No model, no features, no fitting. One line of arithmetic, ten years of SPY.
"""
import numpy as np, pandas as pd
rng = np.random.default_rng(1)

spy = pd.read_parquet('data/spy.parquet').set_index('date').sort_index()
r = spy['close'].pct_change().dropna()
print(f"SPY daily returns: {r.index.min().date()} -> {r.index.max().date()}  "
      f"({len(r)} days, {len(r)/252:.1f} years)\n")

def ann(x): return (1+x).prod()**(252/len(x)) - 1
def vol(x): return x.std(ddof=1)*np.sqrt(252)
def shp(x): return ann(x)/vol(x)
def mdd(x):
    c = (1+x).cumprod(); return (c/c.cummax()-1).min()

# --- how predictable is each quantity? the whole argument rests on this ---
print("="*78)
print("STEP 0. WHICH QUANTITY IS ACTUALLY FORECASTABLE?")
print("="*78)
rv = r.rolling(20).std()
nxt_rv = rv.shift(-20)
nxt_ret = r.shift(-20).rolling(20).sum()
m = pd.concat([rv, nxt_rv, r.rolling(20).sum(), nxt_ret], axis=1).dropna()
m.columns = ['vol_t','vol_next','ret_t','ret_next']
print(f"  corr( vol this month , vol next month )  =  {m.vol_t.corr(m.vol_next):+.3f}")
print(f"  corr( ret this month , ret next month )  =  {m.ret_t.corr(m.ret_next):+.3f}")
print("  -> volatility persists. return does not. Build on the one that persists.")

# --- how precisely can each be MEASURED? ---
print()
print("="*78)
print("STEP 0b. WHICH QUANTITY CAN YOU MEASURE WELL ENOUGH TO PROVE A CHANGE?")
print("="*78)
n = len(r)
se_vol_rel = 1/np.sqrt(2*n)
se_ret_abs = r.std(ddof=1)*np.sqrt(252)/np.sqrt(n/252)
print(f"  over {n/252:.1f} years of daily data:")
print(f"    annual VOLATILITY   {vol(r):6.2%}  +/- {vol(r)*se_vol_rel:.2%}   "
      f"({se_vol_rel*100:.1f}% relative error)")
print(f"    annual RETURN       {ann(r):6.2%}  +/- {se_ret_abs:.2%}   "
      f"({se_ret_abs/abs(ann(r))*100:.0f}% relative error)")
print(f"  -> volatility is measured ~{(se_ret_abs/abs(ann(r)))/se_vol_rel:.0f}x more precisely than return.")
print("     A strategy whose edge is in the denominator is PROVABLE.")
print("     A strategy whose edge is in the numerator is not, at this sample size.")

# --- the rule ---
print()
print("="*78)
print("STEP 1. VOLATILITY TARGETING ON SPY, 2016-2026")
print("="*78)
LOOK, TARGET = 20, 0.15
fvol = r.rolling(LOOK).std().shift(1) * np.sqrt(252)   # shift(1): yesterday's info only

results = {}
results['SPY buy & hold'] = r.copy()
for cap in [1.0, 1.5, 2.0]:
    w = (TARGET / fvol).clip(upper=cap)
    results[f'vol-target 15%, max {cap:.1f}x'] = (w * r).dropna()

# trend, for comparison
mom12 = spy['close'].pct_change(252).shift(1)
results['12m trend (in/out)'] = (r * (mom12 > 0).astype(float)).dropna()
results['trend + vol-target'] = ((TARGET/fvol).clip(upper=1.5) *
                                 (mom12 > 0).astype(float) * r).dropna()

idx = results['trend + vol-target'].index          # common window, no free lunch
print(f"common window: {idx.min().date()} -> {idx.max().date()} ({len(idx)/252:.1f}y)\n")
print(f"{'strategy':26} {'ret':>7} {'vol':>7} {'Sharpe':>7} {'+/-SE':>6} {'maxDD':>8} {'avg lev':>8}")
BL = 10
base = None
for k, v in results.items():
    v = v.reindex(idx).dropna()
    shps = []
    x = v.values; nn = len(x); nb = nn//BL
    for _ in range(2000):
        st = rng.integers(0, nn-BL, nb)
        s = pd.Series(np.concatenate([x[i:i+BL] for i in st]))
        shps.append(shp(s))
    lev = (v/r.reindex(idx)).replace([np.inf,-np.inf], np.nan).abs().mean()
    print(f"{k:26} {ann(v):>6.2%} {vol(v):>6.2%} {shp(v):>7.2f} {np.std(shps):>6.2f} "
          f"{mdd(v):>7.1%} {lev:>8.2f}")
    if k == 'SPY buy & hold': base = v

# paired test on the Sharpe difference - the honest one
print()
print("="*78)
print("STEP 2. IS THE IMPROVEMENT REAL, OR INSIDE THE ERROR BAR?")
print("="*78)
print("Paired block bootstrap: resample the SAME days for both series, so the")
print("shared market move cancels and only the difference is tested.\n")
print(f"{'strategy vs SPY':26} {'dSharpe':>9} {'95% CI':>20} {'p':>7}")
xb = base.values
for k, v in results.items():
    if k == 'SPY buy & hold': continue
    v = v.reindex(idx).dropna(); xv = v.values
    nn = min(len(xb), len(xv)); nb = nn//BL
    d = []
    for _ in range(4000):
        st = rng.integers(0, nn-BL, nb)
        ii = np.concatenate([np.arange(i,i+BL) for i in st])
        d.append(shp(pd.Series(xv[ii])) - shp(pd.Series(xb[ii])))
    d = np.array(d); lo, hi = np.percentile(d, [2.5, 97.5])
    p = 2*min((d<=0).mean(), (d>=0).mean())
    flag = 'REAL' if lo > 0 else 'not proven'
    print(f"{k:26} {shp(v)-shp(base):>+9.2f} [{lo:>+6.2f}, {hi:>+6.2f}] {p:>7.3f}  {flag}")
