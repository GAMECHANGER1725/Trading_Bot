# STATUS — Fri 14 Aug 2026

## Build progress

| Step | State |
|---|---|
| 1. Data + backtest engine | **Done, verified twice** |
| 2. Momentum baseline | **Done.** Sharpe 0.77 |
| 3. XGBoost | **Done. No edge found.** Four generations, all failed |
| 4. Sentiment | Not started |
| 5. Execution + guardrails | **LIVE on paper 14 Aug 2026.** 13 guardrails, 54 tests green (D32–D36) |

## What exists

- Paper account PA3VR8OK3RL7, $100k, keys in `.env`
- 1,336,592 daily bars, 725 symbols, 2016-01-04 → 2026-02-27 (26 MB)
- Point-in-time S&P 500 membership, 474 change-dates
- Backtest engine reproducing SPY to **0.58%** in 2016–2026 and to **0.15%**
  in the 2022–2024 re-check, independently finding the COVID bottom
- Feature pipelines: 14 price, 11 sector/market/dividend, 8 SEC Form 4 insider
- Triple-barrier labelling, walk-forward training, sector clustering
- Entry is a LIMIT at prior close +0.5%, bounding the gap distortion (D35)
- **Execution layer**: `src/broker.py` (paper-only, idempotent),
  `src/guardrails.py` (13 checks), `scripts/daily_run.py` (dry-run default,
  writes nothing unless --live),
  `data/model_prod.json` (v1, 14 features),
  `data/issuer_groups.json` (25 groups, correlation-derived)
- **LIVE on paper since 14 Aug 2026**: 25 bracket orders placed, 0 duplicates
  across 4 runs, idempotency confirmed against the real account (D36)
- **2025+ holdout never evaluated** — one clean shot preserved

## Headline results

Two windows, because v4 could only run on 2020+ (its data source starts there).

**2020–2026, 7 folds** (D22/D23)

| | CAGR | Sharpe |
|---|---|---|
| XGBoost v1 | 13.56% | 0.73 |
| Momentum baseline | 16.21% | 0.77 |
| SPY | 14.90% | 0.79 |

Sharpe standard error ±0.46.

**2020–2024, 3 folds, all four versions on identical rows/folds/targets** (D30)

| | arch | feat | out-samp acc | overfit gap | IC vs bracket |
|---|---|---|---|---|---|
| v1 | CLS | 14 | **50.71%** | 3.43% | +0.0216 |
| v2 | REG | 14 | 49.65% | **1.89%** | −0.0091 |
| v3 | REG | 25 | 50.02% | 2.15% | +0.0013 |
| v4 | CLS | 22 | 50.48% | 6.92% | **+0.0229** |

375,697 identical test rows per cell. **Noise floor on these accuracies is
±0.30% (block bootstrap, D31).** Paired on identical days, with Bonferroni
across 7 comparisons, **exactly one difference in the grid is real: v1 beats
v2 by 1.07pp (t=3.78).** v1 vs v4 is p=0.30 — not distinguishable.

**Backtest Sharpes are NOT quoted here.** See D30: at 10 positions the same
model produced 0.26 and 0.66 on two runs differing only in row ordering.
**Step 5 must hold ≥25 positions** — for measurement, not performance:

| Sharpe | N=10 | N=25 | N=100 | N=200 | SPY |
|---|---|---|---|---|---|
| v1 | 0.26 | 0.52 | 0.41 | **0.49** | 0.56 |
| v4 | 0.85 | 0.42 | 0.33 | **0.38** | 0.56 |
| v2 | 0.10 | −0.15 | 0.17 | **0.22** | 0.56 |

**All three converge below SPY.** That is the signature of zero ranking skill:
as N grows you approach owning the index minus what you paid to trade it.
Raising N buys measurement, not performance.

## The core finding

**33 features tested individually across price, sector, market regime,
corporate actions, insider transactions and earnings timing. One exceeded
|t| = 2 for a reason other than a data artefact. Chance predicts 1.7.**

The model explains a fraction of a percent of the variation in outcomes. It is
not broken — it is correctly reporting that the inputs contain nothing.

Narrow and honest version: *nothing freely available at daily resolution, on
S&P large caps, at 5–10 day horizons, contains a tradeable edge after costs.*

## Traps found and blocked in code

1. `feed=iex` default → 3.5% of volume (D6)
2. `adjustment=raw` default → fake -89.9% day on NVDA (D7)
3. Bare dates resolve to US Eastern → misleading 403 from Sydney (D10)
4. Four reused tickers (STI, TE, WYND, AABA) → different companies (D12)
5. `adjustment=all` does not cover spin-offs (D15)
6. Delisted positions silently erased $50k of equity (D20)
7. Overlapping windows inflated a null result into an apparent 13x edge (D24)
8. **A missing-data sentinel re-imported survivorship bias as a feature that
   scored t = +2.67 and collapsed to -0.16 once tested on covered rows (D29)**
9. **Ticker renames overlap: Alpaca serves FB and META, RTX and UTX, ANTM and
   ELV simultaneously for ~a year. Buying both is one double position recorded
   as two. 20 such pairs found by return correlation (D33)**
10. **A pre-live audit found 6 blocking bugs in code that passed its own 43
    tests. Worst: local state and broker state were required to match exactly,
    so a bracket exiting normally deadlocked the bot for up to 10 sessions.
    3 of 11 guardrails were structurally unreachable (D34)**
11. **The bracket was anchored to yesterday's close while the backtest and
    labels anchored to the fill. A +2% overnight gap turned 8%/12% into
    9.8%/9.8%. Every historical number graded a trade the bot could not
    place. Fixed with a limit entry (D35)**

## Ideas tested and rejected

| Idea | Why |
|---|---|
| Intraday / multiple trades a day | 20 trades/day = 35%/yr in costs. SPY returns ~10% |
| Longer holds (40d) | The apparent edge was market beta |
| Kaggle **price** history | Zero delisted tickers — would teach that crashes always recover |
| **Kaggle SEC Form 4 insider data** | **Built properly and tested. 0 of 8 features above \|t\|=2; shuffled values beat real ones (D29)** |
| **Kaggle earnings dates** | **The only signal found was survivorship bias through a missing-symbol sentinel (D29)** |
| Frontier LLM for sentiment | Training cutoff inside the whole window; contamination is 100% |
| Removing the stop-loss | Tested. Sharpe 0.82 → 0.61. The stop earns its keep on risk |
| Volatility timing | Cederburg et al: fails out of sample and on costs |

## Open, in priority order

1. **Schedule the cron.** The bot is live but only runs when invoked by hand.
   `CRON_TZ=America/New_York` / `35 16 * * 1-5`. Until this exists there is no
   daily run and no time stop
2. **Watch the first fills** at 09:30 EDT 14 Aug. Names that gapped above
   their limit will not fill — that is D35 working, not a fault
3. **Trade count: 25 of 200.** ~5 months at this rate
4. **CLS architecture + non-overlapping sampling** — never run. The grid says
   sampling is the lever that controls overfitting (gap 3.43% → ~1.9%) and
   CLS is the architecture with the better out-of-sample accuracy. Cheap
5. Volatility-based position sizing — last cheap untested lever
6. Paid point-in-time fundamentals / analyst revisions / short interest —
   would change the answer, over budget. Nothing free and daily is untried

**Model work is finished until there is new information to feed it (D31).**
Eight configurations are statistically identical to one another and all sit
below SPY at any position count where the measurement is trustworthy. The
ceiling on a strategy with no ranking skill is SPY minus costs; no
architecture in the grid reaches it and no feature set moves it.

Caveat on 4: C2 cuts the overfitting gap (3.43% → 1.89%) but **costs 1.07pp of
out-of-sample accuracy** — the only statistically real effect in the grid
(D31). It is a trade-off curve, not a free upgrade. One clean run, not an
assumed win.

## Standing constraints

- Training window fixed at 10.6 years. No free source extends it honestly
- Budget $30/mo. Survivorship-free history (Norgate, Sharadar, CRSP) is more
- Sydney: bot runs 6:30am local, ~30 min after US close
- **Schedule in `CRON_TZ=America/New_York`, NOT UTC.** D10 had this backwards:
  the US session is defined in New York time, so a fixed UTC cron drifts.
  20:35 UTC is 15:35 ET in winter — 25 minutes before the close (D34)
- Any dataset with per-symbol coverage gaps needs a covered-rows-only test
  before its features are believed (D29)

## Rules earned the hard way

- Every result computed from overlapping windows gets an independent-observation
  count beside it
- No strategy comparison without its error bar
- Never choose the metric after seeing the result
- Searching until a target is hit always hits it: 20 worthless variants give a
  100% chance of a fake +30%
- **A feature that clears |t|=2 gets attacked before it gets used. Ask which
  rows carry the signal, and whether those rows are a population or an
  artefact**
- **Shuffle-control every new feature block. If permuted values score the
  same, the block is noise, whatever its importance score says**
- **Also compare a new feature block against a pure-noise block of the same
  width. v1 + 8 random columns barely moved (gap 3.44% → 3.64%); v1 + 8 real
  insider columns doubled it (→ 6.74%). "More columns hurt" and "these
  columns hurt" are different findings and only the control separates them**
- **Report in-sample accuracy beside out-of-sample, always. v4's failure was
  invisible in the out-of-sample number alone (50.68% → 50.46%) and obvious
  in the pair (in-sample 54.12% → 57.20%)**
- **Every version comparison is a grid with one axis varying at a time, on
  identical rows, folds and scoring targets. A version is a cell, not a point
  on a line (D30)**
- **Score every model on the same target regardless of what it was trained to
  predict. Training target is architecture; evaluation target is the test**
- **An error bar printed but not obeyed is worse than one that is absent — it
  makes the write-up look rigorous while the conclusion ignores it. D29
  printed ±0.62 and then argued from a 0.38 difference**
- **A guardrail that cannot fire on real input is a comment. Check the
  REACHABILITY of every limit against actual sizing, not just that it blocks
  synthetic input. 3 of 11 failed this (D34)**
- **Audit the execution layer with independent reviewers before going live.
  It passed 43 of its own tests and had 6 blocking bugs. No backtest can
  reach any of them (D34)**
- **A dry run must write nothing. Verify by diffing state across a run,
  not by reading the code (D34)**
- **Check that the backtest is grading a trade the bot can actually place.
  Every number from D19–D31 assumed a stop and target set as a percentage of
  the fill, which the live bot cannot do — it must name dollar prices the
  night before (D35)**
- **Establish the noise floor before asking how to improve something. Bootstrap
  the metric first; if the SE is comparable to the spread between variants,
  there is no target to aim at (D31). Unpaired the grid spanned 3.5 SE; paired,
  1 of 7 comparisons survived**
- **More positions buys measurement, not performance. A portfolio with no
  ranking skill converges on the index minus costs as N grows — which is
  exactly what all three models do (D31)**
