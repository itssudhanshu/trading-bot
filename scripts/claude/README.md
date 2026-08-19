# scripts/claude — the .claude payload

Staged here rather than in `.claude/` because the sandbox this repo is worked on
denies writes to `.claude/settings.json`, `.claude/skills/`, `.claude/hooks/`,
`.claude/agents/` and `.claude/commands/`. Same pattern as `scripts/deploy/`:
the file lives in the repo, reviewable in a diff, and one command installs it.

```bash
cp -R scripts/claude/settings.json scripts/claude/skills .claude/
```

## settings.json

Project permissions, checked in and shared. `.claude/settings.local.json` stays
gitignored for personal overrides.

The **deny** list is the part that earns its place. Each entry is a rule this
repo already holds in prose:

| denied | why |
|---|---|
| `Read(./.env)`, `Read(./data/upstox_token.json)` | a live Upstox access token; only key NAMES belong in `.env.example` |
| `Edit(strategies.jsonl)`, `Edit(trade_features.jsonl)`, `Edit(findings.jsonl)` | append-only ledgers; a mixed ledger cannot be un-mixed |
| `Edit(positions.db)` | the order record: rows are edited through `positions.py`, never deleted |
| `Bash(git push:*)` | pushing is the operator's decision, always |
| `Bash(rm:*)` | one `rm` in `data/raw/` is a permanent hole in a point-in-time record |

## skills/experiment

The protocol for measuring anything here: pre-register the hypothesis, name the
control, report the error bar, adopt nothing inside the noise. It is CLAUDE.md's
"Always be improving the strategy" and "re-checked with error bars" sections
turned into a checklist, because the failure mode is deciding what a number
means after seeing it.

## No hooks, deliberately

The two obvious candidates are already handled somewhere better:

- **Restart the listener after a source edit** — `tg.py --listen` watches every
  module in `paths.SRC` and exits when one changes; launchd's `KeepAlive`
  restarts it. A hook would be a second mechanism for a job already done. (It
  watched only `src/ops/` for one day after the `src/` move, which is what a
  stale second mechanism looks like.)
- **Guard the append-only ledgers and the token** — permissions do it natively
  and cannot be bypassed by a shell one-liner the way a `PostToolUse` hook can.
