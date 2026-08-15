---
description: The checklist before any change that can touch real orders.
---

You are about to change code that submits or cancels orders. Work through this
before writing anything.

1. **What breaks if this is wrong, and how would you find out?** A wrong
   trading bot does not crash. D40 ran for a full session looking perfectly
   healthy while every stop-loss was gone.
2. **What is still true tomorrow?** Not "is it accepted" — does it *persist*
   through a close, a weekend, a restart, a crash mid-loop? That question is
   the entire content of D40.
3. **Does a test read the field you are changing?** `time_in_force` was one
   hardcoded string and 54 tests never looked at it. If no test reads it, write
   one first.
4. **Is it idempotent?** Re-running the same day must not double a position.
   The `client_order_id` guard is the mechanism; verify it still holds.
5. **Does the dry run still write nothing?** Diff `data/run_state.json` before
   and after.
6. **Does `touch HALT` still stop it**, checked before the broker is contacted?

Then: `python3 scripts/test_guardrails.py`, `python3 scripts/health_check.py`,
`python3 scripts/daily_run.py`. All three, in that order.

Write the D-number entry before running anything live, not after.
