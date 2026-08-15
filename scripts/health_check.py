#!/usr/bin/env python3
"""
What is still true at the broker RIGHT NOW.

D34 audited what happens when the bot runs. D40 was missed by all 54 tests
because nobody audited what survives afterwards: the bracket was submitted
DAY, so every stop-loss was cancelled at the first close and 24 positions
worth $86,459 sat unprotected for nine days of a ten-day hold.

An order is not a fact. It is a fact with an expiry date.

This script asserts on persistence, not on acceptance. Read-only — it submits
nothing and writes nothing. Run it after every live session.

Exit 0 = healthy, 1 = something needs attention.
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.broker import PaperBroker, load_trading_credentials  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / "data" / "run_state.json"
STOP_PCT, MAX_HOLD_DAYS = 0.08, 10

PROBLEMS: list[str] = []
WARNINGS: list[str] = []


def check(label: str, ok: bool, detail: str = "", warn_only: bool = False,
          ok_detail: str = "") -> None:
    """
    `detail` explains a FAILURE and is printed only on failure. Printing the
    failure text next to a green tick reads as an alarm and trains you to
    ignore the output, which is how D40 survived 54 passing tests.
    """
    mark = "ok  " if ok else ("WARN" if warn_only else "FAIL")
    shown = ok_detail if ok else detail
    print(f"  [{mark}] {label}{('  — ' + shown) if shown else ''}")
    if not ok:
        (WARNINGS if warn_only else PROBLEMS).append(f"{label}: {detail}")


def main() -> int:
    broker = PaperBroker(load_trading_credentials(ROOT / ".env"))
    acct = broker.account()
    positions = broker.positions()
    orders = broker.orders(status="open", limit=500)
    today = datetime.now(timezone.utc).date()

    equity = float(acct["equity"])
    print(f"account {acct.get('account_number')}   "
          f"equity ${equity:,.2f}   cash ${float(acct['cash']):,.2f}")
    print(f"positions {len(positions)}   open orders {len(orders)}")
    print()

    # ---- 1. paper, and unblocked -----------------------------------------
    print("ACCOUNT")
    check("paper account", "PA" in str(acct.get("account_number", "")),
          detail=f"{acct.get('account_number')} does not look like a paper "
                 f"account — STOP",
          ok_detail=str(acct.get("account_number")))
    check("trading not blocked", not acct.get("trading_blocked", False))
    check("no accrued fees", float(acct.get("accrued_fees", 0) or 0) == 0,
          detail=f"${float(acct.get('accrued_fees', 0) or 0):,.2f}",
          warn_only=True)

    # ---- 2. THE D40 CHECK: is every position actually protected? ----------
    print("\nPROTECTION (D40 — the check that did not exist)")
    protected, sell_legs, tifs = set(), 0, set()
    for o in orders:
        for leg in [o] + list(o.get("legs") or []):
            if str(leg.get("side")) == "sell":
                protected.add(leg.get("symbol"))
                sell_legs += 1
            tifs.add(str(leg.get("time_in_force", "")).lower())

    naked = sorted({p["symbol"] for p in positions} - protected)
    exposure = 0.0
    for p in positions:
        if p["symbol"] in naked:
            last = float(p.get("current_price", 0) or 0)
            entry = float(p["avg_entry_price"])
            exposure += (last - entry * (1 - STOP_PCT)) * abs(float(p["qty"]))

    check("every position has a live protective order", not naked,
          detail=(f"{len(naked)} unprotected: {', '.join(naked[:8])}"
                  f"{'...' if len(naked) > 8 else ''}  "
                  f"(${exposure:,.0f} = {exposure/equity:.1%} of equity down to "
                  f"the intended stops)" if naked else ""),
          ok_detail=f"{len(positions)} positions, {sell_legs} legs")

    bad_tif = {t for t in tifs if t and t != "gtc"}
    check("every open order is GTC", not bad_tif,
          detail=f"found {sorted(bad_tif)} — DAY legs die at the close (D40)",
          ok_detail=f"{len(orders)} orders")

    # ---- 3. stale entries: the cost of GTC -------------------------------
    print("\nSTALE ORDERS (the cost of GTC — see cancel_stale_entries)")
    stale = [(o.get("symbol"), str(o.get("submitted_at", ""))[:10])
             for o in orders
             if str(o.get("side")) == "buy"
             and str(o.get("submitted_at", ""))[:10] < today.isoformat()]
    check("no unfilled BUY older than today", not stale,
          detail=f"{len(stale)} stale: {', '.join(s for s, _ in stale[:8])} — "
                 f"these can fill at a price the model never chose",
          ok_detail="none")

    # ---- 4. local state agrees with the broker ---------------------------
    print("\nSTATE FILE")
    if not STATE.exists():
        check("run_state.json exists", False, "missing — entry dates come from "
              "the broker on the next run, but the peak-equity ratchet resets")
    else:
        state = json.loads(STATE.read_text())
        broker_syms = {p["symbol"] for p in positions}
        state_syms = set(state.get("positions", {}))
        pending = {o.get("symbol") for o in orders if str(o.get("side")) == "buy"}
        ghosts = sorted(state_syms - broker_syms - pending)
        missing = sorted(broker_syms - state_syms)
        # A symbol whose entry order expired unfilled lingers in state until the
        # next run prunes it (prune_state_to_broker keeps only live | pending).
        # Self-healing, so it is a warning, not a failure — but it is reported,
        # because a state file that quietly disagrees with the broker is how
        # positions get orphaned (D34).
        check("state matches broker positions", not ghosts and not missing,
              detail=(f"ghosts in state (no position, no pending order): {ghosts}; "
                      f"held at broker but absent from state: {missing}. "
                      f"Ghosts clear on the next run; missing entries do not."),
              warn_only=not missing,
              ok_detail=f"{len(state_syms)} symbols")
        check("account number pinned and matching",
              state.get("account_number") == str(acct.get("account_number")),
              detail=f"state says {state.get('account_number')} — the peak-equity "
                     f"ratchet resets when this changes",
              ok_detail=str(acct.get("account_number")))
        no_date = [s for s, v in state.get("positions", {}).items()
                   if not v.get("entry_date")]
        check("every position has an entry date", not no_date,
              detail=f"missing for {no_date} — the time stop cannot fire "
                     f"without it")

        # ---- 5. anything overdue for the time stop? ----------------------
        print("\nTIME STOP")
        overdue = []
        for s, v in state.get("positions", {}).items():
            d = v.get("entry_date")
            if not d:
                continue
            held = (today - datetime.fromisoformat(d).date()).days
            if held > MAX_HOLD_DAYS + 4:      # +4 for weekends/holidays
                overdue.append(f"{s} ({held}d)")
        check(f"nothing held past {MAX_HOLD_DAYS} sessions", not overdue,
              detail=", ".join(overdue), warn_only=True)

    # ---- 6. is the schedule actually firing? -----------------------------
    print("\nSCHEDULE")
    log = ROOT / "data" / "trade_log.jsonl"
    if log.exists():
        lines = log.read_text().strip().splitlines()
        last = json.loads(lines[-1]) if lines else {}
        age = (datetime.now(timezone.utc)
               - datetime.fromisoformat(last["ts"])).days if last.get("ts") else 999
        check("bot has acted within the last 4 days", age <= 4,
              detail=f"last entry {age} days old — a quiet log is not a quiet "
                     f"market; check launchctl list | grep trading-bot",
              warn_only=True, ok_detail=f"last action {age}d ago")
    else:
        check("trade_log.jsonl exists", False,
              detail="no record of any run", warn_only=True)

    kill = ROOT / "HALT"
    check("kill switch not engaged", not kill.exists(),
          detail="HALT present — the bot will refuse to trade", warn_only=True)

    # ---- verdict ---------------------------------------------------------
    print("\n" + "=" * 72)
    if PROBLEMS:
        print(f"{len(PROBLEMS)} PROBLEM(S) — the account is not in the state the "
              f"backtest assumes:")
        for p in PROBLEMS:
            print(f"  - {p}")
        if naked:
            print("\n  To re-arm unprotected positions:")
            print("    python3 scripts/repair_brackets.py          # dry run")
            print("    python3 scripts/repair_brackets.py --live   # submit")
    else:
        print("Healthy. Everything the backtest assumes is actually standing.")
    if WARNINGS:
        print(f"\n{len(WARNINGS)} warning(s):")
        for w in WARNINGS:
            print(f"  - {w}")
    return 1 if PROBLEMS else 0


if __name__ == "__main__":
    raise SystemExit(main())
