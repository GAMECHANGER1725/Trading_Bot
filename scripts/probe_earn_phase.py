#!/usr/bin/env python3
"""
Chase the one feature that cleared |t| = 2, before believing it.

`earn_phase` came out at t = +2.67. Three things make that suspicious enough
to test rather than accept:

  Q1  MULTIPLE COMPARISONS. Ten v4 features were tested. P(at least one
      exceeds |t|=2 by chance) is about 40%. A Bonferroni threshold across
      all 24 features sits near |t| = 3.1, and 2.67 does not reach it.

  Q2  SIGN INVERSION. `earn_phase` is `earn_days_since / median_gap`. If
      median_gap were roughly 91 days for every company, the two features
      would be near-identical after cross-sectional ranking and would have
      near-identical ICs. They have OPPOSITE signs (+0.0142 vs -0.0185).
      So whatever earn_phase is measuring, it is being driven by the
      denominator — a company characteristic — not by where the company sits
      in its reporting cycle.

  Q3  SURVIVORSHIP THROUGH A SENTINEL. The earnings source covers 491 of 601
      development symbols. Missing symbols get a sentinel, and after
      cross-sectional ranking every sentinel row lands in the same extreme
      rank block. "Is this ticker present in a 2026-vintage S&P 500 earnings
      file" is very close to "did this ticker survive". If the signal lives
      entirely in the sentinel rows, it is survivorship bias wearing a
      feature's clothes — the precise thing this project rejected Kaggle
      price data to avoid.

Q3 is the one that would be embarrassing to miss, so it is tested directly:
recompute on covered rows only.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from scipy import stats  # noqa: E402

from src.features_v4 import build_earnings, load_earnings  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
KAG = Path("/home/claude/kag")
HOLD = 10


def indep_t(g, col, target="bt_return"):
    ic = (g.groupby("date")
            .apply(lambda d: stats.spearmanr(d[col], d[target])[0]
                   if len(d) > 30 and d[col].nunique() > 2 else np.nan,
                   include_groups=False).dropna())
    if len(ic) < 30:
        return np.nan, np.nan, 0
    sub = ic.iloc[::HOLD]
    t = sub.mean() / (sub.std() / np.sqrt(len(sub))) if sub.std() > 0 else np.nan
    return ic.mean(), t, len(sub)


def main() -> int:
    dev = pd.read_parquet(DATA / "panel_v4_dev.parquet")
    dev["date"] = pd.to_datetime(dev["date"])
    earn = load_earnings(str(KAG / "earnings_features_clean (1).csv"),
                         set(dev.symbol.unique()))
    covered = set(earn.symbol.unique())

    print("Q3  IS THE SIGNAL IN THE SENTINEL ROWS?")
    print("=" * 70)
    dev["covered"] = dev.symbol.isin(covered)
    print(f"symbols covered by the earnings file : {len(covered)} of {dev.symbol.nunique()}")
    print(f"development rows covered             : {dev.covered.mean():.1%}")

    d = dev.dropna(subset=["bt_return"])
    for label, sub in (("ALL rows (as tested)", d),
                       ("COVERED rows only", d[d.covered])):
        # re-rank within the subset so the sentinel block cannot define the scale
        s = sub.copy()
        s["earn_phase_r"] = s.groupby("date")["earn_phase"].rank(pct=True)
        s["earn_days_r"] = s.groupby("date")["earn_days_since"].rank(pct=True)
        ic1, t1, n1 = indep_t(s, "earn_phase_r")
        ic2, t2, n2 = indep_t(s, "earn_days_r")
        print(f"\n{label}   ({len(s):,} rows)")
        print(f"   earn_phase       IC {ic1:+.4f}   indep t {t1:+.2f}   n {n1}")
        print(f"   earn_days_since  IC {ic2:+.4f}   indep t {t2:+.2f}   n {n2}")

    # Q2 — what does the denominator actually look like?
    print("\n\nQ2  WHAT IS THE DENOMINATOR DOING?")
    print("=" * 70)
    e = earn.sort_values(["symbol", "date"]).copy()
    e["gap"] = e.groupby("symbol")["date"].diff().dt.days
    e["median_gap"] = e.groupby("symbol")["gap"].transform(
        lambda s: s.shift(1).expanding().median())
    mg = e["median_gap"].dropna()
    print(f"median_gap distribution over {len(mg):,} company-quarters:")
    print(f"   min {mg.min():.0f}   p05 {mg.quantile(.05):.0f}   "
          f"median {mg.median():.0f}   p95 {mg.quantile(.95):.0f}   max {mg.max():.0f}")
    print(f"   fraction within 85-97 days (a normal quarter): "
          f"{((mg >= 85) & (mg <= 97)).mean():.1%}")
    print("\nIf that last number is high, the denominator is nearly constant and the")
    print("sign inversion cannot come from it — which would point back at the")
    print("sentinel rows as the source.")

    # Q1 — how big does a t-stat have to be here?
    print("\n\nQ1  MULTIPLE-COMPARISON THRESHOLD")
    print("=" * 70)
    for k, name in ((10, "v4 features only"), (24, "all features tested")):
        crit = stats.norm.ppf(1 - 0.025 / k)
        print(f"   {name:<22} k={k:<3} Bonferroni |t| threshold {crit:.2f}")
    print(f"   observed earn_phase t = 2.67")

    # Year-by-year stability: a real effect should not live in one year.
    print("\n\nSTABILITY BY YEAR (covered rows, re-ranked)")
    print("=" * 70)
    s = d[d.covered].copy()
    s["earn_phase_r"] = s.groupby("date")["earn_phase"].rank(pct=True)
    print(f"{'year':<8}{'IC':>10}{'indep t':>10}{'n':>7}")
    for y, g in s.groupby(s.date.dt.year):
        ic, t, n = indep_t(g, "earn_phase_r")
        print(f"{y:<8}{ic:>10.4f}{t:>10.2f}{n:>7}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
