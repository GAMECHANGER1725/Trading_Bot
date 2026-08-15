"""Attribution across EVERY config, so the conclusion is not an artefact
of which equity curve got picked."""
import numpy as np, pandas as pd

def ann_ret(x): return (1+x).prod()**(252/len(x)) - 1
def ann_vol(x): return x.std(ddof=1)*np.sqrt(252)

def attribute(curves, spy_col, label):
    r_all = curves.pct_change().dropna()
    spy = r_all[spy_col]
    print(f"\n{'='*86}\n{label}\n{'='*86}")
    print(f"{'strategy':26} {'ret':>7} {'vol':>7} {'Sharpe':>7} {'beta':>6} "
          f"{'alpha/yr':>9} {'t(a)':>6} {'volmatch':>9} {'gap':>7}")
    rows = []
    for c in curves.columns:
        if c == spy_col: continue
        y = r_all[c].values
        X = np.column_stack([np.ones(len(y)), spy.values])
        coef, *_ = np.linalg.lstsq(X, y, rcond=None)
        res = y - X@coef
        s2 = res@res/(len(y)-2)
        se = np.sqrt(np.diag(s2*np.linalg.inv(X.T@X)))
        a, b = coef; ta = a/se[0]
        k = ann_vol(r_all[c])/ann_vol(spy)
        vm = ann_ret(spy*k)
        gap = ann_ret(r_all[c]) - vm
        rows.append((c, ann_ret(r_all[c]), ann_vol(r_all[c]),
                     ann_ret(r_all[c])/ann_vol(r_all[c]), b, a*252, ta, vm, gap))
    for c,rt,vl,sh,b,al,ta,vm,gap in rows:
        print(f"{c[:26]:26} {rt:>6.2%} {vl:>6.2%} {sh:>7.2f} {b:>6.2f} "
              f"{al:>+8.2%} {ta:>6.2f} {vm:>8.2%} {gap:>+6.2%}")
    print(f"{'-'*86}")
    print(f"{'SPY':26} {ann_ret(spy):>6.2%} {ann_vol(spy):>6.2%} "
          f"{ann_ret(spy)/ann_vol(spy):>7.2f}")
    n_beat = sum(1 for *_ , gap in [(r[0], r[-1]) for r in rows] if gap > 0)
    print(f"\nconfigs beating a vol-matched SPY sleeve: "
          f"{sum(1 for r in rows if r[-1] > 0)} of {len(rows)}")
    print(f"configs with |t(alpha)| > 2:              "
          f"{sum(1 for r in rows if abs(r[6]) > 2)} of {len(rows)}")
    return rows

bk = pd.read_csv('data/bakeoff_curves.csv', index_col=0, parse_dates=True)
attribute(bk, 'SPY', "ALL 8 BAKEOFF CONFIGS + 20d momentum baseline, 2022-2024")

v4 = pd.read_csv('data/equity_curves_v4.csv', index_col=0, parse_dates=True)
attribute(v4, 'SPY buy & hold', "THE HEADLINE CURVES (equity_curves_v4)")

# power: minimum detectable Sharpe difference
print(f"\n{'='*86}\nSTATISTICAL POWER — what the sealed holdout can actually decide\n{'='*86}")
r = bk[['v1  CLS price','SPY']].pct_change().dropna()
S1 = ann_ret(r.iloc[:,0])/ann_vol(r.iloc[:,0]); rho = r.iloc[:,0].corr(r.iloc[:,1])
print(f"assumed correlation to SPY: {rho:.2f}  (higher correlation = easier to detect)")
print(f"\n{'holdout length':>16} {'smallest detectable dSharpe (p<0.05)':>40}")
for T in [0.6, 1, 2, 3, 5, 10, 20]:
    lo, hi = 0.001, 20.0
    for _ in range(200):
        d = (lo+hi)/2
        S2 = S1+d
        se = np.sqrt((2-2*rho+0.5*(S1**2+S2**2-2*rho**2*S1*S2))/T)
        if 1.96*se < d: hi = d
        else: lo = d
    print(f"{T:>14.1f}y {hi:>39.2f}")
print("\nSPY's own Sharpe over 2022-24 was ~0.49. A dSharpe of 0.5 means")
print("roughly DOUBLING SPY's risk-adjusted return.")
