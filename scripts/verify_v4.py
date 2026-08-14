#!/usr/bin/env python3
"""
Verification BEFORE any model is fitted.

Rule 3 of this project: no result stands unvalidated, and the question
"what would make this wrong?" is asked before anything is built on top.
There are exactly three ways the v4 features could be wrong, and each gets
its own test here.

  T1  SPARSITY. If almost every row has zero insider filings in the window,
      the cross-sectional rank collapses into one enormous tie block and the
      feature is informative on a handful of rows while looking like a normal
      feature on all of them. Measured on RAW values, before ranking, because
      ranking hides exactly this.

  T2  LOOKAHEAD. The one failure that would make the results look good and be
      worthless. Features are rebuilt from a panel truncated at a cutoff date
      and compared to the same features built from the full panel. If a single
      value differs on a date at or before the cutoff, future information is
      leaking backwards. This is the same test D22 used on the price features,
      which passed at max difference 0.00e+00.

  T3  SIGNAL. Each feature individually against the bracket outcome, with the
      independent-observation correction from D24. Overlapping daily windows
      inflated a null result into an apparent 13x edge once already; every
      t-statistic here is computed on non-overlapping subsamples.

If T3 shows nothing, the honest move is to say so rather than to fit a model
and hope the interactions rescue it. D28 already ran that experiment: adding
eleven noise features moved development IC from +0.0111 to -0.0206.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from scipy import stats  # noqa: E402

from src.features import FEATURE_NAMES  # noqa: E402
from src.features_v4 import (  # noqa: E402
    INSIDER_FEATURES, V4_FEATURES, build_earnings, build_insider,
    load_earnings, load_form4,
)

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
KAG = Path("/home/claude/kag")
HOLD = 10  # bracket max hold; subsample stride for independence


def t1_sparsity(dev: pd.DataFrame, f4: pd.DataFrame) -> None:
    print("=" * 78)
    print("T1  SPARSITY — how often is there actually anything to see?")
    print("=" * 78)

    panel = dev[["symbol", "date"]].copy()
    raw = build_insider(panel, f4)
    print(f"{'feature':<22}{'% rows == 0':>14}{'mean|x|':>10}{'p95':>8}{'max':>8}")
    print("-" * 62)
    for c in INSIDER_FEATURES:
        v = raw[c]
        if c == "ins_days_since_buy":
            frac = (v >= 999).mean()
            print(f"{c:<22}{frac:>13.1%}{v[v < 999].mean():>10.1f}"
                  f"{'(never)':>8}{'':>8}")
        else:
            print(f"{c:<22}{(v == 0).mean():>13.1%}{v.abs().mean():>10.2f}"
                  f"{v.abs().quantile(.95):>8.0f}{v.abs().max():>8.0f}")

    daily = raw.groupby("date")["ins_net_60"].apply(lambda s: (s != 0).mean())
    print(f"\nper-day fraction of the cross-section with a non-zero 60d net: "
          f"{daily.mean():.1%} (min {daily.min():.1%}, max {daily.max():.1%})")
    print("Read this as: the insider features carry information on that share of\n"
          "names on a typical day. The rest are in one tied rank block.")


def t2_lookahead(dev: pd.DataFrame, f4: pd.DataFrame, earn: pd.DataFrame) -> None:
    print("\n" + "=" * 78)
    print("T2  LOOKAHEAD — rebuild from truncated data, demand identical values")
    print("=" * 78)

    cutoff = pd.Timestamp("2022-06-30")
    panel = dev[["symbol", "date"]].copy()

    full_i = build_insider(panel, f4)
    full_e = build_earnings(panel, earn)

    panel_t = panel[panel.date <= cutoff].copy()
    f4_t = f4[f4.filing_date <= cutoff]
    earn_t = earn[earn.date <= cutoff]
    trunc_i = build_insider(panel_t, f4_t)
    trunc_e = build_earnings(panel_t, earn_t)

    ok = True
    for name, full, trunc, cols in (
        ("insider", full_i, trunc_i, INSIDER_FEATURES),
        ("earnings", full_e, trunc_e, ["earn_days_since", "earn_phase"]),
    ):
        a = full[full.date <= cutoff].sort_values(["symbol", "date"]).reset_index(drop=True)
        b = trunc.sort_values(["symbol", "date"]).reset_index(drop=True)
        assert len(a) == len(b), f"{name}: row count differs {len(a)} vs {len(b)}"
        for c in cols:
            diff = (a[c].to_numpy() - b[c].to_numpy())
            worst = np.nanmax(np.abs(diff))
            flag = "OK" if worst == 0 else "*** LEAK ***"
            ok &= worst == 0
            print(f"  {name:<9}{c:<22}max abs diff {worst:>12.2e}   {flag}")

    print(f"\nverdict: {'no lookahead detected' if ok else 'LOOKAHEAD PRESENT — STOP'}")
    if not ok:
        raise SystemExit(1)


def _indep_t(g: pd.DataFrame, col: str, target: str = "bt_return"):
    """Daily cross-sectional Spearman IC, then a t-test on non-overlapping days."""
    ic = (g.groupby("date")
            .apply(lambda d: stats.spearmanr(d[col], d[target])[0]
                   if len(d) > 30 and d[col].nunique() > 2 else np.nan,
                   include_groups=False)
            .dropna())
    if len(ic) < 30:
        return np.nan, np.nan, 0
    sub = ic.iloc[::HOLD]
    t = sub.mean() / (sub.std() / np.sqrt(len(sub))) if sub.std() > 0 else np.nan
    return ic.mean(), t, len(sub)


def t3_signal(dev: pd.DataFrame) -> pd.DataFrame:
    print("\n" + "=" * 78)
    print("T3  SIGNAL — each feature alone, independent observations only")
    print("=" * 78)
    d = dev.dropna(subset=["bt_return"]).copy()

    rows = []
    for c in FEATURE_NAMES + V4_FEATURES:
        ic, t, n = _indep_t(d, c)
        rows.append({"feature": c, "IC": ic, "indep_t": t, "n_indep": n,
                     "group": "price" if c in FEATURE_NAMES else "v4"})
    res = pd.DataFrame(rows).sort_values("indep_t", key=abs, ascending=False)

    print(f"{'feature':<22}{'group':<8}{'IC':>10}{'indep t':>10}{'n':>7}")
    print("-" * 57)
    for _, r in res.iterrows():
        mark = "  <-- |t|>2" if abs(r.indep_t) > 2 else ""
        print(f"{r.feature:<22}{r.group:<8}{r.IC:>10.4f}{r.indep_t:>10.2f}"
              f"{r.n_indep:>7.0f}{mark}")

    v4 = res[res.group == "v4"]
    n_sig = (v4.indep_t.abs() > 2).sum()
    print("-" * 57)
    print(f"\nv4 features above |t|=2 : {n_sig} of {len(v4)}")
    print(f"expected by chance      : {0.05 * len(v4):.1f}")
    print(f"price features above |t|=2 : {(res[res.group=='price'].indep_t.abs()>2).sum()}"
          f" of {len(FEATURE_NAMES)}  (D26 found 1 of 14 on its own window)")
    return res


def main() -> int:
    dev = pd.read_parquet(DATA / "panel_v4_dev.parquet")
    dev["date"] = pd.to_datetime(dev["date"])
    universe = set(dev.symbol.unique())
    f4 = load_form4(str(KAG / "filings.csv"), universe)
    earn = load_earnings(str(KAG / "earnings_features_clean (1).csv"), universe)

    print(f"development panel: {len(dev):,} rows, {dev.symbol.nunique()} symbols, "
          f"{dev.date.min().date()} -> {dev.date.max().date()}\n")

    t1_sparsity(dev, f4)
    t2_lookahead(dev, f4, earn)
    res = t3_signal(dev)
    res.to_csv(DATA / "feature_tests_v4.csv", index=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
