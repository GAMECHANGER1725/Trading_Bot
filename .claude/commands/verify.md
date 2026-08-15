---
description: Full pre-flight — tests, health check, dry run. Run before trusting anything.
---

Verify the bot end to end. Report only what is wrong; do not narrate passes.

1. `python3 scripts/test_guardrails.py` — expect **58 passed, 0 failed**. Any
   failure stops everything else.
2. `python3 scripts/health_check.py` — asserts on what is still true at the
   broker, not on what was accepted (D40). Exit 1 means the account is not in
   the state the backtest assumes.
3. `python3 scripts/daily_run.py` — dry run. It must submit nothing and write
   nothing. Confirm `data/run_state.json` is byte-identical before and after.

Then state, in three lines: tests, broker health, dry run. If anything failed,
say what broke and what it costs — not just that it is red.

Do **not** pass `--live` to anything. That is a separate, explicit request.
