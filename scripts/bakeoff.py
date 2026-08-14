#!/usr/bin/env python3
"""
The bake-off: every version, same rows, same folds, same evaluation targets.

DESIGN
  Two axes, crossed, so that "which question" and "which inputs" can be told
  apart. The four named versions are cells in this grid, not points on a line.

    ARCHITECTURE
      CLS  binary "beats the day's median 5-day forward return",
           XGBClassifier, trained on every row.            <- v1, v4
      REG  cross-sectionally demeaned triple-barrier return,
           XGBRegressor, trained on one row per symbol per
           10-day holding period (non-overlapping, C2).    <- v2, v3

    FEATURES
      price          14                                    <- v1, v2
      price+v3       25   sector / regime / breadth / beta / dividend
      price+insider  22   SEC Form 4                       <- v4
      price+v3+ins   33

  So:  v1 = CLS x price      v2 = REG x price
       v3 = REG x price+v3   v4 = CLS x price+insider
  The other four cells were never run before and are the controls that make
  the named ones interpretable.

WHAT IS HELD IDENTICAL
  Rows        the same 627,844 development rows for every cell
  Folds       test 2022, 2023, 2024; expanding train; same 10-day purge
  Scoring     EVERY model is scored the same three ways, regardless of what
              it was trained to predict, because every model's output is used
              the same way: as a daily ranking.
                IC_bt    rank correlation of score vs bt_return
                IC_fwd   rank correlation of score vs fwd_return
                acc      accuracy on the same binary label
  Sizing      equal weight, 10 positions, for every cell. v2's C4
              volatility-scaled sizing is a separate lever and is deliberately
              NOT applied here; mixing it in would make the sizing rule a
              third uncontrolled axis.
  Hyperparams identical across all eight, unchanged from v1. Never tuned.

MULTIPLE COMPARISONS, STATED BEFORE THE RESULTS
  Eight cells. The best of eight draws from the null is roughly 1.4 standard
  errors above zero by construction. Any cell that looks good here has to be
  read against that, and none of these is promoted on the strength of being
  the maximum of a grid. The grid exists to explain the four named versions,
  not to select a fifth.

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
from src.features_v3 import V3_FEATURES  # noqa: E402
from src.features_v4 import INSIDER_FEATURES  # noqa: E402
from src.labels import non_overlapping_mask  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
PURGE, HOLD = 5, 10

COMMON = dict(max_depth=4, n_estimators=300, learning_rate=0.03,
              subsample=0.7, colsample_bytree=0.7, min_child_weight=200,
              reg_lambda=5.0, reg_alpha=1.0, tree_method="hist",
              n_jobs=4, random_state=42)

FEATURE_SETS = {
    "price": FEATURE_NAMES,
    "price+v3": FEATURE_NAMES + V3_FEATURES,
    "price+insider": FEATURE_NAMES + INSIDER_FEATURES,
    "price+v3+insider": FEATURE_NAMES + V3_FEATURES + INSIDER_FEATURES,
}

NAMED = {
    ("CLS", "price"): "v1",
    ("REG", "price"): "v2",
    ("REG", "price+v3"): "v3",
    ("CLS", "price+insider"): "v4",
}


def indep_t(ic: pd.Series) -> float:
    sub = ic.iloc[::HOLD]
    return sub.mean() / (sub.std() / np.sqrt(len(sub))) if sub.std() > 0 else np.nan


def daily_ic(p: pd.DataFrame, target: str) -> pd.Series:
    return (p.groupby("date")
             .apply(lambda g: stats.spearmanr(g.score, g[target])[0]
                    if len(g) > 30 else np.nan, include_groups=False).dropna())


def run_cell(dev: pd.DataFrame, arch: str, cols: list[str]) -> dict:
    years = sorted(dev.date.dt.year.unique())
    test_years = [y for y in years if y >= years[0] + 2]

    preds, in_accs, out_accs = [], [], []
    for y in test_years:
        te = dev[dev.date.dt.year == y]
        cut = te.date.min()
        tr = dev[dev.date < cut - pd.Timedelta(days=PURGE * 2)]
        if len(tr) < 20_000 or te.empty:
            continue

        if arch == "CLS":
            m = xgb.XGBClassifier(**COMMON, objective="binary:logistic",
                                  eval_metric="logloss")
            m.fit(tr[cols], tr["label"])
            s_tr = m.predict_proba(tr[cols])[:, 1]
            s_te = m.predict_proba(te[cols])[:, 1]
        else:
            trs = tr[non_overlapping_mask(tr, every=HOLD)]      # C2
            m = xgb.XGBRegressor(**COMMON, objective="reg:squarederror")
            m.fit(trs[cols], trs["target_reg"])                 # C3
            s_tr = m.predict(tr[cols])
            s_te = m.predict(te[cols])

        # Same binary yardstick for both architectures: rank the score within
        # the day and call the top half a "beat". A regressor's raw output is
        # not a probability, so thresholding at 0.5 would be meaningless.
        def acc(frame, score):
            f = frame[["date", "label"]].copy()
            f["s"] = score
            r = f.groupby("date")["s"].rank(pct=True)
            return ((r > 0.5).astype(float) == f["label"]).mean()

        in_accs.append(acc(tr, s_tr))
        out_accs.append(acc(te, s_te))

        f = te[["symbol", "date", "fwd_return", "bt_return", "label"]].copy()
        f["score"] = s_te
        preds.append(f)

    p = pd.concat(preds, ignore_index=True)
    ic_bt, ic_fw = daily_ic(p, "bt_return"), daily_ic(p, "fwd_return")
    return {
        "in_acc": np.mean(in_accs), "out_acc": np.mean(out_accs),
        "gap": np.mean(in_accs) - np.mean(out_accs),
        "ic_bt": ic_bt.mean(), "t_bt": indep_t(ic_bt),
        "ic_fwd": ic_fw.mean(), "t_fwd": indep_t(ic_fw),
        "preds": p,
    }


def main() -> int:
    dev = pd.read_parquet(DATA / "panel_all_dev.parquet")
    dev["date"] = pd.to_datetime(dev["date"])
    dev = dev.sort_values(["symbol", "date"]).reset_index(drop=True)

    print(f"development rows : {len(dev):,}  {dev.date.min().date()} -> "
          f"{dev.date.max().date()}")
    print(f"symbols          : {dev.symbol.nunique()}")
    print(f"test folds       : 2022, 2023, 2024 — identical rows for every cell\n")

    rows = []
    for arch in ("CLS", "REG"):
        for fname, cols in FEATURE_SETS.items():
            r = run_cell(dev, arch, cols)
            name = NAMED.get((arch, fname), "—")
            r["preds"].to_parquet(
                DATA / f"preds_{arch}_{fname.replace('+', '_')}.parquet",
                compression="zstd", index=False)
            rows.append({"version": name, "arch": arch, "features": fname,
                         "n_feat": len(cols), **{k: v for k, v in r.items()
                                                 if k != "preds"}})
            print(f"  ran {arch:<4} {fname:<18} ({len(cols):>2} feat) "
                  f"{'= ' + name if name != '—' else ''}")

    res = pd.DataFrame(rows)
    res.to_csv(DATA / "bakeoff.csv", index=False)

    print("\n" + "=" * 92)
    print("SAME ROWS, SAME FOLDS, SAME TARGETS — 627,844 dev rows, "
          "375,697 test rows per cell")
    print("=" * 92)
    print(f"{'ver':<5}{'arch':<5}{'features':<18}{'n':>4}{'in-samp':>9}"
          f"{'out-samp':>10}{'gap':>8}{'IC_bt':>9}{'t_bt':>7}{'IC_fwd':>9}{'t_fwd':>7}")
    print("-" * 92)
    for _, r in res.iterrows():
        print(f"{r.version:<5}{r.arch:<5}{r.features:<18}{r.n_feat:>4}"
              f"{r.in_acc:>8.2%}{r.out_acc:>10.2%}{r.gap:>8.2%}"
              f"{r.ic_bt:>9.4f}{r.t_bt:>7.2f}{r.ic_fwd:>9.4f}{r.t_fwd:>7.2f}")
    print("-" * 92)

    print("\nAXIS 1 — architecture, holding features fixed")
    for f in FEATURE_SETS:
        c = res[(res.arch == "CLS") & (res.features == f)].iloc[0]
        g = res[(res.arch == "REG") & (res.features == f)].iloc[0]
        print(f"  {f:<18} CLS out {c.out_acc:.2%} / gap {c.gap:.2%}   "
              f"REG out {g.out_acc:.2%} / gap {g.gap:.2%}")

    print("\nAXIS 2 — features, holding architecture fixed")
    for a in ("CLS", "REG"):
        base = res[(res.arch == a) & (res.features == "price")].iloc[0]
        for f in FEATURE_SETS:
            if f == "price":
                continue
            r = res[(res.arch == a) & (res.features == f)].iloc[0]
            print(f"  {a}  price -> {f:<18} out-samp {r.out_acc - base.out_acc:+.2%}"
                  f"   gap {r.gap - base.gap:+.2%}   IC_bt {r.ic_bt - base.ic_bt:+.4f}")

    print(f"\nmax |t_bt| across the 8 cells: {res.t_bt.abs().max():.2f}")
    print("Expected max of 8 draws from the null: ~1.4. Nothing here is promoted")
    print("on the strength of being a grid maximum.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
