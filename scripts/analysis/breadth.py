"""
On SPY alone, vol-targeting's +0.18 Sharpe has p=0.38. One market, ten years,
cannot settle it. That is not a defect of the idea; it is arithmetic.

The fix used by every serious systematic shop: stop testing one market.
Run the SAME rule, unchanged, across hundreds of series and count how often
it wins. Breadth buys the significance that time cannot.
"""
import numpy as np, pandas as pd
rng = np.random.default_rng(2)

b = pd.read_parquet('data/bars/daily.parquet', columns=['symbol','date','close'])
px = b.pivot(index='date', columns='symbol', values='close').sort_index()
r = px.pct_change()
# only names with a full history, so survivorship is explicit not accidental
full = r.columns[r.notna().sum() > 2000]
r = r[full]
print(f"{len(full)} symbols with >2000 daily observations, "
      f"{r.index.min().date()} -> {r.index.max().date()}\n")

TARGET, LOOK = 0.15, 20
fvol = r.rolling(LOOK).std().shift(1)*np.sqrt(252)
w = (TARGET/fvol).clip(upper=1.5)
rt = (w*r)

def ann(x): return (1+x).prod()**(252/len(x))-1
def shp(x):
    x = x.dropna()
    if len(x) < 500: return np.nan
    v = x.std(ddof=1)*np.sqrt(252)
    return ann(x)/v if v > 0 else np.nan

base = r.apply(shp); targ = rt.apply(shp)
d = (targ - base).dropna()
n = len(d)
wins = (d > 0).sum()

print("="*78)
print("THE SAME RULE, RUN UNCHANGED ON EVERY S&P 500 NAME")
print("="*78)
print(f"  names tested                     {n}")
print(f"  Sharpe improved                  {wins}  ({wins/n:.1%})")
print(f"  median change in Sharpe          {d.median():+.3f}")
print(f"  mean change in Sharpe            {d.mean():+.3f}")
print(f"  median change in volatility      "
      f"{(rt.apply(lambda x: x.dropna().std()*np.sqrt(252)) / r.apply(lambda x: x.dropna().std()*np.sqrt(252))).median()-1:+.1%}")

# sign test - but stocks are correlated, so a naive binomial overstates it.
# correct for it by estimating the effective number of independent names.
c = r.dropna(axis=1).corr().values
avg_rho = (c.sum()-len(c))/(len(c)*(len(c)-1))
n_eff = n/(1+(n-1)*avg_rho)
from scipy import stats
p_naive = stats.binomtest(int(wins), n, 0.5).pvalue
w_eff = wins/n*n_eff
p_eff = stats.binomtest(int(round(w_eff)), int(round(n_eff)), 0.5).pvalue
print()
print(f"  average pairwise correlation     {avg_rho:.3f}")
print(f"  effective independent names      {n_eff:.1f}   (not {n} — stocks move together)")
print(f"  sign test, naive                 p = {p_naive:.2e}   <- WRONG, ignores correlation")
print(f"  sign test, correlation-adjusted  p = {p_eff:.4f}   <- the honest number")

print()
print("="*78)
print("WHAT BREADTH ACTUALLY BUYS: years needed to prove dSharpe = 0.20")
print("="*78)
S, rho_sp = 0.9, 0.67
for K, lab in [(1,'SPY alone'), (5,'5 liquid ETFs, cross-asset'),
               (10,'10 markets'), (20,'20 markets'), (int(n_eff), f'{int(n_eff)} equity names')]:
    T = (2-2*rho_sp+0.5*(S**2+(S+0.2)**2-2*rho_sp**2*S*(S+0.2)))*(1.96/0.2)**2 / K
    print(f"  {lab:32} {T:>7.1f} years")
print()
print("  Correlated equity names add little — they are nearly the same bet.")
print("  Genuinely different markets (bonds, commodities, FX) are what count.")
