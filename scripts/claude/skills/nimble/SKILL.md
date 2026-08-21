---
name: nimble
description: Use when a page cannot be fetched by ordinary means — it needs JavaScript rendering, returns 403 to a plain client, is geo-restricted, or needs structured extraction from HTML. Wraps the Nimble Web API behind two types, Fetch and Page. General-purpose; it knows nothing about stocks.
---

# Fetching a page with Nimble

`src/core/nimble.py`. A general fetcher, kept general on purpose — it will be
wanted for different things, and a client that knows what a stock is would have
to be rewritten the first time it was pointed at something else.

```python
from nimble import extract

page = extract("https://example.com/story", formats=["markdown"])
if page.ok:
    print(page.markdown)
else:
    print(page.error)          # always readable, never None
```

## The two types are the whole interface

Read these two and you know everything the client does.

**`Fetch`** — what you ask for. Only `url` is required; every other field is
left **unset** rather than defaulted, so this client never silently disagrees
with the service about what a missing field means.

| field | values | for |
|---|---|---|
| `url` | any URL | required |
| `render` | `True` / `False` / `"auto"` | pages that need JavaScript |
| `driver` | `vx6` … `vx10-pro` | heavier drivers for harder pages |
| `formats` | `html`, `markdown`, `screenshot`, `headers` | ask for markdown when a model will read it |
| `country`, `locale` | `IN`, `en-IN` | geo-restricted or localised pages |
| `parser` | CSS selector schema | structured extraction, returned in `Page.parsed` |
| `headers` | dict | custom request headers |
| `respect_robots` | default `True` | see below |

**`Page`** — what comes back. Never `None`, and it carries its own error, so a
caller cannot ignore a failure by accident: `ok` has to be consulted to reach
the content.

| field | note |
|---|---|
| `ok` | the only thing most callers branch on |
| `status_code` | **the target's** status, not Nimble's |
| `html`, `markdown`, `parsed` | content |
| `error` | readable; empty when `ok` |
| `task_id`, `duration_ms` | Nimble's own metadata |

`ok` is deliberately *both* "Nimble succeeded" **and** "the target returned 2xx".
A 404 fetched flawlessly is still a 404, and a client that only checked Nimble's
own status would hand back an error page as content.

## Before reaching for it

It is a paid API against someone else's servers. Prefer, in order:

1. **Data this repo already has.** `announcements.py` holds a million exchange
   filings; no fetcher improves on a company's own disclosure.
2. **A published feed.** `newswatch.py` reads RSS for free, and the general
   market feeds plus the per-company channel already cover a lot.
3. **Nimble**, when a page genuinely needs rendering, structured parsing, or is
   otherwise unreachable.

## Robots, and whose decision it is

`respect_robots` defaults to `True`. A URL that robots.txt excludes is **not
fetched** — `extract` returns a `Page` with `ok=False` and says why, without
making a request.

Two ways to override, both deliberate and both visible:

```python
# once, at a call site — shows up in a diff
extract(url, respect_robots=False)

# or recorded for a host, with a reason, in a commit
nimble.ALLOWED_DESPITE_ROBOTS["www.example.com"] = "why the operator decided this"
```

`ALLOWED_DESPITE_ROBOTS` ships **empty** and the selftest asserts it stays that
way, so nothing is exempted unless a person put it there. That is the operator's
call to make; this skill's job is to make it explicit and reviewable rather than
to make it quietly.

**A 403 is not the same as a robots rule.** Robots.txt is a published
preference; a 403 is a server actively refusing this client. Getting past one by
rotating IPs or spoofing a browser is evasion rather than collection. If a
source 403s, the honest routes are a licensed feed (NewsAPI and similar
aggregators carry many of them) or asking the publisher.

## Credentials

`NIMBLE_API_KEY` in `.env`. Read at call time through
`live_source.env_value`, never printed, never logged, never placed in a `Page`
or a repr — the selftest asserts that against a distinctive value.

```bash
python3 src/core/nimble.py            # shows whether a key is configured
python3 src/core/nimble.py <url>      # fetch one page
```

Without a key every call returns `ok=False` with `no NIMBLE_API_KEY configured`.
Callers should degrade rather than crash: check `nimble.available()` first and
carry on without it.

## Batches

```python
pages = extract_many(urls, pause=1.0, log=print)
```

Sequential and paced deliberately. A burst is neither cheaper nor politer, and
this is billed per request.

## Adding it to a job

Wrap it, never require it:

```python
import nimble
if nimble.available():
    page = nimble.extract(url, formats=["markdown"])
    ...
# and the job still does something useful when it is not configured
```

A scheduled job that hard-fails without an optional API key is a job that stops
running the day the key expires, in a log nobody reads.
