# Crypto NNFX experiment — OFF the main plan

Built 21 Aug 2026 in a session that did not read `CLAUDE.md`, `STATUS.md` or
`DECISIONS.md` first. Filed here rather than in `src/` because it does not
belong to the main system and has not earned a D-number.

## What it is

A forward-testing harness for rule strategies on Binance hourly crypto candles,
using public market data only (no account, no keys). **Three** books now share
one risk model, one $10k account each, across 32 markets:

| book | entry rule |
|---|---|
| `v0_control` | **coin flip**, same regime gate, same everything else |
| `v1_rsi_macd` | EMA100 baseline + RSI + MACD confluence, ADX>20 |
| `v2_stoch_mfi` | EMA100 baseline + Stochastic + MFI confluence, ADX>15 |

Risk model: 2.5x ATR stop, 2.5R target, risk-based sizing capped at 1/20 of
equity, max 8 positions per side, size halves after 3 consecutive losses, hard
halt after 6 or at 15% drawdown, 24h cooldown. Commission 0.05% and slippage
0.02% per side.

    python3 paper_trader.py            # run (writes dashboard.html each poll)
    python3 paper_trader.py --report   # standings, expectancy, t
    python3 paper_trader.py --selftest # ~80 assertions
    python3 replay.py --split          # replay history, first half vs second
    python3 replay.py --controls 30    # rank strategies against 30 random books
    python3 dashboard.py               # render dashboard only

## The control book is the point

A 2.5R target behind an ATR stop wins **~28% of the time on chance alone**. The
measured win rate was 30.4%. So "Bob wins 30% of his trades" was never evidence
of anything, and for the first day of running there was nothing in the system
capable of noticing that.

`v0_control` enters at random on the same markets, through the same regime
filter, with the same stops, targets and sizing. The only difference is whether
the entry carries information. `STATUS.md` has required this test since the
XGBoost work ("shuffle-control every new feature block; if permuted values score
the same, the block is noise") — it had simply never been applied here.

Everything is also carried against **equal-weight buy-and-hold of the same 32
markets**, because a return means nothing without knowing what the market did.

## Findings, 22 Aug 2026

Replay over 15 days of 1h candles, 32 markets, current rules:

| book | return | W/L | win% | per trade | t |
|---|---|---|---|---|---|
| `v0_control` | +1.35% | 76/165 | 31.5% | +0.56 | 0.92 |
| `v1_rsi_macd` | −0.02% | 35/122 | 22.3% | −0.01 | −0.01 |
| `v2_stoch_mfi` | +0.41% | 47/127 | 27.0% | +0.23 | 0.25 |
| **buy & hold** | **+30.79%** | | | | |

### One control is not enough

A single random book is itself one draw: with a 5% tail, about one seed in
twenty clears |t| > 2 on nothing at all — and the first one tried did exactly
that (t = 2.21). So `replay.py --controls 30` runs a **population** of random
books and asks where the real strategies fall in it.

    30 random-entry books
    mean P&L per trade   worst -0.06   median +1.12   best +2.45

    v1_rsi_macd    -0.01 per trade — beats  2/30 random books ( 7th pct)
    v2_stoch_mfi   +0.23 per trade — beats  3/30 random books (10th pct)

Both strategies sit in the **bottom 10%** of pure chance. Over this window the
indicators are not merely failing to add value — they are selecting entries
slightly worse than a coin flip, while paying the same commission and slippage
to do it.

Two things fall out, and neither is flattering:

1. **Neither strategy beats random entry.** Welch t on the difference in mean
   P&L is −0.50 and −0.30. Split into halves the picture is the same in both
   (t = −0.04 / +0.07 for v1), and the control's win rate rises and falls *with*
   the strategies' — 22% in the first half, 35% in the second, for all three
   books at once. That is a regime moving together, not skill.
2. **Trading badly underperformed holding.** +0.4% against +30.8% over the same
   window on the same markets.

### The sizing bug that was manufacturing an edge

Before 22 Aug every position took a fixed 1/20 notional slice. With the stop at
2.5xATR and ATR varying several-fold across these markets, that meant **every
trade risked a different amount** — the volatile coins were quietly betting
several times what the calm ones did. This is not the NNFX model the code claims
to implement, and it makes "2.5R" mean something different on every trade.

Switching to risk-based sizing (capped at the old notional, so total exposure is
unchanged) moved v1 from **+3.47% to −0.02%** and cut the standard error on mean
P&L from 2.47 to 0.99. The apparent edge was concentrated in a handful of
oversized positions in high-volatility markets. It was a volatility bet wearing a
signal's clothes.

The fix was adopted because it is the correct implementation of the stated risk
model, not because of what it did to the number. It happened to make the results
worse, which is the direction that should raise the least suspicion.

## How it conflicts with the repo's settled positions

- **Parameters were fitted by grid search** (36 then 32 combinations). 
  `PLAN_BEAT_SPY.md` names the only live direction as a sleeve with *zero fitted
  parameters*. This is the opposite.
- **Search-until-it-passes.** Two indicator sets were tried and the better one
  kept — exactly the pattern DECISIONS warns gives a ~100% chance of a fake win.
- **v1's original out-of-sample test was contaminated** (fitted 2024-2026,
  "validated" on 2025-2026, a subset).
- **The one honest number never cleared the bar.** v2's clean out-of-sample run
  was t = 0.74 against a standing bar of |t| > 2.

`replay.py` exists so that no future change to this system gets adopted on the
strength of a return alone. It reports the control comparison and the
buy-and-hold comparison every time, and it must not be used to pick parameters.

## Status

**Unproven, and now with evidence pointing at "no edge" rather than silence.**
The forward test restarted from zero on 22 Aug when slippage, risk sizing and
the side cap changed the rules — the earlier 55 trades are archived under
`archive/` and are not comparable. Nothing here should be read as an edge, and
none of it bears on the S&P 500 system in `src/`.
