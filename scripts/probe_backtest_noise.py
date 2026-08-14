#!/usr/bin/env python3
"""
The backtest Sharpe reversed between two runs of essentially the same model.

  backtest_v4.py   v1 = 0.66   v4 = 0.28
  bakeoff_backtest.py  v1 = 0.26   v4 = 0.85

The two v1 models differ only in the row ordering fed to XGBoost (which
changes which rows `subsample=0.7` draws) and 74 rows out of 627,844. Their
out-of-sample accuracies are 50.68% and 50.71% — the same model to within
three hundredths of a percentage point. Their Sharpes differ by 0.40, in the
opposite direction to the earlier claim.

So the question is not "which is right". It is: how many positions does this
strategy need before the backtest measures the model rather than measuring
which ten names happened to get picked?

TEST
  Take one fixed set of predictions. Vary only N_POS. If Sharpe stabilises as
  N grows, concentration is the cause and the 10-position backtest is not a
  usable discriminator between models. If it does not, something else is
  wrong and the engine needs another look.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from src.backtest import Backtest, Order  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
TEST_START, TEST_END = "2022-01-01", "2024-12-31"
STOP, TARGET, MAX_HOLD, SLIP = 0.08, 0.12, 10, 3.5


def strat_factory(preds, n_pos):
    table = {d: g.set_index("symbol")["score"]
             for d, g in preds.groupby(preds.date.dt.date)}

    def strategy(view, positions, cash, equity):
        s = table.get(view.as_of)
        if s is None:
            return []
        pool = s[s.index.isin(view.tradable) & ~s.index.isin(positions)]
        picks = pool.nlargest(n_pos - len(positions)) if not pool.empty else pool
        if picks.empty:
            return []
        return [Order(x, weight=0.95 / n_pos, stop_pct=STOP, target_pct=TARGET,
                      max_hold_days=MAX_HOLD) for x in picks.index]
    return strategy


def main() -> int:
    bars = pd.read_parquet(DATA / "bars" / "daily.parquet")
    bars["date"] = pd.to_datetime(bars["date"])
    win = bars[(bars.date >= "2021-06-01") & (bars.date <= "2025-02-15")]

    files = {"v1 CLS price": "preds_CLS_price.parquet",
             "v4 CLS price+insider": "preds_CLS_price_insider.parquet"}

    print("Sharpe as a function of portfolio concentration")
    print("(identical predictions in each row; only N_POS changes)\n")
    print(f"{'model':<24}" + "".join(f"{f'N={n}':>10}" for n in (10, 25, 50, 100)))
    print("-" * 64)

    store = {}
    for label, f in files.items():
        p = pd.read_parquet(DATA / f)
        p["date"] = pd.to_datetime(p["date"])
        row = []
        for n in (10, 25, 50, 100):
            bt = Backtest(win, slippage_bps=SLIP, members_only=True)
            r = bt.run(strat_factory(p, n), warmup=120)
            eq = r.equity[(r.equity.index >= TEST_START) & (r.equity.index <= TEST_END)]
            rr = eq.pct_change().dropna()
            sh = rr.mean() / rr.std() * np.sqrt(252)
            row.append(sh)
            store[(label, n)] = sh
        print(f"{label:<24}" + "".join(f"{v:>10.2f}" for v in row))

    print("-" * 64)
    print(f"\n{'spread v1 vs v4':<24}" + "".join(
        f"{abs(store[('v1 CLS price', n)] - store[('v4 CLS price+insider', n)]):>10.2f}"
        for n in (10, 25, 50, 100)))
    print("\nIf the spread shrinks as N grows, the 10-position Sharpe was measuring")
    print("name selection luck, not model quality — and no conclusion about which")
    print("model is better can be drawn from it at N=10.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
