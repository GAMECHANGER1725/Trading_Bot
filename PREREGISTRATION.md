# Pre-registration — model v2

Written **before** running anything. The point of writing it down first is that
it cannot be quietly edited afterwards to match whatever came out.

Date: 13 Aug 2026

## Why this document exists

Papaya asked for autonomous iteration until results were 30% better than the
momentum baseline (Sharpe 0.77, so a target of 1.00).

Simulation of that instruction, assuming every variant tried is genuinely
worthless:

| Variants tried | Best Sharpe found | Apparent gain | P(hits +30%) |
|---|---|---|---|
| 1 | 0.77 | 0% | 30% |
| 5 | 1.30 | +69% | 84% |
| 20 | 1.63 | +112% | **100%** |
| 100 | 1.92 | +150% | **100%** |

With a Sharpe standard error of ±0.46, searching until the target is hit
guarantees hitting it. The improvement would be the search, not the model.

So: a fixed list, applied together, evaluated once.

## The changes, fixed in advance

Each is justified by a defect in v1, not by any result.

**C1 — Triple-barrier labels.** v1 predicted "beats the median forward 5-day
return" while the strategy traded a bracket with an 8% stop, 12% target and a
10-day limit. Prediction and trade were different questions. v2 labels each
example by the outcome the bracket actually produced.

**C2 — Non-overlapping samples.** v1 trained on daily rows with 5-day forward
labels, so consecutive rows shared 4 of 5 days of outcome. The model saw each
outcome roughly five times and became overconfident — a plausible source of the
2.93% in-sample/out-of-sample gap. This is the same artefact that made a 40-day
hold look like a 13x edge before correction. v2 samples one observation per
symbol per holding period.

**C3 — Regression, not binary.** v1's label discarded magnitude: beating the
median by 0.1% and by 15% were identical. v2 regresses on the
cross-sectionally demeaned bracket return.

**C4 — Volatility-scaled position sizing.** Sizes positions inversely to
forecast volatility at constant total exposure. Note this is *not* the
Moreira-Muir volatility-timing result, which Cederburg et al. (2020) showed
fails out of sample and Barroso-Detzel showed fails on costs. Total market
exposure is deliberately left unscaled.

**C5 — Hyperparameters unchanged from v1.** Explicitly not tuned. Tuning is the
cheapest way to manufacture the appearance of improvement.

## Protocol

- **Development period: 2020-01-01 to 2024-12-31.** All building and inspection
  happens here.
- **Holdout: 2025-01-01 onward. Not examined until the final evaluation, and
  evaluated exactly once.**
- All five changes applied **together**, as a single model. No picking the best
  of several.
- Result reported with error bars, whichever direction it goes.

## Success criteria, declared now

- **Clear success:** holdout Sharpe beats momentum by more than one standard
  error (~0.46), *and* the information coefficient is significant under a
  non-overlapping test.
- **Ambiguous:** any improvement smaller than the error bar. To be reported as
  "not established", not as success.
- **Failure:** equal or worse.

Given everything measured so far, ambiguous is the most likely outcome and
failure is entirely possible. Both get written down.

## What is NOT being done

- No hyperparameter search
- No trying variants and reporting the winner
- No extending history with yfinance or Kaggle (survivors-only; would teach the
  model that crashes always recover)
- No metric substituted after seeing results
