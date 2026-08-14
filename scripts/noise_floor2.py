#!/usr/bin/env python3
"""
Corrected paired comparison, plus the position-count question.

Fix to noise_floor.py: it printed the mean difference over all days but
computed the t-statistic from every 10th day, so the two disagreed in sign.
Subsampling is a valid independence correction but it discards 90% of the
data and its mean is not the full-sample mean. Replaced with a block
bootstrap on the paired difference series, which uses every day and handles
the autocorrelation directly.

PART A  Are any two cells distinguishable, paired on identical days?
PART B  Does raising the position count let the strategy beat SPY, or only
        let us measure that it does not?

Part B matters because the two are easily confused. More positions lowers
measurement noise. It does not create edge. If the model has no ranking
skill, a larger portfolio converges on owning the index minus costs — so the
Sharpe should approach SPY's from BELOW as N grows, and the gap that remains
is the cost of trading.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from scipy import stats  # noqa: E402

from src.backtest import Backtest, Order, buy_and_hold  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
BLOCK, N_BOOT = 10, 2000
TEST_START, TEST_END = "2022-01-01", "2024-12-31"
STOP, TARGET, MAX_HOLD, SLIP = 0.08, 0.12, 10, 3.5

CELLS = [("v1", "CLS", "price"), ("—", "CLS", "price_v3"),
         ("v4", "CLS", "price_insider"), ("—", "CLS", "price_v3_insider"),
         ("v2", "REG", "price"), ("v3", "REG", "price_v3"),
         ("—", "REG", "price_insider"), ("—", "REG", "price_v3_insider")]


def day_hit(p):
    out = []
    for d, g in p.groupby("date"):
        if len(g) < 30:
            continue
        r = g["score"].rank(pct=True)
        out.append((d, float(((r > 0.5).astype(float) == g["label"]).mean())))
    return pd.Series(dict(out)).sort_index()


def boot_se(x: np.ndarray, rng) -> float:
    """Block-bootstrap standard error of the mean of a serially correlated series."""
    n = len(x)
    nb = int(np.ceil(n / BLOCK))
    pool = np.arange(0, n - BLOCK + 1)
    means = np.empty(N_BOOT)
    for b in range(N_BOOT):
        st = rng.choice(pool, size=nb, replace=True)
        idx = (st[:, None] + np.arange(BLOCK)[None, :]).ravel()[:n]
        means[b] = x[idx].mean()
    return float(means.std())


def part_a(rng):
    print("PART A — paired, identical days, block-bootstrap SE on the difference")
    print("=" * 78)
    hits = {}
    for name, arch, fset in CELLS:
        p = pd.read_parquet(DATA / f"preds_{arch}_{fset}.parquet")
        p["date"] = pd.to_datetime(p["date"])
        hits[(name, arch, fset)] = day_hit(p)

    keys = list(hits)
    best = max(keys, key=lambda k: hits[k].mean())
    print(f"reference cell: {best[0]} {best[1]} {best[2].replace('_','+')} "
          f"({hits[best].mean():.2%})\n")
    print(f"{'vs cell':<30}{'diff':>9}{'SE':>8}{'t':>7}{'p':>8}")
    print("-" * 78)
    n_sig = 0
    for k in keys:
        if k == best:
            continue
        a, b = hits[best].align(hits[k], join="inner")
        d = (a - b).to_numpy()
        se = boot_se(d, rng)
        t = d.mean() / se if se > 0 else np.nan
        pv = 2 * (1 - stats.norm.cdf(abs(t)))
        n_sig += pv < 0.05
        label = f"{k[0]} {k[1]} {k[2].replace('_','+')}"
        print(f"{label:<30}{d.mean():>+9.2%}{se:>8.2%}{t:>7.2f}{pv:>8.3f}"
              f"{'  DIFFERENT' if pv < 0.05 else ''}")
    print("-" * 78)
    print(f"distinguishable from the best: {n_sig} of {len(keys)-1}\n")


def strat(preds, n_pos):
    table = {d: g.set_index("symbol")["score"]
             for d, g in preds.groupby(preds.date.dt.date)}

    def s(view, positions, cash, equity):
        sc = table.get(view.as_of)
        if sc is None:
            return []
        pool = sc[sc.index.isin(view.tradable) & ~sc.index.isin(positions)]
        picks = pool.nlargest(n_pos - len(positions)) if not pool.empty else pool
        return [Order(x, weight=0.95 / n_pos, stop_pct=STOP, target_pct=TARGET,
                      max_hold_days=MAX_HOLD) for x in picks.index]
    return s


def part_b():
    print("\nPART B — does raising N beat SPY, or only measure better?")
    print("=" * 78)
    bars = pd.read_parquet(DATA / "bars" / "daily.parquet")
    bars["date"] = pd.to_datetime(bars["date"])
    win = bars[(bars.date >= "2021-06-01") & (bars.date <= "2025-02-15")]

    spy = pd.read_parquet(DATA / "spy.parquet")
    spy["date"] = pd.to_datetime(spy["date"])
    sw = spy[(spy.date >= TEST_START) & (spy.date <= TEST_END)]
    r_spy = Backtest(sw, slippage_bps=SLIP, members_only=False).run(
        buy_and_hold("SPY"), warmup=0)
    e_spy = r_spy.equity
    ret_spy = e_spy.pct_change().dropna()
    sh_spy = ret_spy.mean() / ret_spy.std() * np.sqrt(252)
    print(f"SPY buy & hold Sharpe: {sh_spy:.2f}\n")

    NS = (10, 25, 50, 100, 200)
    files = {"v1  CLS price": "preds_CLS_price.parquet",
             "v4  CLS price+insider": "preds_CLS_price_insider.parquet",
             "v2  REG price": "preds_REG_price.parquet"}

    print(f"{'model':<24}" + "".join(f"{f'N={n}':>9}" for n in NS)
          + f"{'vs SPY @200':>13}")
    print("-" * 78)
    for label, f in files.items():
        p = pd.read_parquet(DATA / f)
        p["date"] = pd.to_datetime(p["date"])
        shs, last = [], None
        for n in NS:
            r = Backtest(win, slippage_bps=SLIP, members_only=True).run(
                strat(p, n), warmup=120)
            eq = r.equity[(r.equity.index >= TEST_START) & (r.equity.index <= TEST_END)]
            rr = eq.pct_change().dropna()
            shs.append(rr.mean() / rr.std() * np.sqrt(252))
            if n == NS[-1]:
                last = rr
        a, b = last.align(ret_spy, join="inner")
        _, pv = stats.ttest_rel(a, b)
        print(f"{label:<24}" + "".join(f"{v:>9.2f}" for v in shs)
              + f"{shs[-1]-sh_spy:>+8.2f} p={pv:.2f}")
    print("-" * 78)
    print(f"{'SPY':<24}" + "".join(f"{sh_spy:>9.2f}" for _ in NS))
    print("\nIf every model's Sharpe converges to a value BELOW SPY as N rises,")
    print("the models have no ranking skill and the shortfall is trading cost.")


def main() -> int:
    rng = np.random.default_rng(0)
    part_a(rng)
    part_b()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
