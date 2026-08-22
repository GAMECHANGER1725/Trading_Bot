# The dashboard on the web

**Live now: https://gamechanger1725.github.io/Trading_Bot/**

Served by GitHub Pages from the `bob-live` branch, which Bob rewrites and
force-pushes every ten minutes. Nothing to run; a push is a deploy.

GitHub Pages rather than Vercel because Vercel has no non-interactive signup —
it needs a browser login that only the account holder can do. Pages was
reachable with the `gh` credentials already on this machine, so it is deployed
rather than documented. The Vercel route below still works if you want it, and
the same branch serves both.

TradingView loads on this host, so the real widgets appear there. See the
TradingView section at the bottom for why the Claude artifact copy differs.

## Build rate

`GITHUB_PUSH_INTERVAL` is 600s. Every force-push triggers a Pages rebuild and
Pages allows roughly ten an hour, so a five-minute sync would throttle and the
site would fall behind exactly when it looked most current. Six an hour is
comfortably under, and on 1h candles a ten-minute-old page is not stale.

## Alternative: Vercel

The `bob-live` branch of this repo *is* the website. Bob rewrites `index.html`
there every 5 minutes and force-pushes, so a host watching that branch
redeploys itself with no further work.

## One-time setup

1. Sign in at **vercel.com** with the GitHub account that owns this repo
   (free Hobby plan, no card needed).
2. **Add New → Project → Import** `Trading_Bot`.
3. Framework preset: **Other**. Root directory: leave as `/`.
4. Before deploying, open **Settings → Git → Production Branch** and set it to
   **`bob-live`** — *not* `main`. `main` holds the source; `bob-live` holds the
   rendered page.
5. Deploy.

From then on every sync triggers a fresh deployment automatically.

## Why the branch, not `main`

`bob-live` is an orphan branch holding three files, updated by
`commit --amend` + `push --force`. It never grows history, so a page refresh
every five minutes does not leave five minutes' worth of commits behind on the
branch you actually read.

## TradingView

The page tries to load the real TradingView widget and falls back to charts it
draws itself from the same Binance candles.

- **On Vercel it loads**, so you get real TradingView charts.
- **On a Claude artifact it does not** — artifact pages run under a strict
  Content-Security-Policy that blocks every external host. The fallback chart
  appears instead, with a line saying so.

Same file either way; it just detects what the host permits.

## What is public

The repo is public and the deployed page is public. Nothing sensitive is in
either: the bot holds no keys, has no exchange account, and trades simulated
money against public market data.
