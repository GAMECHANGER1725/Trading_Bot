"""
The n_eff = n/(1+(n-1)*rho) shortcut is an approximation borrowed from the
mean of correlated observations. The statistic here is a *win rate across
names*, not a mean, so do it properly instead of trusting the shortcut:

resample TIME in blocks, jointly across every name at once. That preserves
the cross-sectional correlation exactly, because every name sees the same
resampled calendar. The spread of the win-rate across resamples is its true
sampling distribution.
"""
import numpy as np, pandas as pd
rng = np.random.default_rng(3)

b = pd.read_parquet('data/bars/daily.parquet', columns=['symbol','date','close'])
r = b.pivot(index='date', columns='symbol', values='close').sort_index().pct_change()
r = r[r.columns[r.notna().sum() > 2000]]
TARGET, LOOK = 0.15, 20
w = (TARGET/(r.rolling(LOOK).std().shift(1)*np.sqrt(252))).clip(upper=1.5)
rt = w*r
R, RT = r.values, rt.values
ok = ~np.isnan(R) & ~np.isnan(RT)

def sharpes(Rm, Om):
    out = np.full(Rm.shape[1], np.nan)
    for j in range(Rm.shape[1]):
        x = Rm[Om[:, j], j]
        if len(x) < 500: continue
        s = x.std(ddof=1)
        if s <= 0: continue
        out[j] = (((1+x).prod()**(252/len(x)) - 1) / (s*np.sqrt(252)))
    return out

obs = np.nanmean(sharpes(RT, ok) - sharpes(R, ok) > 0)
n, BL = len(R), 10
nb = n // BL
sims = []
for _ in range(400):
    st = rng.integers(0, n-BL, nb)
    ii = np.concatenate([np.arange(i, i+BL) for i in st])
    d = sharpes(RT[ii], ok[ii]) - sharpes(R[ii], ok[ii])
    sims.append(np.nanmean(d > 0))
sims = np.array(sims)

print("="*78)
print("WIN RATE OF VOL-TARGETING ACROSS 402 NAMES — PROPERLY ERROR-BARRED")
print("="*78)
print(f"  observed win rate                {obs:.1%}")
print(f"  bootstrap SE (time resampled     {sims.std():.1%}")
print(f"    jointly across all names)")
lo, hi = np.percentile(sims, [2.5, 97.5])
print(f"  95% interval                     [{lo:.1%}, {hi:.1%}]")
print(f"  does it exclude 50%?             {'YES - real' if lo > 0.5 else 'NO - not proven'}")
print()
print(f"  For contrast, the naive binomial SE that assumes 402 independent")
print(f"  names would be {np.sqrt(0.25/402):.1%} — a {sims.std()/np.sqrt(0.25/402):.0f}x understatement of the true error.")
print()
print("  402 large-cap names is not 402 bets. It is close to one bet,")
print("  measured 402 times.")
