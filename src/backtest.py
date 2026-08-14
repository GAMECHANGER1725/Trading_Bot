"""
Backtest engine.

Replays history one day at a time and simulates the exact order lifecycle the
live bot will use:

    decide on yesterday's close -> bracket order -> fill at today's open
    -> stop / target watched by the broker -> time stop at N days

The single most important property of this file is that a strategy *cannot*
see the future. That is enforced structurally: the strategy is handed a
`MarketView` object that physically contains no data at or after the decision
date. It is not a convention or a promise, it is the shape of the object.

Modelling choices are listed at the bottom of this docstring because every one
of them is a place where a backtest can flatter itself:

  * Entry fills at the day's open, plus slippage. The live bot submits a
    bracket market order pre-open (D17), so the open is the right reference.
  * Exits triggered intraday fill at the trigger price, EXCEPT when the day
    gapped through it, in which case they fill at the open. Gaps are the
    normal way stops cost more than you planned.
  * If a day's range covers both the stop and the target, the STOP is assumed
    to have hit first. Daily bars cannot tell us the order of events, so we
    take the pessimistic branch. Assuming the target would inflate results.
  * A position entered today can also be stopped out today. Excluding that
    would quietly remove the worst trades.
  * Costs are charged as slippage in basis points per side. Alpaca charges no
    commission, but the spread is real and measured (median ~3 bps across the
    S&P 100 sample, far wider in thin names).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date as Date

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# what a strategy is allowed to see
# ---------------------------------------------------------------------------

class MarketView:
    """
    History strictly before `as_of`. Constructed fresh each simulated day.

    There is no method on this object that returns data at or after `as_of`,
    because the underlying frame was sliced before it was handed over.
    """

    def __init__(self, frame: pd.DataFrame, as_of: Date, tradable: set[str]):
        self._frame = frame
        self.as_of = as_of
        self.tradable = tradable

    def history(self, symbol: str, days: int | None = None) -> pd.DataFrame:
        h = self._frame[self._frame["symbol"] == symbol]
        return h.tail(days) if days else h

    def closes(self, days: int) -> pd.DataFrame:
        """Wide frame of closes, dates x symbols, for the last `days` sessions."""
        recent = self._frame[self._frame["date"] >= self._frame["date"].max()
                             - pd.Timedelta(days=days * 2)]
        return recent.pivot_table(index="date", columns="symbol",
                                  values="close", observed=True).tail(days)

    def last_close(self) -> pd.Series:
        last = self._frame["date"].max()
        rows = self._frame[self._frame["date"] == last]
        return rows.set_index("symbol")["close"]


@dataclass
class Order:
    """
    A bracket order, decided at yesterday's close, filled at today's open.

    `stop_pct` / `target_pct` are measured from the ACTUAL FILL, which is what
    a backtest can do and a live bot cannot: the live order has to name dollar
    prices the night before, when only the prior close is known.

    `stop_abs` / `target_abs` let a strategy name those dollar prices directly,
    reproducing what the live bot really sends. When set, they take precedence.

    `limit_price` turns the entry into a limit order — fill only at or below
    this price. Used to test refusing to chase an overnight gap.
    """
    symbol: str
    weight: float                    # fraction of current equity
    stop_pct: float | None = None    # e.g. 0.08 -> stop 8% below the FILL
    target_pct: float | None = None
    max_hold_days: int = 10
    stop_abs: float | None = None    # absolute stop price, set the night before
    target_abs: float | None = None
    limit_price: float | None = None


@dataclass
class Position:
    symbol: str
    qty: float
    entry_price: float
    entry_date: Date
    stop: float | None
    target: float | None
    max_hold_days: int
    days_held: int = 0


@dataclass
class Trade:
    symbol: str
    entry_date: Date
    exit_date: Date
    entry_price: float
    exit_price: float
    qty: float
    reason: str
    pnl: float = 0.0
    return_pct: float = 0.0


@dataclass
class Result:
    equity: pd.Series
    trades: list[Trade] = field(default_factory=list)
    stats: dict = field(default_factory=dict)

    def summary(self) -> str:
        s = self.stats
        return (
            f"  total return   : {s['total_return']:>8.2%}\n"
            f"  CAGR           : {s['cagr']:>8.2%}\n"
            f"  max drawdown   : {s['max_drawdown']:>8.2%}\n"
            f"  Sharpe (ann.)  : {s['sharpe']:>8.2f}\n"
            f"  trades         : {s['n_trades']:>8,}\n"
            f"  win rate       : {s['win_rate']:>8.1%}\n"
            f"  final equity   : ${s['final_equity']:>12,.2f}"
        )


# ---------------------------------------------------------------------------
# engine
# ---------------------------------------------------------------------------

class Backtest:
    def __init__(self, bars: pd.DataFrame, *, starting_cash: float = 100_000.0,
                 slippage_bps: float = 3.5, commission: float = 0.0,
                 members_only: bool = True):
        """
        `bars` needs columns: symbol, date, open, high, low, close, volume,
        and optionally is_member.

        slippage_bps is charged per side. 3.5 bps ~= half the measured median
        S&P 100 spread plus a small allowance for impact, so a round trip costs
        about 0.07% of position value.
        """
        df = bars.copy()
        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values(["date", "symbol"]).reset_index(drop=True)
        if members_only and "is_member" in df.columns:
            self.universe_flag = df.set_index(["date", "symbol"])["is_member"]
        else:
            self.universe_flag = None

        self.bars = df
        self.starting_cash = starting_cash
        self.slip = slippage_bps / 10_000.0
        self.commission = commission
        self.sessions = sorted(df["date"].unique())
        self._by_date = {d: g for d, g in df.groupby("date", observed=True)}
        # Final bar per symbol. Needed to tell a delisting (symbol is gone for
        # good) from a trading halt (symbol returns later). Without this, a
        # position in a delisted name is never closed and its value silently
        # drops out of equity with no trade recorded. See D20.
        self._last_bar = df.groupby("symbol", observed=True)["date"].max().to_dict()
        # Rows sorted by date, so "everything up to today" is a contiguous
        # slice rather than a boolean scan of the whole frame. Without this the
        # engine re-filters 1.3M rows on each of ~2,600 simulated days.
        self._by_date_sorted = df.sort_values("date").reset_index(drop=True)
        self._date_index = self._by_date_sorted["date"].searchsorted(
            pd.DatetimeIndex(self.sessions), side="right")

    # -- costs ------------------------------------------------------------
    def _buy_price(self, px: float) -> float:
        return px * (1 + self.slip)

    def _sell_price(self, px: float) -> float:
        return px * (1 - self.slip)

    # -- main loop --------------------------------------------------------
    def run(self, strategy, *, warmup: int = 60, verbose: bool = False) -> Result:
        cash = self.starting_cash
        positions: dict[str, Position] = {}
        trades: list[Trade] = []
        equity_dates, equity_values = [], []
        pending: list[Order] = []
        last_close: dict[str, float] = {}     # most recent known price per symbol

        for i, today in enumerate(self.sessions):
            day = self._by_date[today]
            px = day.set_index("symbol")
            for s, c in zip(px.index, px["close"]):
                last_close[s] = float(c)

            # ---------- 1. exits, using today's OHLC ----------
            for sym in list(positions):
                if sym not in px.index:
                    # No bar today. Either the name is halted (it comes back
                    # later) or it has delisted for good. Holding a delisted
                    # position forever would erase its value from equity with
                    # no trade recorded, so liquidate at the last known price.
                    if today > self._last_bar.get(sym, today):
                        pos = positions[sym]
                        mark = last_close.get(sym, pos.entry_price)
                        fill = self._sell_price(mark)
                        cash += fill * pos.qty - self.commission
                        pnl = (fill - pos.entry_price) * pos.qty
                        trades.append(Trade(sym, pos.entry_date, today.date(),
                                            pos.entry_price, fill, pos.qty,
                                            "delisted", pnl,
                                            pnl / (pos.entry_price * pos.qty)))
                        del positions[sym]
                    continue
                pos = positions[sym]
                row = px.loc[sym]
                o, h, l = float(row["open"]), float(row["high"]), float(row["low"])
                pos.days_held += 1

                exit_px, reason = None, None
                if pos.stop is not None and o <= pos.stop:
                    exit_px, reason = o, "stop_gap"          # gapped through
                elif pos.target is not None and o >= pos.target:
                    exit_px, reason = o, "target_gap"
                elif pos.stop is not None and l <= pos.stop:
                    exit_px, reason = pos.stop, "stop"       # pessimistic branch
                elif pos.target is not None and h >= pos.target:
                    exit_px, reason = pos.target, "target"
                elif pos.days_held >= pos.max_hold_days:
                    exit_px, reason = o, "time"

                if exit_px is not None:
                    fill = self._sell_price(exit_px)
                    proceeds = fill * pos.qty - self.commission
                    cash += proceeds
                    pnl = proceeds - pos.entry_price * pos.qty
                    trades.append(Trade(sym, pos.entry_date, today.date(),
                                        pos.entry_price, fill, pos.qty, reason,
                                        pnl, pnl / (pos.entry_price * pos.qty)))
                    del positions[sym]

            # ---------- 2. fill orders decided yesterday ----------
            equity_now = cash + sum(
                p.qty * (float(px.loc[p.symbol, "close"]) if p.symbol in px.index
                         else last_close.get(p.symbol, p.entry_price))
                for p in positions.values())

            for order in pending:
                if order.symbol in positions or order.symbol not in px.index:
                    continue
                row = px.loc[order.symbol]
                open_px, low_px = float(row["open"]), float(row["low"])

                # Limit entry: fill at the open if it is already at or below
                # the limit, else at the limit if the day traded down to it,
                # else no fill at all. Refusing to chase a gap means some days
                # simply produce no trade.
                if order.limit_price is not None:
                    if open_px <= order.limit_price:
                        raw = open_px
                    elif low_px <= order.limit_price:
                        raw = order.limit_price
                    else:
                        continue
                else:
                    raw = open_px

                fill = self._buy_price(raw)
                budget = equity_now * order.weight
                qty = np.floor(budget / fill)
                cost = qty * fill + self.commission
                if qty < 1 or cost > cash:
                    continue                       # cannot afford it; skip
                cash -= cost

                # Absolute barriers, when the strategy named them the night
                # before, otherwise percentages off the fill.
                if order.stop_abs is not None:
                    stop_lvl = order.stop_abs
                elif order.stop_pct:
                    stop_lvl = fill * (1 - order.stop_pct)
                else:
                    stop_lvl = None
                if order.target_abs is not None:
                    tgt_lvl = order.target_abs
                elif order.target_pct:
                    tgt_lvl = fill * (1 + order.target_pct)
                else:
                    tgt_lvl = None

                pos = Position(order.symbol, qty, fill, today.date(),
                               stop_lvl, tgt_lvl, order.max_hold_days)
                # a position can be stopped out the same day it opens
                lo = float(row["low"])
                if pos.stop is not None and lo <= pos.stop:
                    out = self._sell_price(pos.stop)
                    cash += out * qty - self.commission
                    pnl = (out - fill) * qty
                    trades.append(Trade(pos.symbol, today.date(), today.date(),
                                        fill, out, qty, "stop_same_day", pnl,
                                        pnl / (fill * qty)))
                else:
                    positions[order.symbol] = pos
            pending = []

            # ---------- 3. mark to market ----------
            # Halted names are marked at their last known price rather than
            # dropped, so equity does not jump around when a bar is missing.
            holdings = sum(
                p.qty * (float(px.loc[p.symbol, "close"]) if p.symbol in px.index
                         else last_close.get(p.symbol, p.entry_price))
                for p in positions.values())
            equity_dates.append(today)
            equity_values.append(cash + holdings)

            # ---------- 4. decide tomorrow's orders ----------
            if i >= warmup and i < len(self.sessions) - 1:
                view = MarketView(
                    # contiguous slice; physically cannot contain a later date
                    self._by_date_sorted.iloc[:self._date_index[i]],
                    as_of=today.date(),
                    tradable=self._tradable_on(today),
                )
                pending = strategy(view, dict(positions), cash,
                                   equity_values[-1]) or []

        equity = pd.Series(equity_values, index=pd.DatetimeIndex(equity_dates))
        return Result(equity, trades, self._stats(equity, trades))

    def _tradable_on(self, day) -> set[str]:
        g = self._by_date[day]
        if "is_member" in g.columns:
            return set(g.loc[g["is_member"], "symbol"].astype(str))
        return set(g["symbol"].astype(str))

    # -- stats ------------------------------------------------------------
    @staticmethod
    def _stats(equity: pd.Series, trades: list[Trade]) -> dict:
        rets = equity.pct_change().dropna()
        years = (equity.index[-1] - equity.index[0]).days / 365.25
        total = equity.iloc[-1] / equity.iloc[0] - 1
        peak = equity.cummax()
        wins = [t for t in trades if t.pnl > 0]
        return {
            "total_return": total,
            "cagr": (equity.iloc[-1] / equity.iloc[0]) ** (1 / years) - 1 if years > 0 else 0.0,
            "max_drawdown": float((equity / peak - 1).min()),
            "sharpe": float(rets.mean() / rets.std() * np.sqrt(252)) if rets.std() > 0 else 0.0,
            "volatility": float(rets.std() * np.sqrt(252)),
            "n_trades": len(trades),
            "win_rate": len(wins) / len(trades) if trades else 0.0,
            "final_equity": float(equity.iloc[-1]),
            "years": years,
        }


# ---------------------------------------------------------------------------
# the calibration strategy — not a strategy anyone would trade
# ---------------------------------------------------------------------------

def buy_and_hold(symbol: str):
    """Buy `symbol` on the first eligible day with everything, then never act."""
    done = {"bought": False}

    def strategy(view: MarketView, positions, cash, equity):
        if done["bought"] or positions:
            return []
        done["bought"] = True
        return [Order(symbol, weight=1.0, stop_pct=None, target_pct=None,
                      max_hold_days=10**6)]

    return strategy
