# Scheduling the daily run

**This has to be set up on your Mac. Nothing in a chat session can do it for
you** — a cloud container is discarded when the session ends, and the Cowork
device VM has no network access to reach Alpaca.

## Why launchd and not cron

Your laptop is asleep at 06:35 Sydney.

**cron silently skips any run whose time passed while the machine was
asleep.** No error, no log entry, no notification. The bot simply does not
trade that day and nothing tells you. Over a month of overnight sleeps that is
most of your trading days gone, and the first symptom would be a suspiciously
low trade count weeks later.

**launchd queues a missed `StartCalendarInterval` and fires it on wake.** The
run happens late rather than never.

## Why the schedule fires twice

The job must run after the US close. That is 16:35 New York, which is:

| period | Sydney local |
|---|---|
| US daylight saving (Mar–Nov) | 06:35 |
| US standard time (Nov–Mar) | 07:35 |

The two countries change clocks on different dates, so no single local time is
correct all year. Encoding one would leave the bot running *during* the US
session for about four months — filling market orders intraday against a
partial, still-forming daily bar. That is the D34 bug that nearly shipped.

So the plist fires at both times, and the guardrails sort it out:

- `check_session_timing` asks the broker whether the market is open and
  **blocks the run if it is**. The wrong-side firing does nothing.
- The `client_order_id` idempotency guard means the second firing of the day
  is **rejected by Alpaca**, verified against the real account: four runs,
  25 orders, zero duplicates.

Two firings, one trading run, and a bad schedule cannot trade through.

## Install

```bash
cd ~/Downloads/Home/Trading_Bot

# 1. Edit the three paths inside the plist to match your machine
open -e deploy/com.trading-bot.daily.plist

# 2. Install
cp deploy/com.trading-bot.daily.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.trading-bot.daily.plist

# 3. Confirm it registered
launchctl list | grep trading-bot
```

## Verify before trusting it

```bash
# force one run now — it will block if the market is open, which is correct
launchctl kickstart -k gui/$(id -u)/com.trading-bot.daily

# then read what happened
tail -40 data/launchd.out.log
tail -20 data/launchd.err.log
```

Expect either a full guardrail report ending `ALL CHECKS PASSED`, or
`[FAIL] session_timing  US market is OPEN`. Both are correct outcomes.

## Check it is actually running, weekly

A scheduled job that stops firing looks exactly like a scheduled job with
nothing to do. Once a week:

```bash
# how many runs has it recorded?
python3 -c "import json;print(json.load(open('data/run_state.json'))['runs'])"

# when did it last do anything?
tail -1 data/trade_log.jsonl
```

If the run count has not moved in a week, launchd is not firing. Do not assume
a quiet log means a quiet market.

## Stopping it

```bash
touch HALT                    # immediate. First guardrail checked, before
                              # the broker is even contacted
launchctl unload ~/Library/LaunchAgents/com.trading-bot.daily.plist
```

`HALT` is deliberately the crudest possible mechanism: when something is wrong
at 3am and you are on a phone, `touch HALT` over SSH has to work without the
bot being healthy enough to parse a config.
