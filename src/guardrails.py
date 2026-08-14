"""
Pre-flight checks. Every one must pass before a single order is allowed.

The project's stated primary risk is "building something large and plausible
that is silently broken — a wrong trading bot doesn't crash, it loses money
quietly". The backtest engine cannot test any of this, because none of it
exists inside a backtest: there is no stale data in a backtest, no duplicate
cron fire, no broker disagreeing about what you own, no half-filled bracket.

So this file is where the project's whole thesis gets cashed out. Each check
below exists because of a specific way a scheduled trading job fails quietly.

DESIGN
  A check returns (passed, message). The runner aborts on the FIRST failure
  and submits nothing. There is no "warn and continue" tier, because a warning
  at 06:30 with nobody watching is the same as no warning.

  Every check is written so that the failing branch is the one that runs when
  something is unavailable. A check that cannot get its data FAILS. Missing
  information is treated as a problem, never as permission.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date as Date
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

# ---------------------------------------------------------------------------
# limits — all explicit, none derived at runtime
# ---------------------------------------------------------------------------
MAX_POSITIONS = 25            # D31: below 25 the backtest measures name luck
MAX_POSITION_PCT = 0.05       # no single name above 5% of equity
MAX_GROSS_EXPOSURE = 0.95     # keep a cash buffer for slippage on entry
MAX_ORDERS_PER_RUN = 25       # a run that wants more than this is malfunctioning
MAX_DRAWDOWN_HALT = 0.25      # stop opening new positions past -25% from peak
MAX_BAR_AGE_DAYS = 4          # weekend + one holiday; older means stale data
MIN_EQUITY = 50_000.0         # half the starting balance — something is wrong


@dataclass
class Check:
    name: str
    passed: bool
    message: str

    def __str__(self) -> str:
        return f"[{'PASS' if self.passed else 'FAIL'}] {self.name:<22} {self.message}"


# ---------------------------------------------------------------------------
# individual checks
# ---------------------------------------------------------------------------

def check_kill_switch(root: Path) -> Check:
    """
    A file named HALT in the project root stops everything.

    Deliberately the crudest possible mechanism. When something is going wrong
    at 3am your time and you are on a phone, `touch HALT` over SSH has to work
    without the bot being healthy enough to read a config, hit an API, or parse
    anything. The check is "does this path exist".
    """
    halt = root / "HALT"
    if halt.exists():
        reason = halt.read_text().strip() or "(no reason given)"
        return Check("kill_switch", False, f"HALT file present: {reason}")
    return Check("kill_switch", True, "no HALT file")


def check_paper_account(account: dict) -> Check:
    """
    The account itself must self-report as paper.

    The key prefix and the base URL are already checked in broker.py. This is
    a third, independent confirmation from the other side of the connection,
    because the two local checks share a single point of failure: a .env file
    someone edited.
    """
    num = str(account.get("account_number", ""))
    status = account.get("status", "")
    if account.get("crypto_status") is None and not num:
        return Check("paper_account", False, "no account number returned")
    if status != "ACTIVE":
        return Check("paper_account", False, f"account status is {status!r}")
    return Check("paper_account", True, f"{num}, status {status}")


def check_equity(account: dict) -> Check:
    eq = float(account.get("equity", 0))
    if eq < MIN_EQUITY:
        return Check("equity", False,
                     f"${eq:,.0f} is below the ${MIN_EQUITY:,.0f} floor — "
                     f"investigate before trading again")
    return Check("equity", True, f"${eq:,.2f}")


def check_drawdown(account: dict, peak_equity: float) -> Check:
    """
    Stop opening new positions past -25% from the running peak.

    Existing positions keep their brackets — this halts entries, it does not
    liquidate. Liquidating into a drawdown is how a drawdown becomes a loss.
    """
    eq = float(account.get("equity", 0))
    if peak_equity <= 0:
        return Check("drawdown", True, "no history yet")
    dd = eq / peak_equity - 1
    if dd < -MAX_DRAWDOWN_HALT:
        return Check("drawdown", False,
                     f"{dd:.1%} from peak ${peak_equity:,.0f} — past the "
                     f"{-MAX_DRAWDOWN_HALT:.0%} halt. No new entries.")
    return Check("drawdown", True, f"{dd:+.1%} from peak")


def check_market_calendar(clock: dict, calendar: list[dict],
                          run_date: Date) -> Check:
    """
    There must be a trading session tomorrow for a `tif=day` order to fill in.

    Submitting a day order before a holiday means it expires unfilled, and an
    unfilled bracket is not an error anywhere — it is just a day the bot
    silently did nothing while appearing to work.
    """
    if not clock or not clock.get("next_open"):
        return Check("market_calendar", False,
                     "clock returned no next_open — cannot confirm a session "
                     "exists for the order to fill in")
    if not calendar:
        return Check("market_calendar", False,
                     "calendar request returned nothing")

    # Use the broker's own next_open rather than scanning dates. The earlier
    # version filtered for sessions strictly AFTER today, which reported
    # "next session Monday" while the order was in fact going to fill at
    # Friday's open a few hours later — right answer to the wrong question,
    # and it would have passed just as happily on a genuine four-day holiday.
    nxt = str(clock["next_open"])[:10]
    try:
        gap = (datetime.fromisoformat(nxt).date() - run_date).days
    except ValueError:
        return Check("market_calendar", False, f"unparseable next_open {nxt!r}")
    if gap > 4:
        return Check("market_calendar", False,
                     f"next open is {nxt}, {gap} days away — a tif=day order "
                     f"submitted now expires unfilled and logs as success")
    return Check("market_calendar", True,
                 f"next open {nxt} ({gap}d), order will fill there")


def check_bar_freshness(bars: pd.DataFrame, now_utc: datetime) -> Check:
    """
    The most recent bar must be recent.

    D10's scheduling note: the 06:30 Sydney run lands ~30 minutes after the
    US close, and DST shifts on both sides during the year. If the schedule
    drifts, or Alpaca's 15-minute SIP lag has not elapsed, the fetch returns
    yesterday's data and the bot trades on a stale ranking without any error
    being raised. Assert the bar arrived rather than assuming it.
    """
    if bars.empty:
        return Check("bar_freshness", False, "no bars at all")
    # Normalise BOTH sides to naive UTC. The fetch path produces naive dates
    # (sliced from the RFC3339 string), but a caller handing in tz-aware
    # timestamps would otherwise raise TypeError here — inside the check whose
    # whole job is to catch a stale feed. A guardrail that crashes is a
    # guardrail that does not run.
    last = pd.to_datetime(bars["date"]).max()
    if last.tzinfo is not None:
        last = last.tz_convert("UTC").tz_localize(None)
    age = (now_utc.replace(tzinfo=None) - last.to_pydatetime()).days
    if age > MAX_BAR_AGE_DAYS:
        return Check("bar_freshness", False,
                     f"newest bar is {last.date()}, {age} days old — the fetch "
                     f"is stale or the schedule has drifted (D10)")
    return Check("bar_freshness", True, f"newest bar {last.date()}, {age}d old")


def check_universe_size(n_scored: int, expected_min: int = 80) -> Check:
    """
    Scoring far fewer names than expected means the universe resolution or the
    feature build silently dropped most of it. The ranking would still look
    perfectly valid — just computed over the wrong population.
    """
    if n_scored < expected_min:
        return Check("universe_size", False,
                     f"only {n_scored} names scored, expected >= {expected_min}")
    return Check("universe_size", True, f"{n_scored} names scored")


def check_unexpected_positions(broker_positions: list[dict],
                               ever_ordered: set[str]) -> Check:
    """
    The BROKER is the source of truth for what we hold. This checks only for
    positions we have no record of ever ordering.

    Replaces an earlier `check_reconciliation` that demanded exact equality
    between the broker and a local state file. That was wrong, and badly so:
    a bracket hitting its target is a NORMAL exit, so on day 4 the broker held
    24 while local state still said 25, the check failed, and the runner
    aborted. Every subsequent run failed identically. The bot would have traded
    once and then gone silent for up to ten sessions with nothing but a
    non-zero exit code to show for it.

    Normal exits are now handled by pruning local state to match the broker.
    What genuinely warrants stopping is the other direction: the broker holding
    something that was never ordered by this strategy — a manual trade in the
    web UI, a corporate action creating a new line, or a run that submitted
    orders and died before recording them.

    `ever_ordered` is read from the append-only trade log, not from the state
    file, so a crash between submitting and saving state cannot make a real
    position look unexplained.
    """
    broker_syms = {p["symbol"] for p in broker_positions}
    unexplained = broker_syms - ever_ordered
    if unexplained:
        return Check("unexpected_positions", False,
                     f"held but never ordered by this strategy: "
                     f"{sorted(unexplained)}")
    return Check("unexpected_positions", True,
                 f"{len(broker_syms)} broker positions, all accounted for")


def check_cash(account: dict, orders: list[dict]) -> Check:
    """
    Enough settled cash to pay for the new orders, without borrowing.

    The backtest refuses an order it cannot afford (`backtest.py`: `if qty < 1
    or cost > cash: continue`). Live had no equivalent, and the paper account
    reports `multiplier: 4` with `buying_power` around 4x equity — so Alpaca
    would have accepted orders on margin, silently, well past anything the
    backtest can produce. That is a divergence that flatters live results and
    raises real risk, with no error anywhere.

    Sizing is off `equity`, which includes the value of positions already held,
    so the shortfall appears exactly when the bot is already near fully
    invested.
    """
    if not orders:
        return Check("cash", True, "no orders to fund")
    cash = float(account.get("cash", 0))
    # With a limit entry the maximum spend is exact: you cannot pay more than
    # the limit. A market entry has no such bound, so it keeps a gap allowance.
    need = sum(o["qty"] * o.get("limit_price", o["ref_price"] * 1.05)
               for o in orders)
    if need > cash:
        return Check("cash", False,
                     f"need ${need:,.0f} but only ${cash:,.0f} settled cash — "
                     f"this would trade on margin, which the backtest never does")
    return Check("cash", True,
                 f"${need:,.0f} max spend, ${cash:,.0f} available")


def check_session_timing(now_utc: datetime, clock: dict) -> Check:
    """
    The run must happen AFTER the US close, never during the session.

    The schedule was originally pinned to UTC on the reasoning that a local
    time drifts with DST. That reasoning is backwards: the US session is
    defined in New York time, so a fixed UTC time is what drifts. 20:35 UTC is
    16:35 ET in summer and **15:35 ET in winter — twenty-five minutes before
    the close**. Running then means the newest daily bar is a partial,
    in-progress bar, and market orders fill immediately intraday instead of at
    the next open. `check_bar_freshness` cannot catch it: the bar carries
    today's date and reads as one day old.

    The broker's own clock is the authority, so this holds whatever the cron
    says and whichever side of a DST change either country is on.
    """
    if not clock:
        return Check("session_timing", False,
                     "clock request returned nothing — cannot confirm the "
                     "market is closed")
    if clock.get("is_open"):
        return Check("session_timing", False,
                     "US market is OPEN. This job must run after the close: "
                     "market orders would fill intraday and the newest daily "
                     "bar is still forming.")
    return Check("session_timing", True, "market closed")


def check_order_sanity(orders: list[dict], equity: float,
                       held_value: float = 0.0) -> Check:
    """
    Last line before submission: are the orders plus what we already hold
    plausible as a whole portfolio?

    An earlier version of this check was theatre. Because sizing is
    `equity * 0.95 / 25 = 3.8%` per name, the per-name test (`> 5.25%`) and the
    gross test (`> 95%`, against a sum that integer rounding caps below 95%)
    were both *structurally unreachable*. The tests proved the checks worked on
    synthetic inputs; they never proved the checks could fire on real ones. A
    guardrail that cannot fire is a comment.

    Two fixes:

      * `held_value` is now included in the gross calculation. Previously only
        new orders were summed, so holding 85% and adding 19% passed at
        "19% < 95%" while putting the account 104% long on margin.
      * The per-name cap is compared against the target weight, not a number
        far above it, so a sizing bug that doubles one position is caught
        instead of sailing through at 7.6%.
    """
    if not orders:
        return Check("order_sanity", True, "no orders to place")
    if len(orders) > MAX_ORDERS_PER_RUN:
        return Check("order_sanity", False,
                     f"{len(orders)} orders exceeds the {MAX_ORDERS_PER_RUN} cap")

    target_weight = MAX_GROSS_EXPOSURE / MAX_POSITIONS      # 3.8%
    per_name_cap = min(MAX_POSITION_PCT, target_weight * 1.25)

    for o in orders:
        notional = o["qty"] * o["ref_price"]
        if o["qty"] < 1:
            return Check("order_sanity", False, f"{o['symbol']} qty {o['qty']}")
        if not (0 < o["ref_price"] < 100_000):
            return Check("order_sanity", False,
                         f"{o['symbol']} ref_price {o['ref_price']}")
        if notional > equity * per_name_cap:
            return Check("order_sanity", False,
                         f"{o['symbol']} notional ${notional:,.0f} is "
                         f"{notional/equity:.2%} of equity, cap is "
                         f"{per_name_cap:.2%} (target weight "
                         f"{target_weight:.2%})")

    new_gross = sum(o["qty"] * o["ref_price"] for o in orders)
    total_gross = new_gross + held_value
    if total_gross > equity * MAX_GROSS_EXPOSURE:
        return Check("order_sanity", False,
                     f"total gross ${total_gross:,.0f} "
                     f"(${held_value:,.0f} held + ${new_gross:,.0f} new) is "
                     f"{total_gross/equity:.0%} of equity, cap is "
                     f"{MAX_GROSS_EXPOSURE:.0%}")
    return Check("order_sanity", True,
                 f"{len(orders)} orders, ${total_gross:,.0f} total gross "
                 f"({total_gross/equity:.0%} of equity)")


def load_issuer_groups(path: Path) -> dict[str, str]:
    """
    Ticker -> group id. Built by scripts/build_issuer_map.py from return
    correlation; see that file for why a measured threshold beats a
    hand-written list.
    """
    import json
    if not path.exists():
        return {}
    payload = json.loads(path.read_text())
    out = {}
    for i, g in enumerate(payload.get("groups", [])):
        for m in g["members"]:
            out[m] = f"g{i}"
    return out


def collapse_to_one_per_issuer(ranked: list[str],
                               groups: dict[str, str]) -> list[str]:
    """
    Keep the highest-ranked ticker from each issuer group, drop the rest.

    Order matters: `ranked` must already be sorted best-first, so the survivor
    is the one the model scored highest rather than whichever sorted first
    alphabetically.
    """
    seen: set[str] = set()
    kept = []
    for sym in ranked:
        g = groups.get(sym)
        if g is not None:
            if g in seen:
                continue
            seen.add(g)
        kept.append(sym)
    return kept


def check_issuer_concentration(order_symbols: list[str], held: set[str],
                               groups: dict[str, str]) -> Check:
    """
    No two positions may belong to the same issuer group.

    The 14 Aug dry run bought GOOG and GOOGL together — 7.6% of equity in one
    issuer under a 5% per-name cap, and `order_sanity` passed it because it
    counts tickers. Worse, the same map catches ticker RENAMES: Alpaca serves
    both the old and new symbol for about a year around a change, so FB/META
    or RTX/UTX are one security appearing twice. Buying both is not
    diversification, it is a double position recorded as two.
    """
    if not groups:
        return Check("issuer_concentration", False,
                     "issuer map missing — run scripts/build_issuer_map.py")
    counts: dict[str, list[str]] = {}
    for sym in list(order_symbols) + sorted(held):
        g = groups.get(sym)
        if g:
            counts.setdefault(g, []).append(sym)
    clashes = {g: v for g, v in counts.items() if len(v) > 1}
    if clashes:
        detail = "; ".join(" + ".join(v) for v in clashes.values())
        return Check("issuer_concentration", False,
                     f"same issuer held twice: {detail}")
    return Check("issuer_concentration", True,
                 f"{len(order_symbols)} orders, no issuer held twice")


def check_position_cap(current: int, new: int) -> Check:
    total = current + new
    if total > MAX_POSITIONS:
        return Check("position_cap", False,
                     f"{current} held + {new} new = {total} > {MAX_POSITIONS}")
    return Check("position_cap", True, f"{current} held + {new} new = {total}")


# ---------------------------------------------------------------------------
# runner
# ---------------------------------------------------------------------------

def run_all(checks: list[Check]) -> tuple[bool, list[Check]]:
    """All must pass. Returns (allowed, checks) — no partial credit."""
    return all(c.passed for c in checks), checks


def format_report(checks: list[Check]) -> str:
    lines = [str(c) for c in checks]
    ok = all(c.passed for c in checks)
    lines.append("-" * 72)
    lines.append("ALL CHECKS PASSED — submission allowed" if ok else
                 "BLOCKED — nothing will be submitted")
    return "\n".join(lines)
