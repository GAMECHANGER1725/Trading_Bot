#!/usr/bin/env python3
"""
The daily job. Step 5.

    python3 scripts/daily_run.py             # dry run, submits nothing,
                                             #          writes nothing
    python3 scripts/daily_run.py --live      # actually submits

SEQUENCE
    0. kill switch, before anything that can trade
    1. read broker state; prune local state to match it
    2. time-stop anything past the hold limit (cancel legs, close, verify)
    3. fetch bars, resolve the universe, score, build the order list
    4. run every guardrail
    5. submit, recording each order as it is accepted
    6. append to the trade log

-----------------------------------------------------------------------------
REWRITTEN 14 Aug 2026 after an audit found six blocking bugs. What changed and
why, because each one would have failed quietly:

  THE BROKER IS THE SOURCE OF TRUTH FOR POSITIONS.
  The previous version compared broker positions against a local state file and
  aborted on any difference. But a bracket hitting its target is a normal exit,
  so on day 4 the broker held 24 and state said 25, the check failed, and the
  run aborted — every run after it too. The bot would have traded once and gone
  silent for up to ten sessions. State is now PRUNED to the broker each run and
  holds only what the broker cannot tell us: the entry date, for the time stop.

  A DRY RUN WRITES NOTHING.
  `enforce_time_stop` used to drop symbols from state regardless of `--live`,
  and both exit paths saved. One preview run after day 10 orphaned every
  position permanently, with nothing sold. `save_state` is now a no-op unless
  `--live`.

  THE TIME STOP CANCELS THE BRACKET LEGS FIRST, AND VERIFIES.
  A filled bracket's stop and target legs reserve the shares, so liquidation
  was being rejected — and the symbol was dropped from state anyway. Positions
  are now only forgotten after the broker confirms the close.

  STATE IS SAVED AFTER EVERY ORDER, ATOMICALLY.
  A crash at order 12 of 25 previously left eleven real positions with no local
  record, and the idempotency guard then made that permanent on retry. Writes
  go to a temp file and are renamed, so a crash mid-write cannot truncate it.

  THE KILL SWITCH RUNS FIRST.
  It used to be checked at step 4, after the time stop had already sent live
  liquidations. `touch HALT` now stops everything.

  CASH IS CHECKED.
  Sizing is off equity, which includes open positions. The account reports
  `multiplier: 4`, so Alpaca would have accepted orders on margin without
  complaint. The backtest never borrows; live now cannot either.

-----------------------------------------------------------------------------
SCHEDULING — the earlier advice in this file was wrong.

It said pin the cron to UTC because local times drift with DST. Backwards: the
US session is defined in New York time, so a fixed UTC time is the thing that
drifts. `35 20 * * 1-5` is 16:35 ET in summer and **15:35 ET in winter, which
is twenty-five minutes BEFORE the close** — the newest daily bar would be
partial and market orders would fill intraday instead of at the next open.

Schedule in US Eastern and let the OS handle DST:

    CRON_TZ=America/New_York
    35 16 * * 1-5  cd /path/to/Trading_Bot && python3 scripts/daily_run.py --live

`check_session_timing` asks the broker whether the market is open and refuses
to run if it is, so a bad schedule is caught rather than traded through.
-----------------------------------------------------------------------------
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date as Date
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402
import xgboost as xgb  # noqa: E402

from src import guardrails as G  # noqa: E402
from src.broker import PaperBroker, load_trading_credentials  # noqa: E402
from src.data_layer import AlpacaBars, Universe, day_start, load_credentials, utc_now  # noqa: E402
from src.features import FEATURE_NAMES, build  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
LOG = DATA / "trade_log.jsonl"
STATE = DATA / "run_state.json"

N_POSITIONS = G.MAX_POSITIONS
STOP_PCT, TARGET_PCT, MAX_HOLD = 0.08, 0.12, 10
LOOKBACK_DAYS = 420

# D35. The entry is a LIMIT at the prior close plus this slack, not a market
# order. The stop and target have to be named in dollars tonight off the prior
# close, so a market fill at tomorrow's open silently rewrites the bracket: a
# +2% gap turns 8%-risk / 12%-reward into 9.8% / 9.8%. The limit caps the worst
# case at 8.46% / 11.44%. Fills below reference only make the bracket better.
# Cost, measured 2022-2024: 4 skipped trades out of 1,982.
LIMIT_SLACK = 0.005


# ---------------------------------------------------------------------------
# state — entry dates and peak equity only. Positions come from the broker.
# ---------------------------------------------------------------------------

def load_state() -> dict:
    if not STATE.exists():
        return {"peak_equity": 0.0, "positions": {}, "runs": 0}
    try:
        return json.loads(STATE.read_text())
    except json.JSONDecodeError:
        # A truncated file used to brick every future run. Rebuild instead.
        print(f"WARNING: {STATE.name} is corrupt. Rebuilding from the broker.")
        return {"peak_equity": 0.0, "positions": {}, "runs": 0}


def save_state(s: dict, *, live: bool) -> None:
    """Atomic, and a no-op on a dry run."""
    if not live:
        return
    tmp = STATE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(s, indent=2, default=str))
    os.replace(tmp, STATE)


def log_event(kind: str, payload: dict) -> None:
    rec = {"ts": datetime.now(timezone.utc).isoformat(), "kind": kind, **payload}
    with LOG.open("a") as fh:
        fh.write(json.dumps(rec, default=str) + "\n")
        fh.flush()
        os.fsync(fh.fileno())


def symbols_ever_ordered() -> set[str]:
    """
    Every symbol this strategy has ever sent an order for, from the append-only
    log rather than from state. A crash between submitting and saving state
    must not make a real position look unexplained.
    """
    if not LOG.exists():
        return set()
    out = set()
    for line in LOG.read_text().splitlines():
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        if r.get("kind") == "order" and r.get("symbol") and r.get("live"):
            out.add(r["symbol"])
    return out


def entry_dates_from_broker(broker: PaperBroker) -> dict[str, str]:
    """
    Reconstruct entry dates from the broker's own filled-order history.

    The state file holds entry dates because an Alpaca position does not carry
    one, and the time stop needs it. But that made the file the single point of
    failure for the whole time stop: lose it — a wiped machine, a container
    that does not persist, running the bot from somewhere new — and every open
    position becomes permanently exempt, because `enforce_time_stop` skips any
    entry whose date is unknown. Silent, and it only shows up as positions that
    never age out.

    Filled buy orders persist on the account regardless of where the bot runs,
    so the entry date is recoverable. With this, `run_state.json` is a cache
    rather than a source of truth, and the bot can move machines without
    orphaning anything.
    """
    out: dict[str, str] = {}
    try:
        closed = broker.orders(status="closed", limit=500)
    except Exception:                       # noqa: BLE001
        return out
    for o in closed:
        for leg in [o] + list(o.get("legs") or []):
            if (leg.get("side") == "buy" and leg.get("filled_at")
                    and float(leg.get("filled_qty") or 0) > 0):
                sym, when = leg["symbol"], leg["filled_at"][:10]
                # keep the most recent buy fill per symbol
                if sym not in out or when > out[sym]:
                    out[sym] = when
    return out


def cancel_stale_entries(broker: PaperBroker, today: str, *, live: bool) -> list[str]:
    """
    Cancel unfilled BUY orders left over from a previous session.

    Required by the switch to GTC brackets (see broker.submit_bracket). With
    DAY orders an unfilled entry expired at the close and cleaned itself up —
    GE did exactly that on 14 Aug 2026, correctly. Under GTC it would instead
    sit there and could fill days later at a limit price the model set against
    a stale reference close, taking a position nothing in today's ranking asked
    for and skewing the size of the book.

    Protective SELL legs are deliberately left alone. They are the whole point
    of GTC and must survive.

    Safe to run here because the job executes after the US close: a cancelled
    order cannot fill overnight, and today's fresh orders are submitted later
    in the same run.
    """
    stale = []
    for o in broker.orders(status="open", limit=500):
        if str(o.get("side")) != "buy":
            continue
        submitted = str(o.get("submitted_at", ""))[:10]
        if submitted and submitted < today:
            stale.append((o.get("symbol"), o.get("id"), submitted))
    if not stale or not live:
        return [s for s, _, _ in stale]
    done = []
    for sym, oid, when in stale:
        try:
            r = broker.session.delete(f"{broker.base}/v2/orders/{oid}", timeout=30)
            if r.status_code in (200, 204):
                done.append(sym)
                log_event("stale_entry_cancelled",
                          {"symbol": sym, "submitted": when, "live": live})
        except Exception as e:                                  # noqa: BLE001
            print(f"  WARNING: could not cancel stale {sym}: {e}")
    return done


def prune_state_to_broker(state: dict, positions: list[dict],
                          broker: PaperBroker) -> tuple[list[str], list[str]]:
    """
    Drop local entries the broker no longer holds — normal bracket exits — and
    fill any missing entry date from the broker's order history.

    A symbol with a live UNFILLED order is not an exit. Orders submitted after
    the close sit as `accepted` until the next open, so a second run the same
    evening would otherwise see "no position" and wipe the entry it had just
    written. The recovery path repairs that later, but churning state on every
    re-run makes the file lie in the meantime.

    Returns (exited, recovered).
    """
    live_syms = {p["symbol"] for p in positions}
    try:
        pending = {o["symbol"] for o in broker.orders(status="open", limit=500)}
    except Exception:                       # noqa: BLE001
        pending = set()
    keep = live_syms | pending
    gone = [s for s in state["positions"] if s not in keep]
    for s in gone:
        state["positions"].pop(s, None)

    needs_date = [p["symbol"] for p in positions
                  if not state["positions"].get(p["symbol"], {}).get("entry_date")]
    from_broker = entry_dates_from_broker(broker) if needs_date else {}

    recovered = []
    for p in positions:
        sym = p["symbol"]
        cur = state["positions"].setdefault(sym, {"entry_date": None,
                                                  "qty": p["qty"]})
        if not cur.get("entry_date") and sym in from_broker:
            cur["entry_date"] = from_broker[sym]
            recovered.append(f"{sym}@{from_broker[sym]}")
        cur["qty"] = p["qty"]
    return gone, recovered


# ---------------------------------------------------------------------------
# pipeline
# ---------------------------------------------------------------------------

def fetch_recent(symbols: list[str], days: int) -> tuple[pd.DataFrame, int]:
    """Recent bars. feed=sip, adjustment=all (D6/D7). Returns (bars, n_failed)."""
    client = AlpacaBars(headers=load_credentials(ROOT / ".env"), pause=0.05)
    start = day_start((datetime.now(timezone.utc) - timedelta(days=days))
                      .strftime("%Y-%m-%d"))
    end = utc_now(minutes_ago=30)

    from concurrent.futures import ThreadPoolExecutor
    failures: list[str] = []

    def one(sym):
        try:
            return [{"symbol": sym, "date": b["t"][:10], "open": b["o"],
                     "high": b["h"], "low": b["l"], "close": b["c"],
                     "volume": b["v"]} for b in
                    client.daily(sym, start, end, feed="sip", adjustment="all")]
        except Exception as exc:            # noqa: BLE001
            # Previously swallowed silently, which let the universe shrink to
            # "whatever responded" without any check noticing.
            failures.append(f"{sym}: {type(exc).__name__}")
            return []

    with ThreadPoolExecutor(max_workers=6) as ex:
        rows = [r for batch in ex.map(one, symbols) for r in batch]

    if failures:
        print(f"WARNING: {len(failures)} symbols failed to fetch: "
              f"{', '.join(failures[:6])}{' ...' if len(failures) > 6 else ''}")
    df = pd.DataFrame(rows)
    if df.empty:
        return df, len(failures)
    df["date"] = pd.to_datetime(df["date"])
    df["is_member"] = True
    return df.sort_values(["symbol", "date"]).reset_index(drop=True), len(failures)


def top_by_liquidity(bars: pd.DataFrame, members: set[str], n: int = 100) -> list[str]:
    recent = bars[bars.date >= bars.date.max() - pd.Timedelta(days=90)]
    recent = recent[recent.symbol.isin(members)]
    dv = (recent.assign(dv=recent.close * recent.volume)
                .groupby("symbol")["dv"].mean().sort_values(ascending=False))
    return list(dv.head(n).index)


def score_universe(bars: pd.DataFrame) -> pd.DataFrame:
    meta = json.loads((DATA / "model_prod_meta.json").read_text())
    assert meta["features"] == FEATURE_NAMES, (
        "Feature order changed since the model was trained. XGBoost would "
        "score silently wrong columns. Retrain or fix the order."
    )
    feats = build(bars)
    if feats.empty:
        return feats
    latest = feats[feats.date == feats.date.max()].copy()
    m = xgb.XGBClassifier()
    m.load_model(str(DATA / "model_prod.json"))
    latest["score"] = m.predict_proba(latest[FEATURE_NAMES])[:, 1]
    return latest.sort_values("score", ascending=False)


def enforce_time_stop(broker: PaperBroker, state: dict, positions: list[dict],
                      today: Date, live: bool) -> tuple[list[str], list[str]]:
    """
    Close anything held longer than MAX_HOLD sessions.

    Alpaca's bracket has no time limit, so without this the live strategy
    diverges from the backtested one a little more every week with nothing
    raising an error.

    Only symbols the broker CONFIRMS closed are dropped from state. A rejected
    close leaves the entry in place so the next run retries it, instead of
    orphaning a real position forever.
    """
    closed, failed = [], []
    broker_syms = {p["symbol"] for p in positions}
    for sym, info in list(state["positions"].items()):
        if sym not in broker_syms or not info.get("entry_date"):
            continue
        entry = pd.Timestamp(info["entry_date"]).date()
        held = len(pd.bdate_range(entry, today)) - 1
        if held < MAX_HOLD:
            continue
        f = broker.close_position(sym, live=live)
        log_event("time_stop", {"symbol": sym, "held_sessions": held,
                                "status": f.status, "submitted": f.submitted,
                                "error": f.error, "live": live})
        if f.submitted and live:
            closed.append(sym)
            state["positions"].pop(sym, None)
        elif live:
            failed.append(f"{sym} ({f.status})")
        else:
            closed.append(f"{sym} [dry]")
    return closed, failed


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true",
                    help="actually submit orders (default is a dry run)")
    ap.add_argument("--universe", type=int, default=100)
    args = ap.parse_args()
    live = args.live

    now = datetime.now(timezone.utc)
    today = now.date()
    print(f"{'LIVE' if live else 'DRY RUN'}  {now.isoformat(timespec='seconds')}")
    print("=" * 72)

    # ---- 0. kill switch, BEFORE anything that can trade -------------------
    ks = G.check_kill_switch(ROOT)
    print(str(ks))
    if not ks.passed:
        log_event("abort", {"reason": "HALT file", "live": live})
        return 3

    state = load_state()
    broker = PaperBroker(load_trading_credentials(ROOT / ".env"))

    # ---- 1. broker state, then prune local state to match -----------------
    acct = broker.account()
    positions = broker.positions()
    equity, cash = float(acct["equity"]), float(acct["cash"])
    held_value = sum(abs(float(p.get("market_value", 0))) for p in positions)

    # peak_equity is a one-way ratchet feeding check_drawdown, so a single bad
    # reading halts the bot forever with no way back except editing JSON.
    # Two poisoning routes, both closed here:
    #   - Alpaca's paper reset lets you pick a new starting balance. Reset to
    #     $1M, run once, reset back to $100k, and drawdown reads -90% forever.
    #   - Pointing .env at a different, larger paper account for one run.
    # The account number is now pinned, and the peak resets when it changes.
    acct_no = str(acct.get("account_number", ""))
    if state.get("account_number") and state["account_number"] != acct_no:
        print(f"account changed {state['account_number']} -> {acct_no}; "
              f"resetting peak equity")
        log_event("account_changed", {"from": state["account_number"],
                                      "to": acct_no, "live": live})
        state["peak_equity"] = 0.0
        state["positions"] = {}
    state["account_number"] = acct_no
    prev_peak = state.get("peak_equity", 0.0)
    if prev_peak and equity > prev_peak * 3:
        print(f"equity ${equity:,.0f} is more than 3x the recorded peak "
              f"${prev_peak:,.0f} — treating as an account reset, not a gain")
        log_event("peak_reset", {"old": prev_peak, "new": equity, "live": live})
        state["peak_equity"] = 0.0
    state["peak_equity"] = max(state.get("peak_equity", 0.0), equity)

    # Brackets are GTC so their protective legs survive the close; the cost is
    # that an unfilled ENTRY also survives. Clear yesterday's before ranking.
    stale = cancel_stale_entries(broker, today.isoformat(), live=live)
    if stale:
        print(f"cancelled {len(stale)} stale unfilled entry order(s): "
              f"{', '.join(sorted(stale))}")

    exited, recovered = prune_state_to_broker(state, positions, broker)
    print(f"equity ${equity:,.2f}   cash ${cash:,.2f}   "
          f"positions {len(positions)} (${held_value:,.0f})   "
          f"peak ${state['peak_equity']:,.2f}")
    if exited:
        print(f"exited since last run (bracket or manual): {', '.join(exited)}")
        log_event("exits_detected", {"symbols": exited, "live": live})
    if recovered:
        print(f"entry dates recovered from broker order history: "
              f"{', '.join(recovered)}")
        log_event("entry_dates_recovered", {"entries": recovered, "live": live})

    # Any position the broker holds whose entry date could not be recovered is
    # invisible to the time stop. Surface it rather than let it age silently.
    undated = [p["symbol"] for p in positions
               if not state["positions"].get(p["symbol"], {}).get("entry_date")]
    if undated:
        print(f"WARNING: no entry date for {', '.join(undated)} — these are "
              f"NOT covered by the time stop until one is found")

    # ---- 2. time stop -----------------------------------------------------
    closed, failed_close = enforce_time_stop(broker, state, positions, today, live)
    if closed:
        print(f"time-stopped: {', '.join(closed)}")
    if failed_close:
        print(f"TIME STOP FAILED for: {', '.join(failed_close)} — kept in "
              f"state so the next run retries")
    if closed and live:
        positions = broker.positions()
        held_value = sum(abs(float(p.get("market_value", 0))) for p in positions)

    # ---- 3. universe, data, scoring ---------------------------------------
    uni = Universe(DATA / "sp500_history.csv")
    as_of = min(today.isoformat(), uni.snapshot_dates[-1])
    members = uni.members_on(as_of)
    candidates = sorted(members - {"STI", "TE", "WYND", "AABA"})
    print(f"universe: {len(candidates)} members as of {as_of}")

    bars, n_failed = fetch_recent(candidates, LOOKBACK_DAYS)
    if bars.empty:
        print("FATAL: no bars returned")
        log_event("abort", {"reason": "no bars", "live": live})
        save_state(state, live=live)
        return 1
    liquid = top_by_liquidity(bars, members, args.universe)
    bars = bars[bars.symbol.isin(liquid)]
    print(f"fetched {len(bars):,} bars, {bars.symbol.nunique()} symbols, "
          f"newest {bars.date.max().date()}")

    scored = score_universe(bars)

    # ---- 4. order list ----------------------------------------------------
    held = {p["symbol"] for p in positions}
    room = max(N_POSITIONS - len(held), 0)

    issuer_groups = G.load_issuer_groups(DATA / "issuer_groups.json")
    eligible = scored[~scored.symbol.isin(held)]
    ranked = list(eligible.symbol)
    keep = G.collapse_to_one_per_issuer(ranked, issuer_groups)
    held_groups = {issuer_groups[s] for s in held if s in issuer_groups}
    keep = [s for s in keep
            if issuer_groups.get(s) is None or issuer_groups[s] not in held_groups]
    dropped = [s for s in ranked[:max(room, 1) * 2] if s not in set(keep)]
    if dropped:
        print(f"issuer filter dropped: {', '.join(dropped[:8])}")

    picks = (eligible[eligible.symbol.isin(keep)]
             .set_index("symbol").loc[keep].reset_index().head(room))
    per_name = equity * G.MAX_GROSS_EXPOSURE / N_POSITIONS

    # A limit entry makes the maximum spend exactly knowable, so the cash
    # budget is against the limit price rather than a guessed gap allowance.
    orders, budget_left = [], cash
    for _, r in picks.iterrows():
        px = float(r["close"])
        lim = round(px * (1 + LIMIT_SLACK), 2)
        qty = int(per_name // lim)
        if qty < 1 or qty * lim > budget_left:
            continue
        budget_left -= qty * lim
        orders.append({"symbol": r["symbol"], "qty": qty, "ref_price": px,
                       "limit_price": lim, "score": float(r["score"])})

    # ---- 5. guardrails ----------------------------------------------------
    cal = broker.calendar((today - timedelta(days=2)).isoformat(),
                          (today + timedelta(days=10)).isoformat())
    checks = [
        ks,
        G.check_paper_account(acct),
        G.check_equity(acct),
        G.check_drawdown(acct, state["peak_equity"]),
        G.check_session_timing(now, broker.clock()),
        G.check_market_calendar(broker.clock(), cal, today),
        G.check_bar_freshness(bars, now),
        G.check_universe_size(len(scored)),
        G.check_unexpected_positions(positions, symbols_ever_ordered()),
        G.check_position_cap(len(held), len(orders)),
        G.check_issuer_concentration([o["symbol"] for o in orders], held,
                                     issuer_groups),
        G.check_cash(acct, orders),
        G.check_order_sanity(orders, equity, held_value),
    ]
    if failed_close:
        checks.append(G.Check("time_stop_completed", False,
                              f"could not close {', '.join(failed_close)} — "
                              f"resolve before opening new positions"))
    print("\n" + G.format_report(checks))
    allowed, _ = G.run_all(checks)

    log_event("preflight", {"live": live, "allowed": allowed, "equity": equity,
                            "cash": cash, "fetch_failures": n_failed,
                            "checks": [{"name": c.name, "passed": c.passed,
                                        "message": c.message} for c in checks]})

    if not allowed:
        print("\nNo orders submitted.")
        save_state(state, live=live)
        return 2

    # ---- 6. submit, saving state after each acceptance --------------------
    print(f"\n{'submitting' if live else 'would submit'} {len(orders)} orders:")
    for o in orders:
        try:
            f = broker.submit_bracket(o["symbol"], o["qty"], STOP_PCT, TARGET_PCT,
                                      o["ref_price"], today, live=live,
                                      limit_slack=LIMIT_SLACK)
        except Exception as exc:            # noqa: BLE001
            # Do not retry: a blind retry is how one order becomes two.
            print(f"  {o['symbol']:<6} EXCEPTION {type(exc).__name__} — "
                  f"stopping. State up to here is saved.")
            log_event("submit_exception", {"symbol": o["symbol"],
                                           "error": repr(exc), "live": live})
            save_state(state, live=live)
            return 4
        print(f"  {o['symbol']:<6} {o['qty']:>5} lim ${o['limit_price']:>8.2f}  "
              f"stop ${o['ref_price']*(1-STOP_PCT):>8.2f}  "
              f"tgt ${o['ref_price']*(1+TARGET_PCT):>8.2f}  -> {f.status}"
              f"{'  ' + f.error if f.error else ''}")
        log_event("order", {"symbol": o["symbol"], "qty": o["qty"],
                            "ref_price": o["ref_price"],
                            "limit_price": o["limit_price"], "score": o["score"],
                            "status": f.status, "client_order_id": f.client_order_id,
                            "error": f.error, "live": live})
        if f.submitted and live:
            state["positions"][o["symbol"]] = {"entry_date": today.isoformat(),
                                               "qty": o["qty"]}
            save_state(state, live=live)     # after EVERY order, not at the end

    state["runs"] = state.get("runs", 0) + 1
    save_state(state, live=live)
    print(f"\nstate: {len(state['positions'])} tracked, run #{state['runs']}"
          f"{'  [dry run — nothing written]' if not live else ''}")
    print(f"log:   {LOG}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
