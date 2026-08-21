#!/usr/bin/env python3
"""Nimble Web API client. A general fetcher, not a news scraper.

Built as a reusable piece because it will be wanted repeatedly and for
different things -- a filing PDF behind a renderer, an exchange page that needs
JavaScript, a one-off page this repo has no other route to. So the surface here
is deliberately generic: give it a URL, get a `Page` back. Nothing in it knows
what a stock is.

    from nimble import extract
    page = extract("https://example.com/story", render=True)
    if page.ok:
        print(page.markdown or page.html)

THE TYPES ARE THE POINT
-----------------------
`Fetch` is what you ask for and `Page` is what comes back, and both are plain
dataclasses. A caller reads the two definitions and knows the whole interface,
which is the difference between a reusable component and a function somebody
else's code has to reverse-engineer from its call sites.

CREDENTIALS
-----------
`NIMBLE_API_KEY` from the environment or `.env`, through `live_source.env_value`
-- the reader this repo already has, tolerant of `export `, quotes and the
empty-value case that once presented as "the key is there". The key is never
printed, never logged, never placed in a `Page`, and never included in a repr.
`_selftest` asserts that on a deliberately distinctive value.

ROBOTS, AND THE DECISION THAT IS NOT MINE
-----------------------------------------
`respect_robots` defaults to True and a call that overrides it has to say so at
the call site, where it shows up in a diff. Hosts the operator has decided to
fetch anyway are listed in `ALLOWED_DESPITE_ROBOTS` with the reason, so the
decision lives in one reviewable place instead of being scattered through
call sites -- and so that removing it is a one-line change.

That list is the operator's to set. This file makes the choice explicit and
recorded; it does not make it.

    python3 src/core/nimble.py --selftest
    python3 src/core/nimble.py https://example.com     # needs a key
"""
import sys as _sys, pathlib as _pl
_sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[1]))
import paths  # noqa: F401  -- puts the source dirs on sys.path
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
from dataclasses import dataclass, field

# Documented endpoint. Older material shows api.webit.live/api/v1/realtime/web;
# accounts differ, so it is overridable rather than hardcoded and wrong for
# somebody.
ENDPOINT_DEFAULT = "https://sdk.nimbleway.com/v2/extract"

KEY_NAME = "NIMBLE_API_KEY"
ENDPOINT_NAME = "NIMBLE_ENDPOINT"

# Hosts the OPERATOR has decided to fetch even though robots.txt excludes a
# generic agent. Empty by default and deliberately so: nothing is here unless a
# person put it here, with a reason, in a commit.
#
#   "www.moneycontrol.com": "operator holds a Nimble account; ...",
ALLOWED_DESPITE_ROBOTS: dict = {}

_robots_cache: dict = {}


class NimbleError(RuntimeError):
    """A request could not be made or came back unusable."""


@dataclass
class Fetch:
    """What to ask for. Mirrors the Web API's documented request body.

    Only `url` is required. Everything else is left unset rather than defaulted
    to Nimble's own defaults, so this client never silently disagrees with the
    service about what a missing field means.
    """
    url: str
    render: bool | str | None = None       # True | False | "auto"
    driver: str | None = None              # vx6 | vx8 | vx8-pro | vx10 | vx10-pro
    formats: list | None = None            # html | markdown | screenshot | headers
    country: str | None = None             # ISO alpha-2
    locale: str | None = None              # e.g. en-IN
    parser: dict | None = None             # CSS selector schema
    headers: dict | None = None
    timeout: int = 60
    respect_robots: bool = True

    def body(self) -> dict:
        """-> the JSON body, omitting anything unset."""
        out = {"url": self.url}
        for k in ("render", "driver", "formats", "country", "locale",
                  "parser", "headers"):
            v = getattr(self, k)
            if v is not None:
                out[k] = v
        return out


@dataclass
class Page:
    """What came back. `ok` is the only thing most callers need to branch on."""
    url: str
    ok: bool = False
    status_code: int | None = None         # the TARGET's status, not Nimble's
    html: str = ""
    markdown: str = ""
    parsed: dict = field(default_factory=dict)
    task_id: str = ""
    duration_ms: float | None = None
    error: str = ""

    def __repr__(self):                    # never carries a key; see _selftest
        tail = f", error={self.error!r}" if self.error else ""
        return (f"Page(url={self.url!r}, ok={self.ok}, "
                f"status_code={self.status_code}, "
                f"html={len(self.html)}b, markdown={len(self.markdown)}b{tail})")


def _key() -> str:
    import live_source
    return live_source.env_value(KEY_NAME)


def _endpoint() -> str:
    import live_source
    return live_source.env_value(ENDPOINT_NAME) or ENDPOINT_DEFAULT


def available() -> bool:
    """-> True if a key is configured. Callers should degrade, not crash."""
    return bool(_key())


def robots_ok(url: str, ua: str = "*") -> bool:
    """-> True if the target's robots.txt permits `url` for a generic agent.

    An unreadable robots.txt is treated as permission, which is the standard's
    documented default. A host that explicitly excludes is excluded.
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
            _robots_cache[root] = False    # unreadable -> do not re-fetch
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


def extract(url: str, transport=None, **opts) -> Page:
    """Fetch one URL. -> Page, which is never None and carries its own error.

    Raising on a dead page would make every caller wrap this in a try; a `Page`
    with `ok=False` and a readable `error` composes better and is impossible to
    ignore silently, because `ok` has to be consulted to get at the content.
    """
    f = Fetch(url=url, **opts)
    allowed, why = permitted(f)
    if not allowed:
        return Page(url=url, error=f"not fetched: {why}")

    key = _key()
    if not key:
        return Page(url=url, error=f"no {KEY_NAME} configured")

    req = urllib.request.Request(
        _endpoint(),
        data=json.dumps(f.body()).encode(),
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {key}"},
        method="POST")
    try:
        raw = (transport or _send)(req, f.timeout)
    except urllib.error.HTTPError as e:
        # The body may explain the failure; the request headers must never be
        # echoed, since they carry the key.
        detail = ""
        try:
            detail = e.read().decode("utf-8", "replace")[:200]
        except Exception:
            pass
        return Page(url=url, error=f"HTTP {e.code} from Nimble: {detail}")
    except Exception as e:
        return Page(url=url, error=f"{type(e).__name__}: {e}")

    try:
        d = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return Page(url=url, error="Nimble returned a body that is not JSON")

    data = d.get("data") or {}
    meta = d.get("metadata") or {}
    status = d.get("status_code")
    return Page(
        url=d.get("url") or url,
        # Nimble succeeding is not the target succeeding. A 404 fetched
        # perfectly is still a 404, and a caller that only checked Nimble's own
        # status would treat an error page as content.
        ok=(d.get("status") == "success"
            and (status is None or 200 <= int(status) < 300)),
        status_code=status,
        html=data.get("html") or "",
        markdown=data.get("markdown") or "",
        parsed=data.get("parsing") or {},
        task_id=d.get("task_id") or "",
        duration_ms=meta.get("query_duration"),
        error="" if d.get("status") == "success" else str(d.get("status") or "failed"),
    )


def _send(req, timeout):
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def extract_many(urls, pause=1.0, log=None, transport=None, **opts) -> list:
    """Fetch several URLs in order. -> [Page], one per input, same order.

    Sequential and paced on purpose. This is a paid API pointed at somebody
    else's servers, and a burst is neither cheaper nor politer.
    """
    import time
    out = []
    for i, u in enumerate(urls):
        p = extract(u, transport=transport, **opts)
        out.append(p)
        if log:
            log(f"  {u[:70]} -> {'ok' if p.ok else p.error[:60]}")
        if pause and i < len(urls) - 1:
            time.sleep(pause)
    return out


def _selftest():
    # --- the types describe themselves ------------------------------------
    f = Fetch(url="https://example.com")
    assert f.body() == {"url": "https://example.com"}, f.body()
    assert f.respect_robots is True, "robots must default to respected"
    f2 = Fetch(url="https://e.com", render=True, country="IN",
               formats=["markdown"])
    assert f2.body() == {"url": "https://e.com", "render": True,
                         "country": "IN", "formats": ["markdown"]}, f2.body()
    # An unset option must be ABSENT, not None: sending {"render": null} lets
    # the service apply a default we did not choose and cannot see.
    assert "driver" not in f2.body() and "parser" not in f2.body()

    # --- a missing key degrades, it does not crash -------------------------
    import live_source
    real_env = live_source.env_value
    try:
        live_source.env_value = lambda n: ""
        p = extract("https://example.com", respect_robots=False)
        assert not p.ok and KEY_NAME in p.error, p
    finally:
        live_source.env_value = real_env

    # --- THE key never leaks ------------------------------------------------
    SECRET = "nimble-key-do-not-print-8f3a9c"
    seen = {}

    def fake(req, timeout):
        seen["auth"] = req.get_header("Authorization")
        return json.dumps({
            "url": "https://e.com/a", "task_id": "t1", "status": "success",
            "status_code": 200,
            "data": {"html": "<p>hi</p>", "markdown": "hi"},
            "metadata": {"query_duration": 12.5},
        }).encode()

    try:
        live_source.env_value = lambda n: SECRET if n == KEY_NAME else ""
        pg = extract("https://e.com/a", transport=fake, respect_robots=False)
    finally:
        live_source.env_value = real_env

    assert seen["auth"] == f"Bearer {SECRET}", "the key never reached the request"
    assert pg.ok and pg.html == "<p>hi</p>" and pg.markdown == "hi", pg
    assert pg.status_code == 200 and pg.duration_ms == 12.5
    blob = repr(pg) + str(pg) + json.dumps(pg.parsed) + pg.html + pg.error
    assert SECRET not in blob, "the API key leaked into the Page"

    # --- Nimble succeeding is not the target succeeding --------------------
    def fake404(req, timeout):
        return json.dumps({"url": "u", "status": "success", "status_code": 404,
                           "data": {"html": "<h1>Not Found</h1>"}}).encode()
    try:
        live_source.env_value = lambda n: SECRET if n == KEY_NAME else ""
        p404 = extract("https://e.com/missing", transport=fake404,
                       respect_robots=False)
    finally:
        live_source.env_value = real_env
    assert not p404.ok, "a 404 fetched perfectly was reported as ok"
    assert p404.status_code == 404

    # --- robots: the default is respected, the override is explicit --------
    global _robots_cache, ALLOWED_DESPITE_ROBOTS
    _robots_cache = {"https://blocked.example":
                     type("RP", (), {"can_fetch": lambda s, ua, u: False})()}
    blocked = Fetch(url="https://blocked.example/x")
    ok, why = permitted(blocked)
    assert not ok and "excludes" in why, (ok, why)

    # waived at the call site
    ok2, why2 = permitted(Fetch(url="https://blocked.example/x",
                                respect_robots=False))
    assert ok2 and "waived" in why2, (ok2, why2)

    # ...or recorded once, in the operator's list, with a reason
    old = dict(ALLOWED_DESPITE_ROBOTS)
    try:
        ALLOWED_DESPITE_ROBOTS["blocked.example"] = "a reason, in a commit"
        ok3, why3 = permitted(blocked)
        assert ok3 and "operator override" in why3, (ok3, why3)
    finally:
        ALLOWED_DESPITE_ROBOTS.clear()
        ALLOWED_DESPITE_ROBOTS.update(old)
    _robots_cache = {}

    # and it is EMPTY as shipped -- nothing is waived unless a person did it
    assert ALLOWED_DESPITE_ROBOTS == {}, \
        "a host is exempted from robots by default; that must be a decision"

    # --- a blocked URL is never fetched ------------------------------------
    _robots_cache = {"https://blocked.example":
                     type("RP", (), {"can_fetch": lambda s, ua, u: False})()}
    called = []
    p = extract("https://blocked.example/x",
                transport=lambda r, t: called.append(1) or b"{}")
    assert not called, "a robots-excluded URL was fetched anyway"
    assert not p.ok and "not fetched" in p.error, p
    _robots_cache = {}

    print("nimble selftest ok (key never leaks; robots respected by default)")


def main(argv):
    if not argv:
        print(__doc__.strip().splitlines()[0])
        print(f"\n  key configured: {available()}")
        print(f"  endpoint:       {_endpoint()}")
        print(f"  robots waivers: {sorted(ALLOWED_DESPITE_ROBOTS) or 'none'}")
        print("\n  python3 src/core/nimble.py <url>")
        return 0
    p = extract(argv[0], formats=["markdown"])
    print(repr(p))
    if p.ok:
        print((p.markdown or p.html)[:2000])
        return 0
    print(f"failed: {p.error}")
    return 1


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    else:
        sys.exit(main([a for a in sys.argv[1:] if not a.startswith("-")]))
