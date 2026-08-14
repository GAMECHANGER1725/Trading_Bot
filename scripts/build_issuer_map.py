#!/usr/bin/env python3
"""
Build the issuer map: groups of tickers that must never be held together.

WHY THIS EXISTS
The 14 Aug dry run bought both GOOG and GOOGL — 7.6% of equity in Alphabet
under a 5%-per-name cap. `order_sanity` checks concentration per ticker, and a
ticker is not an issuer.

Chasing that turned up a second and worse case. Ranking every pair in the
universe by return correlation produces two distinct clusters above 0.95:

  corr = 1.0000  TICKER RENAMES. Alpaca serves both the old and new symbol for
                 roughly a year around the change, so FB and META, RTX and UTX,
                 ANTM and ELV, ABC and COR are each ONE security appearing
                 twice. Buying both is not concentration, it is buying the same
                 stock and recording it as diversification.

  corr ~ 0.97+   DUAL SHARE CLASSES. GOOG/GOOGL, FOX/FOXA, NWS/NWSA,
                 DISCA/DISCK. Two real securities, one issuer, one business.

Both collapse to the same rule, so both get the same fix: never hold two
tickers whose returns are nearly identical.

WHY A CORRELATION THRESHOLD AND NOT A HAND-WRITTEN LIST
A hand-written list is a guess that looks like a fact, and it silently rots as
the index changes. The threshold is instead read off a measured gap in the
data:

    NWS  / NWSA   0.9717
    DISCA/ DISCK  0.9673
    -------------------- gap
    AVB  / EQR    0.9343   <- two different apartment REITs
    DHI  / LEN    0.9198   <- two different homebuilders

0.95 sits inside a 3.3-point gap with nothing in it. Sector peers that move
together, which is a real and useful thing for the model to see, sit clearly
below. Same security wearing two tickers sits clearly above.

Groups are connected components, so a three-way case (a rename of a name that
also has two share classes) is handled without special-casing.

    python3 scripts/build_issuer_map.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
OUT = DATA / "issuer_groups.json"

THRESHOLD = 0.95      # read off the measured gap, see the docstring
MIN_OVERLAP = 120     # sessions; fewer than this and the correlation is noise


def main() -> int:
    bars = pd.read_parquet(DATA / "bars" / "daily.parquet")
    bars["date"] = pd.to_datetime(bars["date"])
    w = bars.pivot_table(index="date", columns="symbol",
                         values="close").pct_change()
    w = w.dropna(axis=1, thresh=MIN_OVERLAP)
    syms = list(w.columns)
    print(f"symbols with >= {MIN_OVERLAP} sessions: {len(syms)}")

    c = w.corr(min_periods=MIN_OVERLAP).to_numpy().copy()
    np.fill_diagonal(c, 0.0)
    c = np.nan_to_num(c, nan=0.0)
    iu = np.triu_indices(len(syms), 1)
    pairs = [(float(v), syms[i], syms[j])
             for v, i, j in zip(c[iu], iu[0], iu[1]) if v >= THRESHOLD]
    pairs.sort(reverse=True)
    print(f"pairs at or above {THRESHOLD}: {len(pairs)}")

    # --- verify the threshold sits in a gap, rather than trusting it --------
    below = sorted((float(v) for v in c[iu] if v < THRESHOLD), reverse=True)
    lowest_kept = pairs[-1][0] if pairs else float("nan")
    highest_dropped = below[0] if below else float("nan")
    gap = lowest_kept - highest_dropped
    print(f"lowest kept   {lowest_kept:.4f}")
    print(f"highest dropped {highest_dropped:.4f}")
    print(f"gap           {gap:.4f}")
    if gap < 0.01:
        print("*** WARNING: the threshold does not sit in a gap. Something "
              "sits right on the boundary — inspect before trusting this map.")

    # --- connected components ---------------------------------------------
    parent = {s: s for s in syms}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for _, a, b in pairs:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    groups: dict[str, list[str]] = {}
    for s in syms:
        groups.setdefault(find(s), []).append(s)
    multi = {k: sorted(v) for k, v in groups.items() if len(v) > 1}

    # --- classify, for the human reading the file -------------------------
    lookup = {(a, b): v for v, a, b in pairs}
    lookup.update({(b, a): v for v, a, b in pairs})
    out = []
    for members in sorted(multi.values()):
        corrs = [lookup[(x, y)] for i, x in enumerate(members)
                 for y in members[i + 1:] if (x, y) in lookup]
        mn = min(corrs) if corrs else float("nan")
        # Advisory only, and demonstrably imperfect: GOOG/GOOGL is a dual share
        # class and scores 0.9952, while the DOC/PEAK and WLTW/WTW renames
        # score below it. Correlation cannot separate the two cases cleanly and
        # pretending otherwise would put a wrong fact in a data file. The label
        # is never read by any code — grouping is what matters, and both cases
        # get handled identically.
        kind = "likely_rename" if mn > 0.995 else "likely_dual_class"
        out.append({"members": members, "min_corr": round(mn, 4),
                    "likely": kind, "label_is_advisory": True})

    payload = {
        "threshold": THRESHOLD,
        "min_overlap_sessions": MIN_OVERLAP,
        "built_from": "data/bars/daily.parquet",
        "n_groups": len(out),
        "gap_below_threshold": round(gap, 4),
        "note": ("Never hold two tickers from the same group. Renames are one "
                 "security under two symbols; dual classes are one issuer. "
                 "Both break per-ticker concentration limits."),
        "groups": out,
    }
    OUT.write_text(json.dumps(payload, indent=2))

    print(f"\n{len(out)} groups written to {OUT.name}\n")
    print(f"{'likely':<20}{'min corr':>10}   members")
    for g in sorted(out, key=lambda g: (-g["min_corr"], g["members"])):
        print(f"{g['likely']:<20}{g['min_corr']:>10.4f}   {', '.join(g['members'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
