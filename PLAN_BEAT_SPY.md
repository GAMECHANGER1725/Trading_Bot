# How to beat SPY — and why the current plan cannot tell you whether you did

Written 14 Aug 2026, after measuring rather than speculating. Every number
below is reproducible from the repo; the scripts are listed at the end.

---

## The short version

The bot has no measurable stock-selection skill. It is a 0.68-beta SPY proxy
with drag. That was already suspected.

What was **not** known, and is the actual blocker: **the project's measuring
instrument is roughly three times cruder than the effect it is trying to
detect.** Until that is fixed, no model change can be evaluated — good ones
and bad ones both land inside the error bar. Six weeks of feature engineering
were spent on a scale that cannot read the result.

The sealed 2025 holdout, in its current form, cannot answer the question it
was created to answer. Not "probably won't" — *cannot*, as a matter of
arithmetic. That is the most important finding here.

---

## 1. What the bot actually is

Regressing the bot's daily returns on SPY, 2022–2024:

| | value |
|---|---|
| beta | **0.68** (t = 24.7) |
| annual alpha | **−1.9%** (t = −0.25) |
| 95% CI on alpha | **[−16.9%, +13.1%]** |
| R² | 0.45 |

Alpha is indistinguishable from zero, and the confidence interval is 30
percentage points wide. Across all nine strategies tested (8 model configs +
a momentum baseline), **zero have |t(alpha)| > 2.**

The honest benchmark is not SPY — it is SPY held at the bot's own volatility:

| | annual return |
|---|---|
| bot (v1) | 3.18% |
| SPY scaled to the bot's volatility | 8.66% |
| **difference** | **−5.48%** |

Six of nine configurations lose to a passive SPY sleeve at matched risk.

### The 2022 result is not skill

| year | bot | SPY | beta × SPY | residual |
|---|---|---|---|---|
| 2022 | −9.9% | −19.0% | −13.0% | **+3.1%** |
| 2023 | +5.0% | +26.3% | +17.9% | **−13.0%** |
| 2024 | +16.0% | +24.8% | +16.9% | **−1.0%** |

"beta × SPY" is what a dumb SPY position at the bot's exposure would have
returned. The residual — the part the model added — is +3.1% in the crash
year against an annual error bar of roughly ±11%. The headline "beat SPY by
17.8% in 2022" is almost entirely *being 68% invested*, not picking stocks.

---

## 2. The instrument is too crude to read the experiment

### 2a. A natural experiment already sitting in the repo

Two prediction files, `preds_v1_window.parquet` and `preds_CLS_price.parquet`,
are both "v1: CLS architecture, price features only". They ran through the
same engine, same 25 positions, same 8%/12%/10-day barriers, same 3.5 bps,
same dates.

| | |
|---|---|
| agreement between the two models | rank correlation **0.962** |
| identical names chosen each day | **20 of 25** |
| backtested annual return | **10.60%** vs **3.18%** |
| backtested Sharpe | 0.60 vs 0.18 |

A five-name-a-day difference moves the headline by **7.4 percentage points per
year.** The eight-cell bakeoff spans −1.58% to +17.65%. That table is not
ranking models. It is ranking coin flips.

*(Side effect: two different files are both labelled "v1" and disagree. Fix
the naming before anything else — a result you cannot attribute to a config
is not a result.)*

### 2b. Block-bootstrap error bars on the equity curve

The existing `noise_floor.py` measured error bars on *accuracy* and *IC*. It
never measured them on the equity curve, which is the number the project
actually acts on. Ten-day blocks, 2,000 resamples:

| strategy | annual return | ± SE | Sharpe | ± SE |
|---|---|---|---|---|
| v1 CLS price | 3.18% | **±11.1%** | 0.18 | **±0.63** |
| v4 CLS price+insider | 15.26% | ±14.1% | 0.81 | ±0.75 |
| SPY | 8.53% | ±10.7% | 0.49 | ±0.63 |

**SPY's entire Sharpe ratio is 0.49 and the measurement error on Sharpe is
±0.63.** Nothing in this table is distinguishable from anything else in it.

### 2c. The sealed holdout cannot decide anything

The holdout is `2025-01-02 → 2026-01-14`: **259 trading days, 1.03 years.**

For the standard Sharpe-difference test (Jobson–Korkie with the Memmel
correction), the significance threshold requires **T > 1.92 years before *any*
effect size is detectable at all.** Below that, the standard error grows with
the effect faster than the effect does. A one-year holdout is not a weak test;
it is a test with no power at any effect size.

Minimum detectable ΔSharpe, at the observed 0.67 correlation to SPY:

| holdout length | smallest detectable ΔSharpe |
|---|---|
| 1.0 y | *none* |
| 2.0 y | 8.60 |
| 3.0 y | 1.74 |
| 5.0 y | 0.98 |
| 10.0 y | 0.59 |
| 20.0 y | 0.39 |

To prove a ΔSharpe of 0.20 on SPY alone takes **≈118 years of data.**

This is not a flaw in this project. It is a property of financial returns that
every fund faces. What is a flaw is a success criterion written as if it were
decidable.

---

## 3. Breadth does not rescue it — the arithmetic of correlation

The standard escape is breadth: if one market needs 118 years, run the same
rule on many markets and pool the evidence. Tested properly, resampling time
in blocks *jointly across all names* so cross-sectional correlation is
preserved exactly:

**Volatility targeting applied unchanged to 402 S&P 500 names, 2016–2026:**

| | |
|---|---|
| names where Sharpe improved | 56.7% |
| bootstrap SE on that win rate | **±18.0%** |
| 95% interval | [16.7%, 83.6%] — includes 50% |
| naive binomial SE (assumes independence) | ±2.5% — **understates the error 7×** |

Average pairwise correlation across 402 large caps is **0.414**. That is
roughly **2.4 effective independent bets**, not 402. A universe of S&P 500
names is close to one bet, measured 402 times.

Widening to 15 cross-asset ETFs (SPY QQQ IWM EFA EEM TLT IEF LQD HYG GLD SLV
DBC VNQ XLE UUP) only lifts it to **3.2 effective bets** — average correlation
0.261. Since 2016 nearly everything has moved together.

Years needed to prove ΔSharpe = 0.20:

| | |
|---|---|
| SPY alone | 118 y |
| 5 genuinely different markets | 24 y |
| 10 markets | 12 y |
| 20 markets | 6 y |
| 402 large caps (correlated) | 59 y |

**Adding more S&P 500 names buys almost nothing. Adding genuinely different
markets is the only thing that moves this number** — and even 15 ETFs since
2016 do not get there.

---

## 4. What was tested, and what it says

Everything below is pre-specified from the literature. No parameter was
searched: 20-day vol lookback, 15% vol target, 12-1 month momentum, inverse-vol
weights. All are textbook defaults.

### On SPY, 2016–2026 (10.6 years)

| strategy | return | vol | Sharpe | maxDD | ΔSharpe vs SPY | p |
|---|---|---|---|---|---|---|
| SPY buy & hold | 16.12% | 17.48% | 0.92 | −33.8% | — | — |
| vol-target 15%, max 1.0× | 13.77% | 12.78% | 1.08 | −18.8% | +0.16 | 0.37 |
| vol-target 15%, max 1.5× | 16.75% | 15.17% | 1.10 | −18.8% | +0.18 | 0.38 |
| 12m trend (in/out) | 10.67% | 13.31% | 0.80 | −23.9% | −0.12 | 0.49 |

### Cross-asset, 15 ETFs, 2016–2026

| strategy | return | vol | Sharpe | maxDD | ΔSharpe vs SPY | p |
|---|---|---|---|---|---|---|
| SPY buy & hold | 16.05% | 17.48% | 0.92 | −33.8% | — | — |
| 60/40 SPY/IEF | 10.16% | 10.51% | 0.97 | −21.0% | +0.05 | 0.64 |
| risk parity | 7.24% | 7.20% | 1.01 | −15.9% | +0.09 | 0.71 |
| **risk parity + vol target 2.5×** | **15.88%** | **13.65%** | **1.16** | **−24.7%** | **+0.24** | 0.38 |
| risk parity + 12m trend | 5.69% | 6.53% | 0.87 | −11.8% | −0.05 | 0.82 |

**Not one result in this document has a confidence interval that excludes
zero — including the ones I am recommending.** Turnover is not the problem:
risk parity costs 0.31%/yr at the project's 3.5 bps assumption, and monthly
rebalancing cuts that ~20×.

### The caveat that kills the easy story

Risk parity + vol targeting run on the bot's own 2022–2024 window scores
Sharpe **0.38 vs SPY's 0.49** — it *loses* there. Its ten-year advantage comes
almost entirely from 2016–2021. 2022 broke the stock–bond correlation and
risk parity broke with it. This is regime dependence, and it is exactly the
trap the project has been careful about everywhere else. It applies to my
suggestion as much as to the ML model.

---

## 5. What survives all of it

One thing, and it is not a return claim.

Over 10.6 years, measured on daily data:

| quantity | estimate | standard error | relative error |
|---|---|---|---|
| annual **volatility** | 17.54% | ±0.24% | **1.4%** |
| annual **return** | 15.38% | ±5.39% | **35%** |

**Volatility is measured ~26× more precisely than return.** Volatility also
persists — the correlation between this month's vol and next month's is
**+0.52**, while for returns it is **−0.15**. Volatility is both forecastable
and measurable. Return is neither, at this sample size.

So: risk parity + vol targeting vs SPY over ten years —

- return −0.17% → **unprovable**, SE is ±5pp
- volatility **−21.9%** → **conclusive**, SE is ±1.4% relative
- max drawdown **−26.9%** → same direction in both sub-periods

The claim "same return, a fifth less volatility, a quarter smaller drawdown"
is defensible. The claim "higher Sharpe" is not, and will not become
defensible within your lifetime on this data.

**Build the strategy around the half of the Sharpe ratio you can actually
measure.**

---

## 6. The plan

### Phase 0 — repair the instrument (week of 17 Aug, ~4 days)

Nothing else is worth doing first. Every subsequent result is unreadable
without this.

1. **Extend the backtest window from 3 years to 8.** `data/bars/daily.parquet`
   already holds 2016-01-04 → 2026-02-27, ~550 names a year. The backtest used
   2022–2024. Six years of clean data are sitting unused on disk. Shrinks the
   error bar by 1.6× for the cost of a re-run.
2. **Attach a bootstrap CI to every equity-curve number, in code.** Extend
   `noise_floor.py` from accuracy/IC to returns and Sharpe. Make a bare return
   figure impossible to print.
3. **Re-run the 8-cell bakeoff with CIs.** Predicted outcome: all eight
   intervals overlap each other and SPY. Then formally close the "which
   config" question in `DECISIONS.md` and stop reopening it.
4. **Fix the `v1` label collision** between `preds_v1_window.parquet` and
   `preds_CLS_price.parquet`.

### Phase 1 — change the shape of the bet (weeks of 24 Aug and 31 Aug)

Move the objective from *predicting returns* to *controlling risk*, because
that is the part that is both forecastable and measurable.

Build a cross-asset sleeve on the existing engine and guardrails: 10–15 liquid
ETFs, inverse-vol weights, portfolio vol targeted to 15%, leverage capped,
monthly rebalance with a no-trade band. Reuse `broker.py`, `guardrails.py`,
`daily_run.py` unchanged — the execution layer is the one part of this project
that is genuinely finished and audited.

Test it over the full 2016–2024 dev window with CIs on everything, and
explicitly report the 2022 sub-period, since that is where it is weakest.

### Phase 2 — only if Phase 1 clears

Reintroduce stock selection as a small overlay on a risk-controlled base,
sized by measured IC rather than by hope. Do not restart feature engineering
until there is an instrument that can see a 2% effect.

### Phase 3 — the holdout rule, written before looking

- Extend the holdout to today (~1.6 years). Still under the 1.92-year floor,
  so **it remains a consistency check, not a significance test.**
- The only legitimate question to ask it: *does the forward result fall inside
  the backtest's confidence interval?* Pass/fail on that, nothing more.
- **One evaluation. One candidate. Decision rule written down first.** Every
  extra look converts the holdout into another training set.

---

## 7. The honest answer to "when will it beat SPY?"

Two different questions have been running together.

**"When will it have better risk-adjusted performance?"** — plausibly within
three weeks, on the point estimate, via risk control rather than prediction.

**"When will you be able to *prove* it?"** — never, with data available to a
retail account. Proving ΔSharpe = 0.2 takes ~118 years on one market, ~6 years
across 20 genuinely uncorrelated ones, and 15 ETFs since 2016 only supply 3.2
independent bets. This is why real quant funds trade 50+ futures markets across
40 years of history: not for the returns, for the *sample size*.

So the decision to make is not technical. It is:

> Do you act on a positive point estimate backed by outside evidence and a
> sound mechanism, knowing you will never get a p-value — or do you keep
> chasing proof that the data cannot supply?

Every systematic investor answers the first way. The discipline is not in
demanding proof; it is in being ruthless about *mechanism* when proof is
unavailable — which is why "volatility is forecastable and measurable"
matters and "XGBoost found a pattern" does not.

## 8. What to stop doing

- **More features.** 33 tested, one crossed |t| = 2, chance predicts 1.7.
- **More model configurations.** Two runs of the *same* config differ by
  7.4pp/yr. The grid is measuring noise.
- **Reading three-year backtests as if they were evidence.** ±11pp/yr.
- **Treating the 2022 result as a demonstrated crash edge.** n = 1 crash,
  +3.1% residual, ±11% error bar.

## 9. What the bot is worth keeping for

It is live, idempotent, guardrailed, and audited. Leave it running on paper.
Every forward day is free out-of-sample data, and forward data is the only
kind that cannot be overfitted. Just do not mistake it for a strategy with an
edge — it is an execution layer with a placeholder signal in it, and the
execution layer is the valuable half.

---

## Reproduce

```
scripts/analysis/attribution.py     beta, alpha, vol-matched benchmark, power curve
scripts/analysis/attr2.py           the same across all 8 configs
scripts/analysis/noise_returns.py   block bootstrap on equity curves + natural experiment
scripts/analysis/vol_target.py      forecastability and measurability, SPY 2016-2026
scripts/analysis/breadth.py         402-name replication, effective independent bets
scripts/analysis/breadth2.py        joint-time bootstrap of the win rate
scripts/analysis/crossasset.py      15-ETF risk parity / trend / vol target with CIs
scripts/analysis/final_compare.py   head to head
```

---

# Addendum, 14 Aug 2026 — Phase 0 item 1 executed

Papaya asked whether v4 was short-changed on training data and should be
retrained on a longer window "like v1". Both halves of that are wrong, and
checking why produced the sharpest result in this document.

## The premise

- **v1 and v4 train on an identical window.** Both read a 2020-01-02 →
  2024-12-31 dev panel, both use the same expanding walk-forward
  (`test_years = [y for y in years if y >= years[0] + 2]` → 2022, 2023, 2024),
  both use `random_state=42` and the same hyperparameters, and both take the
  same `FEATURE_NAMES` list as the price base. v4 has no handicap.
- **Nothing in this project was ever trained on 8 years.** Production v1 is
  `train_start 2020-01-02, train_end 2024-12-31` — 5 years. The "8 years" was
  a recommendation for the *test* window, not a description of v1.

## The real asymmetry the question exposed

| source | coverage |
|---|---|
| Form 4 filings (`kag/filings.csv`) | **2020-01-02 → 2025-07-14** |
| daily price bars | **2016-01-04 → 2026-02-27** |

v4 **can never** be extended before 2020 — the insider data does not exist.
`build_panel_all.py` truncates everything to 2020 so no variant is scored on
rows another variant could not see. Correct for fairness, but it means the
price-only model has been evaluated on 3 test folds when 6 were available.

**Also worth knowing: Form 4 coverage ends 2025-07-14 while the holdout runs
to 2026-01-14.** Any insider-flavoured model evaluated on the holdout would
score the back half on missing features — and D29 already documents a
missing-data sentinel re-importing survivorship bias as a t = +2.67 feature.
Production is price-only and `daily_run.py` asserts
`meta["features"] == FEATURE_NAMES`, so the live bot is not exposed. Do not
remove that assert.

## What extending the window actually did

Same architecture, same seed, same hyperparameters, same engine, same
25 positions / 8% / 12% / 10 days / 3.5 bps. Only the window moves.

| | test folds | out-of-sample accuracy | forward IC | t |
|---|---|---|---|---|
| as shipped | 3 (2022–24) | **0.5070** | +0.0167 | +0.27 |
| extended | 6 (2019–24) | **0.5039** | +0.0085 | +0.91 |

**The accuracy edge halves when tested on twice the data: +0.70pp → +0.39pp.**
Coin flip is 0.5000 and the standard error on accuracy is ±0.0031, so +0.39pp
is 1.3 SE — nothing. `model_prod_meta.json` advertises
`expected_oos_accuracy: 0.5071`. On an honest window it is 0.5039.

Backtested:

| | return | vol | Sharpe | ± SE | maxDD |
|---|---|---|---|---|---|
| bot, 3 folds (2022–24) | 2.49% | 15.39% | 0.16 | ±0.62 | −24.0% |
| SPY, same window | 8.53% | 17.45% | 0.49 | ±0.62 | −24.4% |
| bot, 6 folds (2019–24) | 10.05% | 18.22% | 0.55 | ±0.47 | −34.5% |
| SPY, same window | 17.25% | 19.44% | 0.89 | ±0.50 | −33.8% |

It loses to SPY on both windows, and loses by more on the longer one.

## Two things I got wrong, stated plainly

**1. The error bar barely moved.** I predicted extending 3 → 6 years would
shrink it ~1.6×. Measured: SE on annual return went ±9.56% → ±8.40%, a factor
of **1.14×**. The extra years include COVID, so volatility rose from 15.39% to
18.22% and ate most of the √T gain. The arithmetic is consistent
((18.22/15.39)/√2 = 0.84 predicted vs 0.88 observed) — my forecast just assumed
constant volatility, which is exactly the assumption this document argues
against everywhere else. **More history does not buy proportional precision if
the added history is more turbulent.**

**2. A third run of the same spec produced a third answer.** The same cell —
CLS, `FEATURE_NAMES`, seed 42, identical params, identical 2022–24 window —
now has three recorded backtest returns:

| run | panel | annual return |
|---|---|---|
| `bakeoff_curves.csv` | `panel_all_dev` (601 symbols) | 3.18% |
| `equity_curves_v4.csv` | `panel_v4_dev` (601 symbols) | 10.60% |
| this addendum | price-only (673 symbols) | 2.49% |

**Spread: 8.1 percentage points a year, from nothing but which rows the panel
intersection kept.** Section 2a called it a 7.4pp effect. It is larger.

## So: is extending the window still worth it?

Yes — but not for the reason in the original plan. It did not deliver
meaningful precision. What it did was convert an apparent +0.70pp edge into a
measured +0.39pp non-edge. That is the correct use of more data: not to make
the number better, but to find out the number was never there.

The revised Phase 0 conclusion is stronger than the original: **the price-only
signal does not survive its own test window being doubled.** Stop trying to
improve it. Go to Phase 1.

Reproduce: `scripts/analysis/long_window.py`, then
`scripts/analysis/long_window_bt.py`.

---

# Addendum 2 — "Which model is best?"

Asked directly. Answered by first testing whether the question has an answer.

## Every model, every metric (2022–2024, 25 positions, 3.5 bps)

| model | acc | fwd IC | t(IC) | return | Sharpe | alpha | t(α) | beta | maxDD |
|---|---|---|---|---|---|---|---|---|---|
| v1 CLS price | 0.5071 | +0.0171 | +0.07 | 3.18% | 0.18 | −1.92% | −0.25 | 0.68 | −25.7% |
| — CLS price+v3 | 0.5036 | +0.0088 | +0.72 | 2.61% | 0.14 | −3.16% | −0.42 | 0.77 | −18.6% |
| v4 CLS price+insider | 0.5048 | +0.0127 | −0.22 | 15.26% | 0.81 | +7.86% | 1.13 | 0.84 | −22.7% |
| — CLS price+v3+insider | 0.5043 | +0.0107 | +0.67 | 9.50% | 0.52 | +3.49% | 0.47 | 0.75 | −21.3% |
| v2 REG price | 0.4965 | −0.0111 | −0.99 | 0.10% | 0.01 | −5.09% | −0.66 | 0.70 | −26.7% |
| v3 REG price+v3 | 0.5002 | +0.0018 | +1.26 | 2.97% | 0.14 | −2.36% | −0.24 | 0.79 | −26.0% |
| — REG price+insider | 0.4991 | −0.0025 | −0.90 | −1.58% | −0.08 | −8.25% | −1.09 | 0.90 | −30.7% |
| — REG price+v3+insider | 0.5020 | +0.0056 | +1.12 | 17.65% | 0.80 | +9.78% | 1.11 | 0.92 | −26.9% |
| mom 20d, no ML | — | — | — | 6.13% | 0.30 | +0.29% | 0.03 | 0.80 | −18.7% |
| SPY buy & hold | — | — | — | 8.53% | 0.49 | — | — | 1.00 | −24.4% |

Read across, not down. Highest accuracy (v1, 0.5071) is **eighth of eight on
return**. Highest return (REG price+v3+insider, 17.65%) is **sixth on
accuracy**. Every |t(α)| is below 1.2 and every |t(IC)| below 1.3.

## Bootstrapping time: is the ranking stable?

4,000 resamples, 10-day blocks:

| model | #1 by Sharpe | beats SPY |
|---|---|---|
| v4 CLS price+insider | 42.4% | 74.4% |
| — REG price+v3+insider | 32.9% | 73.6% |
| — CLS price+v3+insider | 11.4% | 51.0% |
| v1 CLS price | 5.7% | 27.0% |
| mom 20d | 5.0% | 32.6% |
| the other four | <2% each | 4–22% |

That looks like a result — v4 leads, beats SPY three times in four. **It is
not.** This bootstrap resamples *time* while holding the trained model fixed.
It cannot see the larger source of variation.

## The decisive test: enter the same specification twice

D38 measured an 8.1pp/yr spread from re-running one specification. So run the
race again with the duplicates included as separate horses — same architecture,
same seed, same hyperparameters, same window, same costs:

| entrant | return | Sharpe | #1 by Sharpe | beats SPY |
|---|---|---|---|---|
| **v4 run A** (panel_all) | 15.26% | 0.81 | **38.6%** | 74.2% |
| best-of-8 (REG p+v3+ins) | 17.65% | 0.80 | 30.5% | 75.1% |
| **v1 run B** (panel_v4) | 10.60% | 0.60 | **18.5%** | 57.8% |
| SPY buy & hold | 8.53% | 0.49 | 6.0% | — |
| mom 20d, no ML | 6.13% | 0.30 | 4.8% | 33.4% |
| **v1 run A** (panel_all) | 3.18% | 0.18 | **1.2%** | 26.1% |
| **v1 run C** (price-only, 673 sym) | 2.49% | 0.16 | **0.3%** | 21.2% |
| **v4 run B** (panel_v4) | 3.65% | 0.19 | **0.1%** | 21.9% |

**v4 run A takes first place 38.6% of the time. v4 run B takes it 0.1%.**
Same specification. A 386-fold difference between a model and itself.

The gap between the two v4 runs is **38.5 points**. The gap between v4 run B
and v1 run B is **18.4 points**. *Within-specification variation is more than
twice between-specification variation.*

## Formally

One-way ANOVA on annual return, grouping the runs by feature set:

| | |
|---|---|
| v1 (price only), 3 runs | 3.18%, 10.60%, 2.49% — mean 5.42% |
| v4 (price + insider), 2 runs | 15.26%, 3.65% — mean 9.46% |
| between-spec difference | 4.03 pp/yr |
| pooled within-spec SD | **6.00 pp/yr** |
| difference, in within-spec SDs | **0.67** |
| **F = 0.543, p = 0.515** | |

The insider features explain **none** of the spread. Neither does architecture.
Re-running one model moves it further than switching models does.

## So which is best?

**None of them, and the question cannot be answered with this data.** Any
ranking produced here is a ranking of which realisation happened to get
recorded in which CSV.

Note especially what tops the naive table: `v4 run A` and `best-of-8`. Both
were **chosen after seeing the results.** Their apparent advantage *is* the
selection. That is the mechanism, not a coincidence.

When effects are indistinguishable the tie-break is not performance — it is
how much opportunity each candidate had to fit noise:

| candidate | fitted parameters | selected from | verdict |
|---|---|---|---|
| SPY buy & hold | 0 | nothing | benchmark |
| risk parity + vol target | 0 (textbook constants) | nothing | **the one to build on** |
| 20d momentum | 0 | nothing | honest baseline |
| v1 CLS price | 900 trees | 8 cells, named before results | keep as the placeholder it is |
| best-of-8 / v4 | 900 trees | 8 cells, named **after** results | discard |

`model_prod_meta.json` already says it: *"Placeholder ranker … fails Bonferroni
across the 8 cells it was selected from."* That was the right call then and it
survives this analysis. **v1 stays in production not because it won, but
because it was named before the results were seen and it is the cheapest thing
to be wrong with.**

Reproduce: `scripts/analysis/rank_models.py`, `scripts/analysis/same_model_race.py`.
