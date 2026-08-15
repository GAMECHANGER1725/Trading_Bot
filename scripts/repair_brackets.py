#!/usr/bin/env python3
"""
One-time repair: re-attach protective legs to positions that lost them.

WHY THIS EXISTS
On 14 Aug 2026 the first live run submitted 25 brackets with
`time_in_force: "day"`. Alpaca's bracket child legs inherit the parent's TIF,
so at that session's close every take-profit leg EXPIRED and every stop-loss
leg was CANCELLED as its OCO sibling. The account was left holding $86,459 of
stock across 24 names with zero open orders — no stop, no target.

D40 fixed the cause (`submit_bracket` now sends "gtc"). New entries are
protected from submission. This script repairs the positions opened BEFORE
the fix; nothing else re-arms them.

WHAT IT DOES
For each position with no open sell order, submits one OCO sell for the full
quantity: stop at entry * (1 - stop_pct), limit at entry * (1 + target_pct).
Anchored to the ACTUAL average fill price, which is the correct anchor and the
one the backtest assumes (D35).

Dry run by default. Pass --live to submit.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.broker import PaperBroker, load_trading_credentials  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
STOP_PCT, TARGET_PCT = 0.08, 0.12


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true", help="actually submit")
    args = ap.parse_args()
    live = args.live

    broker = PaperBroker(load_trading_credentials(ROOT / ".env"))

    acct = broker.account()
    positions = broker.positions()
    open_orders = broker.orders(status="open", limit=500)

    protected = set()
    for o in open_orders:
        for leg in [o] + list(o.get("legs") or []):
            if str(leg.get("side")) == "sell":
                protected.add(leg.get("symbol"))

    naked = [p for p in positions if p["symbol"] not in protected]
    print(f"{'LIVE' if live else 'DRY RUN'}  account {acct.get('account_number')}")
    print(f"equity ${float(acct['equity']):,.2f}   positions {len(positions)}   "
          f"open orders {len(open_orders)}")
    print(f"positions WITH protection    : {len(positions) - len(naked)}")
    print(f"positions WITHOUT protection : {len(naked)}")
    if not naked:
        print("\nNothing to repair.")
        return 0

    print(f"\n{'sym':6} {'qty':>5} {'entry':>10} {'last':>10} {'stop':>10} "
          f"{'target':>10} {'status'}")
    exposure = 0.0
    for p in sorted(naked, key=lambda x: x["symbol"]):
        sym = p["symbol"]
        qty = int(float(p["qty"]))
        entry = float(p["avg_entry_price"])
        last = float(p.get("current_price", entry))
        stop = round(entry * (1 - STOP_PCT), 2)
        target = round(entry * (1 + TARGET_PCT), 2)
        exposure += (last - stop) * qty

        if stop >= last:
            print(f"{sym:6} {qty:>5} {entry:>10.2f} {last:>10.2f} {stop:>10.2f} "
                  f"{target:>10.2f} SKIPPED — already below the stop, an OCO "
                  f"would fire instantly. Decide manually.")
            continue

        if not live:
            print(f"{sym:6} {qty:>5} {entry:>10.2f} {last:>10.2f} {stop:>10.2f} "
                  f"{target:>10.2f} would submit")
            continue

        body = {
            "symbol": sym, "qty": str(qty), "side": "sell",
            "type": "limit", "limit_price": str(target),
            "time_in_force": "gtc", "order_class": "oco",
            "client_order_id": f"repair-{sym}",
            "take_profit": {"limit_price": target},
            "stop_loss": {"stop_price": stop},
        }
        r = broker.session.post(f"{broker.base}/v2/orders", json=body, timeout=30)
        ok = r.status_code in (200, 201)
        note = "SUBMITTED" if ok else f"FAILED HTTP {r.status_code}: {r.text[:120]}"
        print(f"{sym:6} {qty:>5} {entry:>10.2f} {last:>10.2f} {stop:>10.2f} "
              f"{target:>10.2f} {note}")

    print(f"\nunprotected downside to the intended stops: ${exposure:,.0f} "
          f"({exposure/float(acct['equity']):.1%} of equity)")
    if not live:
        print("\nDry run. Nothing was submitted. Re-run with --live to repair.")
    else:
        after = broker.orders(status="open", limit=500)
        print(f"\nopen orders now: {len(after)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
