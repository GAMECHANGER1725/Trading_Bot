# Trading_Bot

A swing-trading system for US large caps. Alpaca paper account, daily bars,
2–10 day holds, XGBoost ranking, bracket orders with hard guardrails.

**Live on paper since 14 Aug 2026.** Read `STATUS.md` for current state and
`DECISIONS.md` for why every choice was made.

---

## What this actually is

An honest negative result with a working execution layer attached.

Thirty-three features were tested individually across price, sector, market
regime, corporate actions, SEC Form 4 insider transactions and earnings
timing. **One exceeded |t| = 2 for a reason that was not a data artefact.
Chance alone predicts 1.7.** Eight model configurations — two architectures
crossed with four feature sets — were statistically indistinguishable from one
another and all sat below SPY at any position count where the measurement can
be trusted.

Backtest, 2022–2024, $100,000 start, 25 positions, 3.5 bps per side:

| | final | profit | 3yr return |
|---|---|---|---|
| this bot | $120,872 | +$20,872 | +20.9% |
| SPY buy & hold | $127,656 | +$27,656 | +27.7% |

| year | bot | SPY | diff |
|---|---|---|---|
| 2022 | −1.0% | −18.9% | **+17.8%** |
| 2023 | +2.1% | +26.6% | −24.5% |
| 2024 | +21.1% | +25.5% | −4.5% |

It makes money. It makes less money than doing nothing. The stops earn their
keep in a crash and cost more than that in a rally.

---

## Layout

```
src/
  data_layer.py     Alpaca bars. feed=sip and adjustment=all are mandatory
                    arguments — the defaults are silently wrong (D6, D7)
  features.py       14 price features, cross-sectionally ranked per day
  features_v3.py    11 sector / regime / breadth / beta / dividend features
  features_v4.py    8 SEC Form 4 insider features, keyed on FILING date
  labels.py         triple-barrier labelling
  backtest.py       event-driven engine; the strategy physically cannot see
                    the future because the future is not in the object
  broker.py         Alpaca trading. Paper-only, three independent checks
  guardrails.py     13 pre-flight checks. All must pass or nothing is sent

scripts/
  daily_run.py             the daily job. Dry run by default
  test_guardrails.py       54 assertions. Every check driven into failure
  train_prod_model.py      trains data/model_prod.json
  build_issuer_map.py      finds ticker renames and dual share classes
  bakeoff.py               8-cell architecture x features grid
  compare_bracket_anchoring.py   why the entry is a limit order
  noise_floor.py           block bootstrap on the headline accuracies
```

## Running it

```bash
python3 scripts/test_guardrails.py     # expect: 54 passed, 0 failed
python3 scripts/daily_run.py           # dry run — submits nothing, writes nothing
python3 scripts/daily_run.py --live    # submits
touch HALT                             # kill switch. Stops everything, first check
```

`.env` holds `APCA_API_KEY_ID` and `APCA_API_SECRET_KEY` and is gitignored.
Paper keys start with `PK`; the loader refuses anything starting with `AK`.

Bars are not committed — rebuild with `scripts/refetch_bars.py` (~3 minutes)
then `scripts/build_panel_all.py`.

## Schedule

macOS `launchd`, not cron: a laptop is asleep at 06:35 Sydney and cron
silently skips missed runs while launchd catches up on wake. See
`deploy/README-schedule.md`.

The job must run **after the US close**. `check_session_timing` asks the
broker whether the market is open and refuses to run if it is, so a bad
schedule is caught rather than traded through.

---

## Rules this project runs on

1. Verification over velocity. A wrong trading bot does not crash — it loses
   money quietly.
2. No result stands unvalidated. Every number gets "what would make this
   wrong?" before anything is built on it.
3. Overfitting is the default outcome, not an edge case. Time-based splits
   only, walk-forward, and a good-looking curve is a bug until proven
   otherwise.
4. No strategy comparison without its error bar — and an error bar that is
   printed but not obeyed is worse than one that is absent.
5. Never choose the metric after seeing the result.
6. Every architectural decision is logged in `DECISIONS.md` with its
   reasoning, so settled questions stay settled.

## Traps found and blocked in code

Each of these produced plausible, wrong output with no error raised:

1. `feed=iex` default → 3.5% of consolidated volume (D6)
2. `adjustment=raw` default → a fabricated −89.9% day on NVDA (D7)
3. Bare dates resolve to US Eastern → a misleading 403 from Sydney (D10)
4. Four tickers reused by unrelated companies (D12)
5. `adjustment=all` does not cover spin-offs (D15)
6. Delisted positions silently erased $50k of equity (D20)
7. Overlapping windows turned a null result into an apparent 13x edge (D24)
8. A missing-data sentinel re-imported survivorship bias as a feature scoring
   t = +2.67, which collapsed to −0.16 on covered rows (D29)
9. Ticker renames overlap: Alpaca serves FB and META simultaneously for about
   a year. Buying both is one double position recorded as two (D33)
10. A pre-live audit found 6 blocking bugs in code that passed its own 43
    tests. Three of eleven guardrails were structurally unreachable (D34)
11. The bracket was anchored to yesterday's close while the backtest and
    labels anchored to the fill. Every historical number graded a trade the
    bot could not place (D35)

## Status

Paper only. Moving to real money is a separate decision requiring evidence,
not a config change.

Success bar: **beats SPY buy-and-hold on risk-adjusted return, over 200+
trades, on data the model has never seen.** Not met. The 2025+ holdout has
never been evaluated — its single unbiased shot is preserved.
