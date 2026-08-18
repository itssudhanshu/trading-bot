# rules.md — how this project names and explains things

Standing rules. These are not style preferences; each one exists because
breaking it already cost something here.

---

## R1 — Never invent a word for something that already has one

Before introducing a term, check whether the project already names that thing.
If it does, use that name. If two names exist for one thing, one of them is
wrong and must be deleted, not tolerated.

**How this was broken:** `book` was added for a paper portfolio while `bucket`
and `portfolio` already existed for overlapping ideas. That produced `BOOKS`,
`/books`, a `book` column and the phrase "the record book" — three words for
one concept, none of which a newcomer could rank against the others.

**The related failure**, one layer down: a name that collides with an existing
word in a *different* sense is the same bug. `rank2` as a portfolio name
printed beside a stock at rank 5 put two meanings of "rank" on one line saying
different numbers.

## R2 — A non-trader must be able to read any output without a glossary

Every user-facing surface — Telegram replies, `overview.py`, logs a person
reads — must be understandable to someone who does not trade. This is the test:
could a friend who has never bought a share tell what happened?

**Plain word first, precise word only if it earns its place.** Prefer:

| instead of | say |
|---|---|
| book / bucket | portfolio, or "today's picks" |
| cluster | size group |
| micro / small | smallest companies / small companies |
| liquidity, illiquid | how easily it trades / thinly traded |
| 200-DMA | its 200-day average price |
| breakout | price broke above its recent high |
| delivery % | shares actually taken home, not day-traded |
| relative strength | 6-month price gain vs other stocks |
| realised / unrealised | banked / on paper |
| drawdown | worst drop from a high point |
| occupancy | how many stocks are held |
| per-trade edge | average gain per trade |
| worst block | worst six months |
| std err, t-statistic, CI | margin of error |
| market impact | the cost of your own buying moving the price |

## R3 — Fix the words, do not bolt on an explanation

If a result needs a paragraph to be understood, the labels are wrong. Rewrite
the labels. An explanation appended to jargon leaves the jargon in place and
makes the message longer, which is two problems.

A short parenthetical that adds a *fact* is fine ("stop 1,918 — sells if it
falls 10%"). A parenthetical that translates your own word choice is a sign the
word choice was wrong.

## R4 — The same rule governs what Claude writes

Explanations in chat follow R2 and R3. State the finding in plain words first.
Precise terms are allowed once the plain meaning is established, and only where
precision changes the meaning.

Numbers keep their error bars — plain language is not permission to drop the
uncertainty. "Average gain per trade +2.96%, give or take 1.9%" is plain AND
honest. "+2.96%" alone is plain and misleading.

## R5 — Precision inside the code, plain language at the edges

Internal identifiers may keep exact domain terms where the precision is
load-bearing and defined in `CLAUDE.md` — `cluster`, `bucket`, `cohort`. What
must never happen is an internal term leaking to a user-facing surface
untranslated.

When a precise internal name does appear to a user, it must carry its meaning
in the same breath, and that meaning must be **derived, not restated**. A
hand-written gloss goes stale: "ranks 4-6 micro" written as a string outlived
the mix that made it true within a day.

---

See `CLAUDE.md` for the vocabulary the code itself must use, and `lessons.md`
for the evidence behind the trading rules.
