"""
What actually produced the bot's return? Measure before theorising.

Three questions the headline table cannot answer:
  1. Is the 2022 outperformance selection skill, or just low exposure?
  2. Does the bot beat SPY *risk-adjusted* — i.e. against a SPY position
     scaled to the bot's own volatility?
  3. How much data would be needed to answer (2) with statistical confidence?
"""
import numpy as np, pandas as pd

df = pd.read_csv('data/bakeoff_curves.csv', index_col=0, parse_dates=True)
BOT = 'v1  CLS price'          # the production config
SPY = 'SPY'

r = df[[BOT, SPY]].pct_change().dropna()
r.columns = ['bot', 'spy']
T_years = len(r) / 252

def ann_ret(x):  return (1 + x).prod() ** (252 / len(x)) - 1
def ann_vol(x):  return x.std(ddof=1) * np.sqrt(252)
def sharpe(x):   return ann_ret(x) / ann_vol(x)

print("=" * 72)
print("1. RISK AND RETURN, 2022-2024")
print("=" * 72)
print(f"{'':12} {'ann ret':>9} {'ann vol':>9} {'Sharpe':>8} {'maxDD':>8}")
for name in ['bot', 'spy']:
    x = r[name]
    cum = (1 + x).cumprod()
    dd = (cum / cum.cummax() - 1).min()
    print(f"{name:12} {ann_ret(x):>8.2%} {ann_vol(x):>8.2%} {sharpe(x):>8.2f} {dd:>8.2%}")

print()
print("=" * 72)
print("2. WHERE THE RETURN CAME FROM  (r_bot = alpha + beta*r_spy)")
print("=" * 72)
X = np.column_stack([np.ones(len(r)), r['spy'].values])
y = r['bot'].values
coef, *_ = np.linalg.lstsq(X, y, rcond=None)
resid = y - X @ coef
dof = len(y) - 2
s2 = resid @ resid / dof
cov = s2 * np.linalg.inv(X.T @ X)
se = np.sqrt(np.diag(cov))
alpha_d, beta = coef
t_alpha, t_beta = coef / se

print(f"  beta          {beta:6.3f}   (t = {t_beta:5.2f})   "
      f"-> bot carries {beta:.0%} of SPY's market risk")
print(f"  alpha, daily  {alpha_d:+.5f}  (t = {t_alpha:5.2f})")
print(f"  alpha, annual {alpha_d*252:+.2%}  <- the ONLY part that is skill")
print(f"  R^2           {1 - resid.var()/y.var():6.3f}")
ci_lo, ci_hi = (alpha_d - 1.96*se[0])*252, (alpha_d + 1.96*se[0])*252
print(f"  95% CI on annual alpha: [{ci_lo:+.2%}, {ci_hi:+.2%}]")
print(f"  {'ALPHA IS INDISTINGUISHABLE FROM ZERO' if abs(t_alpha) < 2 else 'alpha significant'}")

print()
print("=" * 72)
print("3. THE HONEST BENCHMARK: SPY scaled to the bot's volatility")
print("=" * 72)
k = ann_vol(r['bot']) / ann_vol(r['spy'])
spy_scaled = r['spy'] * k
print(f"  scale factor needed          {k:.3f}  (hold {k:.0%} SPY, {1-k:.0%} cash)")
print(f"  vol-matched SPY  ann ret     {ann_ret(spy_scaled):>8.2%}")
print(f"  bot              ann ret     {ann_ret(r['bot']):>8.2%}")
print(f"  difference                   {ann_ret(r['bot'])-ann_ret(spy_scaled):>+8.2%}")
print(f"  -> at equal risk the bot {'BEATS' if ann_ret(r['bot'])>ann_ret(spy_scaled) else 'LOSES TO'} "
      f"a passive SPY sleeve")

print()
print("=" * 72)
print("4. YEAR BY YEAR: is 2022 skill, or is it just being 60% invested?")
print("=" * 72)
print(f"{'year':6} {'bot':>8} {'SPY':>8} {'beta*SPY':>10} {'residual':>10}")
for yr, g in r.groupby(r.index.year):
    b, s = ann_ret(g['bot']), ann_ret(g['spy'])
    print(f"{yr:6} {b:>7.1%} {s:>7.1%} {beta*s:>9.1%} {b-beta*s:>9.1%}")
print("  'beta*SPY' = what you get from a dumb SPY position at the bot's")
print("  exposure. 'residual' = what the model added on top.")

print()
print("=" * 72)
print("5. COULD THE HOLDOUT EVEN ANSWER THE QUESTION?")
print("=" * 72)
S1, S2 = sharpe(r['bot']), sharpe(r['spy'])
rho = r['bot'].corr(r['spy'])
def se_sharpe_diff(S1, S2, rho, T):
    # Jobson-Korkie with Memmel correction, T in years (annualised Sharpes)
    v = (2 - 2*rho + 0.5*(S1**2 + S2**2 - 2*rho**2*S1*S2)) / T
    return np.sqrt(v)

print(f"  bot Sharpe {S1:.2f}, SPY Sharpe {S2:.2f}, correlation {rho:.2f}")
print()
print(f"  {'target dSharpe':>15} {'years needed for p<0.05':>26}")
for d in [0.10, 0.20, 0.30, 0.50, 0.75]:
    # solve 1.96 * se(T) = d
    T_need = (2 - 2*rho + 0.5*(S1**2 + (S1+d)**2 - 2*rho**2*S1*(S1+d))) * (1.96/d)**2
    print(f"  {d:>15.2f} {T_need:>24.1f}")
print()
for T in [1, 3, 5]:
    mde = None
    for d in np.arange(0.01, 3, 0.005):
        if 1.96 * se_sharpe_diff(S1, S1+d, rho, T) < d:
            mde = d; break
    print(f"  with {T} year(s) of holdout you can only detect dSharpe >= {mde:.2f}")

print()
print("=" * 72)
print("6. NOISE FLOOR ON THE 8-CONFIG BAKEOFF")
print("=" * 72)
bk = pd.read_csv('data/bakeoff.csv')
print(f"  out-of-sample accuracy range: "
      f"{bk.out_acc.min():.4f} to {bk.out_acc.max():.4f}  (coin flip = 0.5000)")
print(f"  spread across all 8 configs:  {bk.out_acc.max()-bk.out_acc.min():.4f}")
print(f"  forward IC t-stats:           "
      f"{', '.join(f'{t:+.2f}' for t in bk.t_fwd)}")
print(f"  |t| > 2 count: {(bk.t_fwd.abs()>2).sum()} of 8   "
      f"(chance alone predicts 0.4)")
