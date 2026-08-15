"""
Alpaca trading client. Paper only, and it refuses to be anything else.

This is the first module in the project that can move money. Every other file
reads data; this one writes orders. So the design rule is inverted from
`data_layer.py`: that module refuses to guess, this one refuses to act.

WHAT THIS ENFORCES, IN CODE

  1. PAPER ONLY. Two independent checks, because one can be defeated by a
     typo. The key must start with PK (D11), AND the base URL must be the
     paper host. A live key (AK...) raises before a session is even opened.
     D1 says paper-only and moving to real money is a separate decision
     requiring evidence, not a config change.

  2. IDEMPOTENT SUBMISSION. Every order carries a deterministic
     `client_order_id` built from date + symbol + strategy. Alpaca rejects a
     duplicate id with 422. So if the 06:30 job runs twice — cron fires twice,
     you re-run it manually, the machine wakes from sleep and catches up — the
     second run cannot double the position. This is the single most likely way
     a scheduled trading job hurts you, and it fails silently: two fills look
     exactly like one big fill in the equity curve.

  3. NO DEFAULTS ON ANYTHING THAT COSTS MONEY. qty, stop and target are
     required arguments. There is no default position size.

  4. DRY RUN IS THE DEFAULT. `submit()` will not reach the network unless
     `live=True` is passed explicitly. The daily runner must opt in.

WHAT THIS DELIBERATELY DOES NOT DO
  No retry-on-failure for order submission. A failed order is a failed order;
  retrying blind is how you end up with three of them. Failures are logged and
  the run continues to the next symbol.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import date as Date
from pathlib import Path

import requests

PAPER_URL = "https://paper-api.alpaca.markets"
LIVE_URL = "https://api.alpaca.markets"


@dataclass
class Fill:
    symbol: str
    qty: float
    side: str
    status: str
    order_id: str
    client_order_id: str
    submitted: bool
    error: str | None = None


class PaperBroker:
    """Alpaca paper trading. Constructing one against a live key raises."""

    def __init__(self, headers: dict[str, str], base_url: str = PAPER_URL,
                 pause: float = 0.25):
        key = headers.get("APCA-API-KEY-ID", "")
        if not key.startswith("PK"):
            raise RuntimeError(
                f"Key prefix {key[:2]!r} is not 'PK'. This project is paper-only "
                f"(D1). A live key must never reach this class."
            )
        if base_url != PAPER_URL:
            raise RuntimeError(
                f"base_url is {base_url!r}, not the paper host. Refusing. "
                f"Switching to live is a separate decision requiring evidence "
                f"(D1), not a URL change."
            )
        self.base = base_url
        self.session = requests.Session()
        self.session.headers.update(headers)
        self.pause = pause

    # -- reads -------------------------------------------------------------
    def _get(self, path: str, params: dict | None = None) -> dict | list:
        r = self.session.get(f"{self.base}{path}", params=params or {}, timeout=30)
        if r.status_code != 200:
            raise RuntimeError(f"GET {path} -> HTTP {r.status_code}: {r.text[:300]}")
        time.sleep(self.pause)
        return r.json()

    def account(self) -> dict:
        return self._get("/v2/account")

    def positions(self) -> list[dict]:
        return self._get("/v2/positions")

    def clock(self) -> dict:
        return self._get("/v2/clock")

    def calendar(self, start: str, end: str) -> list[dict]:
        return self._get("/v2/calendar", {"start": start, "end": end})

    def orders(self, status: str = "open", limit: int = 500) -> list[dict]:
        return self._get("/v2/orders", {"status": status, "limit": limit,
                                        "nested": "true"})

    # -- writes ------------------------------------------------------------
    @staticmethod
    def client_order_id(run_date: Date, symbol: str, strategy: str = "v1") -> str:
        """
        Deterministic per (day, symbol, strategy).

        Alpaca 422s on a duplicate, which is exactly what we want: the second
        run of the same day is rejected by the broker rather than trusted to
        be prevented by our own bookkeeping.
        """
        return f"{strategy}-{run_date.isoformat()}-{symbol}"[:48]

    def submit_bracket(self, symbol: str, qty: int, stop_pct: float,
                       target_pct: float, ref_price: float, run_date: Date,
                       *, live: bool, strategy: str = "v1",
                       limit_slack: float | None = 0.005) -> Fill:
        """
        One bracket order: LIMIT entry, stop and target attached (D17, D35).

        `tif=day` because Alpaca rejects brackets with `opg`, and
        market-on-open followed by a second routine to attach stops would
        leave the position naked through the open, the most volatile part of
        the session.

        WHY THE ENTRY IS A LIMIT, NOT A MARKET ORDER
        The stop and target must be named as dollar prices tonight, and the
        only price known tonight is `ref_price`, the prior close. A market
        entry fills at tomorrow's open, which can be anywhere. A stock closing
        at $100 with a $92 stop and $112 target that opens at $102 gives a
        bracket 9.8% wide in both directions — the designed 8%-risk /
        12%-reward becomes 1:1. At a +12% gap the take-profit is marketable
        at the open and the trade round-trips instantly.

        A limit at `ref_price * (1 + limit_slack)` bounds it. The worst fill
        is 0.5% above reference, so the bracket can never be worse than
        8.46% / 11.44% — a 1:1.35 ratio against the designed 1:1.50. Fills
        below reference make it better than designed, never worse.

        Measured over 2022-2024: Sharpe 0.50 against 0.52 for the
        never-achievable percentage-of-fill assumption, at a cost of 4 skipped
        trades out of 1,982. A market entry scored 0.28.

        Pass `limit_slack=None` for a market entry. Nothing should.
        """
        if qty < 1:
            return Fill(symbol, 0, "buy", "skipped", "", "", False,
                        "qty < 1 share")

        coid = self.client_order_id(run_date, symbol, strategy)
        body = {
            "symbol": symbol,
            "qty": str(int(qty)),
            "side": "buy",
            # GTC, not DAY. Alpaca's bracket child legs inherit the parent's
            # time-in-force, and there is no way to set them independently at
            # submission. With "day" the take-profit leg EXPIRES and the
            # stop-loss leg is CANCELLED as its OCO sibling at that session's
            # close — so a 10-day hold is protected for its first 6.5 hours and
            # naked for the remaining nine days.
            #
            # Verified on the live paper account, 14 Aug 2026: 24 positions
            # filled, and by the next session all 48 protective legs were gone,
            # 0 open orders against $86,459 of stock. Every backtest number
            # assumes those barriers are standing. 21.2% of trades are decided
            # by them, and removing them widens the 1st-percentile trade from
            # -8.00% to -16.44% and raises per-trade volatility by 253%.
            #
            # The cost of GTC: an entry that does not fill no longer expires at
            # the close, so a stale limit from a previous session could fill at
            # a price the model never chose. `cancel_stale_entries` in
            # daily_run.py clears those before new orders go out. That runs
            # after the close, so a cancelled order cannot fill overnight.
            "time_in_force": "gtc",
            "order_class": "bracket",
            "client_order_id": coid,
            "stop_loss": {"stop_price": round(ref_price * (1 - stop_pct), 2)},
            "take_profit": {"limit_price": round(ref_price * (1 + target_pct), 2)},
        }
        if limit_slack is None:
            body["type"] = "market"
        else:
            body["type"] = "limit"
            body["limit_price"] = round(ref_price * (1 + limit_slack), 2)

        if not live:
            return Fill(symbol, qty, "buy", "DRY_RUN", "", coid, False, None)

        r = self.session.post(f"{self.base}/v2/orders", json=body, timeout=30)
        time.sleep(self.pause)
        if r.status_code == 422 and "client_order_id" in r.text:
            # A duplicate is PROOF the order exists at the broker, so it counts
            # as submitted for bookkeeping. Reporting it as not-submitted meant
            # a re-run left state empty while 25 live orders sat at the broker
            # — the exact blind spot the idempotency guard exists to prevent.
            return Fill(symbol, qty, "buy", "duplicate", "", coid, True,
                        "already submitted today — idempotency guard held")
        if r.status_code not in (200, 201):
            return Fill(symbol, qty, "buy", "rejected", "", coid, False,
                        f"HTTP {r.status_code}: {r.text[:200]}")
        j = r.json()
        status = j.get("status", "?")
        # `submitted` means the broker holds a live order, NOT that it filled.
        # After the close the status is `accepted` / `pending_new` and the fill
        # does not happen until the next open, so nothing downstream may treat
        # this as a position. Terminal-failure statuses are separated out so a
        # rejected order is never mistaken for a live one.
        dead = {"rejected", "canceled", "expired", "suspended", "stopped"}
        return Fill(symbol, qty, "buy", status, j.get("id", ""), coid,
                    status not in dead,
                    None if status not in dead else f"broker returned {status}")

    def cancel_orders_for(self, symbol: str, *, live: bool) -> int:
        """
        Cancel every open order on one symbol. MUST run before liquidating.

        Once a bracket entry fills, its take-profit and stop-loss legs are live
        sell orders for the full quantity, so `qty_available` on the position is
        zero and `DELETE /v2/positions/{symbol}` is rejected for insufficient
        quantity. That endpoint has no `cancel_orders` parameter, so the legs
        must be cleared explicitly first.

        This was the defect that made the time stop a no-op: the close came
        back `rejected`, and the caller dropped the symbol from local state
        anyway, orphaning a real position permanently.
        """
        if not live:
            return 0
        n = 0
        for o in self.orders(status="open", limit=500):
            for leg in [o] + list(o.get("legs") or []):
                if leg.get("symbol") != symbol or not leg.get("id"):
                    continue
                r = self.session.delete(f"{self.base}/v2/orders/{leg['id']}",
                                        timeout=30)
                time.sleep(self.pause)
                if r.status_code in (200, 204):
                    n += 1
        return n

    def close_position(self, symbol: str, *, live: bool) -> Fill:
        """
        Liquidate one position, cancelling its open legs first.

        `submitted=True` only when the broker actually accepted the sell (or
        the position was already gone). The caller must not drop the symbol
        from its own bookkeeping on anything else.
        """
        if not live:
            return Fill(symbol, 0, "sell", "DRY_RUN", "", "", False, None)

        self.cancel_orders_for(symbol, live=True)

        r = self.session.delete(f"{self.base}/v2/positions/{symbol}", timeout=30)
        time.sleep(self.pause)
        if r.status_code == 404:
            return Fill(symbol, 0, "sell", "already_closed", "", "", True, None)
        if r.status_code not in (200, 207):
            return Fill(symbol, 0, "sell", "rejected", "", "", False,
                        f"HTTP {r.status_code}: {r.text[:200]}")
        j = r.json()
        return Fill(symbol, float(j.get("qty", 0) or 0), "sell",
                    j.get("status", "?"), j.get("id", ""), "", True, None)

    def cancel_all_open(self, *, live: bool) -> int:
        if not live:
            return 0
        r = self.session.delete(f"{self.base}/v2/orders", timeout=30)
        time.sleep(self.pause)
        return len(r.json()) if r.status_code in (200, 207) else 0


def load_trading_credentials(env_path: Path) -> dict[str, str]:
    """Same .env as the data layer (D11), same PK/AK check."""
    import os
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip("'\""))
    key = os.environ.get("APCA_API_KEY_ID")
    sec = os.environ.get("APCA_API_SECRET_KEY")
    if not key or not sec:
        raise RuntimeError("No Alpaca credentials. See SETUP.md Part 2.")
    if key.startswith("AK"):
        raise RuntimeError("Key ID starts with 'AK' — that is a LIVE trading "
                           "key. Refusing to run. This project is paper-only (D1).")
    return {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": sec}
