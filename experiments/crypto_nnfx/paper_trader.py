#!/usr/bin/env python3
"""Multi-book paper-trading simulator — no exchange account, no API keys, no signup.

Runs BOTH candidate strategies across MULTIPLE symbols at once, each as an
independent book with its own virtual balance. The point is sample size: one
strategy on one symbol needs years to produce a statistically meaningful
result, six books gather the same evidence ~6x faster.

  v1_rsi_macd   EMA100 baseline + RSI + MACD confluence, ADX20 filter
  v2_stoch_mfi  EMA100 baseline + Stochastic + MFI confluence, ADX15 filter

Both wrap the same NNFX risk boilerplate: ATR stop at 2.5x, take-profit at
2.5R, consecutive-loss and max-drawdown halts with a cooldown.

Run:      python3 paper_trader.py
Report:   python3 paper_trader.py --report     (standings across all books)
Selftest: python3 paper_trader.py --selftest
Reset:    python3 paper_trader.py --reset
Stop:     Ctrl+C  (state is saved; restarting resumes every book)
"""
import csv
import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timezone

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
INTERVAL = "1h"
# data-api.binance.vision is Binance's dedicated public market-data host (no auth,
# no geo-gating on market data). api.binance.com is the fallback.
HOSTS = ["https://data-api.binance.vision", "https://api.binance.com"]
KLINE_LIMIT = 500
POLL_SECONDS = 60
LOG_FILE = "paper_trades.csv"
STATE_FILE = "paper_state.json"

# ---- Shared risk boilerplate (identical across strategies, so the comparison
# ---- isolates the entry signal rather than the risk model) ----
ATR_LEN = 14
ATR_MULT_SL = 2.5
RR = 2.5
MAX_CONSEC_LOSS = 4
MAX_DD_PCT = 15.0
# Bars to sit out after a risk halt. Without this the bot deadlocks: clearing a
# loss streak requires a win, which requires being allowed to enter.
HALT_COOLDOWN_BARS = 24
INITIAL_CAPITAL = 10_000.0
COMMISSION_PCT = 0.05  # per side, matches backtest parity profile
WARMUP_BARS = 150      # bars before indicators are trustworthy


# ============================== data feed ==============================

def fetch_klines(symbol, interval=INTERVAL, limit=KLINE_LIMIT, retries=3):
    """Fetch candles from Binance public market data, with host failover."""
    last_err = None
    for attempt in range(retries):
        for host in HOSTS:
            url = f"{host}/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}"
            req = urllib.request.Request(url, headers={"User-Agent": "paper-trader/2.0"})
            try:
                with urllib.request.urlopen(req, timeout=15) as resp:
                    raw = json.loads(resp.read())
                return [{
                    "open": float(r[1]), "high": float(r[2]), "low": float(r[3]),
                    "close": float(r[4]), "volume": float(r[5]),
                    "openTime": int(r[0]), "closeTime": int(r[6]),
                } for r in raw]
            except Exception as e:  # noqa: BLE001 - any transport error -> next host
                last_err = e
        time.sleep(2 ** attempt)
    raise RuntimeError(f"all hosts failed for {symbol}: {last_err}")


# ============================== indicators ==============================

def ema_series(values, length):
    k = 2 / (length + 1)
    out, prev = [], None
    for v in values:
        prev = v if prev is None else v * k + prev * (1 - k)
        out.append(prev)
    return out


def rma_series(values, length):
    """Wilder's smoothing (Pine ta.rma)."""
    out, prev = [], None
    for v in values:
        prev = v if prev is None else (prev * (length - 1) + v) / length
        out.append(prev)
    return out


def sma_series(values, length):
    return [sum(values[max(0, i - length + 1): i + 1]) /
            len(values[max(0, i - length + 1): i + 1]) for i in range(len(values))]


def rsi_series(closes, length):
    """Pine ta.rsi — Wilder-smoothed average gain / average loss."""
    gains, losses = [0.0], [0.0]
    for i in range(1, len(closes)):
        change = closes[i] - closes[i - 1]
        gains.append(max(change, 0.0))
        losses.append(max(-change, 0.0))
    avg_gain, avg_loss = rma_series(gains, length), rma_series(losses, length)
    out = []
    for g, l in zip(avg_gain, avg_loss):
        if l == 0:
            out.append(100.0 if g > 0 else 50.0)
        else:
            out.append(100 - 100 / (1 + g / l))
    return out


def macd_series(closes, fast=12, slow=26, signal=9):
    """Pine ta.macd -> (macd line, signal line)."""
    fast_e, slow_e = ema_series(closes, fast), ema_series(closes, slow)
    line = [f - s for f, s in zip(fast_e, slow_e)]
    return line, ema_series(line, signal)


def stoch_series(highs, lows, closes, length, smooth):
    """Pine: ta.sma(ta.stoch(close, high, low, length), smooth)."""
    raw = []
    for i in range(len(closes)):
        lo = min(lows[max(0, i - length + 1): i + 1])
        hi = max(highs[max(0, i - length + 1): i + 1])
        raw.append(50.0 if hi == lo else 100 * (closes[i] - lo) / (hi - lo))
    return sma_series(raw, smooth)


def mfi_series(highs, lows, closes, volumes, length):
    """Money Flow Index (Pine ta.mfi) — volume-weighted, hlc3 typical price."""
    tp = [(h + l + c) / 3 for h, l, c in zip(highs, lows, closes)]
    pos_flow, neg_flow = [0.0], [0.0]
    for i in range(1, len(tp)):
        flow = tp[i] * volumes[i]
        pos_flow.append(flow if tp[i] > tp[i - 1] else 0.0)
        neg_flow.append(flow if tp[i] < tp[i - 1] else 0.0)
    out = [50.0]
    for i in range(1, len(tp)):
        lo = max(0, i - length + 1)
        pos_sum, neg_sum = sum(pos_flow[lo:i + 1]), sum(neg_flow[lo:i + 1])
        if neg_sum == 0:
            out.append(100.0 if pos_sum > 0 else 50.0)
        else:
            out.append(100 - 100 / (1 + pos_sum / neg_sum))
    return out


def true_range(highs, lows, closes):
    tr = [highs[0] - lows[0]]
    for i in range(1, len(closes)):
        tr.append(max(highs[i] - lows[i],
                      abs(highs[i] - closes[i - 1]),
                      abs(lows[i] - closes[i - 1])))
    return tr


def dmi_series(highs, lows, closes, length):
    """Wilder ADX (Pine ta.dmi(length, length) -> third element)."""
    plus_dm, minus_dm = [0.0], [0.0]
    for i in range(1, len(closes)):
        up, down = highs[i] - highs[i - 1], lows[i - 1] - lows[i]
        plus_dm.append(up if (up > down and up > 0) else 0.0)
        minus_dm.append(down if (down > up and down > 0) else 0.0)
    tr_rma = rma_series(true_range(highs, lows, closes), length)
    plus_rma, minus_rma = rma_series(plus_dm, length), rma_series(minus_dm, length)
    plus_di = [100 * p / t if t else 0.0 for p, t in zip(plus_rma, tr_rma)]
    minus_di = [100 * m / t if t else 0.0 for m, t in zip(minus_rma, tr_rma)]
    dx = [100 * abs(p - m) / (p + m) if (p + m) else 0.0
          for p, m in zip(plus_di, minus_di)]
    return rma_series(dx, length)


def atr_series(highs, lows, closes, length):
    return rma_series(true_range(highs, lows, closes), length)


# ============================== strategies ==============================
# Each signal fn returns (long_entry, short_entry, bull_bias, bear_bias, trending).
# The bias/trend flags drive the shared exit rule, so both strategies are judged
# on entry quality alone.

def signal_v1(ind, i, cfg):
    close = ind["close"][i]
    baseline = ind["ema"][cfg["baseline_len"]][i]
    bull, bear = close > baseline, close < baseline
    trending = ind["adx"][i] > cfg["adx_min"]
    rsi = ind["rsi"][i]
    macd_bull = ind["macd"][i] > ind["macd_signal"][i]
    long_ok = bull and rsi > cfg["rsi_long"] and macd_bull and trending
    short_ok = bear and rsi < cfg["rsi_short"] and not macd_bull and trending
    return long_ok, short_ok, bull, bear, trending


def signal_v2(ind, i, cfg):
    close = ind["close"][i]
    baseline = ind["ema"][cfg["baseline_len"]][i]
    bull, bear = close > baseline, close < baseline
    trending = ind["adx"][i] > cfg["adx_min"]
    stoch, mfi = ind["stoch"][i], ind["mfi"][i]
    long_ok = bull and stoch > cfg["stoch_long"] and mfi > cfg["mfi_long"] and trending
    short_ok = bear and stoch < cfg["stoch_short"] and mfi < cfg["mfi_short"] and trending
    return long_ok, short_ok, bull, bear, trending


STRATEGIES = {
    "v1_rsi_macd": {
        "signal": signal_v1, "baseline_len": 100, "adx_min": 20,
        "rsi_long": 55, "rsi_short": 45,
    },
    "v2_stoch_mfi": {
        "signal": signal_v2, "baseline_len": 100, "adx_min": 15,
        "stoch_long": 55, "stoch_short": 45, "mfi_long": 55, "mfi_short": 40,
    },
}


def compute_indicators(closed):
    highs = [b["high"] for b in closed]
    lows = [b["low"] for b in closed]
    closes = [b["close"] for b in closed]
    volumes = [b["volume"] for b in closed]
    macd_line, macd_sig = macd_series(closes)
    lengths = {cfg["baseline_len"] for cfg in STRATEGIES.values()}
    return {
        "close": closes,
        "ema": {n: ema_series(closes, n) for n in lengths},
        "rsi": rsi_series(closes, 14),
        "macd": macd_line, "macd_signal": macd_sig,
        "stoch": stoch_series(highs, lows, closes, 14, 3),
        "mfi": mfi_series(highs, lows, closes, volumes, 14),
        "adx": dmi_series(highs, lows, closes, 14),
        "atr": atr_series(highs, lows, closes, ATR_LEN),
    }


# ============================== one book ==============================

class Book:
    """One strategy trading one symbol, with its own balance and risk state."""

    def __init__(self, strategy, symbol, log_file=LOG_FILE, quiet=False):
        self.strategy, self.symbol, self.quiet = strategy, symbol, quiet
        self.name = f"{strategy}/{symbol}"
        self.log_file = log_file
        self.cash_equity = INITIAL_CAPITAL
        self.peak_equity = INITIAL_CAPITAL
        self.consec_losses = 0
        self.position = None
        self.halted_until_bar = 0
        self.bar_count = 0
        self.last_close_ms = 0
        self.wins = 0
        self.losses = 0

    # ---- state ----
    def to_dict(self):
        return {k: getattr(self, k) for k in
                ("cash_equity", "peak_equity", "consec_losses", "position",
                 "halted_until_bar", "bar_count", "last_close_ms", "wins", "losses")}

    def load(self, d):
        for k, v in d.items():
            if hasattr(self, k):
                setattr(self, k, v)

    # ---- accounting ----
    def mark_to_market(self, price):
        """Equity including unrealized P&L — matches Pine's strategy.equity."""
        if not self.position:
            return self.cash_equity
        p = self.position
        delta = (price - p["entry"]) if p["side"] == "long" else (p["entry"] - price)
        return self.cash_equity + delta * p["qty"]

    def _log(self, event, side, price, bar_ms, note=""):
        stamp = (datetime.fromtimestamp(bar_ms / 1000, timezone.utc).isoformat()
                 if bar_ms else datetime.now(timezone.utc).isoformat())
        with open(self.log_file, "a", newline="") as f:
            csv.writer(f).writerow([stamp, self.strategy, self.symbol, event, side,
                                    f"{price:.4f}", f"{self.cash_equity:.2f}", note])
        if not self.quiet:
            print(f"[{event:5}] {self.name:22} {side:5} @ {price:,.4f}  "
                  f"equity=${self.cash_equity:,.2f}  {note}")

    def _close_position(self, exit_price, reason, bar_ms):
        p = self.position
        gross = ((exit_price - p["entry"]) if p["side"] == "long"
                 else (p["entry"] - exit_price))
        pnl = gross * p["qty"] - (p["entry"] + exit_price) * p["qty"] * (COMMISSION_PCT / 100)
        self.cash_equity += pnl
        if pnl < 0:
            self.consec_losses += 1
            self.losses += 1
        else:
            self.consec_losses = 0
            self.wins += 1
        self.position = None
        self._log("EXIT", p["side"], exit_price, bar_ms, f"{reason} pnl={pnl:+.2f}")

    def _open_position(self, side, price, atr, bar_ms):
        stop_dist = atr * ATR_MULT_SL
        if stop_dist <= 0 or price <= 0 or self.cash_equity <= 0:
            return  # zero-volatility bar or blown account — nothing sane to size
        sl = price - stop_dist if side == "long" else price + stop_dist
        tp = price + stop_dist * RR if side == "long" else price - stop_dist * RR
        self.position = {"side": side, "entry": price,
                         "qty": self.cash_equity / price, "sl": sl, "tp": tp}
        self._log("ENTRY", side, price, bar_ms, f"sl={sl:,.4f} tp={tp:,.4f}")

    def on_bar(self, bar, sig, atr):
        """sig = (long_entry, short_entry, bull_bias, bear_bias, trending)."""
        long_ok, short_ok, bull, bear, trending = sig
        close, high, low = bar["close"], bar["high"], bar["low"]
        bar_ms = bar.get("closeTime", 0)
        self.bar_count += 1

        # 1. Intrabar stop / target. Stop checked first: when one bar's range spans
        #    both levels we can't know the order, so assume the losing one.
        exited_intrabar = False
        if self.position:
            p = self.position
            if p["side"] == "long":
                if low <= p["sl"]:
                    self._close_position(p["sl"], "stop-loss", bar_ms); exited_intrabar = True
                elif high >= p["tp"]:
                    self._close_position(p["tp"], "take-profit", bar_ms); exited_intrabar = True
            else:
                if high >= p["sl"]:
                    self._close_position(p["sl"], "stop-loss", bar_ms); exited_intrabar = True
                elif low <= p["tp"]:
                    self._close_position(p["tp"], "take-profit", bar_ms); exited_intrabar = True

        # 2. Risk halts, measured on mark-to-market equity like the backtest.
        equity_now = self.mark_to_market(close)
        self.peak_equity = max(self.peak_equity, equity_now)
        dd_pct = ((self.peak_equity - equity_now) / self.peak_equity * 100
                  if self.peak_equity else 0.0)
        if self.bar_count >= self.halted_until_bar:
            if self.consec_losses >= MAX_CONSEC_LOSS or dd_pct >= MAX_DD_PCT:
                self.halted_until_bar = self.bar_count + HALT_COOLDOWN_BARS
                self._log("HALT", "-", close, bar_ms,
                          f"losses={self.consec_losses} dd={dd_pct:.1f}% "
                          f"cooldown={HALT_COOLDOWN_BARS} bars")
                # Clear the trip conditions so the cooldown actually ends the halt.
                # Side effect: drawdown is then measured from the halt point, not
                # the all-time peak. Deliberate — the alternative is a book that
                # locks itself out forever on its first bad run.
                self.consec_losses = 0
                self.peak_equity = equity_now
        halted = self.bar_count < self.halted_until_bar

        # 3. Signal flatten at close.
        exited_at_close = False
        if self.position:
            p = self.position
            if (p["side"] == "long" and (bear or not trending)) or \
               (p["side"] == "short" and (bull or not trending)):
                self._close_position(close, "signal-flatten", bar_ms)
                exited_at_close = True

        # 4. Entry. Pine evaluates `strategy.position_size == 0` before this bar's
        #    close orders fill, so a signal-flatten blocks same-bar reversal while
        #    an intrabar stop-out does not. Mirroring that keeps parity with the
        #    backtests these params came from.
        if self.position is None and not exited_at_close and not halted:
            if long_ok:
                self._open_position("long", close, atr, bar_ms)
            elif short_ok:
                self._open_position("short", close, atr, bar_ms)

        if bar_ms:
            self.last_close_ms = bar_ms
        return {"exited_intrabar": exited_intrabar,
                "exited_at_close": exited_at_close, "halted": halted}


# ============================== portfolio ==============================

def make_books(quiet=False):
    return [Book(s, sym, quiet=quiet) for s in STRATEGIES for sym in SYMBOLS]


def save_books(books, path=STATE_FILE):
    tmp = f"{path}.tmp"
    with open(tmp, "w") as f:
        json.dump({"books": {b.name: b.to_dict() for b in books}}, f, indent=2)
    os.replace(tmp, path)  # atomic: no half-written state on crash


def load_books(books, path=STATE_FILE):
    if not os.path.exists(path):
        return False
    try:
        with open(path) as f:
            saved = json.load(f).get("books", {})
    except (json.JSONDecodeError, OSError) as e:
        print(f"warning: could not read {path} ({e}); starting fresh")
        return False
    for b in books:
        if b.name in saved:
            b.load(saved[b.name])
    return bool(saved)


def ensure_log(path=LOG_FILE):
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        with open(path, "w", newline="") as f:
            csv.writer(f).writerow(["bar_time", "strategy", "symbol", "event",
                                    "side", "price", "equity", "note"])


def report(path=STATE_FILE):
    if not os.path.exists(path):
        print("no state yet — run the simulator first")
        return
    with open(path) as f:
        saved = json.load(f).get("books", {})
    print(f"\n{'book':24} {'equity':>12} {'return':>9} {'W/L':>8} {'position':>10}")
    print("-" * 68)
    by_strategy = {}
    for name, s in sorted(saved.items()):
        eq = s["cash_equity"]
        ret = (eq / INITIAL_CAPITAL - 1) * 100
        pos = s["position"]["side"] if s.get("position") else "flat"
        w, l = s.get("wins", 0), s.get("losses", 0)
        print(f"{name:24} ${eq:>11,.2f} {ret:>8.2f}% {f'{w}/{l}':>8} {pos:>10}")
        by_strategy.setdefault(name.split("/")[0], []).append((eq, w, l))
    print("-" * 68)
    for strat, rows in sorted(by_strategy.items()):
        total_eq = sum(r[0] for r in rows)
        base = INITIAL_CAPITAL * len(rows)
        w = sum(r[1] for r in rows); l = sum(r[2] for r in rows)
        n = w + l
        print(f"{strat:24} ${total_eq:>11,.2f} {(total_eq/base-1)*100:>8.2f}% "
              f"{f'{w}/{l}':>8} {n:>6} trades")
    total_trades = sum(s.get("wins", 0) + s.get("losses", 0) for s in saved.values())
    print(f"\ntotal closed trades: {total_trades}")
    if total_trades < 100:
        print(f"need ~{100 - total_trades} more before the comparison means much "
              "(error bars stay wide below ~100 trades per strategy)")


# ============================== main loop ==============================

def main():
    print(f"Paper trading {len(STRATEGIES)} strategies x {len(SYMBOLS)} symbols "
          f"= {len(STRATEGIES) * len(SYMBOLS)} books, ${INITIAL_CAPITAL:,.0f} each")
    print(f"Symbols: {', '.join(SYMBOLS)}   Interval: {INTERVAL}")
    print(f"Log: {LOG_FILE}   State: {STATE_FILE}   Ctrl+C to stop\n")

    ensure_log()
    books = make_books()
    resumed = load_books(books)
    if resumed:
        for b in books:
            held = f" holding {b.position['side']}" if b.position else ""
            print(f"resumed {b.name:24} ${b.cash_equity:,.2f}{held}")
        print()

    first_pass = True
    while True:
        live = {}
        for symbol in SYMBOLS:
            try:
                bars = fetch_klines(symbol)
            except Exception as e:  # noqa: BLE001 - keep the loop alive through outages
                print(f"fetch error ({symbol}): {e}")
                continue

            closed = bars[:-1]  # drop the still-forming candle
            if len(closed) <= WARMUP_BARS:
                continue
            ind = compute_indicators(closed)
            last = len(closed) - 1
            live[symbol] = {
                "price": ind["close"][last],
                "ema": ind["ema"][STRATEGIES["v1_rsi_macd"]["baseline_len"]][last],
                "rsi": ind["rsi"][last], "stoch": ind["stoch"][last],
                "mfi": ind["mfi"][last], "adx": ind["adx"][last],
                "atr": ind["atr"][last], "bar_ms": closed[last]["closeTime"],
                "reads": {},
            }
            for sname, scfg in STRATEGIES.items():
                lo_ok, sh_ok, _, _, trending = scfg["signal"](ind, last, scfg)
                live[symbol]["reads"][sname] = ("long" if lo_ok else "short" if sh_ok
                                                else "chop" if not trending else "wait")
            sym_books = [b for b in books if b.symbol == symbol]

            for b in sym_books:
                cfg = STRATEGIES[b.strategy]
                if first_pass and b.last_close_ms == 0:
                    # Cold start: history is warmup ONLY. Replaying it as live
                    # trades would book fictional P&L against real timestamps.
                    b.last_close_ms = closed[-1]["closeTime"]
                    continue
                for i, bar in enumerate(closed):
                    if bar["closeTime"] <= b.last_close_ms or i < WARMUP_BARS:
                        continue
                    sig = cfg["signal"](ind, i, cfg)
                    b.on_bar(bar, sig, ind["atr"][i])

        save_books(books)
        try:  # imported lazily: dashboard.py imports this module
            import dashboard
            dashboard.generate(live=live)
        except Exception as e:  # noqa: BLE001 - a render failure must not stop trading
            print(f"dashboard render failed: {e}")
        if first_pass:
            last = datetime.fromtimestamp(
                max(b.last_close_ms for b in books) / 1000, timezone.utc)
            print(f"warmed up (last close {last:%Y-%m-%d %H:%M} UTC) — "
                  "waiting for the next candle\n")
        first_pass = False
        time.sleep(POLL_SECONDS)


# ============================== selftest ==============================

def _bar(o, h, l, c, v=100.0, t=0):
    return {"open": o, "high": h, "low": l, "close": c, "volume": v, "closeTime": t}


def selftest():  # noqa: C901 - a flat list of asserts reads better than helpers
    import tempfile

    # ---- indicators ----
    assert all(abs(x - 5) < 1e-9 for x in ema_series([5.0] * 20, 3)), "EMA of constant"
    rising = [float(i) for i in range(1, 51)]
    assert ema_series(rising, 10)[-1] < rising[-1], "EMA lags a rising series"
    assert abs(sma_series([1.0, 2, 3, 4], 2)[-1] - 3.5) < 1e-9, "SMA window"
    assert abs(rma_series([10.0, 20.0], 2)[-1] - 15.0) < 1e-9, "RMA step"

    # RSI: only-up -> 100, only-down -> 0, and it stays inside [0, 100].
    assert abs(rsi_series([float(i) for i in range(40)], 14)[-1] - 100) < 1e-9, "RSI up"
    assert rsi_series([float(40 - i) for i in range(40)], 14)[-1] < 1e-9, "RSI down"
    mixed = rsi_series([100, 102, 101, 104, 103, 106, 105, 108, 60, 62, 61, 59] * 4, 14)
    assert all(0 <= v <= 100 for v in mixed), "RSI stays in range"

    # MACD: line = ema12 - ema26; on a steady uptrend the line leads its signal.
    line, sig = macd_series([float(i) for i in range(120)])
    assert line[-1] > sig[-1], "MACD bullish in an uptrend"
    line_d, sig_d = macd_series([float(120 - i) for i in range(120)])
    assert line_d[-1] < sig_d[-1], "MACD bearish in a downtrend"

    # Stoch: close at top of range -> 100, bottom -> 0, flat -> no divide by zero.
    hi, lo = [10.0] * 20, [0.0] * 20
    assert abs(stoch_series(hi, lo, [10.0] * 20, 14, 1)[-1] - 100) < 1e-9, "stoch top"
    assert abs(stoch_series(hi, lo, [0.0] * 20, 14, 1)[-1]) < 1e-9, "stoch bottom"
    assert abs(stoch_series(hi, lo, [5.0] * 20, 14, 1)[-1] - 50) < 1e-9, "stoch mid"
    assert stoch_series([1.0] * 5, [1.0] * 5, [1.0] * 5, 3, 1)[-1] == 50.0, "stoch flat"

    # MFI: monotonic up -> 100, monotonic down -> 0.
    n = 20
    h = [float(i + 2) for i in range(n)]; l = [float(i) for i in range(n)]
    c = [float(i + 1) for i in range(n)]
    assert abs(mfi_series(h, l, c, [50.0] * n, 14)[-1] - 100) < 1e-9, "MFI all-up"
    assert mfi_series(list(reversed(h)), list(reversed(l)), list(reversed(c)),
                      [50.0] * n, 14)[-1] < 1e-9, "MFI all-down"

    # ATR converges to a constant range; ADX separates trend from chop.
    assert abs(atr_series([101.0] * 60, [99.0] * 60, [100.0] * 60, 14)[-1] - 2.0) < 1e-6, "ATR"
    th = [100.0 + i for i in range(60)]; tl = [99.0 + i for i in range(60)]
    tc = [99.5 + i for i in range(60)]
    assert dmi_series(th, tl, tc, 14)[-1] > 40, "ADX high in a trend"
    assert dmi_series([101.0] * 60, [99.0] * 60, [100.0] * 60, 14)[-1] < 15, "ADX low in chop"

    # ---- strategy signal wiring ----
    bars = [_bar(100 + i, 101 + i, 99 + i, 100.5 + i) for i in range(200)]
    ind = compute_indicators(bars)
    i = len(bars) - 1
    for sname, cfg in STRATEGIES.items():
        long_ok, short_ok, bull, bear, trending = cfg["signal"](ind, i, cfg)
        assert bull and not bear, f"{sname}: steady uptrend should read bullish"
        assert trending, f"{sname}: steady uptrend should pass the ADX filter"
        assert not short_ok, f"{sname}: must not signal short in an uptrend"
    down = [_bar(300 - i, 301 - i, 299 - i, 299.5 - i) for i in range(200)]
    ind_d = compute_indicators(down)
    for sname, cfg in STRATEGIES.items():
        long_ok, short_ok, bull, bear, trending = cfg["signal"](ind_d, len(down) - 1, cfg)
        assert bear and not bull, f"{sname}: downtrend should read bearish"
        assert not long_ok, f"{sname}: must not signal long in a downtrend"

    # ---- book state machine ----
    with tempfile.TemporaryDirectory() as d:
        log, state = os.path.join(d, "t.csv"), os.path.join(d, "t.json")
        ensure_log(log)

        def fresh():
            return Book("v2_stoch_mfi", "BTCUSDT", log_file=log, quiet=True)

        #        (long_ok, short_ok, bull, bear, trending)
        LONG = (True, False, True, False, True)
        SHORT = (False, True, False, True, True)
        CHOP = (False, False, True, False, False)   # trend filter off -> flatten

        # entry sizing and bracket placement
        b = fresh()
        b.on_bar(_bar(100, 100, 100, 100), LONG, 1.0)
        assert b.position and b.position["side"] == "long", "long entry"
        assert abs(b.position["sl"] - 97.5) < 1e-9, "SL = 2.5 x ATR below entry"
        assert abs(b.position["tp"] - 106.25) < 1e-9, "TP = SL distance x RR"
        assert abs(b.position["qty"] - 100.0) < 1e-9, "sized to full equity"

        # take-profit P&L, commission both sides
        b = fresh()
        b.on_bar(_bar(100, 100, 100, 100), LONG, 1.0)
        b.on_bar(_bar(100, 107, 100, 106), CHOP, 1.0)
        assert b.position is None, "TP closes the position"
        expected = 10000 + (106.25 - 100) * 100 - (100 + 106.25) * 100 * 0.0005
        assert abs(b.cash_equity - expected) < 1e-6, f"TP P&L, got {b.cash_equity}"
        assert b.wins == 1 and b.losses == 0, "win counted"

        # stop-loss P&L and streak
        b = fresh()
        b.on_bar(_bar(100, 100, 100, 100), LONG, 1.0)
        b.on_bar(_bar(100, 100, 97, 98), CHOP, 1.0)
        expected = 10000 + (97.5 - 100) * 100 - (100 + 97.5) * 100 * 0.0005
        assert abs(b.cash_equity - expected) < 1e-6, f"SL P&L, got {b.cash_equity}"
        assert b.consec_losses == 1 and b.losses == 1, "loss counted"

        # ambiguous bar (both levels touched) resolves to the stop
        b = fresh()
        b.on_bar(_bar(100, 100, 100, 100), LONG, 1.0)
        b.on_bar(_bar(100, 107, 97, 100), CHOP, 1.0)
        assert b.cash_equity < 10000, "ambiguous bar resolves to the stop"

        # short side mirrors
        b = fresh()
        b.on_bar(_bar(100, 100, 100, 100), SHORT, 1.0)
        assert b.position["side"] == "short", "short entry"
        assert abs(b.position["sl"] - 102.5) < 1e-9, "short SL above entry"
        assert abs(b.position["tp"] - 93.75) < 1e-9, "short TP below entry"
        b.on_bar(_bar(100, 100, 93, 94), CHOP, 1.0)
        assert b.cash_equity > 10000, "short take-profit is a gain"

        # chop blocks entry, and flattens an open position
        b = fresh()
        b.on_bar(_bar(100, 100, 100, 100), CHOP, 1.0)
        assert b.position is None, "no entry when the trend filter is off"
        b = fresh()
        b.on_bar(_bar(100, 100, 100, 100), LONG, 1.0)
        b.on_bar(_bar(100, 100, 100, 100), CHOP, 1.0)
        assert b.position is None, "chop flattens an open position"

        # signal-flatten must NOT reverse on the same bar (Pine parity)
        b = fresh()
        b.on_bar(_bar(100, 100, 100, 100), LONG, 1.0)
        b.on_bar(_bar(100, 100, 100, 100), SHORT, 1.0)
        assert b.position is None, "no same-bar reversal after signal flatten"
        b.on_bar(_bar(100, 100, 100, 100), SHORT, 1.0)
        assert b.position and b.position["side"] == "short", "reversal on the next bar"

        # an intrabar stop-out DOES free the same bar to re-enter
        b = fresh()
        b.on_bar(_bar(100, 100, 100, 100), LONG, 1.0)
        b.on_bar(_bar(100, 100, 97, 100), LONG, 1.0)
        assert b.position and b.position["side"] == "long", "re-entry after intrabar stop"

        # halt trips on the loss limit and the cooldown releases it
        b = fresh()
        for _ in range(MAX_CONSEC_LOSS):
            b.on_bar(_bar(100, 100, 100, 100), LONG, 1.0)
            b.on_bar(_bar(100, 100, 97, 98), CHOP, 1.0)
        assert b.halted_until_bar > b.bar_count, "halt trips after the loss limit"
        blocked = b.bar_count
        b.on_bar(_bar(100, 100, 100, 100), LONG, 1.0)
        assert b.position is None, "halt blocks new entries"
        while b.bar_count < blocked + HALT_COOLDOWN_BARS:
            b.on_bar(_bar(100, 100, 100, 100), CHOP, 1.0)
        b.on_bar(_bar(100, 100, 100, 100), LONG, 1.0)
        assert b.position is not None, "cooldown releases the halt (no deadlock)"

        # a win clears the streak
        b = fresh()
        b.on_bar(_bar(100, 100, 100, 100), LONG, 1.0)
        b.on_bar(_bar(100, 100, 97, 98), CHOP, 1.0)
        assert b.consec_losses == 1
        b.on_bar(_bar(100, 100, 100, 100), LONG, 1.0)
        b.on_bar(_bar(100, 107, 100, 106), CHOP, 1.0)
        assert b.consec_losses == 0, "a win resets the streak"

        # zero-ATR bar must not open a zero-width stop
        b = fresh()
        b.on_bar(_bar(100, 100, 100, 100), LONG, 0.0)
        assert b.position is None, "no entry when ATR is zero"

        # mark-to-market includes unrealized P&L
        b = fresh()
        b.on_bar(_bar(100, 100, 100, 100), LONG, 1.0)
        assert abs(b.mark_to_market(110) - (10000 + 10 * b.position["qty"])) < 1e-9, "MTM"

        # ---- portfolio: books stay independent and survive a restart ----
        books = make_books(quiet=True)
        assert len(books) == len(STRATEGIES) * len(SYMBOLS), "one book per pair"
        assert len({b.name for b in books}) == len(books), "book names are unique"
        for bk in books:
            bk.log_file = log
        books[0].on_bar(_bar(100, 100, 100, 100), LONG, 1.0)
        assert books[1].position is None, "books do not share position state"
        save_books(books, state)
        restored = make_books(quiet=True)
        assert load_books(restored, state), "state loads"
        assert restored[0].position and restored[0].position["side"] == "long", \
            "position survives restart"
        assert restored[1].position is None, "flat book stays flat after restart"
        assert abs(restored[0].cash_equity - books[0].cash_equity) < 1e-9, "equity restored"

    print("selftest: all checks passed")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    elif "--report" in sys.argv:
        report()
    elif "--reset" in sys.argv:
        for path in (STATE_FILE, LOG_FILE):
            if os.path.exists(path):
                os.remove(path)
                print(f"removed {path}")
    else:
        try:
            main()
        except KeyboardInterrupt:
            print("\nstopped — state saved, rerun to resume")
