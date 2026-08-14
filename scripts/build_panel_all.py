#!/usr/bin/env python3
"""
One panel, all feature generations, identical rows.

Papaya's objection, and it is correct: the four "versions" reported so far
were not asked the same question and were not scored on the same thing.

  * v1 and v4 predict a BINARY label — does this stock beat the day's median
    5-day forward return — and their ICs were measured against `fwd_return`.
  * v2 and v3 predict a REGRESSION target — the cross-sectionally demeaned
    triple-barrier bracket return — and their ICs were measured against
    `bt_return`.
  * v1's headline numbers came from 2020-2026 with 7 folds. Everything else
    ran 2020-2024 with 3.

Putting those ICs in one column implied a comparison that was never run.

This script builds a single panel carrying every feature generation, then
drops to the rows where ALL of them are defined, so no variant is ever
evaluated on rows another variant could not see. Everything downstream runs
on this file.

Two axes are deliberately kept separate from here on:
  ARCHITECTURE  which question the model is asked (binary vs regression, all
                rows vs non-overlapping sampling)
  FEATURES      which inputs it gets (price / +v3 / +insider / both)

v1 and v4 differ only on the features axis. v1 and v2 differ only on the
architecture axis. Reporting them in one column conflated the two.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402

from src.features import FEATURE_NAMES, build  # noqa: E402
from src.features_v3 import V3_FEATURES, build_v3  # noqa: E402
from src.features_v4 import (  # noqa: E402
    INSIDER_FEATURES, build_insider, load_form4, rank_cross_sectionally,
)
from src.labels import cross_sectional_target, triple_barrier  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
KAG = Path("/home/claude/kag")
DEV_START, HOLDOUT_START = "2020-01-01", "2025-01-01"


def main() -> int:
    bars = pd.read_parquet(DATA / "bars" / "daily.parquet")
    bars["date"] = pd.to_datetime(bars["date"])
    print(f"bars        {len(bars):,} rows, {bars.symbol.nunique()} symbols")

    # ---- v1: 14 price features + binary beat-median-5d label --------------
    f1 = build(bars)
    print(f"v1 price    {len(f1):,} rows, {len(FEATURE_NAMES)} features")

    # ---- v3: 11 sector / regime / breadth / beta / dividend features ------
    div = pd.read_parquet("/mnt/user-data/uploads/Trading_Bot/data/dividends.parquet")
    f3 = build_v3(bars, dividends=div)
    print(f"v3 extra    {len(f3):,} rows, {len(V3_FEATURES)} features, "
          f"{f3.sector.nunique()} return-correlation sectors")

    # ---- triple-barrier outcome: what the strategy actually trades --------
    lab = triple_barrier(bars)[["symbol", "date", "bt_return", "bt_exit", "bt_days"]]

    df = (f1.merge(f3.drop(columns=["sector"]), on=["symbol", "date"], how="inner")
            .merge(lab, on=["symbol", "date"], how="inner"))
    print(f"after inner join on v1 + v3 + labels: {len(df):,} rows")

    # ---- v4: 8 SEC Form 4 insider features --------------------------------
    f4 = load_form4(str(KAG / "filings.csv"), set(df.symbol.unique()))
    ins = build_insider(df[["symbol", "date"]], f4)
    df = df.merge(ins, on=["symbol", "date"], how="left")
    df[INSIDER_FEATURES] = df[INSIDER_FEATURES].fillna(0.0)
    df.loc[df["ins_days_since_buy"] == 0, "ins_days_since_buy"] = 999.0

    # ---- common-row restriction ------------------------------------------
    allf = FEATURE_NAMES + V3_FEATURES + INSIDER_FEATURES
    before = len(df)
    df = df.dropna(subset=allf + ["label", "bt_return", "fwd_return"])
    print(f"common rows (every feature defined): {len(df):,} "
          f"(dropped {before - len(df):,})")

    dev = df[(df.date >= DEV_START) & (df.date < HOLDOUT_START)].copy()
    hold = df[df.date >= HOLDOUT_START].copy()

    dev = rank_cross_sectionally(dev, INSIDER_FEATURES)
    dev["target_reg"] = cross_sectional_target(dev)   # v2/v3 regression target

    dev.to_parquet(DATA / "panel_all_dev.parquet", compression="zstd", index=False)
    hold.to_parquet(DATA / "panel_all_holdout_SEALED.parquet",
                    compression="zstd", index=False)

    print(f"\ndevelopment {len(dev):,} rows  {dev.date.min().date()} -> "
          f"{dev.date.max().date()}, {dev.symbol.nunique()} symbols")
    print(f"holdout     {len(hold):,} rows  [written, not inspected]")
    print(f"features    {len(FEATURE_NAMES)} price + {len(V3_FEATURES)} v3 "
          f"+ {len(INSIDER_FEATURES)} insider = {len(allf)}")
    print("\nrows per test year (identical for every variant):")
    print(dev[dev.date.dt.year >= 2022].groupby(dev.date.dt.year).size().to_string())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
