# DECISIONS.md

Architectural decisions and their reasoning. Settled entries are not
relitigated without new evidence.

Started 13 Aug 2026.

---

## Locked

### D1 — US equities, swing trading, 2–10 day holds, daily bars
Rejected intraday: the news/sentiment edge is arbitraged within seconds, the
free feed is IEX-only in real time, and US market hours are 11:30pm–6am
Sydney time. Rejected crypto: no news flow to feed sentiment, wide spreads,
no close to anchor a daily cycle. Rejected options: Greeks and assignment
risk are not a v1 problem.

### D2 — Universe: S&P 100, historical constituents per date
Liquid enough for realistic fills, well covered by news, large enough for
statistical significance, small enough to limit data-mining. Constituent
membership must be resolved as of each backtest date. Using today's list
would mean only companies that survived and stayed large are ever tested.
**Depends on D8** — this decision is only meaningful if delisted price
history is obtainable.

### D3 — Alpaca free (Basic) tier
Algo Trader Plus is $99/mo against a ~$30/mo budget. Basic is adequate for
daily bars. See D6 — the free tier serves historical SIP data with a
15-minute lag, so the IEX limitation does not apply to backtests.
yfinance is the fallback for deep history.

### D4 — Sentiment: FinBERT, not a local LLM
FinBERT is ~400MB. A 7B model consumes 4–5GB of 8GB total RAM and performs
worse on financial sentiment. Not close.

### D5 — Daily run after the US close (~6:30am Sydney), scheduled locally
No VPS until the system earns one.

---

## New — 13 Aug 2026

### D6 — Every bars request passes `feed="sip"` explicitly
On a Basic account the bars endpoint **defaults to the IEX feed**. IEX is
roughly 2.5% of US consolidated volume, so IEX daily bars have a "close"
that is merely the last IEX print of the day and a "volume" that is a small
fraction of reality. Historical queries ending more than 15 minutes ago may
request `feed=sip` on the free tier, giving the full consolidated tape.

Failure mode: omitting the parameter raises no error. The bars look normal.
The backtest runs to completion on prices that were never the market's
prices. This is the archetype of the silent-breakage risk this project is
built to avoid.

Enforcement: the data layer must refuse to return bars unless the feed
parameter was explicitly supplied. No default value in the function
signature.

**CONFIRMED 13 Aug 2026.** SIP works on this free account. Measured over ten
sessions on AAPL: IEX carried an average of **3.5%** of consolidated volume,
and IEX closes diverged from SIP closes by up to **0.108%**. A tenth of a
percent per bar sounds small until you notice the strategy's whole edge has
to fit inside a few tenths of a percent per trade.

### D7 — Every bars request passes `adjustment="all"`
Alpaca defaults to `adjustment=raw`. Raw prices contain split
discontinuities: AAPL's 4:1 split in Aug 2020 appears as a one-day -75%
return, NVDA's 10:1 in Jun 2024 as -90%. Feature engineering ingests these
as genuine crashes, and the model trains on events that never occurred.

Dividends matter independently of splits. The success bar is beating SPY
buy-and-hold on risk-adjusted return. SPY yields ~1.2–1.5%/yr. Benchmarking
a total-return strategy against a price-only SPY gifts the strategy ~1.3%/yr
it never earned — roughly 14% of phantom outperformance over a decade, which
is on its own enough to flip a verdict from "fails" to "beats SPY."

Enforcement: same as D6 — explicit argument required, no default.

**CONFIRMED 13 Aug 2026.** With `adjustment=raw`, NVDA's worst one-day move
across the split window is **-89.93%**. With `adjustment=all` it is +5.15%,
which is a real day. The fabricated crash is not subtle, and nothing in the
API flags it.

### D8 — Delisted price history must be verified before the engine is built
D2 assumes bars are retrievable for companies that have since been delisted.
If Alpaca returns nothing for SIVB / FRC, then any historical constituent
that later died silently drops out of the universe and survivorship bias
returns through the back door — while appearing to have been handled.

**CONFIRMED 13 Aug 2026.** Both return full history to their delisting dates:
SIVB 297 bars to 2023-03-09, FRC 332 bars to 2023-04-28. D2 is viable as
written. No second data source required for dead names.

### D9 — Training window is set by measurement, not documentation
Public sources disagree on free-tier depth (Alpaca support says 5 years,
their data page says 7+, forum posts say data begins 2016). Docs on
free-tier limits go stale quickly. The probe reports the actual earliest
available bar per symbol and that number sets the training window.

Threshold for action:
- ≥9 years → Alpaca alone is sufficient.
- 5–9 years → workable but regime-poor; extend with yfinance and reconcile
  the overlapping period bar-by-bar before trusting either source.
- <5 years → insufficient for credible walk-forward validation; yfinance
  becomes the primary history source.

**RESOLVED 13 Aug 2026. Training window: 2016-01-04 to present, ~10.6
years.** Identical earliest bar across SPY, AAPL, MSFT, JPM, XOM, which
matches Alpaca's stated 2016 data origin rather than any rolling window.
Above the 9-year threshold, so **Alpaca is the sole price source** and
yfinance is dropped from the plan. One source means one set of conventions
and no reconciliation bugs — a real simplification, take it.

Regimes covered: the 2018 Q4 selloff, the Feb–Mar 2020 COVID crash, the
2020–21 liquidity melt-up, the 2022 rate-driven bear market, and the
2023–2026 period. That is enough distinct regimes for walk-forward folds
that are not all the same market wearing different hats.

### D10 — All Alpaca time parameters are explicit RFC3339 UTC instants
Alpaca resolves a bare `YYYY-MM-DD` in `start`/`end` against **US Eastern**,
at the end of that trading day. Sydney is 14–16 hours ahead of New York, so
"yesterday in Sydney" frequently resolves to a New York instant still in the
future. SIP then returns:

```
HTTP 403 {"message":"subscription does not permit querying recent SIP data"}
```

The message describes a subscription problem. The cause is a timezone bug.
The first probe run hit exactly this and produced the confident, wrong
conclusion that the free tier blocked SIP entirely — which would have
rerouted the whole project onto yfinance for no reason.

Worth dwelling on: this is the failure mode in miniature. Not a crash, not a
silent wrong number, but an error message that was **plausible, specific and
misleading**. Believing it would have cost weeks. The check that caught it
was re-testing the same claim a second way — the older-dated calls in checks
3 and 4 were succeeding with SIP at the same moment check 1 was "proving"
SIP was unavailable. Two results that contradict each other mean at least
one is a bug, and the contradiction is the signal.

**Rule: never pass a bare date to Alpaca.** Format every timestamp as
`2026-08-13T00:30:00Z`. Applies to the backtest engine, the daily live
fetch, and the news endpoint.

Scheduling consequence: the ~6:30am Sydney run lands roughly 30 minutes after
the 4pm ET close, by which point the 15-minute SIP lag has elapsed. That
day's daily bar is available at run time. The schedule works — but it is a
30-minute margin, and Sydney observes DST while it also shifts in the US, so
the gap moves during the year. Pin the schedule to a UTC time, not a local
one, and have the fetcher assert the expected bar date is present rather
than assuming it.

### D12 — Universe: top 100 by liquidity within the historical S&P 500
Alpaca serves bars for a delisted ticker if you know the ticker, but will not
enumerate delisted tickers — SIVB, FRC, TWTR, ATVI and MRO are all absent
from its 19,198-entry "inactive" asset list while their bars remain fully
retrievable. So the universe cannot be self-computed from Alpaca; an external
membership list is required.

Source: [fja05680/sp500](https://github.com/fja05680/sp500), the **dated**
components file (`...(01-17-2026).csv`). Each rebalance, rank that date's
members by 60-day average dollar volume and take the top 100.

Audited 13 Aug 2026 — see `DATA_AUDIT.md`. 729 tickers, 474 change-dates,
503–507 members per date, four delisting events verified against reality.

Three enforcement rules came out of the audit, all of which must live in the
loader rather than in anyone's memory:

1. **Discard bars outside the ticker's membership window.** Four tickers
   (STI, TE, WYND, AABA) are reused by unrelated companies that Alpaca serves
   happily under the same symbol.
2. **Membership is a set of intervals, not a span.** Eight tickers left and
   re-entered. SNDK is two unrelated companies nine years apart.
3. **Assert dataset coverage on load.** The obvious filename in that repo is
   stale by seven years and fails silently.

Known limitation: the file ends 2026-01-14, so live trading would run on a
universe frozen at January composition. Irrelevant to backtesting, revisit
before real money.

### D13 — No frontier LLM in the signal path. FinBERT stands.
Raised 13 Aug 2026: use Opus 5 (or DeepSeek, or Gemini Flash) for sentiment
instead of FinBERT. D4 rejected a *local* LLM on RAM grounds, which an API
model sidesteps entirely — so the original objection genuinely does not
apply. Rejected anyway, on three stronger grounds.

**1. Training-data contamination, and it scales the wrong way.**
Every current frontier model's cutoff falls inside the 2016–2026 training
window, so contamination applies to 100% of the backtest, not part of it.
Measured in the literature as "Lookahead Propensity": materially positive
in-sample, collapsing to ~zero immediately after the cutoff
([arXiv 2512.23847](https://arxiv.org/abs/2512.23847)).

The perverse part: a more capable model has memorised more specific firm
outcomes. For this task, better is worse. Opus is a poorer choice than
DeepSeek Flash, which is a poorer choice than FinBERT. Entity anonymisation
reduces the effect ([arXiv 2309.17322](https://arxiv.org/html/2309.17322v1))
but does not remove event recall, and it strips context worth keeping.

FinBERT is not immune — its cutoff is ~2019 — but a 110M-parameter BERT
fine-tuned on a phrasebank holds nothing like a frontier model's memory of
what happened to a specific company next.

**2. A subscription is not an API.** Claude usage limits apply to interactive
surfaces. A 6:30am scheduled script needs an API key with per-token billing;
there is no supported path pointing a headless cron job at subscription
limits. So this is an API cost question regardless, and Opus-tier pricing on
~750M input tokens of historical news is four figures per pass.

**3. Cost asymmetry breeds train/serve skew.** Live scoring is ~$1/month;
historical scoring is hundreds to thousands. That gap tempts using FinBERT
for history and an LLM live, which trains the model on one feature
distribution and serves it another. Silent and fatal.

**4. The input is the bottleneck, not the scorer.** Measured on Alpaca's news
endpoint, AAPL, 50 articles per window:

| Window | empty summary | empty content | avg symbols/article | >10 symbols |
|---|---|---|---|---|
| 2016 Q1 | 100% | 32% | 5.3 | 12% |
| 2019 Q1 | 100% | 72% | 3.4 | 6% |
| 2022 Q1 | 34% | 30% | 4.3 | 8% |
| 2026 Q2 | 18% | 6% | 6.8 | 20% |

For roughly the first six years of the window the corpus is **headlines
only**. Whatever edge a frontier model has over FinBERT on a full article
mostly evaporates on ten words of headline text. Paying four figures to
score bare headlines with a contaminated model is the worst available trade.

Volume is ~10–18 articles/day for a mega-cap, and articles average 3–7 tagged
symbols with a long tail (one 2016 article tagged 89 symbols). Attribution
filtering matters more than model choice.

**Legitimate LLM uses here:** writing code, and post-hoc explanation of
trades for the operator's understanding. Never an input to a decision.

**What would reopen this:** an A/B of FinBERT vs an API model on
post-cutoff data only, where Lookahead Propensity is ~zero. Cheap, a few
dollars. Meaningless until step 3 produces a baseline to measure against,
so it is scheduled after step 3, not before.

### D14 — The Alpaca MCP server is a research tool, not a runtime dependency
Connected 13 Aug 2026. Useful for interactive inspection during design
sessions — it answered the news-depth question above without writing a
throwaway script. The bot itself calls the Alpaca REST API directly from
Python. A scheduled headless job must not depend on an MCP server being
present, authenticated, or version-stable.

Also noted from MCP output: news items carry both `created_at` and
`updated_at`, and querying a window can return articles created before it.
Keying off `updated_at` while reading current text is a lookahead vector —
an article revised after the fact would leak later information. The news
loader filters on `created_at` and records both.

### D15 — `adjustment=all` does not cover spin-offs
Found 13 Aug 2026 by verifying the cached data, not by reading docs.

D7 established that `adjustment=all` fixes splits and dividends. It does. It
does **not** adjust for spin-offs, where a company hands shareholders stock in
a new entity and its own share price drops accordingly. The holder loses
nothing; the price series shows a crash.

Confirmed artifacts in the cache:

| Symbol | Date | Apparent move | Actually |
|---|---|---|---|
| ARNC | 2020-04-01 | -89.2% | Howmet spin-off |
| WRK | 2016-05-16 | -84.5% | Ingevity spin-off |
| APTV | 2017-12-05 | -71.6% | Delphi Technologies spin-off |
| RTX | 2020-04-03 | -71.0% | Carrier and Otis spin-offs |
| EQT | 2021-08-30 | -57.5% | Equitrans spin-off |

Real crashes in the same range, which must **not** be removed: FRC -61.8%
(2023-03-13, banking crisis), OXY -52.0% and APA -53.9% (2020-03-09, oil price
war), GL -53.1% (2024-04-11, short-seller report).

So a blanket "discard extreme moves" filter would delete genuine market events
— the exact observations a swing-trading model most needs to learn from. The
fix must distinguish corporate actions from price action, not threshold on
magnitude.

Scale: 292 rows exceed ±25% out of 1,316,452 (0.022%); 23 exceed ±50%. Small
in count, large in influence — these are the highest-leverage rows in any
tree-based model.

**Action before feature engineering (step 3):** cross-check every |return| >
25% against Alpaca's corporate actions endpoint, adjust the series for
confirmed spin-offs, and leave genuine moves untouched. Tracked as a
prerequisite, not a refinement.

### D16 — SPY is fetched separately from the constituent universe
SPY is an ETF, not an S&P 500 member, so it never appears in the constituent
file. It was missing from the first full pull. Since the entire success bar is
stated relative to SPY buy-and-hold, the benchmark must be fetched explicitly
and its presence asserted before any backtest is allowed to report a number.

Cached 2016-01-04 to 2026-08-12, 2,667 bars.

### D17 — Entry is a bracket order with `tif=day`, not market-on-open
Tested against the paper account 13 Aug 2026. Alpaca rejects bracket orders
with `time_in_force=opg`:

> `bracket orders support only "day" or "gtc" time_in_force`

So attaching a stop and target at entry is incompatible with the opening
auction order type. The alternative — market-on-open, then a second routine to
attach stops after the fill — leaves the position unprotected through the
open, which is the most volatile part of the session.

Chosen: one bracket order per entry, `type=market`, `tif=day`, submitted
pre-open. Fills immediately at the open and the exits are live from the first
moment. Confirmed accepted with 2 legs.

Consequence for the backtest: fills are modelled at the day's open, which is
the correct reference for this order type.

### D18 — Backtest modelling choices, each biased against the strategy
Where daily bars are ambiguous, the engine takes the unfavourable branch. Each
of these is a place a backtest can flatter itself:

- **Both stop and target inside one bar** → assume the **stop** hit first.
  Daily bars cannot order intraday events. Assuming the target would
  manufacture returns. Tested explicitly.
- **Gaps through a stop** → fill at the open, not the stop price. Gaps are the
  normal way stops cost more than planned.
- **Same-day stop-out** → a position opened today can be stopped out today.
  Excluding it would quietly delete the worst trades.
- **Whole shares only**, and an order is skipped if cash is short. No
  fractional wishful thinking.
- **Slippage 3.5 bps per side** (~0.07% round trip), from the measured median
  S&P 100 spread. Configurable, never zero by default.

Lookahead is prevented structurally, not by convention: the strategy receives
a `MarketView` built from a frame already sliced to `date <= decision date`.
There is no method on it that can return the future, because the future is not
in the object.

### D19 — Step 1 acceptance: PASSED 13 Aug 2026
| Check | Result |
|---|---|
| SPY buy-and-hold reproduced | 343.53% vs 344.11% truth, **0.58% gap** |
| Gap direction | negative (costs and rounding, not free money) |
| Worst drawdown | -33.77% on **2020-03-23** — the actual COVID bottom |
| Year-by-year vs raw SPY | matches within 0.1% every year, 2017–2026 |
| Lookahead | strategy provably cannot see past its decision date |
| Cost model | monotonic: more slippage, less money |
| Ambiguous bars | stop taken, not target |

The drawdown date matters more than the return number. Nothing in the engine
knows about COVID; it found 2020-03-23 by simulating trades. An engine that
independently rediscovers a real historical event is doing arithmetic on
reality rather than on a bug.

Two results worth keeping in view:

**Costs are not a footnote.** The same churning strategy over the same data:
+372.66% at zero slippage, +173.96% at a realistic 3.5 bps, **-90.30%** at
25 bps. Identical decisions. Only the cost assumption changed.

**Activity is not returns.** A 3-position rotation with stops and targets made
1,254 trades over the decade and returned 13.18% CAGR, against SPY's 15.08%
for buying once and never touching it. It worked harder, took more risk, and
finished behind. That is the bar step 2 and step 3 have to clear.

### D20 — Step 1 re-validated at full scale. Two real defects found.
The first validation pass ran on SPY alone plus a 5-symbol toy set — about
**1% of the data**, and it never exercised the `members_only` path at all.
Papaya questioned how it finished so quickly. The question was correct.

**Defect 1 — delisted positions silently erased equity.** A position whose
symbol stopped having bars was never closed and was excluded from the
mark-to-market. Holding SIVB through its delisting made $50,000 disappear
with no trade recorded and no error. Fixed: a symbol with no bar past its
final bar date is liquidated at the last known price with reason
`delisted`; a symbol merely halted is marked at its last price and held.
The distinction requires knowing each symbol's true final bar, now
precomputed.

**Defect 2 — the constituent file drops failing companies mid-collapse.**
Measured against unfiltered Alpaca data:

| Symbol | List drops it | Really traded until | Move missed |
|---|---|---|---|
| SIVB | 2023-01-04 @ $240 | 2023-03-09 @ $106 | **-55.8%** |
| FRC | 2023-03-20 @ $12.18 | 2023-04-28 @ $3.51 | **-71.2%** |
| TWTR | 2022-10-12 | 2022-10-27 | +7.5% |
| ATVI | 2023-10-02 | 2023-10-13 | +0.5% |

The bias is asymmetric. Acquisitions freeze near the deal price, so missing
the tail is harmless. Failures keep falling. Truncating data at the
membership end meant positions in collapsing companies were force-sold at a
stale price — always flattering.

Fixed with `trail_days=45` in `fetch_symbol`: bars are kept for 45 calendar
days past membership end, comfortably covering the 10-day maximum hold, so
exits price against real bars. 222 of 725 symbols re-fetched.

**Also fixed:** the engine rebuilt the strategy's data view by boolean-scanning
all 1.3M rows on each of 2,529 days. Replaced with a contiguous positional
slice. Full-universe run is now 25 seconds.

**Investigated and NOT a bug:** three trades appeared to enter while the
symbol was not an index member (THC, NFX, KSS). All three were members on the
decision day and removed the next morning. Deciding on day D and filling on
D+1 is exactly what the live bot does. The check was stricter than reality.

### D21 — Momentum baseline result: loses to SPY
First full-universe run, 20-day momentum, top 10 of the S&P 500 by
membership, 8% stop, 12% target, 10-day maximum hold, 3.5 bps per side:

| | Strategy | SPY |
|---|---|---|
| CAGR | **8.07%** | **14.83%** |
| Max drawdown | -38.19% | -33.79% |
| Sharpe | **0.47** | **0.87** |
| Trades | 2,847 over 522 symbols | 1 |

Worse return, deeper drawdown, roughly half the risk-adjusted performance —
after 2,847 trades. This is the expected and useful result. It is the number
step 3 has to beat, and it is deliberately measured before any model exists so
the model's contribution cannot be confused with the engine's.

### D22 — Step 3 result: XGBoost does not beat the baseline or SPY
14 features, cross-sectionally ranked per day. Label is binary: does the stock
beat the day's median forward 5-day return. Predicting relative rather than
absolute return removes the market factor, which is otherwise most of what a
model learns.

Lookahead verified structurally: features rebuilt with a decade of future data
appended are **byte-identical** (max difference 0.00e+00 across 563,739
overlapping rows). Labels correctly go missing at the truncation boundary.

Walk-forward, expanding window, 5-day purge gap, 7 folds 2020–2026,
hyperparameters fixed in advance and never tuned on test data:

- mean out-of-sample accuracy **50.10%**
- mean in-sample/out-of-sample gap **2.93%**

Through the backtest engine, out-of-sample 2020-01 to 2026-01:

| | CAGR | Sharpe | max DD | trades |
|---|---|---|---|---|
| XGBoost | 13.56% | **0.73** | -22.1% | 1,587 |
| Momentum baseline | 16.21% | **0.77** | -24.3% | 1,768 |
| SPY | 14.90% | **0.79** | -33.8% | 1 |

**The model loses to both.** It did not clear the bar.

### D23 — None of these differences are statistically real
The more important finding. Over 6.1 years the standard error on a Sharpe
estimate is **±0.46**:

| | Sharpe | 95% interval |
|---|---|---|
| XGBoost | 0.73 | [-0.17, 1.63] |
| Momentum | 0.77 | [-0.13, 1.68] |
| SPY | 0.79 | [-0.13, 1.70] |

Paired t-tests on daily returns: XGBoost vs SPY p=0.86, XGBoost vs Momentum
p=0.77, Momentum vs SPY p=0.85. **Nothing is distinguishable from noise.**

This cuts both ways and must be applied honestly in both directions. We cannot
claim the model failed on this evidence any more than we could have claimed it
succeeded. Six years is simply not enough to separate a Sharpe of 0.73 from
0.79. Had the numbers come out the other way, the correct conclusion would have
been identical: not proven.

**Rule going forward: no strategy comparison is reported without its error
bar.** A point estimate with three decimal places and an interval of ±0.46 is
a false claim of precision.

**Metric-shopping warning, logged deliberately.** XGBoost has a clearly better
maximum drawdown (-22.1% vs SPY's -33.8%), and on a return-over-drawdown basis
it wins (0.61 vs 0.44). That comparison was constructed *after* seeing the
results, which makes it worthless as evidence. The success bar was written in
advance and names risk-adjusted return; Sharpe is the standard measure and the
model lost on it. Choosing the metric after seeing the outcome is the most
common way a failed strategy gets promoted to a successful one.

### D24 — Diagnosis: no ranking skill. The "longer holds" lead was market beta.
Rather than guess at improvements, measured what the model can actually do.

**Information Coefficient** (daily rank correlation between predicted score and
realised forward return) at increasing holding periods:

| Hold | Overlapping t | Independent obs | Non-overlapping t |
|---|---|---|---|
| 5d | 2.80 | 303 | **0.20** |
| 10d | 2.52 | 151 | **0.44** |
| 20d | 4.13 | 75 | **0.94** |
| 40d | 4.97 | 37 | **1.23** |
| 60d | 4.43 | 25 | 1.74 |

The overlapping column looked like a discovery — IC rising to 0.0168 at 40 days,
a top-minus-bottom decile spread of 0.92%, and an edge/cost ratio of 13x. It was
an artefact. Forward returns computed every day over a 40-day window share 39 of
40 days with their neighbours, so 1,512 "observations" are really about 37
independent ones. Correcting for that, **nothing is significant at any horizon.**

**And the alternative explanation held.** Top-decile return versus SPY over the
same window:

| Hold | Top decile | SPY | Excess |
|---|---|---|---|
| 5d | +0.296% | +0.316% | **-0.020%** |
| 20d | +1.205% | +1.249% | **-0.044%** |
| 40d | +2.458% | +2.495% | **-0.037%** |

The top decile slightly *underperforms* the market at every horizon. The
apparent improvement from holding longer was simply owning stocks for longer in
a decade when stocks went up.

**Conclusion: the model has no demonstrable ranking ability.** Not a weak edge
worth optimising — no measurable edge.

**Rule added: any result computed from overlapping windows is reported with its
independent-observation count and a non-overlapping test beside it.** This
single artefact turned a null result into an apparent 13x edge, and it is the
most convincing wrong answer produced in the project so far.

### D25 — v2 failed. Holdout deliberately NOT spent.
All five pre-registered changes applied together (see `PREREGISTRATION.md`):
triple-barrier labels, non-overlapping sampling, regression objective,
volatility-scaled sizing, hyperparameters untouched.

| | v1 | v2 |
|---|---|---|
| Development IC | +0.0111 | **-0.0072** |
| Significant folds | 0 of 7 | 0 of 3 |

Worse, and negative. **The 2025+ holdout was not evaluated.** Its single
unbiased shot is worth more than a reading on a model that already failed in
development. Spending it now would contaminate the only clean test remaining
for a future candidate.

### D26 — Root cause: the features contain nothing
Rather than search for a better model, tested whether the raw material has any
signal. Each of the 14 features against the bracket outcome, independent
t-statistics, development period only:

| Feature | IC | indep t |
|---|---|---|
| vol_20 | -0.0194 | -2.17 |
| gap_20 | -0.0142 | -1.25 |
| volume_ratio | -0.0003 | -1.09 |
| ...all others | | **\|t\| < 1** |
| mom_20 | -0.0093 | -0.28 |

**One feature out of 14 exceeds \|t\| = 2. Chance alone predicts 0.7.**

And the one that shows up is `vol_20` — volatility, with a *negative* sign,
which is the well-documented low-volatility effect, not a momentum signal. Every
momentum feature, the entire premise of the feature set, sits at \|t\| < 0.5.

This explains everything upstream. v1 failing, v2 failing, the model's
importances spread evenly across all features at 0.07–0.08 — that is what a
tree model does when no feature helps. It was never a modelling problem.
**Fourteen price-derived features on S&P large caps at 5–10 day horizons contain
no tradeable signal.** No architecture, label scheme, or hyperparameter fixes an
absent effect.

### D27 — Iteration stopped, deliberately, short of the requested target
The instruction was to iterate autonomously until results were 30% better than
the momentum baseline and report only then.

Simulated with every variant assumed worthless, at the measured Sharpe standard
error of ±0.46: trying 5 variants hits "+30%" **84%** of the time, and 20
variants hits it **100%** of the time. The target is inside the noise, so a
search terminating on it always succeeds and always means nothing.

One pre-registered iteration was run. It failed. Continuing would have been a
search for a lucky seed, so it was stopped. **No result meeting the requested
threshold can be produced honestly from this data**, and producing one anyway
would violate rules 2, 3 and 4 of this project.

### D28 — v3: new information categories added. Also no signal.
Fair challenge from Papaya: D26 concluded "features are exhausted" when it had
only shown "*price-derived* features are exhausted". Those are different, and
there was free, live-available information never tried. Eleven new features
built from genuinely different sources:

- **Sector-relative momentum** — sectors derived from return-correlation
  clustering fitted only on pre-2020 data, so membership is point-in-time clean.
  No external classification file needed.
- **Market regime** — market volatility, momentum, drawdown from the 1-year high.
- **Breadth** — fraction of the universe above its own 50-day average.
- **Beta and idiosyncratic volatility** — 60-day, market-relative.
- **Dividend proximity** — days to and from ex-dividend. 19,539 events across
  586 symbols pulled from Alpaca's corporate actions endpoint, free and live.

Standalone independent t-statistics, development period only:

| Feature | IC | indep t |
|---|---|---|
| idio_vol60 | -0.0229 | -1.64 |
| days_to_div | -0.0061 | -1.43 |
| sect_rel_mom60 | -0.0066 | -1.38 |
| sect_rel_mom20 | -0.0071 | -1.30 |
| all others | | \|t\| < 1.1 |

**0 of 11 above \|t\| = 2. Chance predicts 0.6.**

Combined model, all 24 features (interactions are the one thing individually
weak features can still provide, and finding them is precisely XGBoost's job):

| Version | Features | Development IC | mean indep t |
|---|---|---|---|
| v1 | 14 price | +0.0111 | 0.20 |
| v2 | 14 price, fixed labels | -0.0072 | — |
| **v3** | **24, three information categories** | **-0.0206** | **-0.16** |

Adding information made it worse, which is what happens when the added
information is noise: more ways to fit the training years.

Feature importances spread from 0.092 to 0.019 across 24 features — no
dominant signal, the flat profile of a model with nothing to find.

**Cumulative: 25 features tested individually across price, sector, market
regime, and corporate-action data. One exceeded \|t\| = 2 (vol_20, negative —
the low-volatility effect, a portfolio-construction tilt rather than a timing
signal). Chance alone predicts 1.3.**

The holdout has still never been evaluated. Three model generations have failed
in development; spending the single unbiased test on any of them would waste it.

### D29 — v4: Kaggle SEC Form 4 insider data. No signal, and it made the model worse.
Papaya asked for valuable Kaggle datasets to feed the model. The blanket
"no Kaggle" in STATUS.md and PREREGISTRATION.md was aimed at *price history*,
which is survivors-only and cannot help anyway — D26/D28 measured that price
features contain nothing, and more rows of nothing is still nothing. That
rejection stands unchanged.

What it did not cover is Kaggle as a source of a **different information
category**. Two were found, audited and tried.

**Source chosen: `secfilingapi/sec-form-4-filings`.** Preferred over
`richard47/us-sec-insider-trading-dataset-3m-transactions` for one reason: it
carries `filing_date` *and* `earliest_execution_date` as separate columns,
while richard47 has a single ambiguous `date`. That distinction is the whole
ballgame — the median filing lag here is 2 days and the 75th percentile is 4,
so joining on execution date hands the model up to a week of future knowledge
on precisely the events that move prices. Coverage: 590 of 729 universe
tickers, 208,949 filings, 53,023 directional, 2020-01-02 to 2025-07-14. It
also ships `under_schedule`, the Rule 10b5-1 flag, which is the
routine-versus-opportunistic split that Cohen, Malloy & Pomorski (2012) found
carries the entire predictive content of insider trading.

**Two columns discarded as corrupt.** `aggregated_value_usd` ranges to
+7.17e15 and -1.50e15; `aggregated_percent_of_shares` reaches 2,857,143%.
These were the two most natural features to build and both would have handed a
tree model a handful of absurd outliers to carve out. Counts and signs used
instead.

**Eight insider features built, keyed on `filing_date` only, lagged one
further day** because EDGAR accepts Form 4s until 22:00 ET, which is after
this bot's 06:30 Sydney run. Lookahead tested the same way D22 tested the
price features — rebuilt from a panel truncated at 2022-06-30 and compared to
the full-panel build. **Max absolute difference 0.00e+00 on all ten features.**

#### The earnings feature that looked real and was survivorship bias

`adarsh1077/epsclassification` supplied earnings announcement dates. Only the
dates were used; `beat`, `beat_streak`, `historical_beat_rate` and
`avg_surprise_4q` were discarded because `beat` is the outcome of the
announcement and the derived columns are of unverifiable construction. From
the dates, `earn_phase` = days since the last release over that company's
median gap between *prior* releases — deliberately backward-only, since
"days until next earnings" needs a date that is not always pre-announced.

`earn_phase` scored **indep t = +2.67**, the only feature out of 24 above
|t| = 2, and it survives none of the three checks it was given:

| Check | Result |
|---|---|
| Restricted to symbols the earnings file covers | **t = -0.16** (from +2.67) |
| Year by year, covered rows | +0.42, -1.52, -0.83, -0.36, +1.53 — sign flips annually |
| Bonferroni across 10 v4 features | needs \|t\| > 2.81. Across all 24: > 3.08 |

The earnings file covers 472 of 601 development symbols. The 129 it misses are
disproportionately names that were acquired or delisted — a 2026-vintage
S&P 500 earnings dataset has no reason to contain SIVB or FRC. Missing symbols
got a sentinel, and after cross-sectional ranking every sentinel row landed in
the same extreme rank block. **The feature was ranking "did this ticker
survive" and reporting it as "position in the earnings cycle."**

This is worth dwelling on, because it is the D10 pattern again: not a crash,
not an obviously wrong number, but a *plausible, specific and misleading*
result. A t of 2.67 on a feature with a real academic story behind it — the
earnings announcement premium is a documented effect — is exactly the kind of
finding that gets promoted to a model input without a second look. It is also
the same survivorship bias the project rejected Kaggle price data to avoid,
arriving through a missing-data sentinel instead of through a ticker list.

Both earnings features were dropped before any model was fitted.

#### Standalone feature tests, development window, independent observations

| Feature | IC | indep t |
|---|---|---|
| ins_activity_20 | +0.0073 | 0.78 |
| ins_days_since_buy | -0.0045 | -0.64 |
| ins_sell_60 | +0.0093 | 0.52 |
| ins_exec_net_60 | -0.0069 | -0.39 |
| ins_buy_60 | +0.0021 | 0.38 |
| ins_net_60 | -0.0079 | -0.34 |
| ins_opp_net_60 | -0.0054 | -0.32 |
| ins_net_20 | -0.0067 | -0.10 |

**0 of 8 above |t| = 2. Chance predicts 0.4.** The opportunistic split, which
is where the literature says the entire effect lives, is the second-weakest
feature in the set.

#### The model

v1 architecture, v1 hyperparameters untouched, 14 price features -> 22.
Window 2020-01-01 to 2024-12-31 for both, because the Form 4 source starts
2020. Three walk-forward folds, not v1's original seven.

| | oos accuracy | in/out gap | pooled IC | indep t |
|---|---|---|---|---|
| v1, price only | **50.70%** | **3.44%** | +0.0171 | +0.02 |
| v4, price + insider | **50.44%** | **6.82%** | +0.0115 | -0.53 |

Means across five random seeds. **v4 lost on all 5, and had the larger
overfitting gap on all 5.** Seed-to-seed standard deviation is 0.07% on
accuracy against a 0.26% gap, so this is not seed luck.

The insider features took **43.2% of total feature importance** while being
36.4% of the columns. The model leaned on them more than proportionally and
got worse out of sample. That combination — heavy importance, doubled
in/out gap, lower out-of-sample accuracy — is the signature of a model given
more ways to memorise the training years.

#### Shuffle control, the decisive test

Insider values permuted within each day, preserving the marginal distribution
exactly and destroying only the symbol-to-value mapping:

| | oos accuracy | in/out gap | IC |
|---|---|---|---|
| v1, no insider features | 50.68% | 3.44% | +0.0166 |
| v4, real insider values | 50.46% | 6.74% | +0.0115 |
| v4, **shuffled** insider values | **50.52%** | 6.17% | +0.0131 |

**Randomised insider data performs marginally better than real insider data.**
The real values carry no more information than a permutation of themselves.
The entire measurable effect of adding eight insider columns was to double the
overfitting gap, and that happened whether the values meant anything or not.

#### Backtest, 2022-01-01 to 2024-12-31, 3.5 bps per side

| | CAGR | Sharpe | ±SE | max DD | trades |
|---|---|---|---|---|---|
| v1 (price only) | 10.57% | **0.66** | 0.64 | -21.6% | 803 |
| v4 (price+insider) | 3.64% | **0.28** | 0.59 | -25.1% | 807 |
| momentum 20d | 6.12% | **0.39** | 0.60 | -18.7% | 878 |
| SPY buy & hold | 8.50% | **0.56** | 0.62 | -24.7% | 1 |

Over three years the Sharpe standard error is **±0.62**, up from D23's ±0.46
over 6.1 years. Every 95% interval overlaps every other: v1 [-0.59, 1.91],
v4 [-0.87, 1.44], momentum [-0.78, 1.57], SPY [-0.66, 1.77]. All six paired
t-tests on daily returns return p > 0.45. **Nothing here is distinguishable
from anything else**, including v4's apparent collapse from 0.66 to 0.28.

This must be applied in both directions, per D23. v4 looking worse in the
backtest is not evidence that it is worse. The *development* evidence is what
carries weight, because it is seed-stable, shuffle-controlled and consistent
across every diagnostic — and it says v4 is worse.

**Engine re-validated on the way through.** SPY 2022 -18.64%, 2023 +26.71%,
2024 +25.59%, against externally known -18%, +26%, +25%. Engine CAGR 8.50% vs
8.65% computed outside the engine, the gap in the correct direction (costs).

#### Why v4 lost — the mechanism, with a control that refined the answer

Papaya asked how v1 beat "a more trained model". v4 is not more trained: same
300 trees, same depth 4, same learning rate, same 627,918 rows, same 3 folds.
Only the column count changed, 14 -> 22.

What actually happened is visible in the in-sample column, which is why v1
reports both:

| | in-sample | out-of-sample | gap |
|---|---|---|---|
| v1 | 54.12% | **50.68%** | **3.44%** |
| v4 | **57.20%** | 50.46% | 6.74% |

v4 got 3.1 points better at data it had already seen and 0.2 points worse at
data it had not. It learned 3.1 points of something, none of which survived a
new year.

The obvious explanation — more columns, more chances to find a spurious split
— was tested rather than assumed. v1 plus **8 columns of pure random numbers**:

| variant | in-samp | out-samp | gap | IC |
|---|---|---|---|---|
| v1, 14 price | 54.12% | **50.68%** | **3.44%** | **+0.0166** |
| v1 + 8 **pure random** columns | 54.28% | 50.63% | 3.64% | +0.0161 |
| v4, 14 price + 8 insider | 57.20% | 50.46% | 6.74% | +0.0115 |

**Random columns were nearly harmless.** `min_child_weight=200` and
`reg_lambda=5.0` absorb i.i.d. noise without difficulty. So the damage is not
the column count — it is the *shape* of these particular columns.

The insider features are sparse and heavily tied: `ins_net_60` is exactly zero
on 43% of rows, `ins_buy_60` on 90%, `ins_net_20` on 69%. Cross-sectional
ranking collapses every zero onto one identical value, so each feature is a
large discrete block plus a thin tail. A greedy split placed at a tie boundary
produces a big, clean partition with high apparent gain — measured on training
data. Eight such features give the model a lattice of well-defined
subpopulations to assign per-cell biases to. Uniform noise offers no such
boundaries and the partitions do not hold from tree to tree.

The shuffle control is consistent: permuted insider values, which preserve the
sparse marginal distribution and destroy the content, still produced a 6.17%
gap. Structure did the damage; content contributed nothing.

Dilution compounds it. `colsample_bytree=0.7` means each tree sees ~10 of 14
columns in v1 and ~15 of 22 in v4, so the weak-but-nonzero price signal gets
crowded out: IC +0.0166 -> +0.0115.

**Rule added: a new feature block is compared against a pure-noise block of
the same width, not only against the model without it.** "Adding features hurt"
and "adding *these* features hurt" are different findings, and only the control
separates them.

Note on why the backtest gap (Sharpe 0.66 -> 0.28) exceeds the accuracy gap
(0.22 points): the strategy holds 10 of ~500 candidates, so a small ranking
change reshuffles the whole portfolio and 10 names is too small a sample for
luck to average out. That is why the backtest sits inside its error bar
(p=0.45) while the development diagnostics, seed-stable to 0.07%, sit outside
theirs. The development evidence convicts v4; the backtest merely agrees, and
does so unreliably.

#### Conclusion

Cumulative across the project: **33 features tested individually across price,
sector, market regime, corporate actions, insider transactions and earnings
timing. One exceeded |t| = 2 for a reason other than a data artefact.** Chance
alone predicts 1.7.

The Kaggle question is answered: not for lack of a good dataset. The Form 4
data is real, well constructed, point-in-time honest and cleanly joined, and
it contains nothing this strategy can use at a 5-10 day horizon on S&P large
caps. That is a stronger result than "we could not find good data".

**The 2025+ holdout was not evaluated.** Four model generations have now
failed in development. Spending the single unbiased shot on the worst of them
would be the most expensive mistake available.

**What would reopen this:** a longer holding period. The insider-trading
literature works at 3-12 month horizons and mostly in small and mid caps;
this project trades 2-10 day brackets on mega caps. That is not evidence the
effect is absent, only that it is absent *here*. Testing it properly would
mean changing D1, which is locked, and D24 already measured that longer holds
produce market beta rather than skill.

### D30 — CORRECTION to D29's backtest claim. The 10-position backtest measures nothing.
Papaya's objection: the four versions were never asked the same question, so
the comparison table in D29 and STATUS.md implied a test that was never run.
Correct on three counts.

1. **Different targets.** v1 and v4 predict a binary "beats the day's median
   5-day forward return" and their ICs were measured against `fwd_return`.
   v2 and v3 predict the cross-sectionally demeaned triple-barrier return and
   their ICs were measured against `bt_return`. Those numbers were printed in
   one column. They are not the same quantity.
2. **Different windows.** v1's headline came from 2020-2026 with 7 folds;
   everything else ran 2020-2024 with 3.
3. **Two axes conflated.** v1 -> v2 changes the architecture. v1 -> v4 changes
   the features. A single ranked list cannot separate them.

Rebuilt as a factorial on one panel: 2 architectures x 4 feature sets, 627,844
identical development rows, identical folds, 375,697 identical test rows per
cell, every model scored the same three ways regardless of what it was trained
to predict. See `scripts/bakeoff.py`.

#### The retraction

**D29 reported v1 Sharpe 0.66 against v4 Sharpe 0.28 and treated that as
supporting evidence. It was not evidence.** Re-running v1 on the unified panel
— same features, same architecture, same folds, differing only in the row
ordering fed to XGBoost and 74 rows out of 627,844 — produced:

| | out-of-sample acc | backtest Sharpe |
|---|---|---|
| v1, run A | 50.68% | 0.66 |
| v1, run B | 50.71% | **0.26** |
| v4, run A | 50.46% | 0.28 |
| v4, run B | 50.48% | **0.85** |

The models are identical to three hundredths of a percentage point. The
Sharpes swapped places.

Diagnosed by holding the predictions fixed and varying only position count:

| | N=10 | N=25 | N=50 | N=100 |
|---|---|---|---|---|
| v1 CLS price | 0.26 | 0.52 | 0.37 | 0.41 |
| v4 CLS price+insider | 0.85 | 0.42 | 0.56 | 0.33 |
| **spread** | **0.58** | **0.10** | **0.19** | **0.08** |

**Roughly 75% of the apparent difference at 10 positions was name-selection
luck.** Holding 10 of ~500 candidates means the backtest samples ten draws
from the cross-section every few days; the model's contribution is smaller
than that sampling noise. D23's rule — no comparison without its error bar —
was followed, and the error bar (+/-0.62) was correct. It was then
under-weighted in the narrative anyway, which is the more insidious failure:
the caveat was present and the conclusion ignored it.

**Consequence for step 5: the live strategy needs at least 25 positions, not
10, or no amount of paper trading will distinguish a working model from a
broken one.** This is the most actionable result in the project so far and it
would have been baked into the execution layer wrong.

#### What survives from D29

The features conclusion, which never depended on the backtest:

| | in-samp | out-samp | gap | IC_bt | t_bt |
|---|---|---|---|---|---|
| v1 CLS price (14) | 54.14% | **50.71%** | 3.43% | +0.0216 | 2.10 |
| v4 CLS price+insider (22) | 57.40% | 50.48% | 6.92% | +0.0229 | 0.98 |

Reproduced on the unified panel, and previously across 5 seeds. The insider
data does not help.

#### What the factorial showed that no earlier run could

**Architecture dominates overfitting.** REG's in/out gap is 1.89-2.50% across
every feature set; CLS's is 3.43-7.51%.

| arch | features | in-samp | out-samp | gap | IC_bt | t_bt |
|---|---|---|---|---|---|---|
| CLS | price (v1) | 54.14% | **50.71%** | 3.43% | +0.0216 | 2.10 |
| CLS | price+v3 | 55.85% | 50.36% | 5.49% | +0.0111 | 2.08 |
| CLS | price+insider (v4) | 57.40% | 50.48% | 6.92% | +0.0229 | 0.98 |
| CLS | price+v3+insider | 57.94% | 50.43% | 7.51% | +0.0195 | 1.86 |
| REG | price (v2) | 51.54% | 49.65% | **1.89%** | -0.0091 | -0.52 |
| REG | price+v3 (v3) | 52.17% | 50.02% | 2.15% | +0.0013 | 1.75 |
| REG | price+insider | 52.29% | 49.91% | 2.37% | -0.0016 | 0.52 |
| REG | price+v3+insider | 52.71% | 50.20% | 2.50% | +0.0070 | 1.85 |

**C2 — non-overlapping sampling — worked exactly as pre-registered.** D25
declared v2 a failure on an IC measured against a different target and never
noticed that the defect it was built to fix had been fixed. That is the direct
cost of not holding the evaluation constant.

**The feature effect changes sign with architecture:**

| | adding v3 | adding insider | adding both |
|---|---|---|---|
| under CLS | -0.35% acc, +2.06% gap | -0.23% acc, +3.49% gap | -0.28% acc, +4.08% gap |
| under REG | **+0.37%** acc, +0.25% gap | **+0.27%** acc, +0.48% gap | **+0.56%** acc, +0.61% gap |

Mechanism: CLS trains on all 627k rows whose 5-day labels overlap on 4 of 5
days, so the effective sample is roughly five times smaller than it appears
and the model is already overconfident — extra columns are fuel. REG trains on
genuinely independent observations and can absorb them. Same features,
opposite sign. D29's "adding features hurts" was true only of the CLS half of
the grid.

Max |t_bt| across the eight cells is 2.10 against an expected max of ~1.4
under the null. Nothing is promoted for being a grid maximum. All eight cells
are indistinguishable from SPY on paired daily returns, p > 0.22.

#### Rules added

- **Every version comparison is run as a grid with one axis varying at a time,
  on identical rows, folds and scoring targets.** A version is a cell, not a
  point on a line.
- **A model is always scored on the same target as every other model,
  regardless of what it was trained to predict.** Training target is part of
  the architecture; evaluation target is part of the test and must be held
  constant.
- **Backtest comparisons at fewer than 25 positions are not evidence.**
  Confirm with an N sweep before quoting a Sharpe difference between models.
- **An error bar that is printed but not obeyed is worse than one that is
  absent**, because it makes the write-up look rigorous while the conclusion
  ignores it. D29 printed +/-0.62 and then argued from a 0.38 difference.

### D31 — The noise floor. Only one difference in the grid is real, and raising N does not beat SPY.
Two questions asked after D30: raise the position count so the backtest can
measure, and improve v4. Both were measured before being answered, and both
answers are the opposite of the expected one.

#### The noise floor on out-of-sample accuracy

Block bootstrap, 2,000 resamples, 10-session blocks (blocks rather than days
because the 5-day forward label makes adjacent days share four fifths of their
outcome; resampling single days would understate the error by roughly sqrt(5)).

Standard error per cell: **0.20% to 0.34%, mean 0.30%.** The eight cells span
1.07%. Unpaired that looks like 3.5 standard errors, which is why the earlier
table read as though it contained real differences.

Paired on identical days, with block-bootstrapped standard errors on the
difference series:

| v1 vs | diff | SE | t | p |
|---|---|---|---|---|
| **v4** CLS price+insider | +0.24% | 0.23% | 1.04 | 0.300 |
| CLS price+v3 | +0.35% | 0.24% | 1.45 | 0.148 |
| CLS price+v3+insider | +0.28% | 0.27% | 1.05 | 0.294 |
| **v3** REG price+v3 | +0.69% | 0.45% | 1.54 | 0.125 |
| REG price+insider | +0.80% | 0.37% | 2.14 | 0.032 |
| REG price+v3+insider | +0.51% | 0.44% | 1.16 | 0.244 |
| **v2** REG price | **+1.07%** | 0.28% | **3.78** | **0.0002** |

Seven comparisons, so the Bonferroni threshold is p < 0.007. **Exactly one
difference in the grid survives: v1 beats v2 by 1.07 percentage points.** It
is an architecture difference. Every feature-set difference, v1 vs v4
included, is inside the noise.

**Consequence: "improve v4" has no measurable target.** v4 and v1 are not
distinguishable on out-of-sample accuracy (p = 0.30). What separates them is
the overfitting gap (6.92% vs 3.43%) and the shuffle control, and neither of
those is fixed by better insider features — the shuffle control is precisely
the finding that the values carry nothing.

#### Correction to D30 on C2

D30 said non-overlapping sampling "worked exactly as pre-registered". Half
right, and stated too cleanly. C2 does cut the overfitting gap, 3.43% ->
1.89%. It also costs 1.07 percentage points of out-of-sample accuracy, and
**that cost is the only statistically real effect in the grid.**

Mechanism: C2 discards 90% of training rows on the theory that overlapping
5-day labels are redundant. They are mostly redundant — but labels at day t
and day t+1 differ on one of their five days, and that residual carried
something. C2 is a lower-variance, lower-accuracy point on a trade-off curve,
not a free upgrade. The open-list item is reworded accordingly.

#### Raising the position count does not beat SPY

Same predictions, varying only N:

| Sharpe | N=10 | N=25 | N=50 | N=100 | N=200 | vs SPY @200 |
|---|---|---|---|---|---|---|
| v1 CLS price | 0.26 | 0.52 | 0.37 | 0.41 | **0.49** | -0.07 (p=0.46) |
| v4 CLS price+insider | 0.85 | 0.42 | 0.56 | 0.33 | **0.38** | -0.17 (p=0.29) |
| v2 REG price | 0.10 | -0.15 | 0.05 | 0.17 | **0.22** | -0.34 (p=0.13) |
| SPY | 0.56 | 0.56 | 0.56 | 0.56 | 0.56 | |

The prediction stated before running it: a portfolio with no ranking skill
converges, as N grows, on owning the index minus trading cost — so the Sharpe
should approach SPY's **from below**. That is what all three do. At N=200 the
strategy holds 40% of the universe, which is an index fund with a slippage
bill, and v1's 0.07 shortfall is approximately what the trading costs.

**Raising N buys measurement, not performance.** Build step 5 at N >= 25
because it is how a broken live system will be detected, not because it makes
the system good.

#### What this closes

The ceiling on a strategy with no ranking skill is SPY minus costs. No
architecture in the grid reaches it and no feature set moves it. Thirty-three
features across six information categories, one clearing |t| = 2 for a
non-artefact reason, eight model configurations statistically identical to one
another and all below the benchmark.

**Model work is finished until there is new information to feed it.** The
remaining project value is in step 5, which no backtest can test, and in the
200-trade paper clock, which is the only source of new evidence left.

**What would reopen model work:** point-in-time fundamentals, analyst revision
data, or short interest — all paid, all over the $30/month budget. Nothing
free and daily remains untried.

### D32 — Step 5 built. Guardrails first, happy path second.
The execution layer exists. Dry run passes end to end against PA3VR8OK3RL7.

**Files:** `src/broker.py`, `src/guardrails.py`, `scripts/daily_run.py`,
`scripts/test_guardrails.py`, `scripts/train_prod_model.py`.

**Model shipped: v1** (CLS, 14 price features), `data/model_prod.json`, trained
on all 627,844 development rows. Chosen per D31 — highest out-of-sample
accuracy, lowest CLS overfitting gap, smallest feature set. The metadata file
carries its own warning: accuracy edge +0.71pp, t = 2.33, fails Bonferroni
across the eight cells it was selected from, does not beat SPY at any
measurable position count. It is a placeholder ranker so the 200-trade clock
can start, not evidence of an edge.

**N_POSITIONS = 25**, from D31. Also a correction: N_POS was never in
`src/backtest.py` — it is a strategy parameter and lives in the backtest
scripts. Both now set to 25.

#### The ten guardrails, and the failure each one exists for

| Check | Fails when | Silent failure it prevents |
|---|---|---|
| kill_switch | a `HALT` file exists | no way to stop a running bot from a phone |
| paper_account | account not ACTIVE | trading against a dead account |
| equity | below $50k | something already went very wrong |
| drawdown | -25% from peak | compounding into a hole. Halts entries, does not liquidate |
| market_calendar | no session tomorrow | `tif=day` order expires unfilled, logged as success |
| bar_freshness | newest bar > 4 days | schedule drift or SIP lag — trading a stale ranking (D10) |
| universe_size | fewer than 80 scored | universe silently collapsed; ranking still looks valid |
| reconciliation | broker and local disagree | wrong position count drives wrong sizing |
| position_cap | held + new > 25 | unbounded accumulation |
| order_sanity | qty, price, concentration or gross out of range | a NaN price producing a colossal order |

**Design rule: a check that cannot get its data FAILS.** Missing information is
never treated as permission. The empty-calendar case is the example — no
calendar means no confirmation that a session exists, so it blocks.

**No partial execution.** All ten pass or nothing is submitted. A half-done
rebalance leaves the position count wrong and the next run sizes against it.

**Idempotency.** Every order carries a deterministic `client_order_id` of
`v1-YYYY-MM-DD-SYMBOL`. Alpaca 422s a duplicate, so a second run of the same
day is rejected by the broker rather than prevented by our own bookkeeping.
Double-firing a cron is the most likely way a scheduled job hurts you, and two
fills look exactly like one big fill in the equity curve.

**Paper-only, three independent checks:** key must start with PK, base URL must
be the paper host, account must self-report ACTIVE. The first two share a
single point of failure — an edited `.env` — which is why the third comes from
the other side of the connection.

**Dry run is the default.** `--live` is the only way past it.

**Time-stop enforced in the runner, not the broker.** Alpaca's bracket carries
a stop and a target and has no concept of "close after 10 sessions". Without
this the live strategy diverges from the backtested one a little more every
week, with nothing raising an error.

#### Testing

35 assertions in `scripts/test_guardrails.py`, all passing. Every check is
driven into its failure branch AND its passing branch — a check that always
fails is as useless as one that never does.

**One real bug found by the tests before it reached production:**
`check_bar_freshness` raised `TypeError` on tz-aware timestamps, inside the
check whose entire job is to catch a stale feed. A guardrail that crashes is a
guardrail that does not run. Fixed by normalising both sides to naive UTC.

#### Known gap, logged rather than quietly patched

The 14 Aug dry run selected **both GOOG and GOOGL**, putting ~7.6% of equity
into Alphabet under a 5%-per-name cap. DATA_AUDIT flagged that dual-class
tickers count twice in the constituent file; nothing in the pipeline collapses
them. `order_sanity` checks per-ticker concentration, not per-issuer. Same
applies to FOX/FOXA. Not fixed in this pass — it needs an issuer map, and
inventing one silently is worse than naming the gap.

Also worth recording: the model's scores across the top 25 span **0.5154 to
0.5496**. A 3.4-point spread around a 0.5 coin flip is what a model with no
ranking skill looks like from the outside, and it matches every diagnostic in
D31.

#### Schedule

Pin to UTC, never local — Sydney and New York shift DST on different dates and
the margin after the close is only ~30 minutes (D10).

```
35 20 * * 1-5  cd /path/to/Trading_Bot && python3 scripts/daily_run.py --live
```

### D33 — A ticker is not a security. 25 issuer groups, derived not assumed.
D32 logged the dual-class gap: the 14 Aug dry run bought GOOG and GOOGL,
putting 7.6% of equity into Alphabet under a 5%-per-name cap, because
`order_sanity` counts tickers and a ticker is not an issuer.

Chasing it found something worse.

#### The measurement

Every pair in the universe ranked by daily-return correlation, full history.
The result splits into two clusters above 0.95, with a clean 3.9-point gap
below them:

| corr | what it is | examples |
|---|---|---|
| 1.0000 | **ticker renames** | FB/META, RTX/UTX, ANTM/ELV, ABC/COR, CPRI/KORS, BALL/BLL, J/JEC, GL/TMK, SW/WRK, MMC/MRSH, HRS/LHX, PKI/RVTY, CCE/CCEP, DD/DWDP, CPAY/FLT |
| 0.9999–0.9673 | renames + **dual share classes** | BHGE/BKR, CTL/LUMN, EG/RE, GEN/NLOK, GOOG/GOOGL, DOC/PEAK, WLTW/WTW, FOX/FOXA, NWS/NWSA, DISCA/DISCK |
| **0.9280** | **← threshold sits here, in the gap** | |
| 0.9343 and below | genuine sector peers | AVB/EQR (apartment REITs), DHI/LEN (homebuilders), AMAT/LRCX (semi equipment) |

**The rename case is the serious one.** Alpaca serves both the old and the new
symbol for roughly a year around a change — 270-ish overlapping sessions at
correlation 1.0000. FB and META are not two companies, they are one security
appearing twice. Buying both is a double position recorded as diversification,
and nothing anywhere raises an error. This is the D12 ticker-reuse trap running
in the opposite direction: there, one ticker held two companies; here, one
company holds two tickers.

**20 rename pairs and 5 dual-class pairs. 50 tickers, 25 groups.**

#### Why a threshold and not a hand-written list

A hand-written list is a guess that looks like a fact and rots silently as the
index changes. The 0.95 cut is read off a measured 3.9-point gap with nothing
in it. Sector peers that genuinely co-move — which is real information the
model should see — sit clearly below. One security wearing two tickers sits
clearly above. The build script asserts the gap still exists on every run and
warns if anything lands on the boundary.

Groups are connected components, so a rename of a name that also has two share
classes needs no special case.

#### The fix

`scripts/build_issuer_map.py` writes `data/issuer_groups.json`.
`collapse_to_one_per_issuer` runs BEFORE the top-N cut, so dropping GOOGL
promotes the next distinct name instead of leaving a slot empty.
`check_issuer_concentration` is guardrail #11 and also blocks a new position
whose issuer is already held.

Verified on the 14 Aug dry run: `issuer filter dropped: GOOGL`, AMGN promoted,
still 25 positions, `issuer_concentration` PASS.

Guardrail tests: **35 → 43, all passing.**

#### One honest wrinkle, logged rather than hidden

The `likely` label in the JSON is imperfect and marked advisory. GOOG/GOOGL is
a dual share class and scores 0.9952; the DOC/PEAK and WLTW/WTW renames score
below it. Correlation cannot separate the two cases cleanly. No code reads the
label — grouping is what matters and both cases are handled identically — but
a wrong fact sitting in a data file is exactly how a future session gets
misled, so it is flagged in the file itself.

### D34 — Pre-live audit. 13 bugs, 6 blocking. Did NOT go live.
Papaya asked for a bug check before going live. Two independent reviewers went
over `daily_run.py`, `broker.py` and `guardrails.py` on separate dimensions —
state machine and reconciliation, order sizing and broker interaction. The code
passed 43 of its own tests and would have failed within a week of going live.

**This is the entry that justifies the whole project's design rule.** Step 5
is the half no backtest can reach, and every bug below is silent: none of them
crash, none of them raise, several of them just make the bot quietly stop
trading while reporting success.

#### The six blocking bugs

**B1 — Reconciliation deadlock. Nothing removed a symbol from local state when
a bracket exited normally.** Day 1: 25 orders, state records 25. Day 4: a
target fills at the broker, which now holds 24. Day 5: the old
`check_reconciliation` demanded exact equality, saw `only_local = {TSLA}`,
failed, and aborted the run. So did every run after it, until the whole cohort
aged out together via the time stop. **Effective duty cycle roughly 1 day in
12**, and the only symptom was a non-zero exit code under cron.

Fixed by inverting the model: **the broker is the source of truth.** Local
state is now PRUNED to the broker each run and holds only what the broker
cannot tell us — the entry date, for the time stop. The replacement check,
`check_unexpected_positions`, blocks only on the other direction: the broker
holding something this strategy has no record of ordering.

**B2 — A dry run permanently corrupted state.** `enforce_time_stop` popped
symbols from state regardless of `--live`, and both exit paths called
`save_state`. One preview run after day 10 orphaned every position forever:
nothing sold, state emptied, positions invisible to the time stop from then on,
recoverable only by hand-editing JSON. The docstring promised "submits
nothing", which was true of orders and false of state. `save_state` is now a
no-op unless `--live`, verified by diffing the file across a run.

**B3 — The time stop could not work, and hid its own failure.** A filled
bracket's stop and target legs are live sell orders for the full quantity, so
`qty_available` is zero and `DELETE /v2/positions/{symbol}` is rejected.
`PaperBroker.cancel_all_open` existed and was never called from anywhere. The
close returned `rejected`; the caller ignored `f.status` and popped the symbol
anyway. Fixed with `cancel_orders_for(symbol)` before liquidation, and state is
only pruned when the broker confirms. A failed close now keeps the entry so the
next run retries, and raises a `time_stop_completed` guardrail that blocks new
entries until it is resolved.

**B4 — A crash mid-submit lost every order already placed.** `save_state` ran
once, after all 25. `requests.post` raises on timeout and nothing caught it, so
order 12 of 25 dying left eleven live positions with no local record — and the
`client_order_id` idempotency guard then made that permanent, because the retry
got 422 and still recorded nothing. Fixed: state is written after **every**
accepted order, atomically via temp-file-and-rename, and `symbols_ever_ordered`
reads the append-only trade log rather than the state file so a crash cannot
make a real position look unexplained. `load_state` now rebuilds instead of
dying on a truncated file.

**B5 — The cron was wrong for about four and a half months a year, and D10's
reasoning was backwards.** D10 said pin the schedule to UTC because local times
drift with DST. The US session is defined in *New York* time, so a fixed UTC
time is the thing that drifts. Measured:

| date | `35 20 * * 1-5` lands at |
|---|---|
| 2026-08-14 | 16:35 EDT — 35 min after the close, correct |
| 2026-12-15 | **15:35 EST — 25 min BEFORE the close** |
| 2027-03-01 | **15:35 EST — 25 min BEFORE the close** |

For those months the newest "daily" bar is a partial, still-forming bar, and
`check_bar_freshness` passes it because it only compares dates. Market orders
submitted then fill immediately intraday rather than at the next open — a
different strategy from the one that was backtested, for a third of the year.

Fixed two ways. The schedule is now `CRON_TZ=America/New_York` with
`35 16 * * 1-5`, and `check_session_timing` asks the broker's own clock whether
the market is open and refuses to run if it is. A bad schedule is now caught
rather than traded through.

**B6 — No cash check anywhere, on a 4x margin account.** Sizing is
`equity * 0.95 / 25`, and equity includes open positions. `cash`,
`buying_power` and `regt_buying_power` were never read. The paper account
reports `multiplier: 4`, so Alpaca accepts orders on margin up to ~2x equity
overnight without an error. The backtest explicitly refuses an order it cannot
afford (`if qty < 1 or cost > cash: continue`); live had no equivalent. That is
a divergence that flatters live and raises real risk. Added `check_cash`, and
the order builder now spends against a cash budget with a 5% gap allowance.

#### The finding that stung

**Three of the eleven guardrails were structurally unreachable.** With sizing
fixed at 3.8% per name, the per-name test (`> 5.25%`) and the gross test
(`> 95%`, against a sum integer rounding caps below 95%) could never fire on
real input. The 43 tests proved the checks *worked*; they never proved the
checks could *fire*. A guardrail that cannot fire is a comment.

Worse, the gross test summed only new orders and ignored existing holdings, so
85% held plus 19% new passed at "19% < 95%" while putting the account 104%
long. `check_order_sanity` now takes `held_value` and compares the per-name cap
against the target weight rather than a number far above it.

#### Also fixed

- **The kill switch could not stop the bot from trading.** `HALT` was checked
  at step 4, after `enforce_time_stop` had already sent live liquidations. It
  now runs first, before the broker is contacted at all. Verified: `touch HALT`
  → `[FAIL] kill_switch` on line 3, nothing else executes.
- **`peak_equity` was a one-way ratchet with no reset.** An Alpaca paper reset
  to $1M followed by a reset back to $100k made `check_drawdown` read -90%
  forever. The account number is now pinned in state and the peak resets when
  it changes, or when equity exceeds 3x the recorded peak.
- **`submitted=True` was hardcoded on any 2xx.** After the close the status is
  `accepted`, not `filled`. Terminal-failure statuses are now separated out.
- **Fetch failures were swallowed** by `except Exception: return []`, letting
  the universe silently become "whatever responded". Now counted and printed.

#### Tests

**43 → 52**, all passing, with an explicit regression test for each blocking
bug — including one asserting that a bracket exiting normally is now ALLOWED,
which is the exact case the old code blocked on.

#### Still open, and NOT fixed — needs a decision

**The bracket is anchored to yesterday's close, not the fill price.** Both the
backtest engine and the training labels anchor the barriers to the entry
(`backtest.py`: `fill * (1 - stop_pct)`; `labels.py`: `entry = open.shift(-1)`).
Live anchors to `ref_price`, the prior close, because the bracket must be
submitted before the open. The effect scales with the overnight gap:

| gap | effective stop | effective target | R:R |
|---|---|---|---|
| 0% | 8.00% | 12.00% | 1 : 1.50 (design) |
| +2% | 9.80% | 9.80% | **1 : 1.00** |
| +5% | 12.38% | 6.67% | 1 : 0.54 |
| ≥ +12% | — | marketable at the open | instant round trip |
| ≤ −8% | stop above fill | — | naked or instant stop-out |

Estimated drag ≈ **1% of equity per year**, which is larger than any edge this
project has measured. Two ways out, and they are not equivalent:
(a) change the backtest and labels to anchor to the prior close, so live and
backtest agree — honest, but it changes every historical number; or
(b) submit the entry alone and attach brackets after the fill — D17 rejected
this because it leaves the position unprotected through the open, the most
volatile part of the session.

**This is a real trade-off and it is Papaya's call, not one to make silently.**

#### Verdict

**It did not go live.** The audit was the correct call and it should be run
again after any change to the execution layer.

### D35 — Entry is a LIMIT at the prior close +0.5%, not a market order.
Resolves the open question from D34. **Supersedes the market-entry half of
D17**; the `tif=day` bracket and the reason for it are unchanged.

#### The problem

The stop and target must be named as dollar prices the night before, and the
only price known then is the prior close. A market entry fills at the next
open, which can be anywhere, so the gap silently rewrites the bracket. A stock
closing at $100 gets a $92 stop and a $112 target. It opens at $102:

| gap | effective stop | effective target | R:R |
|---|---|---|---|
| 0% | 8.00% | 12.00% | 1 : 1.50 (design) |
| **+2%** | **9.80%** | **9.80%** | **1 : 1.00** |
| +5% | 12.38% | 6.67% | 1 : 0.54 |
| ≥ +12% | — | marketable at the open | instant round trip |
| ≤ −8% | stop above the fill | — | naked, or instant stop-out |

The backtest and the training labels both anchor to the fill
(`backtest.py`: `fill * (1 - stop_pct)`; `labels.py`: `entry = open.shift(-1)`),
so **every number this project has produced graded a trade the bot cannot
place.**

#### The measurement

Model v1, 25 positions, 3.5 bps/side, 2022-2024, identical universe:

| scheme | live-able | CAGR | Sharpe | trades | vs baseline |
|---|---|---|---|---|---|
| BASELINE, % of fill | **no** | 7.11% | 0.52 | 1,982 | — |
| A, anchored to prior close (market entry) | yes | 3.13% | 0.28 | 1,980 | −0.24, p=0.043 |
| C, limit at prior close | yes | 5.83% | 0.46 | 1,949 | −0.06, p=0.61 |
| **C_WIDE, limit at close +0.5%** | **yes** | **6.54%** | **0.50** | 1,978 | −0.01, p=0.78 |

SPY 0.56. Scheme B — entry alone, barriers attached after the fill — was not
simulated: its real cost is the position sitting unprotected through the open,
which is a live risk and not a historical return. Pretending to measure it
would be worse than saying so.

A is the only scheme that even approaches a significant gap from baseline, but
p=0.043 does not survive Bonferroni across the three comparisons (needs 0.017).
**The magnitude is not established. The mechanism is not in doubt** — the gap
arithmetic is arithmetic.

#### Why C_WIDE, and it is NOT the Sharpe

Choosing on Sharpe here would be the metric-shopping this project has rejected
four times, and with a ±0.6 three-year error bar these differences are not
distinguishable anyway. The reason is structural: **a limit bounds the
distortion, a market order does not.**

| | worst case |
|---|---|
| A (market) | unbounded. +12% gap round-trips instantly; −8% gap leaves it naked |
| C_WIDE (limit +0.5%) | never pay more than 0.5% over reference → **8.46% / 11.44%, a 1:1.35 ratio** |

Fills below reference make the bracket *better* than designed, never worse. The
Sharpe numbers agreeing with that is corroboration, not the argument.

C_WIDE over strict C because 0.5% of slack recovers 29 of the 33 trades C
skips at no measurable cost to the bound.

#### Verified before adopting

Alpaca accepts a bracket with a limit entry — probed against PA3VR8OK3RL7 and
cancelled immediately, the same pattern D17 used for `tif`:

```
HTTP 200  accepted: bracket limit, limit 400, legs 2, status accepted
   leg: limit 448      leg: stop 368
cancelled: 204
```

#### Consequences

- Some days will fill fewer than 25 positions. That is the mechanism working,
  not a fault. `check_position_cap` already allows fewer.
- `check_cash` is now exact rather than estimated: with a limit entry the
  maximum spend is `qty x limit_price`, so the previous 5% gap allowance is
  gone.
- `Order` in `backtest.py` gained `stop_abs`, `target_abs` and `limit_price` so
  the engine can reproduce what the live bot actually sends. The old
  percentage-of-fill path is retained but is now understood to be
  **unachievable live** and must not be used for any future headline number.
- **Every historical result in D19-D31 was computed on BASELINE.** They are not
  wrong as engine tests; they are optimistic as strategy estimates, by roughly
  0.02 Sharpe on this window. Anything re-quoted from them should say so.

### D36 — LIVE on paper. First 25 orders placed 14 Aug 2026. Clock started.
Submitted 03:51 EDT Friday 14 Aug 2026, filling at that day's 09:30 open.
All thirteen guardrails passed. **The 200-trade clock has started.**

```
account PA3VR8OK3RL7   equity $100,000.00   cash $100,000.00
open parent orders 25   legs 50   statuses {'accepted': 25}
classes {'bracket': 25}   types {'limit': 25}   tif {'day': 25}
DUPLICATE SYMBOLS: none
```

25 bracket orders, limit entry at the prior close +0.5%, each with a stop and
target leg `held` behind it. $90,319 committed, 90% of equity, 25 distinct
issuers.

#### Three defects found by running it, not by reading it

**1. The time stop depended on a file that could not survive a machine move.**
Entry dates lived only in `run_state.json`, and `enforce_time_stop` skips any
position whose date is unknown — so losing that file made every open position
permanently exempt, silently. Only visible as positions that never age out.
Fixed with `entry_dates_from_broker`, which reconstructs dates from filled buy
orders on the account. **`run_state.json` is now a cache, not a source of
truth**, and the bot can move machines without orphaning anything. A position
whose date still cannot be found is printed as a warning rather than left to
age quietly.

**2. `check_market_calendar` answered the wrong question.** It filtered for
sessions strictly *after* today, so it reported "next session Monday 17 Aug"
while the orders were in fact filling at **Friday's open five hours later**.
Right answer, wrong question — and it would have passed just as happily
through a genuine four-day holiday. Now reads the broker's own `next_open` and
blocks if it is more than four days out.

**3. A duplicate was recorded as "not submitted".** Re-running the same day
returned `duplicate` for all 25, which is the idempotency guard working — but
the runner then wrote nothing to state, leaving the file empty while 25 live
orders sat at the broker. A 422 duplicate is *proof the order exists*, so it
now counts as submitted for bookkeeping. This was the exact blind spot the
guard is meant to prevent, one level up.

Also: `prune_state_to_broker` no longer drops a symbol that has a live
unfilled order. Orders submitted after the close sit `accepted` until the next
open, so a second run the same evening was wiping entries it had just written.

#### Idempotency, confirmed against the real account

The job was run four times. **25 orders exist. Zero duplicate symbols.**
Runs 2-4 were rejected by the broker on `client_order_id`, exactly as designed.
This is the failure mode most likely to hurt a scheduled trading job — two
fills look like one big fill in the equity curve — and it is now demonstrated
rather than assumed.

Guardrail tests: **52 → 54**, all passing.

#### What happens next

- **09:30 EDT today**: limit orders fill at or below their limit. Names that
  gap above it will not fill, which is D35 working, not a fault.
- Each filled position carries a stop and target immediately. No naked window.
- The time stop closes anything still open after 10 sessions, cancelling its
  legs first (D34).
- Next scheduled run must be **`CRON_TZ=America/New_York`, `35 16 * * 1-5`**.

#### The honest framing, unchanged

This starts the 200-trade clock; it does not start an edge. Eight model
configurations were statistically identical and all sat below SPY at any
position count where the measurement is trustworthy (D31). The paper record is
worth collecting because it tests the half of the system no backtest can
reach — order handling, state, reconciliation, timing — not because the
strategy is expected to beat buy-and-hold.

**Expect it to underperform SPY.** If it does, that is the prediction, not a
surprise, and the value was the systems evidence.

### D11 — Credentials live only in `.env`
Keys are stored in `Trading_Bot/.env`, gitignored, loaded by every script.
Not duplicated into memory files, notes, or source. Paper key IDs start with
`PK`; the loader warns on `AK`.

---

## Corrected misconceptions

- **Paper trades do not train the model.** Training is offline on historical
  data. Paper trades are test results, not a feedback loop.
- **50 trades/day rejected.** Incompatible with a 100-stock universe and
  2–10 day holds, incompatible with the free data tier, and spread plus
  slippage alone would burn roughly 4% of capital per year.
- **"Any profit" is not success.** At 40–60 trades a coin flip finishes
  green about half the time.

## Success bar

Beats SPY buy-and-hold on risk-adjusted return, over 200+ trades, on data
the model has never seen. Nothing else counts as evidence.

## Realistic expectation

The most likely outcome is an excellent ML and systems engineering project
that does not produce durable alpha. That is worth building. It is not worth
misreading.
