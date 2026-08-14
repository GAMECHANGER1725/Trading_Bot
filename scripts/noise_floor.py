#!/usr/bin/env python3
"""
Before asking "how do we improve v4", establish whether the grid contains any
real differences at all.

The eight cells span 49.65% to 50.71% out-of-sample accuracy. Seed-to-seed
variation is 0.07%, so the models themselves are stable. But that is only
model-fitting noise. The question nobody has asked is the sampling one: if
2022-2024 had gone slightly differently, how much would 50.71% move?

METHOD
  Block bootstrap on the test period. Days are resampled in contiguous blocks
  of 10 sessions, with replacement, to a test set of the same length. Blocks
  rather than individual days because the 5-day forward label makes adjacent
  days share most of their outcome — resampling single days would treat
  ~5 correlated observations as independent and understate the error by
  roughly sqrt(5).

  1,000 resamples per cell. The spread of the resampled accuracies is the
  standard error on that cell's headline number.

WHAT THIS DECIDES
  If the standard error is comparable to the spread across cells, then every
  version in the grid is the same number wearing different labels, and
  "improve v4" has no measurable target to aim at. That is a finding, not an
  evasion — it is the difference between "we have not improved it yet" and
  "there is nothing here to improve".
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from scipy import stats  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
BLOCK, N_BOOT = 10, 1000

CELLS = [
    ("v1", "CLS", "price"), ("—", "CLS", "price_v3"),
    ("v4", "CLS", "price_insider"), ("—", "CLS", "price_v3_insider"),
    ("v2", "REG", "price"), ("v3", "REG", "price_v3"),
    ("—", "REG", "price_insider"), ("—", "REG", "price_v3_insider"),
]


def day_stats(p: pd.DataFrame) -> pd.DataFrame:
    """Collapse to one row per day: hit rate and cross-sectional IC."""
    out = []
    for d, g in p.groupby("date"):
        if len(g) < 30:
            continue
        r = g["score"].rank(pct=True)
        out.append({
            "date": d,
            "hit": float(((r > 0.5).astype(float) == g["label"]).mean()),
            "ic": float(stats.spearmanr(g["score"], g["bt_return"])[0]),
            "n": len(g),
        })
    return pd.DataFrame(out).sort_values("date").reset_index(drop=True)


def block_boot(ds: pd.DataFrame, rng) -> tuple[np.ndarray, np.ndarray]:
    n = len(ds)
    n_blocks = int(np.ceil(n / BLOCK))
    hit, ic = ds["hit"].to_numpy(), ds["ic"].to_numpy()
    starts_pool = np.arange(0, n - BLOCK + 1)
    hs, ics = np.empty(N_BOOT), np.empty(N_BOOT)
    for b in range(N_BOOT):
        st = rng.choice(starts_pool, size=n_blocks, replace=True)
        idx = (st[:, None] + np.arange(BLOCK)[None, :]).ravel()[:n]
        hs[b] = hit[idx].mean()
        ics[b] = ic[idx].mean()
    return hs, ics


def main() -> int:
    rng = np.random.default_rng(0)
    print(f"Block bootstrap: {N_BOOT} resamples, {BLOCK}-session blocks, "
          f"test period 2022-2024\n")
    print(f"{'ver':<5}{'arch':<5}{'features':<18}{'acc':>8}{'SE':>7}"
          f"{'95% interval':>18}{'IC_bt':>9}{'SE':>8}")
    print("-" * 78)

    rows = []
    for name, arch, fset in CELLS:
        p = pd.read_parquet(DATA / f"preds_{arch}_{fset}.parquet")
        p["date"] = pd.to_datetime(p["date"])
        ds = day_stats(p)
        hs, ics = block_boot(ds, rng)
        acc, se = ds["hit"].mean(), hs.std()
        ic, ic_se = ds["ic"].mean(), ics.std()
        rows.append({"version": name, "arch": arch, "features": fset,
                     "acc": acc, "acc_se": se, "ic": ic, "ic_se": ic_se,
                     "days": len(ds)})
        print(f"{name:<5}{arch:<5}{fset.replace('_','+'):<18}{acc:>8.2%}{se:>7.2%}"
              f"{f'[{acc-1.96*se:.2%}, {acc+1.96*se:.2%}]':>18}"
              f"{ic:>9.4f}{ic_se:>8.4f}")

    res = pd.DataFrame(rows)
    print("-" * 78)
    spread = res.acc.max() - res.acc.min()
    mean_se = res.acc_se.mean()
    print(f"\nspread across the 8 cells : {spread:.2%}")
    print(f"mean standard error       : {mean_se:.2%}")
    print(f"ratio                     : {spread/mean_se:.2f}x")

    print("\n\nPAIRWISE: is any cell distinguishable from any other?")
    print("Paired on identical days, so the market's own moves cancel.")
    print("-" * 78)
    dss = {}
    for name, arch, fset in CELLS:
        p = pd.read_parquet(DATA / f"preds_{arch}_{fset}.parquet")
        p["date"] = pd.to_datetime(p["date"])
        dss[(name, arch, fset)] = day_stats(p).set_index("date")

    keys = list(dss)
    sig = 0
    total = 0
    best = max(keys, key=lambda k: dss[k]["hit"].mean())
    print(f"best cell by accuracy: {best[0]} {best[1]} "
          f"{best[2].replace('_','+')} ({dss[best]['hit'].mean():.2%})\n")
    for k in keys:
        if k == best:
            continue
        a, b = dss[best]["hit"].align(dss[k]["hit"], join="inner")
        d = (a - b)
        # 10-session blocks -> use every 10th difference for independence
        sub = d.iloc[::BLOCK]
        t = sub.mean() / (sub.std() / np.sqrt(len(sub)))
        p_val = 2 * (1 - stats.norm.cdf(abs(t)))
        total += 1
        sig += p_val < 0.05
        print(f"  best vs {k[0]:<3}{k[1]:<5}{k[2].replace('_','+'):<18}"
              f"diff {d.mean():+.2%}  indep t {t:+.2f}  p={p_val:.3f}"
              f"{'   DIFFERENT' if p_val < 0.05 else ''}")

    print("-" * 78)
    print(f"\ncells distinguishable from the best: {sig} of {total}")
    res.to_csv(DATA / "noise_floor.csv", index=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
