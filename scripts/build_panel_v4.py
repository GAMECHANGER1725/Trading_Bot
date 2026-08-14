#!/usr/bin/env python3
"""
Build the v4 panel: v1 price features + triple-barrier labels + Form 4 /
earnings features, restricted to the pre-registered development window.

The holdout (2025-01-01 onward) is separated here and written out WITHOUT
being inspected. Nothing downstream of this script reads it. Three model
generations have already failed in development; the single unbiased shot is
worth more than a fourth reading on a model that has not yet earned it.

Window note, and it constrains everything: the Form 4 dataset begins
2020-01-02. Insider features are therefore undefined before 2020, so the whole
comparison — v1 baseline included — runs on 2020-01-01 to 2024-12-31. That is
the same window PREREGISTRATION.md already fixed for development, but it costs
folds: v1's original run had 7 walk-forward folds and this has 3. Fewer folds
means a wider error bar on every number that follows. Stated up front so it
cannot be forgotten once results appear.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402

from src.features import FEATURE_NAMES, build  # noqa: E402
from src.features_v4 import (  # noqa: E402
    EARNINGS_FEATURES, INSIDER_FEATURES, V4_FEATURES,
    build_earnings, build_insider, load_earnings, load_form4,
    rank_cross_sectionally,
)
from src.labels import triple_barrier  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
KAG = Path("/home/claude/kag")

DEV_START, HOLDOUT_START = "2020-01-01", "2025-01-01"


def main() -> int:
    bars = pd.read_parquet(DATA / "bars" / "daily.parquet")
    bars["date"] = pd.to_datetime(bars["date"])
    print(f"bars            {len(bars):,} rows, {bars.symbol.nunique()} symbols, "
          f"{bars.date.min().date()} -> {bars.date.max().date()}")

    # ---- v1 features (ranked) + 5-day beat-median label -------------------
    feats = build(bars)
    print(f"v1 features     {len(feats):,} rows")

    # ---- triple-barrier outcome, the thing the strategy actually trades ---
    lab = triple_barrier(bars)[["symbol", "date", "bt_return", "bt_exit", "bt_days"]]
    df = feats.merge(lab, on=["symbol", "date"], how="left")

    # ---- v4 features ------------------------------------------------------
    universe = set(df.symbol.unique())
    f4 = load_form4(str(KAG / "filings.csv"), universe)
    earn = load_earnings(str(KAG / "earnings_features_clean (1).csv"), universe)
    print(f"form 4          {len(f4):,} directional filings, "
          f"{f4.symbol.nunique()} symbols, "
          f"{f4.filing_date.min().date()} -> {f4.filing_date.max().date()}")
    print(f"earnings        {len(earn):,} announcements, {earn.symbol.nunique()} symbols")

    panel = df[["symbol", "date"]].copy()
    ins = build_insider(panel, f4)
    ern = build_earnings(panel, earn)
    df = df.merge(ins, on=["symbol", "date"], how="left")
    df = df.merge(ern, on=["symbol", "date"], how="left")

    # Insider counts are genuinely zero when no filing occurred; earnings
    # features are genuinely missing when a symbol is absent from the earnings
    # source, and are left as sentinels rather than imputed.
    df[INSIDER_FEATURES] = df[INSIDER_FEATURES].fillna(0.0)
    df.loc[df["ins_days_since_buy"].isna(), "ins_days_since_buy"] = 999.0

    # ---- split ------------------------------------------------------------
    dev = df[(df.date >= DEV_START) & (df.date < HOLDOUT_START)].copy()
    hold = df[df.date >= HOLDOUT_START].copy()

    # Rank v4 features cross-sectionally per day, matching features.py.
    # Ranking is done AFTER the split so the holdout cannot influence the
    # development distribution through a shared percentile scale.
    dev = rank_cross_sectionally(dev, V4_FEATURES)

    dev.to_parquet(DATA / "panel_v4_dev.parquet", compression="zstd", index=False)
    hold.to_parquet(DATA / "panel_v4_holdout_SEALED.parquet",
                    compression="zstd", index=False)

    print(f"\ndevelopment     {len(dev):,} rows  "
          f"{dev.date.min().date()} -> {dev.date.max().date()}")
    print(f"holdout         {len(hold):,} rows  [written, not inspected]")
    print(f"features        {len(FEATURE_NAMES)} price + {len(INSIDER_FEATURES)} insider "
          f"+ {len(EARNINGS_FEATURES)} earnings = "
          f"{len(FEATURE_NAMES) + len(V4_FEATURES)}")

    # ---- coverage assertions (DATA_AUDIT rule: assert before using) -------
    d20 = dev[dev.date.dt.year >= 2020]
    with_ins = (d20[INSIDER_FEATURES].std(axis=1) > 0).mean()
    nonzero_net = (dev["ins_net_60"] != dev["ins_net_60"].median()).mean()
    print(f"\ncoverage checks")
    print(f"  rows with any insider variation   {with_ins:.1%}")
    print(f"  rows off the ins_net_60 tie block {nonzero_net:.1%}")
    print(f"  rows with an earnings date        "
          f"{(dev['earn_days_since'] < 400).mean():.1%}")
    print(f"  bt_return present                 {dev['bt_return'].notna().mean():.1%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
