# Crypto NNFX experiment — OFF the main plan

Built 21 Aug 2026 in a session that did not read `CLAUDE.md`, `STATUS.md` or
`DECISIONS.md` first. Filed here rather than in `src/` because it does not
belong to the main system and has not earned a D-number.

## What it is

A forward-testing harness for two hand-written rule strategies on Binance
hourly crypto candles, using public market data only (no account, no keys).

| book | entry rule |
|---|---|
| `v1_rsi_macd` | EMA100 baseline + RSI + MACD confluence, ADX>20 |
| `v2_stoch_mfi` | EMA100 baseline + Stochastic + MFI confluence, ADX>15 |

Both share one risk model: 2.5x ATR stop, 2.5R target, halt after 4
consecutive losses or 15% drawdown, 24-bar cooldown. Six books
(2 strategies x BTC/ETH/SOL), $10k virtual each.

    python3 paper_trader.py            # run (writes dashboard.html each poll)
    python3 paper_trader.py --report   # standings
    python3 paper_trader.py --selftest # ~45 assertions
    python3 dashboard.py               # render dashboard only

## How it conflicts with the repo's settled positions

Read this before taking any number it produces seriously.

- **Parameters were fitted by grid search** (36 then 32 combinations on
  trader.dev). `PLAN_BEAT_SPY.md` names the only live direction as a sleeve
  with *zero fitted parameters*. This is the opposite.
- **Search-until-it-passes.** DECISIONS warns that 20 worthless variants give
  a ~100% chance of a fake win. Two indicator sets were tried and the second
  was kept because it scored better — exactly that pattern.
- **v1's out-of-sample test was contaminated.** Parameters were fitted over
  2024-2026 and "validated" on 2025-2026, a subset of the fitting window.
  v2's split (fit 2024, test 2025-2026) was clean. The two are therefore not
  comparable, and the repo rule "no strategy comparison without its error bar"
  was not met.
- **The one honest number is not significant.** v2's clean out-of-sample run
  was +8.8% over 23 trades, mean trade +$38.22, SE $51.55, **t = 0.74**.
  The repo's own bar is |t| > 2. This does not clear it.

## Status

Unproven. The forward test is the only part with evidence value, and it needs
~100 closed trades per strategy before the standings mean anything. Nothing
here should be read as an edge, and none of it bears on the S&P 500 system in
`src/`.
