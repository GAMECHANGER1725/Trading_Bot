---
description: Append a properly-formed decision entry to DECISIONS.md.
argument-hint: [what was decided]
---

Write the next D-number entry in `DECISIONS.md` for: **$ARGUMENTS**

Find the highest existing `### D<n>` and use n+1. Insert immediately before
`### D11 — Credentials live only in .env`, matching the surrounding style.

The entry must contain:

- **What was believed** before, and by whom, and on what evidence.
- **What was measured.** Numbers with error bars. A claim without a number is
  an opinion; an opinion does not get a D-number.
- **What changed**, stated as an instruction to a future reader.
- **Why the existing tests or checks did not catch it**, if this is a bug. That
  sentence is worth more than the fix — it is the only part that generalises.
- **Corrections to earlier decisions**, named explicitly. D38 corrects D37
  twice. That is the pattern, not an embarrassment.

Do not soften an earlier entry to make the project look better.
`model_prod_meta.json` calling its own model a placeholder that fails
Bonferroni is the standard.

Then update `STATUS.md` and the trap list in `README.md` if the decision
changes what a newcomer needs to know.
