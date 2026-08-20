#!/usr/bin/env python3
"""Forward news capture. Appends what was published today; reads nothing back.

WHY THIS EXISTS AT ALL, GIVEN announcements.py
----------------------------------------------
The announcements feed says a company FILED something. It does not say what the
market made of it. Those are different signals, and only one of them has a
seven-year archive.

News does not. Nobody sells a complete, correctly-timestamped archive of Indian
microcap press coverage back to 2019, and scraping one into existence is not
possible: what you would get is whatever survived to today, dated by when you
fetched it. That backtests beautifully and means nothing.

So this file is the one part of the project that can only ever pay off later.
It starts capturing now because the archive accumulates forward and a day not
captured is a day that cannot be recovered. Its first use is forward paper
trades, where the count is currently zero and where CLAUDE.md puts the highest
expected value in the whole project.

WHAT IT MUST NEVER DO
---------------------
Be read by a backtest. There is no history here, so any backtest that touched
it would be reading data from after the trade it was scoring. The selftest
asserts that no module under research/ or strategies/ imports this one -- the
guarantee lives next to the thing it guards, which is this project's
convention, rather than in a document nobody re-reads.

BEING A POLITE CLIENT
---------------------
This is a long-lived daily job pointed at somebody else's server. It reads
published RSS/Atom feeds only, checks robots.txt before each host, identifies
itself honestly in its User-Agent, and makes one request per source per run.
A feed that fails is logged and skipped; it never retries in a loop.

    python3 src/ops/newswatch.py            # capture today
    python3 src/ops/newswatch.py --selftest
"""
import sys as _sys, pathlib as _pl
_sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[1]))
import paths  # noqa: F401  -- puts the source dirs on sys.path
import json
import re
import sys
import urllib.parse
import urllib.robotparser
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

from paths import ROOT      # one definition; see paths.py

NEWS = ROOT / "data" / "news"

UA = "trading-bot-newswatch/1.0 (personal research; contact via repo owner)"

# Published RSS/Atom feeds. Market sections only -- general news would swamp the
# archive with items no equity signal could ever use.
#
# The two moneycontrol feeds were here and are GONE, not commented out: on the
# first live run they returned HTTP 200 with items dated 848 and 849 days old.
# They are abandoned URLs that still serve, which is the worst failure mode --
# the job reports "15 items" and looks healthy while archiving 2024. Removed on
# evidence; re-add either one only with a run showing same-day items.
# business-standard is absent for a different reason: its robots.txt disallows
# us, and the run correctly skips it.
FEEDS = {
    "et_markets":
        "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms",
    "livemint_markets":
        "https://www.livemint.com/rss/markets",
}

# A feed whose newest item is older than this is treated as dead and its items
# are NOT stored. Feeds die silently and keep returning 200; without this the
# archive fills with old articles that look like today's capture.
MAX_FEED_AGE_DAYS = 7

_robots_cache = {}


def robots_ok(url, ua=UA):
    """-> True if this host's robots.txt permits `url`.

    A host whose robots.txt cannot be read is treated as ALLOWED, which is the
    documented default behaviour of the standard and matches what every feed
    reader does. A host that explicitly disallows is skipped.
    """
    parts = urllib.parse.urlparse(url)
    root = f"{parts.scheme}://{parts.netloc}"
    rp = _robots_cache.get(root)
    if rp is None:
        rp = urllib.robotparser.RobotFileParser()
        rp.set_url(root + "/robots.txt")
        try:
            rp.read()
        except Exception:
            _robots_cache[root] = False      # unreadable -> do not re-fetch
            return True
        _robots_cache[root] = rp
    if rp is False:
        return True
    try:
        return rp.can_fetch(ua, url)
    except Exception:
        return True


def _age_days(published, now=None):
    """-> age of a feed item in days, or None if the date is unparseable.

    None means "cannot judge", never "fresh". A feed whose dates we cannot read
    is allowed through, because an unparseable date is not evidence of death --
    but it is also not evidence of life, and it is logged as such.
    """
    if not published:
        return None
    from email.utils import parsedate_to_datetime
    now = now or datetime.now(timezone.utc)
    for parse in (parsedate_to_datetime, datetime.fromisoformat):
        try:
            d = parse(published)
            if d.tzinfo is None:
                d = d.replace(tzinfo=timezone.utc)
            return (now - d).days
        except (TypeError, ValueError):
            continue
    return None


def _text(el, *names):
    for n in names:
        # Atom tags carry a namespace; RSS ones do not. Match on the local name.
        for child in el:
            if child.tag.rsplit("}", 1)[-1] == n and (child.text or "").strip():
                return child.text.strip()
            if child.tag.rsplit("}", 1)[-1] == n and child.get("href"):
                return child.get("href").strip()
    return ""


def parse_feed(body, source=""):
    """-> [{source, title, link, published}] from RSS or Atom bytes."""
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        return []
    items = [e for e in root.iter()
             if e.tag.rsplit("}", 1)[-1] in ("item", "entry")]
    out = []
    for it in items:
        title = _text(it, "title")
        link = _text(it, "link", "id")
        if not title or not link:
            continue
        out.append({
            "source": source,
            "title": re.sub(r"\s+", " ", title)[:300],
            "link": link,
            "published": _text(it, "pubDate", "published", "updated"),
        })
    return out


def capture(feeds=None, log=print, fetcher=None):
    """Fetch every feed once and append what is new to today's file.

    `captured_at` is OUR timestamp, and it is the one that matters. A feed's own
    pubDate is whatever the publisher chose to put there and cannot be verified;
    the moment we saw an item is the moment it was demonstrably available.
    """
    from snapshot import fetch
    fetcher = fetcher or fetch
    feeds = feeds if feeds is not None else FEEDS
    now = datetime.now(timezone.utc)
    day = now.date().isoformat()
    out = NEWS / f"{day}.jsonl"
    NEWS.mkdir(parents=True, exist_ok=True)

    # Dedupe against everything already captured today, not just this run: the
    # job may tick more than once a day and a feed rarely changes between ticks.
    seen = set()
    if out.exists():
        for line in out.read_text().splitlines():
            try:
                seen.add(json.loads(line)["link"])
            except (json.JSONDecodeError, KeyError):
                continue

    added = 0
    with out.open("a") as fh:
        for name, url in feeds.items():
            if not robots_ok(url):
                log(f"  skip {name}: robots.txt disallows")
                continue
            status, body = fetcher(url, timeout=30)
            if status != 200 or not body:
                log(f"  skip {name}: HTTP {status}")
                continue
            items = parse_feed(body, source=name)

            # Is this feed alive? Judge on its NEWEST item: a live feed always
            # has something recent, a dead one has nothing recent at all.
            ages = [a for a in (_age_days(i["published"], now) for i in items)
                    if a is not None]
            if ages and min(ages) > MAX_FEED_AGE_DAYS:
                log(f"  skip {name}: STALE -- newest item is {min(ages)} days "
                    f"old, feed looks abandoned")
                continue
            if items and not ages:
                log(f"  {name}: no parseable dates; storing but cannot verify "
                    f"the feed is alive")

            new = [i for i in items if i["link"] not in seen]
            for i in new:
                seen.add(i["link"])
                i["captured_at"] = now.isoformat()
                fh.write(json.dumps(i, ensure_ascii=False) + "\n")
            added += len(new)
            log(f"  {name}: {len(items)} items, {len(new)} new")
    log(f"captured {added} new items -> {out.name}")
    return added


def _selftest():
    # --- RSS and Atom both parse, and junk does not crash -------------------
    rss = b"""<?xml version="1.0"?><rss version="2.0"><channel>
      <item><title>Widget Co wins order</title>
            <link>https://example.com/a</link>
            <pubDate>Thu, 20 Aug 2026 09:00:00 +0530</pubDate></item>
      <item><title>No link here</title></item>
    </channel></rss>"""
    got = parse_feed(rss, source="t")
    assert len(got) == 1, f"expected the linkless item dropped, got {got}"
    assert got[0]["title"] == "Widget Co wins order"
    assert got[0]["link"] == "https://example.com/a"

    atom = b"""<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">
      <entry><title>Atom headline</title>
             <link href="https://example.com/b"/>
             <updated>2026-08-20T09:00:00Z</updated></entry></feed>"""
    got = parse_feed(atom, source="t")
    assert len(got) == 1 and got[0]["link"] == "https://example.com/b", got

    assert parse_feed(b"not xml at all") == []
    assert parse_feed(b"") == []

    # --- capture dedupes and never double-writes ----------------------------
    import tempfile
    global NEWS
    real = NEWS
    try:
        with tempfile.TemporaryDirectory() as td:
            NEWS = _pl.Path(td)
            fake = lambda url, timeout=30: (200, rss)
            n1 = capture({"f": "https://example.com/feed"}, log=lambda *_: None,
                         fetcher=fake)
            n2 = capture({"f": "https://example.com/feed"}, log=lambda *_: None,
                         fetcher=fake)
            assert n1 == 1, n1
            assert n2 == 0, "the same item was captured twice"
            body = next(NEWS.glob("*.jsonl")).read_text().strip().splitlines()
            assert len(body) == 1, body
            rec = json.loads(body[0])
            # captured_at is ours and must always be present; published is the
            # publisher's and may be anything, including missing.
            assert rec["captured_at"], "no capture timestamp recorded"
    finally:
        NEWS = real

    # --- THE guarantee: no backtest can read this ---------------------------
    # There is no history here, so anything that read it during a backtest would
    # be reading the future. Asserted here, beside the thing it protects.
    pat = re.compile(r"^\s*(?:import\s+newswatch|from\s+newswatch\s+import)",
                     re.MULTILINE)
    offenders = []
    for d in ("src/research", "src/strategies"):
        for p in sorted((ROOT / d).rglob("*.py")):
            if pat.search(p.read_text(encoding="utf-8", errors="replace")):
                offenders.append(str(p.relative_to(ROOT)))
    assert not offenders, \
        f"a backtest imports the forward news archive, which has no history: {offenders}"

    # --- the staleness guard ------------------------------------------------
    # Both moneycontrol feeds returned HTTP 200 with items 848 days old. A guard
    # that only checked the status code would have called that a healthy run.
    now = datetime(2026, 8, 20, tzinfo=timezone.utc)
    assert _age_days("Tue, 23 Apr 2024 15:46:31 +0530", now) > 800
    assert _age_days("Wed, 19 Aug 2026 09:00:00 +0530", now) <= 1
    assert _age_days("2026-08-19T09:00:00+00:00", now) <= 1, "ISO dates too"
    assert _age_days("") is None and _age_days("not a date") is None, \
        "unparseable must be None -- cannot judge, not fresh"

    stale = b"""<?xml version="1.0"?><rss version="2.0"><channel>
      <item><title>Old news</title><link>https://example.com/old</link>
            <pubDate>Tue, 23 Apr 2024 15:46:31 +0530</pubDate></item>
    </channel></rss>"""
    try:
        with tempfile.TemporaryDirectory() as td:
            NEWS = _pl.Path(td)
            n = capture({"dead": "https://example.com/f"}, log=lambda *_: None,
                        fetcher=lambda url, timeout=30: (200, stale))
            assert n == 0, "a feed 800+ days stale was archived as fresh"
    finally:
        NEWS = real

    # robots handling must not explode on a malformed URL
    assert robots_ok("not-a-url") in (True, False)
    print("newswatch selftest ok (forward-only; no backtest imports it)")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    else:
        print(f"newswatch -> {NEWS}")
        capture()
