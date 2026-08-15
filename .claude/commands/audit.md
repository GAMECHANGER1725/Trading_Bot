---
description: Analyse the live paper account — money, fills, and what is unprotected.
---

Audit the Alpaca paper account. Read-only. Submit nothing.

Pull from the broker (do not trust `run_state.json` — the broker is the source
of truth, D34):

- account: equity, cash, long market value, P&L vs $100,000 start
- every open position: qty, avg entry, last, cost basis, market value, P&L
- full order history: fills, expiries, cancels, rejects — and **the bracket
  legs**, via `nested=True`. A parent that filled tells you nothing about
  whether its stop still exists.
- open orders now: is every position covered by a live protective sell?

Then report:

1. **Money** — equity, cash, deployed, P&L.
2. **Execution quality** — fill rate, fills vs limit prices, rejects,
   duplicates, unfilled entries and why.
3. **Anything unprotected** — positions with no live stop, and the dollar
   exposure down to where the stop should be. This is the D40 class of failure
   and it outranks the P&L.
4. **State drift** — symbols in `run_state.json` that the broker does not have,
   and vice versa.

Lead with whatever is broken, not with the P&L. A flat P&L on an unprotected
book is not a good day.
