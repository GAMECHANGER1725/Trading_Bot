# Claude Code setup

`CLAUDE.md` at the repo root is loaded into every session automatically. It
carries the hard rules, the closed questions, and the file map.

## Commands

| | |
|---|---|
| `/verify` | tests + broker health + dry run. Before trusting anything. |
| `/audit` | analyse the live paper account, money and exposure |
| `/decision` | append a properly-formed D-number entry |
| `/preflight` | the checklist before touching order-submitting code |

## Permissions

`settings.json` pre-approves the read-only and dry-run scripts so sessions do
not stall on prompts, and **denies anything containing `--live`**.

That deny rule is deliberate. Submitting orders is a decision a human makes in
the moment, not a permission granted once in a config file. When a live run is
genuinely wanted, run it yourself in a terminal, or lift the rule for that one
turn and put it back.

Also denied: reading `.env` (D11 — keys never enter a transcript) and touching
anything matching `*holdout*`. The 2025 holdout has never been evaluated and
its single unbiased shot is the most valuable thing in the repo. Accidentally
reading it in a session is enough to spend it.

`git push` is denied because it cannot work from here anyway — see the Git
note in `CLAUDE.md`.
