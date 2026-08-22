#!/usr/bin/env python3
"""Replay every book over recent history under the CURRENT rules.

The forward test is the only evidence that counts, but it moves at ~30 trades a
day and any rule change resets it. This rig re-runs the same Portfolio, the same
signals and the same risk model over the last few weeks of real candles so a
proposed change can be checked in a minute instead of a month.

It is not proof of an edge, and it must never be used to pick parameters — the
repo has a standing rule that searching until a target is hit always hits it.
It answers a narrower question: does this change help or hurt, against the two
benchmarks that matter?

  * the random-entry control  — did the signal do anything?
  * equal-weight buy-and-hold — did trading beat not trading?

Run:  python3 replay.py            whole window
      python3 replay.py --split    first half vs second half
"""
import os
import sys
import tempfile

import paper_trader as pt


def load_series():
    data = pt.fetch_all(pt.SYMBOLS)
    series = {}
    for sym, bars in data.items():
        closed = bars[:-1]                      # drop the still-forming candle
        if len(closed) > pt.WARMUP_BARS:
            series[sym] = (closed, pt.compute_indicators(closed, sym))
    return series


def run(series, lo, hi):
    """Replay bars [lo, hi). Markets are interleaved by bar index, not run one
    at a time, so the shared account sees a realistic arrival order."""
    tmp = os.path.join(tempfile.mkdtemp(), "replay.csv")
    pt.ensure_log(tmp)
    pfs = {s: pt.Portfolio(s, log_file=tmp, quiet=True) for s in pt.STRATEGIES}
    bench = pt.Benchmark()
    for sym, (closed, _) in series.items():
        if lo < len(closed):
            bench.observe(sym, closed[lo]["close"])
    for i in range(lo, hi):
        for sym, (closed, ind) in series.items():
            if i >= len(closed):
                continue
            for name, cfg in pt.STRATEGIES.items():
                pfs[name].on_bar(sym, closed[i], cfg["signal"](ind, i, cfg),
                                 ind["atr"][i])
    for sym, (closed, _) in series.items():
        bench.observe(sym, closed[min(hi, len(closed)) - 1]["close"])
    return pfs, bench


def show(title, pfs, bench):
    print(f"\n=== {title} ===")
    print(f"{'book':16} {'equity':>11} {'ret':>8} {'W/L':>9} {'win%':>6} "
          f"{'per trade':>11} {'SE':>8} {'t':>7}")
    print("-" * 82)
    res = {}
    for name, p in sorted(pfs.items()):
        m, se, t = pt.expectancy(p.pnls)
        n = p.wins + p.losses
        res[name] = (m, se, n)
        print(f"{name:16} ${p.cash_equity:>10,.2f} "
              f"{(p.cash_equity / pt.INITIAL_CAPITAL - 1) * 100:>7.2f}% "
              f"{f'{p.wins}/{p.losses}':>9} {p.wins / n * 100 if n else 0:>5.1f}% "
              f"{m:>+11.2f} {se:>8.2f} {t:>7.2f}")
    print("-" * 82)
    be = bench.equity()
    print(f"{'buy & hold':16} ${be:>10,.2f} "
          f"{(be / pt.INITIAL_CAPITAL - 1) * 100:>7.2f}%")

    cm, cse, _ = res.get("v0_control", (0, 0, 0))
    print("\nvs random entry (Welch t on the difference in mean P&L):")
    for name, (m, se, _) in sorted(res.items()):
        if name == "v0_control":
            continue
        if not (se and cse):
            print(f"  {name:14} too few trades to compare")
            continue
        t = (m - cm) / (se ** 2 + cse ** 2) ** 0.5
        print(f"  {name:14} {m:+.2f} vs {cm:+.2f}   t={t:+.2f}   "
              f"{'BEATS control' if t > 2 else 'not distinguishable from random'}")
    return res


def main():
    series = load_series()
    n = max(len(c) for c, _ in series.values())
    print(f"{len(series)} markets, bars {pt.WARMUP_BARS}..{n} "
          f"({(n - pt.WARMUP_BARS) / 24:.0f} days of {pt.INTERVAL} candles)")

    if "--split" in sys.argv:
        # A cheap stability check, not a walk-forward validation: if a result
        # only exists in one half, it is a regime, not an edge.
        mid = (pt.WARMUP_BARS + n) // 2
        a_pfs, a_b = run(series, pt.WARMUP_BARS, mid)
        show("first half", a_pfs, a_b)
        b_pfs, b_b = run(series, mid, n)
        show("second half", b_pfs, b_b)
    else:
        pfs, bench = run(series, pt.WARMUP_BARS, n)
        show(f"full window", pfs, bench)
    print("\n|t| > 2 is the bar. Nothing below it is evidence, however good the "
          "return looks.\nThis is a backtest on a window the parameters were "
          "fitted near — treat it as a\nsmoke test for changes, not as proof of "
          "anything.")


if __name__ == "__main__":
    main()
