#!/usr/bin/env python3
"""Fetch a page with a real browser. No API key, no metering, self-hosted.

Backed by Crawl4AI (github.com/unclecode/crawl4ai), which drives Playwright and
returns clean markdown alongside the HTML.

    from crawl import fetch

    page = fetch("https://example.com/story")
    if page.ok:
        print(page.markdown or page.html)

NAMED FOR THE JOB, NOT THE VENDOR
---------------------------------
This module replaced one called `nimble.py`, and the rename is the lesson.
Naming a module after a supplier means changing supplier is a rewrite that
touches every caller; naming it after what it does -- crawl a page -- makes the
next swap an edit to one file. `Page` is unchanged from that version precisely
so nothing downstream had to move.

WHY THIS BACKEND
----------------
It needs no key and meters nothing, which is why the operator chose it over a
paid API. The cost is real and worth stating: ~90 packages, including numpy,
scipy, nltk and a full LLM SDK stack, plus a Chromium download -- in a repo
whose defining property is that it loads 1.98M bars with the standard library
alone.

That cost is contained by importing Crawl4AI **lazily, inside the call**.
Nothing else in the repo pays for it, `import crawl` stays instant, and the
selftest sweep does not spend seconds loading a browser stack to check a
dataclass. `available()` reports whether it is installed, and every caller is
expected to degrade rather than fail when it is not.

ROBOTS, AND WHOSE DECISION IT IS
--------------------------------
`respect_robots` defaults True and an excluded URL is not fetched -- no browser
is launched and no request is made. Overriding is either explicit at the call
site, where it shows in a diff, or recorded once in `ALLOWED_DESPITE_ROBOTS`
with a reason. That dict ships EMPTY and the selftest asserts it stays empty.

Robots.txt is a published preference. A 403 is a server refusing this client.
They are not the same thing, and this module makes the first one binding by
default while leaving the second to the operator's judgement.

    python3 src/core/crawl.py --selftest
    python3 src/core/crawl.py https://example.com
"""
import sys as _sys, pathlib as _pl
_sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[1]))
import paths  # noqa: F401  -- puts the source dirs on sys.path
import sys
import urllib.parse
import urllib.robotparser
from dataclasses import dataclass, field

# Hosts the OPERATOR has decided to fetch even though robots.txt excludes a
# generic agent. Empty by default and deliberately so: nothing is here unless a
# person put it here, with a reason, in a commit.
#
#   "www.example.com": "why the operator decided this",
ALLOWED_DESPITE_ROBOTS: dict = {}

_robots_cache: dict = {}


@dataclass
class Fetch:
    """What to ask for. Only `url` is required.

    Deliberately smaller than the browser's full surface. Every option here is
    one this repo has an actual use for; the rest is reachable through
    `extra`, which passes straight to CrawlerRunConfig, so an unusual need does
    not require editing this file.
    """
    url: str
    timeout: int = 60                      # seconds; converted to ms for the page
    locale: str | None = None              # e.g. en-IN
    user_agent: str | None = None
    css_selector: str | None = None        # narrow the extraction to one region
    only_text: bool = False
    wait_for: str | None = None            # CSS/JS predicate to wait on
    cache: bool = False                    # default off: news must be fresh
    respect_robots: bool = True
    extra: dict = field(default_factory=dict)


@dataclass
class Page:
    """What came back. Unchanged from the previous backend, on purpose.

    `ok` is the only thing most callers branch on, and it means BOTH that the
    crawl succeeded AND that the target returned a 2xx. A 404 fetched flawlessly
    is still a 404, and a client that checked only the crawler's own success
    flag would hand back an error page as content.
    """
    url: str
    ok: bool = False
    status_code: int | None = None
    html: str = ""
    markdown: str = ""
    parsed: dict = field(default_factory=dict)
    task_id: str = ""
    duration_ms: float | None = None
    error: str = ""

    def __repr__(self):
        tail = f", error={self.error!r}" if self.error else ""
        return (f"Page(url={self.url!r}, ok={self.ok}, "
                f"status_code={self.status_code}, "
                f"html={len(self.html)}b, markdown={len(self.markdown)}b{tail})")


def available() -> bool:
    """-> True if Crawl4AI is importable. Callers should degrade, not crash."""
    try:
        import crawl4ai  # noqa: F401
        return True
    except Exception:
        return False


def robots_ok(url: str, ua: str = "*") -> bool:
    """-> True if the target's robots.txt permits `url` for a generic agent.

    An unreadable robots.txt is permission, which is the standard's documented
    default. A host that explicitly excludes is excluded.
    """
    try:
        p = urllib.parse.urlparse(url)
        root = f"{p.scheme}://{p.netloc}"
    except Exception:
        return True
    rp = _robots_cache.get(root)
    if rp is None:
        rp = urllib.robotparser.RobotFileParser()
        rp.set_url(root + "/robots.txt")
        try:
            rp.read()
        except Exception:
            _robots_cache[root] = False
            return True
        _robots_cache[root] = rp
    if rp is False:
        return True
    try:
        return rp.can_fetch(ua, url)
    except Exception:
        return True


def _host(url: str) -> str:
    try:
        return urllib.parse.urlparse(url).netloc.lower()
    except Exception:
        return ""


def permitted(f: Fetch) -> tuple:
    """-> (allowed, why). The one place the robots decision is made."""
    if not f.respect_robots:
        return True, "robots check waived at the call site"
    if robots_ok(f.url):
        return True, "robots.txt permits"
    host = _host(f.url)
    if host in ALLOWED_DESPITE_ROBOTS:
        return True, f"operator override: {ALLOWED_DESPITE_ROBOTS[host]}"
    return False, f"robots.txt on {host} excludes this URL"


def _to_page(url, res) -> Page:
    """Map a CrawlResult onto `Page`, defensively.

    Read through getattr rather than by attribute: Crawl4AI moves fast, and a
    field that disappears should degrade to empty rather than raise inside a
    scheduled job. `markdown` in particular is a property whose shape has
    changed between releases, so it is coerced to str.
    """
    md = getattr(res, "markdown", "") or ""
    if not isinstance(md, str):                     # MarkdownGenerationResult
        md = (getattr(md, "raw_markdown", "")
              or getattr(md, "fit_markdown", "") or str(md))
    status = getattr(res, "status_code", None)
    ok = bool(getattr(res, "success", False)) and (
        status is None or 200 <= int(status) < 300)
    return Page(
        url=getattr(res, "url", url) or url,
        ok=ok,
        status_code=status,
        html=getattr(res, "html", "") or "",
        markdown=md,
        parsed=_as_dict(getattr(res, "extracted_content", None)),
        task_id=str(getattr(res, "session_id", "") or ""),
        error="" if ok else (str(getattr(res, "error_message", "") or "")
                             or f"status {status}"),
    )


def _as_dict(v):
    if isinstance(v, dict):
        return v
    if isinstance(v, str) and v.strip().startswith(("{", "[")):
        import json
        try:
            d = json.loads(v)
            return d if isinstance(d, dict) else {"items": d}
        except json.JSONDecodeError:
            return {}
    return {}


def _run_config(f: Fetch):
    from crawl4ai import CacheMode, CrawlerRunConfig
    opts = dict(
        cache_mode=CacheMode.ENABLED if f.cache else CacheMode.BYPASS,
        page_timeout=f.timeout * 1000,
        only_text=f.only_text,
        verbose=False,
    )
    for k in ("locale", "user_agent", "css_selector", "wait_for"):
        v = getattr(f, k)
        if v is not None:
            opts[k] = v
    opts.update(f.extra)
    return CrawlerRunConfig(**opts)


async def _arun(fetches, log=None):
    """One browser for the whole list. Launching one per URL is the slow way."""
    from crawl4ai import AsyncWebCrawler, BrowserConfig
    out = []
    async with AsyncWebCrawler(config=BrowserConfig(headless=True,
                                                   verbose=False)) as c:
        for f in fetches:
            try:
                res = await c.arun(url=f.url, config=_run_config(f))
                # arun returns a container; the first result is the page.
                r = res[0] if hasattr(res, "__getitem__") and len(res) else res
                page = _to_page(f.url, r)
            except Exception as e:
                page = Page(url=f.url, error=f"{type(e).__name__}: {e}")
            out.append(page)
            if log:
                log(f"  {f.url[:70]} -> "
                    f"{'ok' if page.ok else page.error[:60]}")
    return out


def _drive(fetches, log=None):
    """Sync wrapper. Refuses rather than corrupts if a loop is already running."""
    import asyncio
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(_arun(fetches, log=log))
    return [Page(url=f.url, error="crawl.fetch called from inside a running "
                                  "event loop; await _arun directly")
            for f in fetches]


def fetch(url: str, **opts) -> Page:
    """Fetch one URL. -> Page, never None, carrying its own error.

    Raising on a dead page would put a try around every caller. A `Page` with
    `ok=False` composes better and cannot be ignored by accident, because `ok`
    has to be consulted to reach the content.
    """
    f = Fetch(url=url, **opts)
    allowed, why = permitted(f)
    if not allowed:
        return Page(url=url, error=f"not fetched: {why}")
    if not available():
        return Page(url=url, error="crawl4ai is not installed "
                                   "(pip install crawl4ai && crawl4ai-setup)")
    return _drive([f])[0]


def fetch_many(urls, log=None, **opts) -> list:
    """Fetch several URLs through ONE browser. -> [Page], input order.

    Anything robots excludes is refused here too, and without launching a
    browser for it.
    """
    fetches, blocked = [], {}
    for u in urls:
        f = Fetch(url=u, **opts)
        allowed, why = permitted(f)
        if allowed:
            fetches.append(f)
        else:
            blocked[u] = f"not fetched: {why}"
    if not available():
        return [Page(url=u, error=blocked.get(u, "crawl4ai is not installed"))
                for u in urls]
    # _drive returns results in request order, so pair by POSITION. Keying on
    # Page.url would lose any page that redirected, since the result carries the
    # final address and the caller asked for the original one.
    results = _drive(fetches, log=log) if fetches else []
    by_req = {f.url: p for f, p in zip(fetches, results)}
    return [by_req.get(u) or Page(url=u, error=blocked.get(u, "no result"))
            for u in urls]


def _selftest():
    # --- the types -----------------------------------------------------------
    f = Fetch(url="https://example.com")
    assert f.respect_robots is True, "robots must default to respected"
    assert f.cache is False, "news must not be served from a cache by default"
    assert f.timeout == 60

    # --- Page mapping: the crawler succeeding is not the target succeeding ---
    class R:
        url = "https://e.com/a"; success = True; status_code = 200
        html = "<p>hi</p>"; markdown = "hi"; extracted_content = None
        session_id = "s1"; error_message = ""
    p = _to_page("https://e.com/a", R())
    assert p.ok and p.html == "<p>hi</p>" and p.markdown == "hi", p

    class R404(R):
        status_code = 404
    p4 = _to_page("u", R404())
    assert not p4.ok and p4.status_code == 404, "a 404 fetched perfectly read ok"
    assert p4.error, "a failed page must carry a readable error"

    # markdown arrives as an object in some releases; it must still be a str
    class RMd(R):
        markdown = type("M", (), {"raw_markdown": "# heading"})()
    assert _to_page("u", RMd()).markdown == "# heading", "markdown not coerced"

    # a missing field degrades to empty rather than raising inside a job
    class RBare:
        success = True; status_code = 200
    assert _to_page("u", RBare()).html == "", "a missing field raised"

    # --- robots: default respected, override explicit and recorded ----------
    global _robots_cache
    _robots_cache = {"https://blocked.example":
                     type("RP", (), {"can_fetch": lambda s, ua, u: False})()}
    ok, why = permitted(Fetch(url="https://blocked.example/x"))
    assert not ok and "excludes" in why, (ok, why)
    ok2, _ = permitted(Fetch(url="https://blocked.example/x",
                             respect_robots=False))
    assert ok2, "an explicit waiver was ignored"

    old = dict(ALLOWED_DESPITE_ROBOTS)
    try:
        ALLOWED_DESPITE_ROBOTS["blocked.example"] = "a reason, in a commit"
        ok3, why3 = permitted(Fetch(url="https://blocked.example/x"))
        assert ok3 and "operator override" in why3, (ok3, why3)
    finally:
        ALLOWED_DESPITE_ROBOTS.clear()
        ALLOWED_DESPITE_ROBOTS.update(old)

    # a blocked URL must not even reach the browser
    p = fetch("https://blocked.example/x")
    assert not p.ok and "not fetched" in p.error, p
    _robots_cache = {}

    # ...and it ships empty, so nothing is exempt unless a person did it
    assert ALLOWED_DESPITE_ROBOTS == {}, \
        "a host is exempted from robots by default; that must be a decision"

    # --- the heavy import is LAZY -------------------------------------------
    # If importing this module pulled in Crawl4AI, the selftest sweep would load
    # numpy, scipy and a browser stack to check a dataclass.
    assert "crawl4ai" not in sys.modules, \
        "crawl4ai was imported at module import time; it must be lazy"

    print(f"crawl selftest ok (lazy import; robots respected by default; "
          f"crawl4ai {'installed' if available() else 'NOT installed'})")


def main(argv):
    if not argv:
        print(__doc__.strip().splitlines()[0])
        print(f"\n  crawl4ai installed: {available()}")
        print(f"  robots waivers:     {sorted(ALLOWED_DESPITE_ROBOTS) or 'none'}")
        print("\n  python3 src/core/crawl.py <url>")
        return 0
    p = fetch(argv[0])
    print(repr(p))
    if p.ok:
        print((p.markdown or p.html)[:1500])
        return 0
    print(f"failed: {p.error}")
    return 1


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    else:
        sys.exit(main([a for a in sys.argv[1:] if not a.startswith("-")]))
