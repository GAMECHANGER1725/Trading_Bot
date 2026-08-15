#!/usr/bin/env python3
"""
Phase 0, item 1 (D37): does extending the window actually help, and where?

Papaya asked whether v4 is short-changed on training data. It is not — v1 and
v4 share an identical expanding walk-forward on 2020-2024. But the question
exposes a real asymmetry:

  * Form 4 filings run 2020-01-02 to 2025-07-14. v4 CANNOT go earlier. Ever.
  * Price bars run 2016-01-04 to 2026-02-27. v1 can, and never has.

build_panel_all.py truncates everything to 2020 so no variant is scored on
rows another variant could not see. Correct for fairness — but it means the
price-only model has been evaluated on 3 test folds when 7 were available.

This script rebuilds price-only features from 2016 and runs the SAME cell
(CLS, price, seed 42, identical hyperparameters) over both windows.

The claim being tested is NOT "more training data finds more signal". Forward
IC is 0.017 at t = 0.067; there is no signal to estimate better. The claim is
that more TEST folds shrink the error bar, which is the thing actually
blocking every decision in this project.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import numpy as np, pandas as pd, xgboost as xgb
from scipy import stats

from src.features import FEATURE_NAMES, build
from src.labels import triple_barrier

ROOT = Path(__file__).resolve().parent.parent.parent
DATA = ROOT / "data"
PURGE, HOLD = 10, 10
COMMON = dict(max_depth=4, n_estimators=300, learning_rate=0.03,
              subsample=0.7, colsample_bytree=0.7, min_child_weight=200,
              reg_lambda=5.0, reg_alpha=1.0, tree_method="hist",
              n_jobs=4, random_state=42)
HOLDOUT_START = "2025-01-01"


def daily_ic(p, target):
    return (p.groupby("date")
             .apply(lambda g: stats.spearmanr(g.score, g[target])[0]
                    if len(g) > 30 else np.nan, include_groups=False).dropna())


def indep_t(ic):
    sub = ic.iloc[::HOLD]
    return sub.mean()/(sub.std()/np.sqrt(len(sub))) if sub.std() > 0 else np.nan


def walk_forward(dev, label_col="label"):
    years = sorted(dev.date.dt.year.unique())
    test_years = [y for y in years if y >= years[0] + 2]
    preds, out_accs = [], []
    for y in test_years:
        te = dev[dev.date.dt.year == y]
        cut = te.date.min()
        tr = dev[dev.date < cut - pd.Timedelta(days=PURGE*2)]
        if len(tr) < 20_000 or te.empty:
            continue
        m = xgb.XGBClassifier(**COMMON, objective="binary:logistic",
                              eval_metric="logloss")
        m.fit(tr[FEATURE_NAMES], tr[label_col])
        s_te = m.predict_proba(te[FEATURE_NAMES])[:, 1]
        f = te[["date", "label"]].copy(); f["s"] = s_te
        r = f.groupby("date")["s"].rank(pct=True)
        out_accs.append(((r > 0.5).astype(float) == f["label"]).mean())
        o = te[["symbol", "date", "fwd_return", "bt_return", "label"]].copy()
        o["score"] = s_te
        preds.append(o)
        print(f"    fold {y}: train {len(tr):>8,} rows -> test {len(te):>7,} "
              f"rows, acc {out_accs[-1]:.4f}")
    return pd.concat(preds, ignore_index=True), np.mean(out_accs), len(out_accs)


def main():
    bars = pd.read_parquet(DATA/"bars"/"daily.parquet")
    bars["date"] = pd.to_datetime(bars["date"])
    print(f"bars  {len(bars):,} rows  {bars.date.min().date()} -> "
          f"{bars.date.max().date()}\n")

    print("building price features + labels over the FULL bar history ...")
    f1 = build(bars)
    bt = triple_barrier(bars, stop_pct=0.08, target_pct=0.12, max_days=HOLD)
    key = ["symbol", "date"]
    panel = f1.merge(bt[key + ["bt_return"]], on=key, how="inner").dropna(
        subset=FEATURE_NAMES + ["fwd_return", "label", "bt_return"])
    panel["date"] = pd.to_datetime(panel["date"])
    panel = panel[panel.date < HOLDOUT_START].sort_values(key).reset_index(drop=True)
    print(f"panel {len(panel):,} rows  {panel.date.min().date()} -> "
          f"{panel.date.max().date()}  ({panel.symbol.nunique()} symbols)\n")

    out = {}
    for label, start in [("SHORT  (as shipped, 2020 start)", "2020-01-01"),
                         ("LONG   (2016 start)",             "2016-01-01")]:
        d = panel[panel.date >= start].copy()
        print(f"{label}   {d.date.min().date()} -> {d.date.max().date()}, "
              f"{len(d):,} rows")
        p, acc, nf = walk_forward(d)
        ic = daily_ic(p, "fwd_return")
        print(f"    -> {nf} folds, mean out-of-sample accuracy {acc:.4f}, "
              f"forward IC {ic.mean():+.4f} (t = {indep_t(ic):+.2f})\n")
        out[label] = dict(preds=p, acc=acc, folds=nf, ic=ic.mean(),
                          t=indep_t(ic))
    for k, v in out.items():
        v["preds"].to_parquet(
            DATA/f"preds_long_{'short' if 'SHORT' in k else 'long'}.parquet",
            compression="zstd", index=False)
    print("predictions written. Backtest them with "
          "scripts/analysis/long_window_bt.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
