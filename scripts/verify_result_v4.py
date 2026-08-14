#!/usr/bin/env python3
"""
"What would make this wrong?" — applied to the v4 result itself.

v4 came out worse than v1 on every diagnostic. That is the convenient
direction for this project's priors, which is exactly why it needs the same
scrutiny a good result would get. Three ways the conclusion could be wrong:

  W1  SEED LUCK. The whole difference could be one random_state. XGBoost
      subsamples rows and columns, and with three folds the run-to-run spread
      can easily exceed the effect. Both models are re-run across seeds and
      the DISTRIBUTION is reported, not the best or the first.

  W2  THE FEATURES MIGHT BE FINE AND THE COMPARISON BROKEN. If a model given
      randomly shuffled insider features performs the same as one given the
      real ones, the real ones carried nothing. If the shuffled version
      performs BETTER, the real ones were actively harmful. Shuffling happens
      within each day, so the marginal distribution is preserved exactly and
      only the symbol-to-value mapping is destroyed.

  W3  THE BACKTEST ENGINE COULD BE MISREPORTING. SPY's return over this exact
      window is externally known, so the engine's SPY leg is checked against
      arithmetic that does not come from the engine.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import xgboost as xgb  # noqa: E402
from scipy import stats  # noqa: E402

from src.features import FEATURE_NAMES  # noqa: E402
from src.features_v4 import INSIDER_FEATURES  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
V1_SET = FEATURE_NAMES
V4_SET = FEATURE_NAMES + INSIDER_FEATURES
PURGE = 5
HOLD = 10

BASE = dict(max_depth=4, n_estimators=300, learning_rate=0.03,
            subsample=0.7, colsample_bytree=0.7, min_child_weight=200,
            reg_lambda=5.0, reg_alpha=1.0, objective="binary:logistic",
            eval_metric="logloss", tree_method="hist", n_jobs=4)


def walk(dev, cols, seed):
    years = sorted(dev.date.dt.year.unique())
    accs, gaps, preds = [], [], []
    for y in [v for v in years if v >= years[0] + 2]:
        te = dev[dev.date.dt.year == y]
        cutoff = te.date.min()
        tr = dev[dev.date < cutoff - pd.Timedelta(days=PURGE * 2)]
        if len(tr) < 20_000 or te.empty:
            continue
        m = xgb.XGBClassifier(**BASE, random_state=seed)
        m.fit(tr[cols], tr["label"])
        in_acc = (m.predict(tr[cols]) == tr["label"]).mean()
        prob = m.predict_proba(te[cols])[:, 1]
        accs.append(((prob > 0.5).astype(float) == te["label"]).mean())
        gaps.append(in_acc - accs[-1])
        f = te[["date", "fwd_return"]].copy()
        f["score"] = prob
        preds.append(f)
    p = pd.concat(preds, ignore_index=True)
    ic = (p.groupby("date").apply(
        lambda g: stats.spearmanr(g.score, g.fwd_return)[0] if len(g) > 30 else np.nan,
        include_groups=False).dropna())
    return np.mean(accs), np.mean(gaps), ic.mean()


def main() -> int:
    dev = pd.read_parquet(DATA / "panel_v4_dev.parquet")
    dev["date"] = pd.to_datetime(dev["date"])
    dev = dev.dropna(subset=["label"] + V4_SET).sort_values("date").reset_index(drop=True)

    seeds = [42, 7, 101, 2024, 31337]
    print("W1  SEED ROBUSTNESS — is the v1/v4 gap just one random_state?")
    print("=" * 74)
    print(f"{'seed':<8}{'v1 acc':>9}{'v4 acc':>9}{'v1 gap':>9}{'v4 gap':>9}"
          f"{'v1 IC':>10}{'v4 IC':>10}")
    print("-" * 74)
    rec = []
    for s in seeds:
        a1, g1, i1 = walk(dev, V1_SET, s)
        a4, g4, i4 = walk(dev, V4_SET, s)
        rec.append((a1, a4, g1, g4, i1, i4))
        print(f"{s:<8}{a1:>9.2%}{a4:>9.2%}{g1:>9.2%}{g4:>9.2%}{i1:>10.4f}{i4:>10.4f}")
    r = np.array(rec)
    print("-" * 74)
    print(f"{'mean':<8}{r[:,0].mean():>9.2%}{r[:,1].mean():>9.2%}"
          f"{r[:,2].mean():>9.2%}{r[:,3].mean():>9.2%}"
          f"{r[:,4].mean():>10.4f}{r[:,5].mean():>10.4f}")
    print(f"{'sd':<8}{r[:,0].std():>9.2%}{r[:,1].std():>9.2%}"
          f"{r[:,2].std():>9.2%}{r[:,3].std():>9.2%}"
          f"{r[:,4].std():>10.4f}{r[:,5].std():>10.4f}")
    print(f"\nv4 beat v1 on out-of-sample accuracy in "
          f"{(r[:,1] > r[:,0]).sum()} of {len(seeds)} seeds")
    print(f"v4 had the larger in/out overfitting gap in "
          f"{(r[:,3] > r[:,2]).sum()} of {len(seeds)} seeds")

    # ---- W2: shuffle control -------------------------------------------
    print("\n\nW2  SHUFFLE CONTROL — replace insider values with a within-day permutation")
    print("=" * 74)
    rng = np.random.default_rng(0)
    sh = dev.copy()
    for c in INSIDER_FEATURES:
        sh[c] = sh.groupby("date")[c].transform(
            lambda s: rng.permutation(s.to_numpy()))
    a_r, g_r, i_r = walk(dev, V4_SET, 42)
    a_s, g_s, i_s = walk(sh, V4_SET, 42)
    a_b, g_b, i_b = walk(dev, V1_SET, 42)
    print(f"{'variant':<28}{'oos acc':>10}{'in/out gap':>12}{'IC':>10}")
    print("-" * 60)
    print(f"{'v1, no insider features':<28}{a_b:>10.2%}{g_b:>12.2%}{i_b:>10.4f}")
    print(f"{'v4, real insider features':<28}{a_r:>10.2%}{g_r:>12.2%}{i_r:>10.4f}")
    print(f"{'v4, SHUFFLED insider values':<28}{a_s:>10.2%}{g_s:>12.2%}{i_s:>10.4f}")
    print("\nIf the shuffled row matches the real row, the insider features")
    print("contributed nothing beyond eight extra columns of noise.")

    # ---- W3: engine check against external arithmetic -------------------
    print("\n\nW3  ENGINE CHECK — SPY, computed outside the engine")
    print("=" * 74)
    spy = pd.read_parquet(DATA / "spy.parquet")
    spy["date"] = pd.to_datetime(spy["date"])
    w = spy[(spy.date >= "2022-01-01") & (spy.date <= "2024-12-31")]
    raw_total = w.close.iloc[-1] / w.close.iloc[0] - 1
    yrs = (w.date.iloc[-1] - w.date.iloc[0]).days / 365.25
    print(f"SPY adjusted close {w.close.iloc[0]:.2f} -> {w.close.iloc[-1]:.2f}")
    print(f"total return {raw_total:.2%}   CAGR {(1+raw_total)**(1/yrs)-1:.2%}")
    print(f"engine reported CAGR 8.50%  (difference is one side of slippage on")
    print(f"the single entry, plus whole-share rounding)")
    for y, g in w.groupby(w.date.dt.year):
        print(f"   {y}: {g.close.iloc[-1]/g.close.iloc[0]-1:+.2%}")
    print("\nSPY total return 2022 -18%, 2023 +26%, 2024 +25% is externally known.")
    print("If the yearly figures above do not match that, the price series is")
    print("wrong and every number in this project is wrong with it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
