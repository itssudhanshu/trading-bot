---
name: crawl
description: Use when a page cannot be fetched by ordinary means — it needs JavaScript rendering, returns 403 to a plain client, is geo-restricted, or needs content pulled out of messy HTML as markdown. Drives a local headless browser via Crawl4AI. No API key. General-purpose; it knows nothing about stocks.
---

# Fetching a page with a browser

`src/core/crawl.py`. A general fetcher, kept general on purpose — it will be
wanted for different things, and a client that knew what a stock was would need
rewriting the first time it was pointed somewhere else.

```python
from crawl import fetch

page = fetch("https://example.com/story")
if page.ok:
    print(page.markdown or page.html)
```

**No API key and nothing metered.** It runs Chromium locally through Crawl4AI,
so the only limits are your own patience and the politeness you owe the target.

## Named for the job, not the vendor

This replaced a module called `nimble.py`, and that is the lesson worth keeping.
Naming a module after a supplier makes changing supplier a rewrite touching
every caller. `Page` came through the swap unchanged, so nothing downstream
moved — which is what "reusable types" is actually for.

## The two types are the whole interface

**`Fetch`** — what you ask for. Only `url` is required.

| field | for |
|---|---|
| `url` | required |
| `timeout` | seconds; converted to a page timeout |
| `locale` | `en-IN` and similar |
| `user_agent` | override the browser's own |
| `css_selector` | narrow extraction to one region of the page |
| `only_text` | drop markup entirely |
| `wait_for` | CSS or JS predicate to wait on before reading |
| `cache` | **off by default** — news must be fresh |
| `respect_robots` | see below |
| `extra` | passed straight to `CrawlerRunConfig` for anything not listed |

**`Page`** — what comes back. Never `None`, always carries its own error.

| field | note |
|---|---|
| `ok` | the only thing most callers branch on |
| `status_code` | **the target's** status |
| `html`, `markdown`, `parsed` | content; markdown is clean enough to hand a model |
| `error` | readable; empty when `ok` |

`ok` means **both** that the crawl succeeded **and** that the target returned
2xx. A 404 fetched flawlessly is still a 404, and a client checking only the
crawler's own success flag would hand back an error page as content.

## Before reaching for it

It launches a browser. That is slow and it is load on someone else's server.
Prefer, in order:

1. **Data this repo already has.** `announcements.py` holds a million exchange
   filings; no fetcher improves on a company's own disclosure.
2. **A published feed.** `newswatch.py` reads RSS with the standard library.
3. **This**, when a page genuinely needs rendering, or refuses a plain client.

## Robots, and whose decision it is

`respect_robots` defaults `True`. An excluded URL is **not fetched** — no
browser is launched, and `fetch` returns a `Page` with `ok=False` saying why.

Two ways to override, both deliberate and both visible:

```python
fetch(url, respect_robots=False)                    # explicit, shows in a diff

crawl.ALLOWED_DESPITE_ROBOTS["www.example.com"] = "why the operator decided"
```

`ALLOWED_DESPITE_ROBOTS` ships **empty** and the selftest asserts it stays that
way, so nothing is exempt unless a person put it there.

**A 403 is not a robots rule.** Robots.txt is a published preference; a 403 is a
server refusing this particular client. Moneycontrol is the worked example — its
robots.txt permits `/rss/`, and it returns 403 to a `urllib` user-agent. Fetching
it with a real browser asks the same question a reader's browser asks. A site
that actually disallows in robots.txt is a different matter and stays blocked.

## Installing

```bash
pip install crawl4ai && crawl4ai-setup
```

That is ~90 packages plus a Chromium download, in a repo that is otherwise
standard-library only. The cost is contained by importing Crawl4AI **lazily,
inside the call**: `import crawl` stays instant and the selftest sweep does not
load a browser stack to check a dataclass.

`crawl.available()` reports whether it is installed. Callers must degrade rather
than fail:

```python
import crawl
if crawl.available():
    page = crawl.fetch(url)
# and the job still does something useful when it is not
```

A scheduled job that hard-fails on a missing optional dependency is a job that
stops the day someone reinstalls Python, in a log nobody reads.

## Batches

```python
pages = fetch_many(urls, log=print)
```

One browser for the whole list, results in request order. Launching a browser
per URL is the slow way and there is no reason for it.
