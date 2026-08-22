#!/usr/bin/env python3
"""Bob — a multi-market paper-trading simulator. No exchange account, no keys.

Runs BOTH candidate strategies across 32 liquid crypto markets at once. Each
strategy gets ONE $10,000 account and sizes every position at 1/20 of equity,
so the headline number means what it says and the two are directly comparable
to each other and to buy-and-hold.

  v0_control    coin-flip entry, same regime gate and risk model
  v1_rsi_macd   EMA100 baseline + RSI + MACD confluence, ADX20 filter
  v2_stoch_mfi  EMA100 baseline + Stochastic + MFI confluence, ADX15 filter

v0 is the point of the exercise. A 2.5R target with an ATR stop wins ~28% of the
time on chance alone, so a 30% win rate is not evidence of anything. If neither
indicator book beats random entry on mean P&L per trade, the signals are noise
and only the risk model is doing work. Results are also carried against
equal-weight buy-and-hold of the same basket, because "+1.6%" means nothing
without knowing what the market did.

Both wrap the same NNFX risk boilerplate: ATR stop at 2.5x, take-profit at
2.5R, consecutive-loss and max-drawdown halts with a cooldown.

Run:      python3 paper_trader.py
Report:   python3 paper_trader.py --report
Selftest: python3 paper_trader.py --selftest
Reset:    python3 paper_trader.py --reset
Stop:     Ctrl+C  (state is saved; restarting resumes)
"""
import csv
import hashlib
import json
import os
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

BOT_NAME = "Bob"

# 32 markets on 1h rather than fewer markets on a faster timeframe. Commission is
# a fixed toll per trade while the move captured scales with ATR, so dropping the
# timeframe raises the break-even win rate (1h +0.8pp, 15m +1.5pp, 5m +2.6pp,
# 1m +5.6pp above the 28.6% a 2.5R setup needs unpaid). Measured win rate is
# 30.4%, which only clears the 1h bar. More markets buys trade count at the same
# per-trade economics; a faster clock does not.
# Screened 22 Aug 2026: >$20M daily quote volume, spread <0.06%, no stablecoins
# or pegged assets (they do not trend, so a trend filter on them is meaningless).
SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "XRPUSDT", "SOLUSDT", "XPLUSDT", "ZECUSDT", "BNBUSDT",
    "DOGEUSDT", "REUSDT", "SUIUSDT", "LINKUSDT", "ADAUSDT", "PUMPUSDT", "NEARUSDT",
    "TRXUSDT", "AVAXUSDT", "UUSDT", "BCHUSDT", "TAOUSDT", "XLMUSDT", "AAVEUSDT",
    "WLDUSDT", "LTCUSDT", "BOMEUSDT", "UNIUSDT", "TRUMPUSDT", "SPCXBUSDT",
    "ONDOUSDT", "SNDKBUSDT", "TUTUSDT", "CRVUSDT", "NEIROUSDT",
]
INTERVAL = "1h"
HOSTS = ["https://data-api.binance.vision", "https://api.binance.com"]
KLINE_LIMIT = 500
POLL_SECONDS = 120   # 1h bars: no need to poll faster, and 32 markets take a moment
FETCH_WORKERS = 8
LOG_FILE = "paper_trades.csv"
STATE_FILE = "paper_state.json"

# Mirrors dashboard.html to a dedicated orphan branch on the public GitHub repo so
# a cloud /schedule job (which cannot see this Mac's filesystem) can fetch it via
# a plain raw.githubusercontent.com URL and republish the artifact. Amend+force
# rather than a new commit each push — this is a live snapshot, not history worth
# keeping, and a fresh commit every few minutes would spam the repo forever.
GITHUB_SYNC_WORKTREE = "/Users/vaidikpatel/Downloads/Home/Trading_Bot/.bob-live-worktree"
GITHUB_SYNC_BRANCH = "bob-live"
# 600s, not 300: every force-push to bob-live triggers a GitHub Pages rebuild,
# and Pages allows roughly 10 builds an hour. Twelve would throttle and the site
# would fall behind exactly when it looked most current. Six is comfortably
# under, and on 1h candles a ten-minute page is not stale.
GITHUB_PUSH_INTERVAL = 600  # seconds; independent of the trading poll cadence

# ---- Shared risk boilerplate (identical across strategies, so the comparison
# ---- isolates the entry signal rather than the risk model) ----
ATR_LEN = 14
ATR_MULT_SL = 2.5
RR = 2.5
MAX_CONCURRENT = 20      # positions open at once; each sized at 1/20 of equity
# 20 crypto positions are NOT 20 independent bets: majors correlate ~0.8 with BTC,
# so an unconstrained book is one leveraged BTC-direction bet wearing 20 hats. On
# 22 Aug both strategies halted within the same hour, which is what that looks
# like. Capping each side bounds the correlated exposure to ~40% of equity.
MAX_PER_SIDE = 8
MAX_CONSEC_LOSS = 6      # across the whole book now, not per market
# Halving size partway up the streak preserves sample (the scarce resource in a
# forward test) instead of going dark the moment a regime turns.
SOFT_LOSS_STREAK = 3
SOFT_RISK_SCALE = 0.5
MAX_DD_PCT = 15.0
# Fixed notional means every position risks a DIFFERENT amount, because the stop
# sits at 2.5xATR and ATR varies several-fold across these markets. A volatile
# coin was quietly betting many times what a calm one did, which is not the NNFX
# model this claims to implement and makes "2.5R" mean a different thing per
# trade. Size by risk instead, capped at the old notional so total exposure and
# leverage are unchanged — this equalises risk downward on the wild markets
# rather than levering up the quiet ones.
# 0.125% is not tuned: it is the risk a 1%-ATR market already carried under the
# notional cap, so the average position is the same size as before and only the
# distribution across markets changes.
RISK_PCT = 0.125
HALT_COOLDOWN_HOURS = 24  # wall-clock, not bar count: bars arrive from 32 markets
INITIAL_CAPITAL = 10_000.0
COMMISSION_PCT = 0.05    # per side, matches backtest parity profile
# Stops do not fill at the stop price. Crypto gaps through the level, and the 2am
# wick that triggers half of them is exactly when the book is thinnest. Charging
# an adverse half-tick per side makes every number below worse and true.
SLIPPAGE_PCT = 0.02      # per side, always against us
WARMUP_BARS = 150
CHART_BARS = 96          # 4 days of hourly closes, enough to read a position

# The control book. A 2.5R target with an ATR stop already wins ~28% of the time
# on pure chance, and the measured rate is 30.4% — so "Bob wins 30%" is not by
# itself evidence of anything. This enters at random in the same markets, same
# regime filter, same stop, same target, same sizing. If the indicator books
# cannot beat it on mean P&L per trade, the indicators contribute nothing and
# the whole edge is the risk model. STATUS.md already requires this test
# ("shuffle-control every new feature block"); it had never been applied to Bob.
CONTROL_ENTRY_P = 0.10   # per trending bar; frequency need not match, expectancy does
# Five control books, not one. A single coin-flip book is itself a single draw:
# with a 5% tail roughly one seed in twenty clears |t| > 2 on nothing at all,
# and the first seed tried in the 22 Aug replay did exactly that (t = 2.21).
# Comparing one strategy against one control also doubles the noise. Five gives
# a range to place the real books inside, at no cost but five more dicts.
N_CONTROLS = 5


# ============================== data feed ==============================

def fetch_klines(symbol, interval=INTERVAL, limit=KLINE_LIMIT, retries=3):
    """Fetch candles from Binance public market data, with host failover."""
    last_err = None
    for attempt in range(retries):
        for host in HOSTS:
            url = f"{host}/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}"
            req = urllib.request.Request(url, headers={"User-Agent": "bob/3.0"})
            try:
                with urllib.request.urlopen(req, timeout=20) as resp:
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


def fetch_all(symbols):
    """Fetch every market in parallel. A failed market is skipped, not fatal."""
    def one(sym):
        try:
            return sym, fetch_klines(sym)
        except Exception as e:  # noqa: BLE001
            print(f"  fetch failed {sym}: {e}")
            return sym, None
    with ThreadPoolExecutor(max_workers=FETCH_WORKERS) as ex:
        return {s: b for s, b in ex.map(one, symbols) if b}


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


def signal_control(ind, i, cfg):
    """Coin-flip entry. Identical regime gate, exits and risk model to the real
    books, so the only thing that differs is whether the entry carries
    information. Seeded from (symbol, bar) via md5 rather than hash(): PYTHONHASH
    SEED is randomised per process, so hash() would give a different history on
    every restart and the control would never be reproducible."""
    close = ind["close"][i]
    baseline = ind["ema"][cfg["baseline_len"]][i]
    bull, bear = close > baseline, close < baseline
    trending = ind["adx"][i] > cfg["adx_min"]
    if not trending:
        return False, False, bull, bear, trending
    # Seed on (symbol, candle close time) ONLY. The bar index i shifts every
    # poll as the rolling 500-bar window slides, so including it would give the
    # same candle a different coin flip each time it is seen.
    seed = f"{cfg.get('seed', 0)}:{ind['symbol']}:{ind['bar_ms'][i]}"
    digest = hashlib.md5(seed.encode()).digest()
    roll = int.from_bytes(digest[:4], "big") / 2**32          # uniform [0,1)
    if roll >= cfg["entry_p"]:
        return False, False, bull, bear, trending
    go_long = digest[4] & 1
    return bool(go_long), not go_long, bull, bear, trending


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


# Control books first, so they read as the baseline the real books are measured
# against rather than as competitors.
STRATEGIES = {
    **{f"ctrl{k}": {"signal": signal_control, "baseline_len": 100, "adx_min": 20,
                    "entry_p": CONTROL_ENTRY_P, "seed": k}
       for k in range(N_CONTROLS)},
    **STRATEGIES,
}
CONTROL_BOOKS = [f"ctrl{k}" for k in range(N_CONTROLS)]
REAL_BOOKS = [k for k in STRATEGIES if k not in CONTROL_BOOKS]


def control_range(state):
    """(worst, median, best) mean P&L per trade across the live control books."""
    means = sorted(expectancy(state.get(k, {}).get("pnls", []))[0]
                   for k in CONTROL_BOOKS if state.get(k, {}).get("pnls"))
    if not means:
        return None
    return means[0], means[len(means) // 2], means[-1]


def compute_indicators(closed, symbol=""):
    highs = [b["high"] for b in closed]
    lows = [b["low"] for b in closed]
    closes = [b["close"] for b in closed]
    volumes = [b["volume"] for b in closed]
    macd_line, macd_sig = macd_series(closes)
    lengths = {cfg["baseline_len"] for cfg in STRATEGIES.values()}
    return {
        "symbol": symbol,
        "bar_ms": [b["closeTime"] for b in closed],
        "close": closes,
        "ema": {n: ema_series(closes, n) for n in lengths},
        "rsi": rsi_series(closes, 14),
        "macd": macd_line, "macd_signal": macd_sig,
        "stoch": stoch_series(highs, lows, closes, 14, 3),
        "mfi": mfi_series(highs, lows, closes, volumes, 14),
        "adx": dmi_series(highs, lows, closes, 14),
        "atr": atr_series(highs, lows, closes, ATR_LEN),
    }


# ============================== portfolio ==============================

class Portfolio:
    """One strategy's single account, trading many markets from shared equity."""

    def __init__(self, strategy, log_file=LOG_FILE, quiet=False):
        self.strategy, self.log_file, self.quiet = strategy, log_file, quiet
        self.cash_equity = INITIAL_CAPITAL
        self.peak_equity = INITIAL_CAPITAL
        self.consec_losses = 0
        self.halted_until_ms = 0
        self.wins = 0
        self.losses = 0
        self.pnls = []           # every closed trade, for expectancy + t-stat
        self.positions = {}      # symbol -> {side, entry, qty, sl, tp, last}
        self.last_close_ms = {}  # symbol -> ms of last processed candle

    # ---- state ----
    def to_dict(self):
        return {k: getattr(self, k) for k in
                ("cash_equity", "peak_equity", "consec_losses", "halted_until_ms",
                 "wins", "losses", "pnls", "positions", "last_close_ms")}

    def load(self, d):
        for k, v in d.items():
            if hasattr(self, k):
                setattr(self, k, v)

    # ---- accounting ----
    @staticmethod
    def fill(price, side, entering):
        """Adverse fill. Buying (open long / close short) pays up, selling
        receives less."""
        buying = (side == "long") == entering
        return price * (1 + SLIPPAGE_PCT / 100 * (1 if buying else -1))

    def mark_to_market(self):
        """Equity including unrealized P&L on every open position."""
        total = self.cash_equity
        for p in self.positions.values():
            delta = ((p["last"] - p["entry"]) if p["side"] == "long"
                     else (p["entry"] - p["last"]))
            total += delta * p["qty"]
        return total

    def _log(self, event, symbol, side, price, bar_ms, note=""):
        stamp = (datetime.fromtimestamp(bar_ms / 1000, timezone.utc).isoformat()
                 if bar_ms else datetime.now(timezone.utc).isoformat())
        with open(self.log_file, "a", newline="") as f:
            csv.writer(f).writerow([stamp, self.strategy, symbol, event, side,
                                    f"{price:.6f}", f"{self.cash_equity:.2f}", note])
        if not self.quiet:
            print(f"[{event:5}] {self.strategy:13} {symbol:10} {side:5} "
                  f"@ {price:,.6f}  equity=${self.cash_equity:,.2f}  {note}")

    def close_position(self, symbol, exit_price, reason, bar_ms):
        p = self.positions.pop(symbol)
        exit_price = self.fill(exit_price, p["side"], entering=False)
        gross = ((exit_price - p["entry"]) if p["side"] == "long"
                 else (p["entry"] - exit_price))
        pnl = gross * p["qty"] - (p["entry"] + exit_price) * p["qty"] * (COMMISSION_PCT / 100)
        self.cash_equity += pnl
        self.pnls.append(round(pnl, 4))
        if pnl < 0:
            self.consec_losses += 1
            self.losses += 1
        else:
            self.consec_losses = 0
            self.wins += 1
        self._log("EXIT", symbol, p["side"], exit_price, bar_ms,
                  f"{reason} pnl={pnl:+.2f}")

    def open_position(self, symbol, side, price, atr, bar_ms):
        stop_dist = atr * ATR_MULT_SL
        if stop_dist <= 0 or price <= 0 or self.cash_equity <= 0:
            return  # zero-volatility bar or blown account — nothing sane to size
        if len(self.positions) >= MAX_CONCURRENT:
            return  # book is full
        if sum(1 for q in self.positions.values() if q["side"] == side) >= MAX_PER_SIDE:
            return  # correlated-exposure cap: these markets move together
        price = self.fill(price, side, entering=True)
        scale = SOFT_RISK_SCALE if self.consec_losses >= SOFT_LOSS_STREAK else 1.0
        # equal risk per trade, never more notional than the old flat slice
        by_risk = self.cash_equity * (RISK_PCT / 100) / stop_dist
        by_notional = self.cash_equity / MAX_CONCURRENT / price
        qty = min(by_risk, by_notional) * scale
        sl = price - stop_dist if side == "long" else price + stop_dist
        tp = price + stop_dist * RR if side == "long" else price - stop_dist * RR
        self.positions[symbol] = {"side": side, "entry": price,
                                  "qty": qty, "sl": sl, "tp": tp,
                                  "last": price}
        self._log("ENTRY", symbol, side, price, bar_ms, f"sl={sl:,.6f} tp={tp:,.6f}")

    def check_halt(self, now_ms):
        """Portfolio-level risk halts on mark-to-market equity."""
        equity = self.mark_to_market()
        self.peak_equity = max(self.peak_equity, equity)
        dd = ((self.peak_equity - equity) / self.peak_equity * 100
              if self.peak_equity else 0.0)
        if now_ms >= self.halted_until_ms:
            if self.consec_losses >= MAX_CONSEC_LOSS or dd >= MAX_DD_PCT:
                self.halted_until_ms = now_ms + HALT_COOLDOWN_HOURS * 3_600_000
                self._log("HALT", "-", "-", equity, now_ms,
                          f"losses={self.consec_losses} dd={dd:.1f}% "
                          f"cooldown={HALT_COOLDOWN_HOURS}h")
                # Clear the trip conditions so the cooldown actually ends the halt.
                # Drawdown is then measured from the halt point, not the all-time
                # peak — deliberate; the alternative locks the book out forever.
                self.consec_losses = 0
                self.peak_equity = equity
        return now_ms < self.halted_until_ms

    def on_bar(self, symbol, bar, sig, atr):
        """sig = (long_entry, short_entry, bull_bias, bear_bias, trending)."""
        long_ok, short_ok, bull, bear, trending = sig
        close, high, low = bar["close"], bar["high"], bar["low"]
        bar_ms = bar.get("closeTime", 0)

        p = self.positions.get(symbol)
        if p:
            p["last"] = close

        # 1. Intrabar stop / target. Stop checked first: when one bar's range spans
        #    both levels we can't know the order, so assume the losing one.
        exited_intrabar = False
        if p:
            if p["side"] == "long":
                if low <= p["sl"]:
                    self.close_position(symbol, p["sl"], "stop-loss", bar_ms); exited_intrabar = True
                elif high >= p["tp"]:
                    self.close_position(symbol, p["tp"], "take-profit", bar_ms); exited_intrabar = True
            else:
                if high >= p["sl"]:
                    self.close_position(symbol, p["sl"], "stop-loss", bar_ms); exited_intrabar = True
                elif low <= p["tp"]:
                    self.close_position(symbol, p["tp"], "take-profit", bar_ms); exited_intrabar = True

        halted = self.check_halt(bar_ms)

        # 2. Signal flatten at close.
        exited_at_close = False
        p = self.positions.get(symbol)
        if p:
            if (p["side"] == "long" and (bear or not trending)) or \
               (p["side"] == "short" and (bull or not trending)):
                self.close_position(symbol, close, "signal-flatten", bar_ms)
                exited_at_close = True

        # 3. Entry. Pine evaluates `strategy.position_size == 0` before this bar's
        #    close orders fill, so a signal-flatten blocks same-bar reversal while
        #    an intrabar stop-out does not. Mirroring that keeps parity with the
        #    backtests these params came from.
        if symbol not in self.positions and not exited_at_close and not halted:
            if long_ok:
                self.open_position(symbol, "long", close, atr, bar_ms)
            elif short_ok:
                self.open_position(symbol, "short", close, atr, bar_ms)

        if bar_ms:
            self.last_close_ms[symbol] = bar_ms
        return {"exited_intrabar": exited_intrabar,
                "exited_at_close": exited_at_close, "halted": halted}


# ============================== benchmark ==============================

class Benchmark:
    """Equal-weight buy-and-hold of the same 32 markets, started the same moment.

    Without this, "+1.6%" is uninterpretable: if the basket rose 8% over the same
    window the book badly lost while looking like a winner. Equal-weight rather
    than BTC-only because the book trades all 32 — the fair alternative to
    "trade these markets" is "hold these markets"."""

    def __init__(self):
        self.start = {}      # symbol -> first close we ever saw
        self.last = {}       # symbol -> most recent close
        self.start_ms = 0

    def observe(self, symbol, price, bar_ms=0):
        if price and price > 0:
            if symbol not in self.start:
                self.start[symbol] = price
                self.start_ms = self.start_ms or bar_ms
            self.last[symbol] = price

    def equity(self):
        rel = [self.last[s] / self.start[s] for s in self.start
               if s in self.last and self.start[s]]
        return INITIAL_CAPITAL * (sum(rel) / len(rel)) if rel else INITIAL_CAPITAL

    def to_dict(self):
        return {"start": self.start, "last": self.last, "start_ms": self.start_ms}

    def load(self, d):
        self.start = d.get("start", {})
        self.last = d.get("last", {})
        self.start_ms = d.get("start_ms", 0)


# ============================== statistics ==============================

# A t-statistic on a handful of trades is not a weak signal, it is a wrong one.
# Two trades that happen to land near each other give a near-zero standard error
# and a t in the hundreds: on 23 Aug v2 showed t = 610.63 on two wins of $31.12
# and $31.22. Below this many trades the figure is suppressed rather than shown
# small, because a number that looks like evidence is worse than a blank.
MIN_T_TRADES = 10


def fmt_t(pnls):
    """t as text, or an em dash while the sample is too small to mean anything."""
    if len(pnls) < MIN_T_TRADES:
        return "—"
    return f"{expectancy(pnls)[2]:.2f}"


def expectancy(pnls):
    """(mean, standard error, t) on per-trade P&L. t is the only honest way to
    ask whether a return is distinguishable from luck."""
    n = len(pnls)
    if n < 2:
        return (pnls[0] if n else 0.0), 0.0, 0.0
    mean = sum(pnls) / n
    var = sum((x - mean) ** 2 for x in pnls) / (n - 1)
    se = (var / n) ** 0.5
    return mean, se, (mean / se if se else 0.0)


# ============================== persistence ==============================

def make_portfolios(quiet=False):
    return {s: Portfolio(s, quiet=quiet) for s in STRATEGIES}


def save_portfolios(pfs, path=STATE_FILE, bench=None):
    tmp = f"{path}.tmp"
    payload = {"portfolios": {k: p.to_dict() for k, p in pfs.items()}}
    if bench is not None:
        payload["benchmark"] = bench.to_dict()
    with open(tmp, "w") as f:
        json.dump(payload, f, indent=2)
    os.replace(tmp, path)  # atomic: no half-written state on crash


def load_portfolios(pfs, path=STATE_FILE, bench=None):
    if not os.path.exists(path):
        return False
    try:
        with open(path) as f:
            blob = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        print(f"warning: could not read {path} ({e}); starting fresh")
        return False
    saved = blob.get("portfolios", {})
    for k, p in pfs.items():
        if k in saved:
            p.load(saved[k])
    if bench is not None:
        bench.load(blob.get("benchmark", {}))
    return bool(saved)


def sync_dashboard_to_github(dashboard_path="dashboard.html"):
    """Best-effort: copy the dashboard into the bob-live worktree and force-push
    an amended commit. Never raises — a sync failure must not stop trading."""
    import shutil
    import subprocess
    try:
        if not os.path.isdir(GITHUB_SYNC_WORKTREE):
            return False
        # The site is several pages now. dashboard.html is the single-file build
        # kept for one-page hosts (a Claude artifact); index.html and the rest
        # are the real site.
        src_dir = os.path.dirname(os.path.abspath(dashboard_path)) or "."
        for name in ("dashboard.html", "index.html", "orders.html",
                     "markets.html", "research.html"):
            src = os.path.join(src_dir, name)
            if os.path.exists(src):
                shutil.copy(src, os.path.join(GITHUB_SYNC_WORKTREE, name))
        run = lambda *a: subprocess.run(  # noqa: E731
            ["git", *a], cwd=GITHUB_SYNC_WORKTREE, capture_output=True, text=True, timeout=30)
        run("add", "-A")
        status = run("status", "--porcelain")
        if not status.stdout.strip():
            return True  # no change since last sync
        run("-c", "user.email=bob@local", "-c", "user.name=Bob",
            "commit", "--amend", "--no-edit", "-q")
        push = run("push", "--force", "origin", GITHUB_SYNC_BRANCH)
        if push.returncode != 0:
            print(f"github sync push failed: {push.stderr.strip()[:200]}")
            return False
        return True
    except Exception as e:  # noqa: BLE001
        print(f"github sync failed: {e}")
        return False


def write_render_error(exc, count, out="index.html"):
    """Replace the site with an honest error page. Trading is unaffected; this
    only stops the page from lying about how fresh it is."""
    when = datetime.now(timezone.utc).strftime("%d %b %Y %H:%M UTC")
    body = (
        "<title>Bob — dashboard error</title>"
        "<style>body{font:15px/1.6 -apple-system,system-ui,sans-serif;max-width:44rem;"
        "margin:12vh auto;padding:0 1.5rem;background:#0D1418;color:#E4EBEF}"
        "code{background:#18242B;padding:.15em .4em;border-radius:4px;font-size:13px}"
        "h1{font-size:1.4rem;margin-bottom:.6rem}p{color:#8798A5}</style>"
        f"<h1>The dashboard failed to render</h1>"
        f"<p>{count} attempts in a row failed, most recently at {when}.</p>"
        f"<p><code>{str(exc)[:300]}</code></p>"
        "<p><strong>Bob is still trading.</strong> Only the page is broken — "
        "positions, orders and risk limits are unaffected, and the numbers will "
        "reappear as soon as the render is fixed. This page exists so a stale "
        "snapshot is never mistaken for a live one.</p>")
    try:
        for name in (out, "dashboard.html"):
            with open(name, "w") as f:
                f.write(body)
    except OSError:
        pass


def ensure_log(path=LOG_FILE):
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        with open(path, "w", newline="") as f:
            csv.writer(f).writerow(["bar_time", "strategy", "symbol", "event",
                                    "side", "price", "equity", "note"])


def report(path=STATE_FILE):
    if not os.path.exists(path):
        print("no state yet — run Bob first")
        return
    with open(path) as f:
        blob = json.load(f)
    saved = blob.get("portfolios", {})
    bench = Benchmark(); bench.load(blob.get("benchmark", {}))
    bench_eq = bench.equity()
    bench_ret = (bench_eq / INITIAL_CAPITAL - 1) * 100

    print(f"\n{BOT_NAME} — {len(SYMBOLS)} markets, {INTERVAL} candles\n")
    print(f"{'book':16} {'equity':>11} {'return':>8} {'vs hold':>9} "
          f"{'W/L':>8} {'per trade':>11} {'t':>6} {'open':>5} {'halt':>5}")
    print(f"{'':16} {'(incl. open positions)':>11}")
    print("-" * 88)

    def mtm(st):
        """Cash plus unrealised P&L. Cash alone reads as a flat $10,000 for a
        book holding seven open positions, which looks like it has done nothing."""
        total = st["cash_equity"]
        for q in st.get("positions", {}).values():
            delta = ((q["last"] - q["entry"]) if q["side"] == "long"
                     else (q["entry"] - q["last"]))
            total += delta * q["qty"]
        return total

    def line(label, st, equity=None):
        eq = mtm(st) if equity is None else equity
        w, l = st.get("wins", 0), st.get("losses", 0)
        ret = (eq / INITIAL_CAPITAL - 1) * 100
        pnls = st.get("pnls", [])
        mean, se, _ = expectancy(pnls)
        halt = "yes" if st.get("halted_until_ms", 0) > time.time() * 1000 else "no"
        print(f"{label:16} ${eq:>10,.2f} {ret:>7.2f}% {ret - bench_ret:>8.2f}% "
              f"{f'{w}/{l}':>8} {f'{mean:+.2f}':>11} {fmt_t(pnls):>6} "
              f"{len(st.get('positions', {})):>5} {halt:>5}")
        return w + l

    total = 0
    # controls collapse to one row: they are the yardstick, not competitors
    ctrls = [saved[k] for k in CONTROL_BOOKS if k in saved]
    if ctrls:
        # Aggregate, not the median book. Early on most books sit at exactly
        # $10,000, so "the median" is whichever one has not traded yet and the
        # row reported 0/0 beside a non-zero total.
        agg = {
            "cash_equity": sum(st["cash_equity"] for st in ctrls) / len(ctrls),
            "wins": sum(st.get("wins", 0) for st in ctrls),
            "losses": sum(st.get("losses", 0) for st in ctrls),
            "pnls": [x for st in ctrls for x in st.get("pnls", [])],
            "positions": {f"{i}:{k}": v for i, st in enumerate(ctrls)
                          for k, v in st.get("positions", {}).items()},
            "halted_until_ms": max(st.get("halted_until_ms", 0) for st in ctrls),
        }
        # mean of each book's own mark-to-market. Pooling the positions onto one
        # mean cash figure would add five books' unrealised P&L to one account.
        total += line(f"control x{len(ctrls)}", agg,
                      equity=sum(mtm(st) for st in ctrls) / len(ctrls))
        print(f"{'':16} {'(pooled across ' + str(len(ctrls)) + ' random-entry books)':>50}")
    for name in REAL_BOOKS:
        if name in saved:
            total += line(name, saved[name])
    print("-" * 88)
    print(f"{'buy & hold':16} ${bench_eq:>10,.2f} {bench_ret:>7.2f}%   "
          f"(equal-weight, same 32 markets, same start)")

    rng = control_range(saved)
    if rng:
        worst, med_m, best = rng
        print(f"\nrandom entry, {len(ctrls)} books:  worst {worst:+.2f}   "
              f"median {med_m:+.2f}   best {best:+.2f} per trade")
        for name in REAL_BOOKS:
            st = saved.get(name, {})
            if not st.get("pnls"):
                continue
            m = expectancy(st["pnls"])[0]
            beaten = sum(1 for k in CONTROL_BOOKS
                         if saved.get(k, {}).get("pnls")
                         and m > expectancy(saved[k]["pnls"])[0])
            flag = "above every control" if beaten == len(ctrls) else "inside chance"
            print(f"  {name:14} {m:+.2f}  beats {beaten}/{len(ctrls)} — {flag}")
        print("A book that does not sit above every control book has shown no "
              "evidence\nits indicators do anything the risk model was not "
              "already doing.")

    print(f"\ntotal closed trades: {total}")
    if total < 200:
        print(f"need ~{200 - total} more before the comparison means much "
              "(~100 per book)")
    print("|t| > 2 is the bar for calling a result real. Everything below that "
          "is noise.\n")


# ============================== main loop ==============================

def main():
    n_books = len(STRATEGIES)
    print(f"{BOT_NAME} — {n_books} strategies x {len(SYMBOLS)} markets, "
          f"${INITIAL_CAPITAL:,.0f} per strategy")
    print(f"Position size: 1/{MAX_CONCURRENT} of equity  ·  max {MAX_CONCURRENT} open")
    print(f"Interval: {INTERVAL}  ·  poll {POLL_SECONDS}s")
    print(f"Log: {LOG_FILE}   State: {STATE_FILE}   Ctrl+C to stop\n")

    ensure_log()
    pfs = make_portfolios()
    bench = Benchmark()
    if load_portfolios(pfs, bench=bench):
        for k, p in pfs.items():
            print(f"resumed {k:14} ${p.cash_equity:,.2f}  "
                  f"{len(p.positions)} open  {p.wins}W/{p.losses}L")
        print()

    first_pass = True
    last_push = 0.0
    render_fails = 0
    while True:
        t0 = time.time()
        data = fetch_all(SYMBOLS)
        live = {}

        for symbol, bars in data.items():
            closed = bars[:-1]  # drop the still-forming candle
            if len(closed) <= WARMUP_BARS:
                continue
            ind = compute_indicators(closed, symbol)
            last = len(closed) - 1
            live[symbol] = {
                "price": ind["close"][last],
                # The still-forming candle, for DISPLAY only. Trading decisions
                # and the halt logic stay on closed candles -- marking to an
                # unclosed bar would let a wick trip a drawdown halt. But a page
                # that only moves once an hour looks frozen, and the position
                # P&L a reader wants is the one at the current price.
                "now": bars[-1]["close"],
                "ema": ind["ema"][STRATEGIES["v1_rsi_macd"]["baseline_len"]][last],
                "rsi": ind["rsi"][last], "stoch": ind["stoch"][last],
                "mfi": ind["mfi"][last], "adx": ind["adx"][last],
                "atr": ind["atr"][last], "bar_ms": closed[last]["closeTime"],
                # recent closes so the dashboard can draw a price chart without
                # an external script (artifact pages block every remote host)
                "hist": [round(c, 8) for c in ind["close"][-CHART_BARS:]],
                "reads": {},
            }
            bench.observe(symbol, ind["close"][last], closed[last]["closeTime"])
            for sname, scfg in STRATEGIES.items():
                lo_ok, sh_ok, _, _, trending = scfg["signal"](ind, last, scfg)
                live[symbol]["reads"][sname] = ("long" if lo_ok else "short" if sh_ok
                                                else "chop" if not trending else "wait")
                p = pfs[sname]
                if first_pass and symbol not in p.last_close_ms:
                    # Cold start: history is warmup ONLY. Replaying it as live
                    # trades would book fictional P&L against real timestamps.
                    p.last_close_ms[symbol] = closed[last]["closeTime"]
                    continue
                for i, bar in enumerate(closed):
                    if bar["closeTime"] <= p.last_close_ms.get(symbol, 0) or i < WARMUP_BARS:
                        continue
                    p.on_bar(symbol, bar, scfg["signal"](ind, i, scfg), ind["atr"][i])

        save_portfolios(pfs, bench=bench)
        try:  # imported lazily: dashboard.py imports this module
            import importlib

            import dashboard
            # Reload every poll: without it Python caches the module for the life
            # of the process, so a fix to dashboard.py never reaches a running Bob
            # (this silently broke rendering for 12 hours on 22 Aug).
            importlib.reload(dashboard)
            dashboard.generate(live=live, bench=bench.equity())
            render_fails = 0
        except Exception as e:  # noqa: BLE001 - a render failure must not stop trading
            import traceback
            render_fails += 1
            print(f"dashboard render failed ({render_fails}x): {e}")
            traceback.print_exc()
            # A failing render leaves the last good page in place, so the site
            # keeps serving a frozen snapshot that looks current — which is how
            # the dashboard went unnoticed-dead for 12 hours on 22 Aug. After a
            # few consecutive failures, say so on the page itself.
            if render_fails >= 3:
                write_render_error(e, render_fails)
                sync_dashboard_to_github()   # push it, or the site keeps lying

        if time.time() - last_push >= GITHUB_PUSH_INTERVAL:
            if sync_dashboard_to_github():
                last_push = time.time()

        if first_pass:
            print(f"warmed up on {len(data)} markets in {time.time() - t0:.0f}s — "
                  "waiting for the next candle\n")
        first_pass = False
        time.sleep(max(POLL_SECONDS - (time.time() - t0), 5))


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
    assert abs(rsi_series([float(i) for i in range(40)], 14)[-1] - 100) < 1e-9, "RSI up"
    assert rsi_series([float(40 - i) for i in range(40)], 14)[-1] < 1e-9, "RSI down"
    mixed = rsi_series([100, 102, 101, 104, 103, 106, 105, 108, 60, 62, 61, 59] * 4, 14)
    assert all(0 <= v <= 100 for v in mixed), "RSI stays in range"
    line, sig = macd_series([float(i) for i in range(120)])
    assert line[-1] > sig[-1], "MACD bullish in an uptrend"
    line_d, sig_d = macd_series([float(120 - i) for i in range(120)])
    assert line_d[-1] < sig_d[-1], "MACD bearish in a downtrend"
    hi, lo = [10.0] * 20, [0.0] * 20
    assert abs(stoch_series(hi, lo, [10.0] * 20, 14, 1)[-1] - 100) < 1e-9, "stoch top"
    assert abs(stoch_series(hi, lo, [0.0] * 20, 14, 1)[-1]) < 1e-9, "stoch bottom"
    assert stoch_series([1.0] * 5, [1.0] * 5, [1.0] * 5, 3, 1)[-1] == 50.0, "stoch flat"
    n = 20
    h = [float(i + 2) for i in range(n)]; l = [float(i) for i in range(n)]
    c = [float(i + 1) for i in range(n)]
    assert abs(mfi_series(h, l, c, [50.0] * n, 14)[-1] - 100) < 1e-9, "MFI all-up"
    assert mfi_series(list(reversed(h)), list(reversed(l)), list(reversed(c)),
                      [50.0] * n, 14)[-1] < 1e-9, "MFI all-down"
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
        assert bull and not bear and trending, f"{sname}: uptrend reads bullish"
        assert not short_ok, f"{sname}: no short in an uptrend"
    down = [_bar(300 - i, 301 - i, 299 - i, 299.5 - i) for i in range(200)]
    ind_d = compute_indicators(down)
    for sname, cfg in STRATEGIES.items():
        long_ok, short_ok, bull, bear, trending = cfg["signal"](ind_d, len(down) - 1, cfg)
        assert bear and not bull, f"{sname}: downtrend reads bearish"
        assert not long_ok, f"{sname}: no long in a downtrend"

    # ---- portfolio state machine ----
    with tempfile.TemporaryDirectory() as d:
        log, state = os.path.join(d, "t.csv"), os.path.join(d, "t.json")
        ensure_log(log)

        def fresh():
            return Portfolio("v2_stoch_mfi", log_file=log, quiet=True)

        LONG = (True, False, True, False, True)
        SHORT = (False, True, False, True, True)
        CHOP = (False, False, True, False, False)
        HOUR = 3_600_000

        # sizing: 1/MAX_CONCURRENT of equity, not the whole account
        p = fresh()
        p.on_bar("BTCUSDT", _bar(100, 100, 100, 100, t=HOUR), LONG, 1.0)
        pos = p.positions["BTCUSDT"]
        assert abs(pos["qty"] * pos["entry"] - INITIAL_CAPITAL / MAX_CONCURRENT) < 1e-9, \
            "position notional = equity / MAX_CONCURRENT"
        entry = Portfolio.fill(100, "long", True)
        assert entry > 100, "slippage makes a long fill worse than the signal price"
        assert abs(pos["entry"] - entry) < 1e-9, "entry is the slipped fill"
        assert abs(pos["sl"] - (entry - 2.5)) < 1e-9 and \
               abs(pos["tp"] - (entry + 6.25)) < 1e-9, "brackets hang off the fill"

        # risk-based sizing: a wild market gets cut down, a calm one keeps the cap
        p = fresh()
        p.on_bar("CALM", _bar(100, 100, 100, 100, t=HOUR), LONG, 0.2)   # ATR 0.2%
        p.on_bar("WILD", _bar(100, 100, 100, 100, t=HOUR), LONG, 5.0)   # ATR 5%
        calm, wild = p.positions["CALM"], p.positions["WILD"]
        assert calm["qty"] * calm["entry"] <= INITIAL_CAPITAL / MAX_CONCURRENT + 1e-9, \
            "calm market is capped by notional, never levered up"
        assert wild["qty"] * wild["entry"] < calm["qty"] * calm["entry"] / 2, \
            "wild market takes a much smaller position"
        risk = lambda q: abs(q["entry"] - q["sl"]) * q["qty"]  # noqa: E731
        assert risk(wild) <= INITIAL_CAPITAL * RISK_PCT / 100 + 1e-9, \
            "no trade risks more than RISK_PCT of equity"
        assert risk(wild) > risk(calm), \
            "the cap still leaves calm markets risking less, not more"

        # many markets share one account
        p = fresh()
        for k, sym in enumerate(["BTCUSDT", "ETHUSDT", "SOLUSDT"]):
            p.on_bar(sym, _bar(100, 100, 100, 100, t=HOUR * (k + 1)), LONG, 1.0)
        assert len(p.positions) == 3, "three markets open at once"
        assert p.cash_equity == INITIAL_CAPITAL, "cash unchanged while positions are open"

        # correlated-exposure cap: one direction cannot fill the whole book
        p = fresh()
        for k in range(MAX_CONCURRENT + 5):
            p.on_bar(f"SYM{k}", _bar(100, 100, 100, 100, t=HOUR), LONG, 1.0)
        assert len(p.positions) == MAX_PER_SIDE, "one-sided book stops at MAX_PER_SIDE"
        assert all(q["side"] == "long" for q in p.positions.values())
        for k in range(MAX_CONCURRENT + 5):   # the other side is still available
            p.on_bar(f"OPP{k}", _bar(100, 100, 100, 100, t=HOUR), SHORT, 1.0)
        assert len(p.positions) == 2 * MAX_PER_SIDE, "each side capped independently"
        assert 2 * MAX_PER_SIDE <= MAX_CONCURRENT, "side caps must fit inside the book"

        # soft de-risking: size halves partway up a loss streak, before the halt
        p = fresh()
        t = HOUR
        for _ in range(SOFT_LOSS_STREAK):
            p.on_bar("BTCUSDT", _bar(100, 100, 100, 100, t=t), LONG, 1.0); t += HOUR
            p.on_bar("BTCUSDT", _bar(100, 100, 97, 98, t=t), CHOP, 1.0); t += HOUR
        assert p.consec_losses == SOFT_LOSS_STREAK and p.halted_until_ms == 0, \
            "soft tier engages before the hard halt"
        p.on_bar("ETHUSDT", _bar(100, 100, 100, 100, t=t), LONG, 1.0)
        notional = p.positions["ETHUSDT"]["qty"] * p.positions["ETHUSDT"]["entry"]
        assert abs(notional - p.cash_equity / MAX_CONCURRENT * SOFT_RISK_SCALE) < 1e-9, \
            "position halves on a loss streak instead of the book going dark"

        # take-profit P&L on 1/20 sizing, commission both sides
        p = fresh()
        p.on_bar("BTCUSDT", _bar(100, 100, 100, 100, t=HOUR), LONG, 1.0)
        qty = p.positions["BTCUSDT"]["qty"]
        p.on_bar("BTCUSDT", _bar(100, 107, 100, 106, t=HOUR * 2), CHOP, 1.0)
        assert "BTCUSDT" not in p.positions, "TP closes"
        e_in = Portfolio.fill(100, "long", True)
        e_out = Portfolio.fill(e_in + 6.25, "long", False)
        expected = INITIAL_CAPITAL + (e_out - e_in) * qty - (e_in + e_out) * qty * 0.0005
        assert abs(p.cash_equity - expected) < 1e-6, f"TP P&L, got {p.cash_equity}"
        assert p.wins == 1, "win counted"

        # stop-loss
        p = fresh()
        p.on_bar("BTCUSDT", _bar(100, 100, 100, 100, t=HOUR), LONG, 1.0)
        qty = p.positions["BTCUSDT"]["qty"]
        p.on_bar("BTCUSDT", _bar(100, 100, 97, 98, t=HOUR * 2), CHOP, 1.0)
        e_in = Portfolio.fill(100, "long", True)
        e_out = Portfolio.fill(e_in - 2.5, "long", False)
        expected = INITIAL_CAPITAL + (e_out - e_in) * qty - (e_in + e_out) * qty * 0.0005
        assert abs(p.cash_equity - expected) < 1e-6, "SL P&L"
        assert p.consec_losses == 1 and p.losses == 1, "loss counted"

        # ambiguous bar resolves to the stop
        p = fresh()
        p.on_bar("BTCUSDT", _bar(100, 100, 100, 100, t=HOUR), LONG, 1.0)
        p.on_bar("BTCUSDT", _bar(100, 107, 97, 100, t=HOUR * 2), CHOP, 1.0)
        assert p.cash_equity < INITIAL_CAPITAL, "ambiguous bar takes the stop"

        # short side mirrors
        p = fresh()
        p.on_bar("BTCUSDT", _bar(100, 100, 100, 100, t=HOUR), SHORT, 1.0)
        assert p.positions["BTCUSDT"]["side"] == "short"
        s_in = Portfolio.fill(100, "short", True)
        assert s_in < 100, "slippage makes a short fill worse than the signal price"
        assert abs(p.positions["BTCUSDT"]["tp"] - (s_in - 6.25)) < 1e-9, "short TP below entry"
        p.on_bar("BTCUSDT", _bar(100, 100, 93, 94, t=HOUR * 2), CHOP, 1.0)
        assert p.cash_equity > INITIAL_CAPITAL, "short TP is a gain"

        # chop blocks entry and flattens
        p = fresh()
        p.on_bar("BTCUSDT", _bar(100, 100, 100, 100, t=HOUR), CHOP, 1.0)
        assert not p.positions, "no entry in chop"
        p.on_bar("BTCUSDT", _bar(100, 100, 100, 100, t=HOUR), LONG, 1.0)
        p.on_bar("BTCUSDT", _bar(100, 100, 100, 100, t=HOUR * 2), CHOP, 1.0)
        assert not p.positions, "chop flattens"

        # no same-bar reversal after a signal flatten; next bar is fine
        p = fresh()
        p.on_bar("BTCUSDT", _bar(100, 100, 100, 100, t=HOUR), LONG, 1.0)
        p.on_bar("BTCUSDT", _bar(100, 100, 100, 100, t=HOUR * 2), SHORT, 1.0)
        assert "BTCUSDT" not in p.positions, "no same-bar reversal"
        p.on_bar("BTCUSDT", _bar(100, 100, 100, 100, t=HOUR * 3), SHORT, 1.0)
        assert p.positions["BTCUSDT"]["side"] == "short", "reversal next bar"

        # intrabar stop-out DOES free the same bar
        p = fresh()
        p.on_bar("BTCUSDT", _bar(100, 100, 100, 100, t=HOUR), LONG, 1.0)
        p.on_bar("BTCUSDT", _bar(100, 100, 97, 100, t=HOUR * 2), LONG, 1.0)
        assert p.positions.get("BTCUSDT", {}).get("side") == "long", "re-entry after stop"

        # portfolio-level loss streak halts the whole book, cooldown releases it
        p = fresh()
        t = HOUR
        for k in range(MAX_CONSEC_LOSS):
            p.on_bar("BTCUSDT", _bar(100, 100, 100, 100, t=t), LONG, 1.0); t += HOUR
            p.on_bar("BTCUSDT", _bar(100, 100, 97, 98, t=t), CHOP, 1.0); t += HOUR
        assert p.halted_until_ms > t, "halt trips on the loss limit"
        p.on_bar("ETHUSDT", _bar(100, 100, 100, 100, t=t), LONG, 1.0)
        assert "ETHUSDT" not in p.positions, "halt blocks every market, not just one"
        t = p.halted_until_ms + HOUR
        p.on_bar("ETHUSDT", _bar(100, 100, 100, 100, t=t), LONG, 1.0)
        assert "ETHUSDT" in p.positions, "cooldown releases the halt (no deadlock)"

        # a win clears the streak
        p = fresh()
        p.on_bar("BTCUSDT", _bar(100, 100, 100, 100, t=HOUR), LONG, 1.0)
        p.on_bar("BTCUSDT", _bar(100, 100, 97, 98, t=HOUR * 2), CHOP, 1.0)
        assert p.consec_losses == 1
        p.on_bar("BTCUSDT", _bar(100, 100, 100, 100, t=HOUR * 3), LONG, 1.0)
        p.on_bar("BTCUSDT", _bar(100, 107, 100, 106, t=HOUR * 4), CHOP, 1.0)
        assert p.consec_losses == 0, "a win resets the streak"

        # zero ATR opens nothing
        p = fresh()
        p.on_bar("BTCUSDT", _bar(100, 100, 100, 100, t=HOUR), LONG, 0.0)
        assert not p.positions, "no entry when ATR is zero"

        # mark-to-market covers every open position
        p = fresh()
        p.on_bar("BTCUSDT", _bar(100, 100, 100, 100, t=HOUR), LONG, 1.0)
        p.on_bar("ETHUSDT", _bar(100, 100, 100, 100, t=HOUR), LONG, 1.0)
        for pos in p.positions.values():
            pos["last"] = 110
        gain = sum((110 - x["entry"]) * x["qty"] for x in p.positions.values())
        assert abs(p.mark_to_market() - (INITIAL_CAPITAL + gain)) < 1e-9, "MTM"

        # strategies are independent, and state survives a restart
        pfs = make_portfolios(quiet=True)
        for x in pfs.values():
            x.log_file = log
        pfs["v1_rsi_macd"].on_bar("BTCUSDT", _bar(100, 100, 100, 100, t=HOUR), LONG, 1.0)
        assert not pfs["v2_stoch_mfi"].positions, "strategies do not share positions"
        save_portfolios(pfs, state)
        restored = make_portfolios(quiet=True)
        assert load_portfolios(restored, state), "state loads"
        assert restored["v1_rsi_macd"].positions["BTCUSDT"]["side"] == "long", \
            "position survives restart"
        assert not restored["v2_stoch_mfi"].positions, "flat book stays flat"

    # ---- slippage is always adverse ----
    assert Portfolio.fill(100, "long", True) > 100, "open long pays up"
    assert Portfolio.fill(100, "long", False) < 100, "close long sells lower"
    assert Portfolio.fill(100, "short", True) < 100, "open short sells lower"
    assert Portfolio.fill(100, "short", False) > 100, "close short buys back higher"

    # ---- control book ----
    cfg = STRATEGIES[CONTROL_BOOKS[0]]
    ind = {"symbol": "BTCUSDT", "close": [100.0] * 300,
           "ema": {100: [50.0] * 300}, "adx": [30.0] * 300,
           "bar_ms": [i * 3_600_000 for i in range(300)]}
    fires = [signal_control(ind, i, cfg) for i in range(300)]
    # the same candle must flip the same way after the window slides, or the
    # control is not a fixed strategy at all
    slid = {**ind, "bar_ms": ind["bar_ms"][10:] + [0] * 10}
    assert [signal_control(slid, i, cfg) for i in range(290)] == fires[10:], \
        "control depends on the candle, not on its index in the window"
    # reproducible across processes: md5, not hash(), so a restart replays the
    # same coin flips instead of inventing a new history
    assert fires == [signal_control(ind, i, cfg) for i in range(300)], "control is deterministic"
    entries = [f for f in fires if f[0] or f[1]]
    assert 0.03 < len(entries) / 300 < 0.20, \
        f"control entry rate near {CONTROL_ENTRY_P}, got {len(entries) / 300:.3f}"
    longs = sum(1 for f in entries if f[0])
    assert 0.25 < longs / len(entries) < 0.75, "control direction is a fair coin"
    assert not any(f[0] and f[1] for f in fires), "never long and short at once"
    # different seeds must give genuinely different books, or five controls are
    # one control counted five times
    others = [[signal_control(ind, i, STRATEGIES[k], ) for i in range(300)]
              for k in CONTROL_BOOKS[1:]]
    assert all(o != fires for o in others), "each control seed is a distinct book"
    assert len({tuple(map(tuple, o)) for o in others}) == len(others), \
        "control seeds do not collide with each other"

    chop = dict(ind, adx=[5.0] * 300)
    assert not any(signal_control(chop, i, cfg)[:2] != (False, False) for i in range(300)), \
        "control respects the same regime filter as the real books"

    # ---- expectancy ----
    m, se, t = expectancy([10.0] * 30)
    assert abs(m - 10) < 1e-9 and se == 0.0 and t == 0.0, "zero-variance is not infinite t"
    m, se, t = expectancy([1.0, -1.0] * 50)
    assert abs(m) < 1e-9 and abs(t) < 1e-9, "coin flip has no edge"
    m, se, t = expectancy([5.0, 5.0, 5.0, -1.0])
    assert t > 0 and se > 0, "positive mean gives positive t"
    assert expectancy([]) == (0.0, 0.0, 0.0), "empty is safe"

    # ---- t is suppressed until the sample can support it ----
    assert fmt_t([31.12, 31.22]) == "—", "two near-identical trades must not print a t"
    assert fmt_t([1.0] * (MIN_T_TRADES - 1)) == "—", "just under the bar is still blank"
    assert fmt_t([1.0, -1.0] * MIN_T_TRADES) != "—", "enough trades and t appears"
    sample = [5.0, 5.0, 5.0, -1.0] * 5          # fmt_t rounds to 2dp
    assert abs(float(fmt_t(sample)) - expectancy(sample)[2]) < 0.005

    # ---- benchmark ----
    b = Benchmark()
    assert b.equity() == INITIAL_CAPITAL, "empty benchmark is flat, not a crash"
    b.observe("BTCUSDT", 100.0, 1); b.observe("ETHUSDT", 50.0, 1)
    assert b.equity() == INITIAL_CAPITAL, "no move yet"
    b.observe("BTCUSDT", 120.0, 2); b.observe("ETHUSDT", 40.0, 2)
    # equal weight: +20% and -20% average to zero
    assert abs(b.equity() - INITIAL_CAPITAL) < 1e-9, "equal-weight average"
    b.observe("BTCUSDT", 200.0, 3)
    assert b.equity() > INITIAL_CAPITAL, "basket tracks the survivor"
    b.observe("BTCUSDT", 0.0, 4)
    assert b.last["BTCUSDT"] == 200.0, "a zero print is ignored, not booked"
    b2 = Benchmark(); b2.load(b.to_dict())
    assert abs(b2.equity() - b.equity()) < 1e-9, "benchmark survives a restart"

    # pnls are recorded for the t-stat, and persist
    with tempfile.TemporaryDirectory() as d:
        pf = Portfolio("v1_rsi_macd", log_file=os.path.join(d, "t.csv"), quiet=True)
        pf.on_bar("BTCUSDT", _bar(100, 100, 100, 100, t=3_600_000),
                  (True, False, True, False, True), 1.0)
        pf.on_bar("BTCUSDT", _bar(100, 100, 97, 98, t=7_200_000),
                  (False, False, True, False, False), 1.0)
        assert len(pf.pnls) == 1 and pf.pnls[0] < 0, "loss recorded in pnls"
        assert "pnls" in pf.to_dict(), "pnls persist"

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
            print(f"\n{BOT_NAME} stopped — state saved, rerun to resume")
