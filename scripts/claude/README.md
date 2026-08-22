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
| `Bash(git push:*)` | pushing is the operator's decision, always |
| `Bash(rm:*)` | one `rm` in `data/raw/` is a permanent hole in a point-in-time record |

**`positions.db` is deliberately NOT denied.** It was, for one commit. Denying a
path makes it unwritable to the whole sandbox, and `positions.db()` runs its
`CREATE TRIGGER IF NOT EXISTS` script on every connect -- so the audit died with
`attempt to write a readonly database` the moment this file was installed. The
no-delete rule the operator asked for is enforced by `pos_no_delete` and
`pos_log_no_delete` *inside* the database, which binds every connection,
including a shell one-liner that ignores permissions entirely. The permission
protected nothing and broke the one command that checks the ledger.

## skills/experiment

The protocol for measuring anything here: pre-register the hypothesis, name the
control, report the error bar, adopt nothing inside the noise. It is CLAUDE.md's
"Always be improving the strategy" and "re-checked with error bars" sections
turned into a checklist, because the failure mode is deciding what a number
means after seeing it.

## skills/sentiment

How a stock is being talked about, from this repo's own archives: what the
company told the exchange, and what the papers said. Adapted from
`sentiment-analysis` in tradeinsight-info/investment-analysis-skills -- their
rubric, bands and report shape, their data replaced.

Their three channels are NewsAPI, StockTwits and r/wallstreetbets. Those are
right for a US large cap and carry essentially nothing on an NSE microcap, and a
channel with no coverage scored as neutral reads as "no view" while meaning "no
data". So the channels here are the announcement corpus (1,019,495 rows, dated
to the second, complete) and `data/news/`.

The split that matters: `src/ops/sentiment.py` decides **what was visible** and
the skill decides **what it means**. The first half has to be reproducible and
is -- same date in, same evidence out, with the 15:30 visibility rule applied.
The second half is a model's judgement and cannot be, which is exactly why the
skill is forbidden from feeding any measured result. sentiment's `ann_tone` is the
measured version: frozen, deterministic, and currently off at t = 1.71 against a
bar of 2.6.

## skills/crawl

A general fetcher for pages ordinary means cannot reach -- JavaScript rendering,
a 403 to a plain client, geo-restriction, content buried in messy HTML.
`src/core/crawl.py` behind two dataclasses, `Fetch` and `Page`, and it knows
nothing about stocks: it will be wanted for other things, and a client that knew
what a stock was would need rewriting the first time it was pointed elsewhere.

**No API key, nothing metered** -- it drives a local headless Chromium through
Crawl4AI. It briefly used a paid API instead, and `Page` survived that swap
unchanged, which is the argument for naming a module after its job rather than
its supplier.

`Page.ok` means BOTH that the crawl succeeded AND that the target returned 2xx.
A 404 fetched flawlessly is still a 404.

`respect_robots` defaults on. Overriding is explicit at a call site, where it
appears in a diff, or recorded in `ALLOWED_DESPITE_ROBOTS` with a reason. That
dict ships empty and the selftest asserts it stays empty, so no host is exempted
unless a person put it there. The distinction it encodes: robots.txt is a
published preference, a 403 is a server refusing this client, and they are not
the same thing. Moneycontrol is the worked example -- robots permits `/rss/`,
and it 403s a urllib agent.

`pip install crawl4ai && crawl4ai-setup` is ~90 packages and a Chromium
download, in a repo that is otherwise standard-library only. Contained by
importing it LAZILY inside the call: `import crawl` stays instant and the sweep
does not load a browser stack to check a dataclass. Without it installed,
`crawl.available()` is False and `newswatch` does not even attempt the
escalate-only sources -- an optional dependency must never stop a scheduled job.

## No hooks, deliberately

The two obvious candidates are already handled somewhere better:

- **Restart the listener after a source edit** — `tg.py --listen` watches every
  module in `paths.SRC` and exits when one changes; launchd's `KeepAlive`
  restarts it. A hook would be a second mechanism for a job already done. (It
  watched only `src/ops/` for one day after the `src/` move, which is what a
  stale second mechanism looks like.)
- **Guard the append-only ledgers and the token** — permissions do it natively
  and cannot be bypassed by a shell one-liner the way a `PostToolUse` hook can.
