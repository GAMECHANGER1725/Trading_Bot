# Trading_Bot — operating instructions

A swing-trading system on US large caps. Alpaca **paper** account, daily bars,
2–10 day holds, XGBoost ranker, bracket orders behind 13 pre-flight guardrails.

Read `STATUS.md` for current state. `DECISIONS.md` holds D1–D40, every
architectural decision with its reasoning. **Settled questions stay settled** —
if a decision has a D-number, do not relitigate it without new evidence, and if
you do overturn one, write the next D-number explaining what changed.

---

## Hard rules

**1. Paper only. Never submit live orders without being asked, in that turn.**
`--live` on any script is a trading action. Dry run is the default everywhere
and must stay that way. Three independent checks enforce paper-only in
`broker.py`; do not weaken them. Keys starting `AK` are live keys and the
loader must keep refusing them.

**2. `touch HALT` is the kill switch.** First guardrail checked, before the
broker is contacted. It must keep working when nothing else does.

**3. Never print a return without its error bar.** The block-bootstrap SE on
this backtest's annual return is ±11pp (D37). A bare performance number in this
repo is a bug, not a result.

**4. Never choose the metric after seeing the result.** State what would
falsify a claim before running it.

**5. Time-based splits only.** Walk-forward, purged. A good-looking curve is a
bug until proven otherwise. Overfitting is the default outcome here, not an
edge case.

**6. `daily_run.py` asserts `meta["features"] == FEATURE_NAMES`.** That assert
is load-bearing — Form 4 insider data ends 2025-07-14 and a feature-set
mismatch would score live trades on absent inputs. Do not remove it.

---

## Closed questions — do not reopen

| | |
|---|---|
| **The model bake-off** (D31, D39) | Eight cells, all indistinguishable. Re-running one config moves it 8.1pp/yr; ANOVA on feature set gives F = 0.543, p = 0.515. Do not add a ninth cell, do not re-rank the eight. |
| **Signal search on daily price data** (D38) | The accuracy edge halves when the test window doubles (0.5070 → 0.5039 over 6 folds). Stop adding features. 33 were tested; one crossed \|t\| = 2 where chance predicts 1.7. |
| **"Beat SPY on risk-adjusted return"** (D37) | Retired as *undecidable* — ΔSharpe 0.2 needs ~118 years on one market. Current bar: **match SPY's return at materially lower volatility.** Volatility is measurable to ±1.4% relative; return only to ±35%. |
| **Entry is a limit at prior close +0.5%** (D35) | Measured against three alternatives. Market entry scored Sharpe 0.28 vs 0.50. |
| **Bracket TIF is GTC** (D40) | DAY made every stop expire at the first close. Never change this back. |

The only live direction is **Phase 1**: a cross-asset, risk-controlled sleeve
with zero fitted parameters, built on the existing execution layer. See
`PLAN_BEAT_SPY.md`.

---

## The lesson that generalises (D40)

D34 found six bugs by asking *"what happens when this runs?"*. D40 was missed
by all 54 tests because nobody asked *"what is still true tomorrow?"*.

The bracket was verified at submission and never at rest. `time_in_force` was
one hardcoded string no test read, and it left 24 positions worth $86,459 with
zero stops for nine days of a ten-day hold.

**Any check on broker state must assert on what persists, not on what was
accepted.** `scripts/health_check.py` exists for exactly this. Run it after
any live session.

---

## Commands

```bash
python3 scripts/test_guardrails.py    # 58 assertions. Must be 58 passed, 0 failed.
python3 scripts/health_check.py       # what is still true at the broker RIGHT NOW
python3 scripts/daily_run.py          # dry run — submits nothing, writes nothing
python3 scripts/daily_run.py --live   # submits. Only when explicitly asked.
touch HALT                            # stop everything
```

Slash commands in `.claude/commands/`: `/verify`, `/audit`, `/decision`,
`/preflight`.

Bars are not committed. Rebuild: `scripts/refetch_bars.py` (~3 min) then
`scripts/build_panel_all.py`.

---

## Layout

```
src/
  data_layer.py   Alpaca bars. feed=sip and adjustment=all are MANDATORY
                  arguments — the defaults are silently wrong (D6, D7)
  features.py     14 price features, cross-sectionally ranked per day
  features_v3.py  11 sector / regime / breadth / beta / dividend features
  features_v4.py  8 SEC Form 4 features, keyed on FILING date. Source coverage
                  is 2020-01-02 to 2025-07-14 — it cannot be extended
  labels.py       triple-barrier labelling
  backtest.py     event-driven; the strategy cannot see the future because the
                  future is not in the object
  broker.py       Alpaca trading. Paper-only, three independent checks
  guardrails.py   13 pre-flight checks. All must pass or nothing is sent

scripts/
  daily_run.py         the daily job. Dry run by default
  test_guardrails.py   58 assertions, every check driven into failure
  health_check.py      persistent broker state (the D40 class of bug)
  repair_brackets.py   one-time: re-arm positions that lost their legs
  analysis/            13 scripts behind PLAN_BEAT_SPY.md, all reproducible
```

Data spans on disk: bars 2016-01-04 → 2026-02-27 (~550 names/yr). Dev panel
2020–2024 (truncated so every feature generation covers the same rows). Sealed
holdout 2025-01-02 → 2026-01-14, **never evaluated**, one unbiased shot
preserved. It is a *consistency check*, not a significance test — at 1.03 years
it has no power at any effect size.

---

## Working conventions

- **Verification over velocity.** A wrong trading bot does not crash. It loses
  money quietly.
- Every architectural decision gets the next D-number in `DECISIONS.md`, with
  what was believed, what was measured, and what changed. Use `/decision`.
- When you find a bug, write down *why the existing tests missed it*. That
  sentence is worth more than the fix.
- Corrections to earlier decisions go in the new entry, explicitly. D38
  corrects D37 twice; that is the pattern, not an embarrassment.
- Prefer measuring to arguing. Most claims here were settled by a script that
  took ten minutes to write.

## Git

The cloud sandbox cannot push to this repo and the device bridge has no
network. Commit locally; the user runs `git push`. Do not build bundle- or
merge-based workarounds — they have failed twice.
