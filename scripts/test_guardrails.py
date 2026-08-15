#!/usr/bin/env python3
"""
A guardrail that has never fired is not a guardrail. It is a comment.

Every check in src/guardrails.py is driven into its failure branch here and
asserted to block. Also asserted: the passing branch passes, because a check
that always fails is just as useless as one that never does.

This is the only kind of test that can exist for step 5. The backtest engine
cannot reach any of this — there is no stale data in a backtest, no duplicate
cron fire, no broker holding a position you do not know about.

    python3 scripts/test_guardrails.py
"""
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402

from src import guardrails as G  # noqa: E402
from src.broker import LIVE_URL, PaperBroker  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
PASSED, FAILED = [], []


def expect(name: str, check: G.Check, should_pass: bool) -> None:
    ok = check.passed == should_pass
    (PASSED if ok else FAILED).append(name)
    verdict = "ok" if ok else "*** TEST FAILED ***"
    want = "pass" if should_pass else "BLOCK"
    print(f"  {name:<44} want {want:<5} got "
          f"{'pass' if check.passed else 'BLOCK':<5} {verdict}")


def main() -> int:
    now = datetime.now(timezone.utc)
    today = now.date()
    good_acct = {"account_number": "PA3VR8OK3RL7", "status": "ACTIVE",
                 "equity": "100000", "crypto_status": "ACTIVE"}

    print("KILL SWITCH")
    halt = ROOT / "HALT"
    expect("no HALT file -> allowed", G.check_kill_switch(ROOT), True)
    halt.write_text("test")
    expect("HALT file present -> blocked", G.check_kill_switch(ROOT), False)
    halt.unlink()
    expect("HALT removed -> allowed again", G.check_kill_switch(ROOT), True)

    print("\nACCOUNT")
    expect("active paper account -> allowed",
           G.check_paper_account(good_acct), True)
    expect("inactive account -> blocked",
           G.check_paper_account({**good_acct, "status": "ACCOUNT_CLOSED"}), False)

    print("\nEQUITY FLOOR")
    expect("$100k -> allowed", G.check_equity(good_acct), True)
    expect("$49k (below floor) -> blocked",
           G.check_equity({**good_acct, "equity": "49000"}), False)

    print("\nDRAWDOWN HALT")
    expect("-10% from peak -> allowed",
           G.check_drawdown({"equity": "90000"}, 100_000), True)
    expect("-26% from peak -> blocked",
           G.check_drawdown({"equity": "74000"}, 100_000), False)
    expect("no peak history -> allowed",
           G.check_drawdown({"equity": "100000"}, 0), True)

    print("\nMARKET CALENDAR")
    cal = [{"date": (today + timedelta(days=1)).isoformat()}]
    clk_today = {"next_open": f"{today.isoformat()}T09:30:00-04:00"}
    clk_tmrw = {"next_open": f"{(today+timedelta(days=1)).isoformat()}T09:30:00-04:00"}
    clk_far = {"next_open": f"{(today+timedelta(days=9)).isoformat()}T09:30:00-04:00"}
    expect("open later today -> allowed",
           G.check_market_calendar(clk_today, cal, today), True)
    expect("open tomorrow -> allowed",
           G.check_market_calendar(clk_tmrw, cal, today), True)
    expect("next open 9 days away (holiday) -> blocked",
           G.check_market_calendar(clk_far, cal, today), False)
    expect("no clock -> blocked (missing info is not permission)",
           G.check_market_calendar({}, cal, today), False)
    expect("empty calendar -> blocked",
           G.check_market_calendar(clk_tmrw, [], today), False)

    print("\nBAR FRESHNESS")
    fresh = pd.DataFrame({"date": [now - timedelta(days=1)]})
    stale = pd.DataFrame({"date": [now - timedelta(days=9)]})
    expect("1-day-old bar -> allowed", G.check_bar_freshness(fresh, now), True)
    expect("9-day-old bar -> blocked", G.check_bar_freshness(stale, now), False)
    expect("no bars at all -> blocked",
           G.check_bar_freshness(pd.DataFrame({"date": []}), now), False)

    print("\nUNIVERSE SIZE")
    expect("100 scored -> allowed", G.check_universe_size(100), True)
    expect("12 scored -> blocked", G.check_universe_size(12), False)

    print("\nUNEXPECTED POSITIONS (regression: the deadlock bug)")
    bp = [{"symbol": "AAPL"}, {"symbol": "MSFT"}]
    expect("all held names were ordered by us -> allowed",
           G.check_unexpected_positions(bp, {"AAPL", "MSFT"}), True)
    expect("BRACKET EXITED, broker has fewer than we ordered -> ALLOWED",
           G.check_unexpected_positions(bp, {"AAPL", "MSFT", "NVDA", "TSLA"}), True)
    expect("broker holds a name we never ordered -> blocked",
           G.check_unexpected_positions(bp, {"AAPL"}), False)

    print("\nSESSION TIMING (regression: the EST cron bug)")
    expect("market closed -> allowed",
           G.check_session_timing(now, {"is_open": False}), True)
    expect("market OPEN (winter cron fires 15:35 ET) -> blocked",
           G.check_session_timing(now, {"is_open": True}), False)
    expect("no clock response -> blocked",
           G.check_session_timing(now, {}), False)

    print("\nCASH (regression: silent margin)")
    rich = {**good_acct, "cash": "100000"}
    poor = {**good_acct, "cash": "5000"}
    small = [{"symbol": "AAPL", "qty": 20, "ref_price": 190.0}]
    big = [{"symbol": f"S{i}", "qty": 20, "ref_price": 190.0} for i in range(25)]
    expect("$3.8k of orders on $100k cash -> allowed",
           G.check_cash(rich, small), True)
    expect("$95k of orders on $5k cash -> blocked (would use margin)",
           G.check_cash(poor, big), False)
    expect("no orders -> allowed", G.check_cash(poor, []), True)

    print("\nORDER SANITY WITH HOLDINGS (regression: unreachable checks)")
    expect("$85k already held + $19k new on $100k -> blocked (104% gross)",
           G.check_order_sanity([{"symbol": f"S{i}", "qty": 20, "ref_price": 190.0}
                                 for i in range(5)], 100_000.0, 85_000.0), False)
    expect("$50k held + $19k new on $100k -> allowed",
           G.check_order_sanity([{"symbol": f"S{i}", "qty": 20, "ref_price": 190.0}
                                 for i in range(5)], 100_000.0, 50_000.0), True)
    expect("one name at 2x its target weight -> blocked",
           G.check_order_sanity([{"symbol": "AAPL", "qty": 40, "ref_price": 190.0}],
                                100_000.0, 0.0), False)

    print("\nPOSITION CAP")
    expect("20 held + 5 new = 25 -> allowed", G.check_position_cap(20, 5), True)
    expect("24 held + 5 new = 29 -> blocked", G.check_position_cap(24, 5), False)

    print("\nORDER SANITY")
    eq = 100_000.0
    ok_orders = [{"symbol": "AAPL", "qty": 20, "ref_price": 190.0}]
    expect("one $3,800 order on $100k -> allowed",
           G.check_order_sanity(ok_orders, eq, 0.0), True)
    expect("no orders -> allowed", G.check_order_sanity([], eq, 0.0), True)
    expect("single name at 30% of equity -> blocked",
           G.check_order_sanity([{"symbol": "AAPL", "qty": 158,
                                  "ref_price": 190.0}], eq, 0.0), False)
    expect("NaN-derived giant qty -> blocked",
           G.check_order_sanity([{"symbol": "AAPL", "qty": 10**9,
                                  "ref_price": 190.0}], eq, 0.0), False)
    expect("zero price -> blocked",
           G.check_order_sanity([{"symbol": "AAPL", "qty": 10,
                                  "ref_price": 0.0}], eq), False)
    expect("40 orders (loop bug) -> blocked",
           G.check_order_sanity([{"symbol": f"S{i}", "qty": 10,
                                  "ref_price": 100.0} for i in range(40)], eq, 0.0), False)
    expect("gross above 95% of equity -> blocked",
           G.check_order_sanity([{"symbol": f"S{i}", "qty": 24,
                                  "ref_price": 190.0} for i in range(22)], eq, 0.0), False)

    print("\nISSUER CONCENTRATION (dual class + ticker renames)")
    groups = G.load_issuer_groups(ROOT / "data" / "issuer_groups.json")
    print(f"  (issuer map: {len(groups)} tickers in "
          f"{len(set(groups.values()))} groups)")
    expect("distinct issuers -> allowed",
           G.check_issuer_concentration(["AAPL", "MSFT", "NVDA"], set(), groups), True)
    expect("GOOG + GOOGL in one run -> blocked",
           G.check_issuer_concentration(["GOOG", "GOOGL"], set(), groups), False)
    expect("FB + META (rename overlap) -> blocked",
           G.check_issuer_concentration(["FB", "META"], set(), groups), False)
    expect("RTX + UTX (rename overlap) -> blocked",
           G.check_issuer_concentration(["RTX", "UTX"], set(), groups), False)
    expect("new GOOGL while GOOG already held -> blocked",
           G.check_issuer_concentration(["GOOGL"], {"GOOG"}, groups), False)
    expect("missing issuer map -> blocked (missing info is not permission)",
           G.check_issuer_concentration(["AAPL"], set(), {}), False)

    print("\nISSUER COLLAPSE (keeps the higher-ranked class)")
    ranked = ["NVDA", "GOOG", "MSFT", "GOOGL", "AAPL"]
    kept = G.collapse_to_one_per_issuer(ranked, groups)
    ok = kept == ["NVDA", "GOOG", "MSFT", "AAPL"]
    (PASSED if ok else FAILED).append("collapse keeps higher-ranked GOOG")
    print(f"  {'GOOG ranked above GOOGL -> GOOGL dropped':<44} "
          f"{'ok' if ok else '*** TEST FAILED ***'}   {kept}")
    ranked2 = ["GOOGL", "GOOG", "AAPL"]
    kept2 = G.collapse_to_one_per_issuer(ranked2, groups)
    ok2 = kept2 == ["GOOGL", "AAPL"]
    (PASSED if ok2 else FAILED).append("collapse respects rank order")
    print(f"  {'GOOGL ranked above GOOG -> GOOG dropped':<44} "
          f"{'ok' if ok2 else '*** TEST FAILED ***'}   {kept2}")

    print("\nPAPER-ONLY ENFORCEMENT (broker constructor)")
    for name, headers, url in (
        ("live key AK... -> refused",
         {"APCA-API-KEY-ID": "AKLIVEKEY", "APCA-API-SECRET-KEY": "x"}, None),
        ("paper key but live URL -> refused",
         {"APCA-API-KEY-ID": "PKTEST", "APCA-API-SECRET-KEY": "x"}, LIVE_URL),
        ("empty key -> refused",
         {"APCA-API-KEY-ID": "", "APCA-API-SECRET-KEY": "x"}, None),
    ):
        try:
            PaperBroker(headers, **({"base_url": url} if url else {}))
            PASSED.append(name) if False else FAILED.append(name)
            print(f"  {name:<44} want BLOCK got pass  *** TEST FAILED ***")
        except RuntimeError:
            PASSED.append(name)
            print(f"  {name:<44} want BLOCK got BLOCK ok")

    print("\nIDEMPOTENCY")
    a = PaperBroker.client_order_id(date(2026, 8, 14), "AAPL")
    b = PaperBroker.client_order_id(date(2026, 8, 14), "AAPL")
    c = PaperBroker.client_order_id(date(2026, 8, 15), "AAPL")
    same, diff = a == b, a != c
    (PASSED if same else FAILED).append("same day+symbol -> same id")
    (PASSED if diff else FAILED).append("different day -> different id")
    print(f"  {'same day+symbol -> identical id':<44} "
          f"{'ok' if same else '*** TEST FAILED ***'}   ({a})")
    print(f"  {'different day -> different id':<44} "
          f"{'ok' if diff else '*** TEST FAILED ***'}")

    # The bracket is the entire risk model. On 14 Aug 2026 all 24 filled
    # positions lost both protective legs at the first close because the
    # parent was DAY and Alpaca's legs inherit the parent's TIF. Nothing
    # tested the one field the stop-loss depends on. It is tested now.
    print("\nBRACKET SURVIVES THE CLOSE (D40)")
    import inspect
    from src.broker import PaperBroker as _PB
    src = inspect.getsource(_PB.submit_bracket)
    gtc = '"time_in_force": "gtc"' in src
    no_day = '"time_in_force": "day"' not in src
    (PASSED if gtc else FAILED).append("bracket TIF is gtc")
    (PASSED if no_day else FAILED).append("bracket TIF is not day")
    print(f"  {'entry submitted GTC, so legs are GTC':<44} "
          f"{'ok' if gtc else '*** TEST FAILED ***'}")
    print(f"  {'no DAY tif anywhere in submit_bracket':<44} "
          f"{'ok' if no_day else '*** TEST FAILED ***'}")

    # GTC entries do not self-expire, so the cleanup that replaces that must
    # exist, must only ever touch BUY orders, and must never touch the
    # protective sell legs.
    from scripts.daily_run import cancel_stale_entries as _cse
    csrc = inspect.getsource(_cse)
    buy_only = 'o.get("side")) != "buy"' in csrc
    dry_safe = 'if not stale or not live' in csrc
    (PASSED if buy_only else FAILED).append("stale cleanup is buy-only")
    (PASSED if dry_safe else FAILED).append("stale cleanup respects dry run")
    print(f"  {'stale cleanup cancels BUYs only':<44} "
          f"{'ok' if buy_only else '*** TEST FAILED ***'}")
    print(f"  {'stale cleanup submits nothing on dry run':<44} "
          f"{'ok' if dry_safe else '*** TEST FAILED ***'}")

    print("\n" + "=" * 72)
    print(f"{len(PASSED)} passed, {len(FAILED)} failed")
    if FAILED:
        print("FAILED: " + ", ".join(FAILED))
        return 1
    print("Every guardrail blocks what it is supposed to block, and allows")
    print("what it is supposed to allow.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
