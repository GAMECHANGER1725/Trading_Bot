"""
Re-fetch the bar panel into this container using the project's OWN data layer.

Why this exists: the desktop bridge cannot move the 26 MB cached parquet
(everything above ~1.2 MB per call times out). Rather than reimplement the
fetch — which would risk silently different conventions and make any
comparison against v1 meaningless — this calls `data_layer.fetch_symbol`
unchanged. Same source, same feed=sip, same adjustment=all, same membership
filtering, same trail_days=45.

Verification: the resulting panel must match the documented cache
(1,321,276 rows, 724 symbols, 2016-01-04 -> 2026-01-23) closely. It will not
match exactly — the cache was built 13 Aug 2026 and today is 14 Aug, so
symbols still in the index gain one session. Any larger discrepancy means
this fetch is not reproducing the original and must not be used.
"""
from __future__ import annotations

import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.data_layer import AlpacaBars, Universe, fetch_symbol, load_credentials  # noqa: E402

OUT = ROOT / "data" / "bars" / "daily.parquet"


def main() -> None:
    creds = load_credentials(ROOT / ".env")
    uni = Universe(ROOT / "data" / "sp500_history.csv")
    syms = uni.all_tickers(exclude_reused=True)
    print(f"universe: {len(syms)} tickers (4 reused excluded)", flush=True)

    # One client per worker; the pause inside _get keeps us under the rate limit.
    n_workers = 6
    clients = [AlpacaBars(headers=dict(creds), pause=0.05) for _ in range(n_workers)]
    done = {"n": 0}
    t0 = time.time()

    def work(i_sym):
        i, sym = i_sym
        cl = clients[i % n_workers]
        for attempt in range(3):
            try:
                rows = fetch_symbol(cl, uni, sym)
                done["n"] += 1
                if done["n"] % 100 == 0:
                    print(f"  {done['n']}/{len(syms)}  {time.time()-t0:.0f}s", flush=True)
                return rows
            except Exception as exc:  # noqa: BLE001
                if attempt == 2:
                    print(f"  FAIL {sym}: {exc}", flush=True)
                    return []
                time.sleep(2 * (attempt + 1))
        return []

    with ThreadPoolExecutor(max_workers=n_workers) as ex:
        results = list(ex.map(work, enumerate(syms)))

    flat = [r for rows in results for r in rows]
    df = pd.DataFrame(flat)
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values(["symbol", "date"]).reset_index(drop=True)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(OUT, index=False)

    print(f"\nrows      {len(df):,}")
    print(f"symbols   {df.symbol.nunique()}")
    print(f"range     {df.date.min().date()} -> {df.date.max().date()}")
    print(f"member    {df.is_member.sum():,} of {len(df):,}")
    print(f"elapsed   {time.time()-t0:.0f}s")

    # --- reproduction check against the documented cache (STATUS.md) ---
    exp_rows, exp_syms = 1_321_276, 724
    drift = abs(len(df) - exp_rows) / exp_rows
    print(f"\nrow drift vs documented cache: {drift:.4%} "
          f"({len(df):,} vs {exp_rows:,})")
    print(f"symbol match: {df.symbol.nunique()} vs {exp_syms}")
    if drift > 0.01:
        print("*** WARNING: >1% row drift. Do not trust comparisons against v1. ***")


if __name__ == "__main__":
    main()
