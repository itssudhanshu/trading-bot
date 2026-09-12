---
name: review
description: Run the four-reviewer layer over a day's candidates — bull and bear blind, then rebuttals, then risk, then a recorded verdict. Use when asked to review the morning's picks, run the reviewers, or produce verdicts for the forward book.
---

# The four-reviewer layer

One session turns a day's candidates into one recorded verdict each. The verdict
is **evidence, not a decision**: `daily.py` queues the picks and
`positions.step` fills them whatever this layer says, and `stand-aside` stands
nothing aside. What accumulates is a judgement paired with what the trade then
did, which is the only shape from which this layer could ever earn authority.

If that feels pointless, read it the other way round: a layer that started
gating trades before it was measured would destroy the evidence needed to
measure it, because the trades it blocked are the counterfactual.

## The split that makes this safe

`src/ops/review.py` holds the invariants. The four prompts hold the judgement.

This is the same division as `skills/pipeline`, and it was added because the
reviewer prompts originally shipped without it: every rule lived in prompt text
— cite the channel, never count the prior, quote a trial count, don't argue the
dials — and every one was a sentence a model could be argued past.
`reviewer-4-verdict.md` even told the model its block "is parsed", when nothing
parsed it.

**If a stage is refused, fix the submission — never work around the gate.** If a
submission passes and you can still see the fault, that is a gap in `review.py`:
name the missing check rather than waving it through.

## Round 1 is blind, and the session is what makes it true

`context_for()` is the only thing that hands a reviewer its context, and at
round 1 the opposing case is **not in the dict it returns** — not redacted, not
marked, absent. Never assemble a prompt from anywhere else.

That is the whole reason this layer is worth recording. The framework this was
adapted from runs Bull → Bear → judge at its shipped default, so its bear reads
the bull's case and its bull never rebuts; the fix there is an instruction to
the judge to ignore speaking order. A verdict that moves with speaking order
cannot be scored against outcomes across candidates. Here there is no order to
ignore.

`submit()` also refuses a round-1 case that quotes the opponent verbatim, which
catches a runner that went around `context_for`.

## Running it

```bash
python3 src/ops/review.py --selftest      # before trusting any of it
python3 src/ops/review.py --status        # where every candidate stands
```

Rehearsing? Set `REVIEW_DIR` and every file moves with it:

```bash
REVIEW_DIR=/tmp/rehearse python3 src/ops/review.py --status
```

It prints a banner when it is on. Use it for anything that is not a real
trading day — the first end-to-end run of this CLI wrote a demo verdict and
five case files into the live strategy directory, which is why the flag exists.

The session:

```bash
python3 src/ops/review.py --open 2026-09-11 --symbols YUKEN,KOVAI,ARCHIDPLY
python3 src/ops/review.py --context YUKEN bull-1        # what the bull may see
python3 src/ops/review.py --submit  YUKEN bull-1 --file case.md
python3 src/ops/review.py --verdict YUKEN --file verdict.md
python3 src/ops/review.py --close
```

`--open` builds each candidate's dossier once and **copies it into the
session**, so every later stage is judged against the evidence the reviewers
actually read rather than a dossier rebuilt hours later. Work one symbol at a
time through `STAGES`: `bull-1`, `bear-1`, `bull-2`, `bear-2`, `risk`,
`verdict`. The order is enforced — a rebuttal before an opening case is refused,
and so is a second attempt at a stage already submitted.

For each stage: run `--context`, dispatch the matching subagent with **only**
what it returns, and `--submit` the reply. Give each reviewer the dossier and
the context — and **nothing about what you hope it concludes.**

Reviewers map to stages one to one:

| stage | subagent |
|---|---|
| `bull-1`, `bull-2` | `reviewer-1-bull` |
| `bear-1`, `bear-2` | `reviewer-2-bear` |
| `risk` | `reviewer-3-risk` |
| `verdict` | `reviewer-4-verdict` |

`submit_verdict()` runs the full gate: the grade is one of three, confidence is
a number in range, the claimed coverage equals the dossier's actual independent
coverage, a return carries its trial count, nothing proposes a rule change, and
the dossier fingerprint matches the one the session opened with. A malformed
block becomes `REVIEW` and is refused — never a default grade.

`close_session()` writes each candidate's cases to
`data/<strategy>/reviews/<date>/<SYMBOL>/` and appends one row per verdict to
`reviews.jsonl`. A candidate with no verdict is **skipped and named**, never
written with a default.

## What the stored cases are for, and what they are not for

They are an audit trail: when a verdict later looks wrong, the argument behind
it is on disk. They are **not** context for a future run. Prose from past
reviews re-entering a later prompt is an unmeasured channel through which a
model teaches itself, and this project has one measured example of what that
costs — a weight table where two of five variants "beat" the live bucket at
t < 0.5.

So: read them when auditing. Never paste them into a reviewer's prompt.

## When to stop

A session is done when every candidate is `complete` or named in `skipped`.
`--status` prints where each one stands. Do not close a session to tidy it up —
a skipped candidate is a fact about the day, and H12 reads the count of them.
