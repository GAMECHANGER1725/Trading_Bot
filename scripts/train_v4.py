#!/usr/bin/env python3
"""
v4: v1 architecture, v1 hyperparameters, augmented feature set.

WHAT CHANGED FROM v1, AND ONLY THIS
  Features: 14 price -> 14 price + 8 SEC Form 4 insider.
  Everything else is v1 verbatim: binary "beats the day's median 5-day forward
  return" label, the same XGBClassifier parameters, expanding walk-forward,
  purge gap. Hyperparameters are not touched. Tuning is the cheapest way to
  manufacture the appearance of an improvement, and with three folds it would
  be trivial to do by accident.

WHY THE EARNINGS FEATURES ARE NOT HERE
  They were built, tested, and dropped for cause before any model was fitted.
  `earn_phase` scored t = +2.67 across all rows — the only feature in the
  whole set above |t| = 2 — and t = -0.16 once restricted to symbols the
  earnings source actually covers. The earnings file covers 472 of 601
  development symbols, and the 129 it misses are disproportionately names that
  were acquired or delisted, which a 2026-vintage S&P 500 earnings dataset has
  no reason to contain. The feature was ranking "did this ticker survive" and
  reporting it as "position in the earnings cycle". Feeding that to a tree
  model hands it a survivorship shortcut, so both earnings features are out.
  See probe_earn_phase.py for the full test.

WINDOW
  2020-01-01 to 2024-12-31, because the Form 4 source starts 2020-01-02.
  The v1 baseline is re-run on this identical window so the comparison is
  like-for-like. It is not the v1 number from D22, which covered 2020-2026
  with 7 folds; that number is not comparable and is not used as the baseline
  here.

  Three folds. D23's Sharpe standard error of +/-0.46 was measured over 6.1
  years; over three test years it is larger still. Any difference this
  reports is very unlikely to be distinguishable from noise, and that is
  stated before the numbers rather than after them.

THE HOLDOUT IS NOT READ BY THIS SCRIPT.
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

# Identical to v1 (scripts/train_model.py). Not tuned.
PARAMS = dict(
    max_depth=4, n_estimators=300, learning_rate=0.03,
    subsample=0.7, colsample_bytree=0.7, min_child_weight=200,
    reg_lambda=5.0, reg_alpha=1.0,
    objective="binary:logistic", eval_metric="logloss",
    tree_method="hist", n_jobs=4, random_state=42,
)

PURGE_DAYS = 5
V1_SET = FEATURE_NAMES
V4_SET = FEATURE_NAMES + INSIDER_FEATURES
HOLD = 10


def indep_t(p: pd.DataFrame) -> tuple[float, float, int]:
    ic = (p.groupby("date")
           .apply(lambda g: stats.spearmanr(g.score, g.fwd_return)[0]
                  if len(g) > 30 else np.nan, include_groups=False).dropna())
    if len(ic) < 30:
        return np.nan, np.nan, 0
    sub = ic.iloc[::HOLD]
    t = sub.mean() / (sub.std() / np.sqrt(len(sub))) if sub.std() > 0 else np.nan
    return ic.mean(), t, len(sub)


def run(dev: pd.DataFrame, cols: list[str], name: str) -> pd.DataFrame:
    years = sorted(dev.date.dt.year.unique())
    test_years = [y for y in years if y >= years[0] + 2]

    print(f"\n{name}  ({len(cols)} features)")
    print(f"{'fold':<7}{'train':>10}{'test':>9}{'in-samp':>10}{'out-samp':>10}"
          f"{'gap':>8}{'IC':>9}{'indep t':>9}")
    print("-" * 72)

    preds, gaps, accs = [], [], []
    for y in test_years:
        te_mask = dev.date.dt.year == y
        cutoff = dev.loc[te_mask, "date"].min()
        tr = dev[dev.date < cutoff - pd.Timedelta(days=PURGE_DAYS * 2)]
        te = dev[te_mask]
        if len(tr) < 20_000 or te.empty:
            continue

        m = xgb.XGBClassifier(**PARAMS)
        m.fit(tr[cols], tr["label"])

        in_acc = (m.predict(tr[cols]) == tr["label"]).mean()
        prob = m.predict_proba(te[cols])[:, 1]
        out_acc = ((prob > 0.5).astype(float) == te["label"]).mean()

        fold = te[["symbol", "date", "fwd_return", "label", "bt_return"]].copy()
        fold["score"] = prob
        preds.append(fold)
        ic, t, _ = indep_t(fold)
        gaps.append(in_acc - out_acc)
        accs.append(out_acc)
        print(f"{y:<7}{len(tr):>10,}{len(te):>9,}{in_acc:>9.1%}{out_acc:>10.1%}"
              f"{in_acc-out_acc:>8.1%}{ic:>9.4f}{t:>9.2f}")

    allp = pd.concat(preds, ignore_index=True)
    ic, t, n = indep_t(allp)
    print("-" * 72)
    print(f"mean out-of-sample accuracy : {np.mean(accs):.2%}")
    print(f"mean in/out gap             : {np.mean(gaps):.2%}")
    print(f"pooled IC                   : {ic:+.4f}   indep t {t:+.2f}  (n={n})")

    # final model on all development rows, for the backtest
    final = xgb.XGBClassifier(**PARAMS)
    final.fit(dev[cols], dev["label"])
    imp = pd.Series(final.feature_importances_, index=cols).sort_values(ascending=False)
    print(f"top features: {', '.join(f'{k} ({v:.3f})' for k, v in imp.head(6).items())}")
    if name.startswith("v4"):
        share = imp[INSIDER_FEATURES].sum()
        print(f"insider features hold {share:.1%} of total importance "
              f"({len(INSIDER_FEATURES)}/{len(cols)} = "
              f"{len(INSIDER_FEATURES)/len(cols):.1%} if they were interchangeable "
              f"with the price features)")
    return allp


def main() -> int:
    dev = pd.read_parquet(DATA / "panel_v4_dev.parquet")
    dev["date"] = pd.to_datetime(dev["date"])
    dev = dev.dropna(subset=["label"] + V4_SET).sort_values("date").reset_index(drop=True)
    print(f"development rows : {len(dev):,}  "
          f"{dev.date.min().date()} -> {dev.date.max().date()}")
    print(f"symbols          : {dev.symbol.nunique()}")

    p1 = run(dev, V1_SET, "v1 baseline (price only, same window)")
    p4 = run(dev, V4_SET, "v4 (price + insider)")

    p1.to_parquet(DATA / "preds_v1_window.parquet", compression="zstd", index=False)
    p4.to_parquet(DATA / "preds_v4.parquet", compression="zstd", index=False)

    # paired comparison on the same days
    print("\n" + "=" * 72)
    print("PAIRED COMPARISON — same rows, same days")
    print("=" * 72)
    j = p1[["symbol", "date", "score"]].merge(
        p4[["symbol", "date", "score", "fwd_return"]],
        on=["symbol", "date"], suffixes=("_v1", "_v4"))
    print(f"overlapping rows: {len(j):,}")
    print(f"score correlation (Spearman): "
          f"{stats.spearmanr(j.score_v1, j.score_v4)[0]:.4f}")

    daily = j.groupby("date").apply(
        lambda g: pd.Series({
            "ic_v1": stats.spearmanr(g.score_v1, g.fwd_return)[0],
            "ic_v4": stats.spearmanr(g.score_v4, g.fwd_return)[0]}),
        include_groups=False).dropna()
    d = (daily.ic_v4 - daily.ic_v1).iloc[::HOLD]
    tt = d.mean() / (d.std() / np.sqrt(len(d)))
    print(f"mean daily IC   v1 {daily.ic_v1.mean():+.4f}   v4 {daily.ic_v4.mean():+.4f}")
    print(f"difference (v4 - v1) : {d.mean():+.4f}   indep t {tt:+.2f}   n {len(d)}")
    print("\nA |t| below 2 here means the two models are not distinguishable,")
    print("whichever direction the point estimate happens to point.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
