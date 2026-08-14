#!/usr/bin/env python3
"""
Train the model the live bot will actually load. v1: CLS, 14 price features.

Chosen per D31: highest out-of-sample accuracy (50.71%, the only cell above
the 50% coin at all), lowest overfitting gap of the CLS cells (3.43%), and the
smallest feature set — fourteen things to break at 6:30am instead of
twenty-two or thirty-three.

Read the honest label on this: "least-bad placeholder ranker", not "model that
makes money". Its accuracy edge is +0.71pp with t = 2.33, which clears a naive
p<0.05 and fails Bonferroni across the eight cells it was selected from. At
200 positions it returns Sharpe 0.49 against SPY's 0.56. It is here so the
execution layer has something to route, and so the 200-trade paper clock can
start. It is not evidence of an edge.

Trained on the full development window, 2020-01-01 to 2024-12-31.
THE 2025+ HOLDOUT IS NOT READ BY THIS SCRIPT.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402
import xgboost as xgb  # noqa: E402

from src.features import FEATURE_NAMES  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

PARAMS = dict(
    max_depth=4, n_estimators=300, learning_rate=0.03,
    subsample=0.7, colsample_bytree=0.7, min_child_weight=200,
    reg_lambda=5.0, reg_alpha=1.0,
    objective="binary:logistic", eval_metric="logloss",
    tree_method="hist", n_jobs=4, random_state=42,
)


def main() -> int:
    dev = pd.read_parquet(DATA / "panel_all_dev.parquet")
    dev["date"] = pd.to_datetime(dev["date"])
    dev = dev.dropna(subset=["label"] + FEATURE_NAMES)

    assert dev.date.max() < pd.Timestamp("2025-01-01"), \
        "Holdout contamination: training data extends past 2024-12-31."

    m = xgb.XGBClassifier(**PARAMS)
    m.fit(dev[FEATURE_NAMES], dev["label"])
    m.save_model(str(DATA / "model_prod.json"))

    # The feature order is part of the contract. If the live bot builds
    # columns in a different order, XGBoost will happily score garbage
    # without raising anything — so the order is written down and asserted
    # at load time rather than assumed.
    meta = {
        "model": "v1",
        "architecture": "XGBClassifier, binary:logistic",
        "label": "beats the day's median 5-day forward return",
        "features": FEATURE_NAMES,
        "n_features": len(FEATURE_NAMES),
        "trained_rows": int(len(dev)),
        "train_start": str(dev.date.min().date()),
        "train_end": str(dev.date.max().date()),
        "params": {k: v for k, v in PARAMS.items()},
        "expected_oos_accuracy": 0.5071,
        "expected_oos_accuracy_se": 0.0031,
        "warning": ("Placeholder ranker. Accuracy edge +0.71pp, t=2.33, fails "
                    "Bonferroni across the 8 cells it was selected from. Does "
                    "not beat SPY at any measurable position count. See D31."),
    }
    (DATA / "model_prod_meta.json").write_text(json.dumps(meta, indent=2))

    print(f"trained on   {len(dev):,} rows  "
          f"{dev.date.min().date()} -> {dev.date.max().date()}")
    print(f"features     {len(FEATURE_NAMES)}")
    print(f"written      data/model_prod.json + model_prod_meta.json")

    imp = pd.Series(m.feature_importances_,
                    index=FEATURE_NAMES).sort_values(ascending=False)
    print(f"\ntop features: {', '.join(f'{k} ({v:.3f})' for k, v in imp.head(5).items())}")
    print(f"importance spread {imp.min():.3f} to {imp.max():.3f} — a flat "
          f"profile is what a model with nothing to find looks like")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
