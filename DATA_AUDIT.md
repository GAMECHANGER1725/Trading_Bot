# Universe data audit — 13 Aug 2026

Source: [fja05680/sp500](https://github.com/fja05680/sp500), file
`S&P 500 Historical Components & Changes(01-17-2026).csv`.
Cross-checked against Alpaca bar availability for every ticker.

## Headline

The dataset is good. 729 unique tickers appear in the S&P 500 between
2016-01-04 and 2026-01-14 across 474 change-dates. Membership sits at
503–507 tickers per date throughout, with no date outside that band.
(Above 500 because companies with two share classes — GOOG/GOOGL,
FOX/FOXA — count twice.)

Four independent reality checks passed:

| Ticker | What happened | Last seen in list |
|---|---|---|
| TWTR | Twitter taken private | 2022-10-12 |
| SIVB | SVB collapsed | 2023-01-04 |
| FRC | First Republic failed | 2023-03-20 |
| ATVI | Activision acquired | 2023-10-02 |

Alpaca serves usable bars for **710 of 729** on the first pass, 712 after
retrying two that hit the rate limit. The remaining 17 are all corporate
actions, not missing data, and they are the interesting part.

## Trap 1 — a ticker is not a company

Four tickers were reused by an entirely different company after the original
left the index. In each case Alpaca returns plenty of bars — for the wrong
business.

| Ticker | In the index as | Alpaca bars cover | Actually |
|---|---|---|---|
| STI | SunTrust, 2016→2019 (merged into Truist) | 2022-05 → 2026-08 | an unrelated company |
| TE | TECO Energy, Jan–Jun 2016 (acquired) | 2020-01 → 2026-08 | an unrelated company |
| WYND | Wyndham Worldwide, 2016→2018 | 2018-06 → 2021-02 | the post-split successor |
| AABA | Altaba (ex-Yahoo), 2016→2017 | 2017-06 → 2019-10 | the liquidating trust |

Fetch STI for a 2018 backtest and you get nothing, which is fine. Fetch STI
for the whole 2016–2026 range and you get 1,072 bars belonging to a company
SunTrust has no relationship with, silently stitched into your universe as if
it were a large-cap bank.

**Rule: for every ticker, discard any bar dated outside the window in which
that ticker was actually an index member.** Non-negotiable, and it must be
enforced in the loader rather than remembered.

## Trap 2 — membership is not continuous

Eight tickers left the index and later came back. Treating membership as a
single span from first-seen to last-seen quietly includes years when the
company was not a member.

```
DD     2016-01-04→2017-08-31  |  2019-06-03→2026-01-14
DOW    2016-01-04→2017-08-31  |  2019-04-02→2026-01-14
EQT    2016-01-04→2018-11-12  |  2022-10-03→2026-01-14
FISV   2016-01-04→2023-05-16  |  2025-11-11→2026-01-14
FSLR   2016-01-04→2017-03-17  |  2022-12-19→2026-01-14
KDP    2016-01-04→2018-06-26  |  2022-06-21→2026-01-14
PCG    2016-01-04→2019-01-11  |  2022-10-03→2026-01-14
SNDK   2016-01-04→2016-05-11  |  2025-11-28→2026-01-14
```

I made this exact mistake mid-audit — my first pass compared first and last
membership dates and reported SNDK as fine. It is the worst case in the file:
old SanDisk was bought by Western Digital in May 2016, and the SNDK trading
today is a Western Digital spin-off from 2025. Two unrelated companies, one
ticker, nine years apart. Alpaca returns **zero bars** for the 2016 interval,
which is the correct answer and also the only reason the error surfaced.

DD and DOW deserve a second look before they're trusted: the pre-2017 bars
are old DuPont and old Dow Chemical, which merged into DowDuPont and then
re-split into different corporate entities in 2019. Alpaca serves both eras
under the same ticker. Whether that's a legitimate continuation or two
companies glued together is a judgement call, and it should be made
deliberately rather than by default.

## Trap 3 — history that starts late

Thirteen tickers have bars that begin after their membership does. These are
renames and spin-offs, not gaps: APTV was Delphi until Dec 2017, IR was
reassigned to Gardner Denver in 2020, BHGE and ANDV and DWDP are all merger
artifacts. The data is real; it simply lives under the predecessor ticker
before the change date.

For a first pass, dropping a name for the months before its bars start is
acceptable — it costs a handful of stock-days out of roughly 1.8 million.
Chasing predecessor tickers is a refinement, not a prerequisite.

## Known limitation — the file ends 2026-01-14

Today is 2026-08-13, so the last seven months of membership are unknown. The
repo keeps a separate `sp500_changes_since_2019.csv` that is updated every
few months and lags too.

For backtesting this is irrelevant: the model trains and validates on data
ending well before the gap. It matters only for live trading, where the
universe would be frozen at its January composition. Acceptable for paper
trading, and worth revisiting before any decision about real money.

## Files written

- `data/sp500_history.csv` — the dated components file
- `data/sp500_changes_since_2019.csv` — incremental changes
- `data/universe_tickers.json` — 729 unique tickers
- `data/ticker_conflicts.json` — the four reuse cases
- `data/membership_intervals.json` — per-ticker membership intervals

## One more thing worth knowing

The obvious filename in that repo, `S&P 500 Historical Components &
Changes.csv`, stops at 2019-01-11. The current data is in a file with a date
stamped into its name. I pulled the obvious one first and it produced a
perfectly plausible audit covering three years instead of ten — correct
numbers, correct-looking event checks, silently missing 70% of the window.

Nothing about the output said "this is stale." The only reason it surfaced
was checking the file's own end date against what it should have been.
Whenever a dataset is loaded, assert its coverage matches expectations before
using it.
