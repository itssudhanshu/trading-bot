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
    "et_stocks":
        "https://economictimes.indiatimes.com/markets/stocks/rssfeeds/2146842.cms",
    "et_ipo":
        "https://economictimes.indiatimes.com/markets/ipos/fpos/rssfeeds/14655708.cms",
    "livemint_markets":
        "https://www.livemint.com/rss/markets",
    "livemint_companies":
        "https://www.livemint.com/rss/companies",
}

# --- the per-company channel ------------------------------------------------
# The general feeds above are market-wide and almost never name a microcap: the
# first day's archive matched ZERO headlines to YUKEN, which makes the whole
# news channel useless for exactly the stocks this book trades. A per-company
# query fixes that, and finding one that is allowed took some looking:
#
#   Google News RSS      perfect data, 64-100 items per company -- and
#                        news.google.com/robots.txt is "Disallow: /" with only
#                        "Allow: /$" for every agent. NOT USED.
#   Yahoo Finance RSS    robots.txt disallows.
#   Reuters              robots.txt disallows.
#   Moneycontrol         HTTP 403 to any non-browser agent, all four feeds.
#   StockTwits           404 on every NSE symbol tried, RELIANCE included --
#                        it does not cover this market at all.
#   Bing News RSS        ALLOWED: robots.txt disallows /search but permits
#                        /news/search, checked both ways rather than assumed.
SYMBOL_QUERY = "https://www.bing.com/news/search?q={q}&format=RSS"

# A per-symbol item older than this is not archived. This is NOT the dead-feed
# guard below: a quiet microcap with nothing but six-month-old coverage is
# normal and its feed is perfectly alive, so the staleness rule that skips a
# whole dead SOURCE must not be applied to a quiet COMPANY. Here the filter is
# per item.
SYMBOL_MAX_AGE_DAYS = 30

# A feed whose newest item is older than this is treated as dead and its items
# are NOT stored. Feeds die silently and keep returning 200; without this the
# archive fills with old articles that look like today's capture.
MAX_FEED_AGE_DAYS = 7

_robots_cache = {}

# Feeds worth retrying through Nimble when a plain request is refused. These
# are sources whose robots.txt PERMITS the path -- the refusal is bot protection
# reacting to a urllib user-agent, not a stated policy -- so escalating to a
# renderer is asking the same question a browser would. Nimble applies its own
# robots check on top; a source that actually disallows is still not fetched.
ESCALATE = {
    "mc_latest":   "https://www.moneycontrol.com/rss/latestnews.xml",
    "mc_results":  "https://www.moneycontrol.com/rss/results.xml",
    "mc_buzz":     "https://www.moneycontrol.com/rss/buzzingstocks.xml",
    "zeebiz":      "https://www.zeebiz.com/latest.xml/feed",
}


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


def _unwrap(link):
    """-> the publisher's own URL behind an aggregator redirect.

    Bing wraps every result in news/apiclick.aspx with the real address in a
    `url=` parameter. Stored wrapped, every item's host reads "bing.com" and the
    archive cannot say who published anything -- which is most of what makes a
    headline worth keeping.
    """
    try:
        p = urllib.parse.urlparse(link)
        if "bing.com" in p.netloc and "apiclick" in p.path:
            q = urllib.parse.parse_qs(p.query).get("url")
            if q and q[0].startswith("http"):
                return q[0]
    except Exception:
        pass
    return link


# A landing or tag page is not a story. Bing returns these alongside articles --
# "Get all latest & breaking news on Happy Forgings" is a tag index, not news --
# and archiving them would pad the count with items that can never be scored.
_NOT_A_STORY = re.compile(r"/(tags?|topic|topics|quotes?|stocks?pricequote"
                          r"|share-price|company)/", re.I)

# The URL filter alone let "Yuken India Share Price" and "Yuken India Ltd YUKEN"
# through on the first live run: quote and profile pages whose paths look like
# ordinary article paths. Their TITLES give them away, and a title that is just
# an entity label carries no sentiment to score -- worse, it reads as coverage.
_TITLE_NOT_A_STORY = re.compile(
    r"\b(share|stock)\s+price\b|\blive\s+today\b|\bquarterly\s+earnings\b"
    r"|\bannual\s+report\s+analysis\b|\bprice\s+live\b|\bstock\s+quote\b"
    r"|\bshare\s+price\s+target\b|\bcompany\s+profile\b"
    r"|\bstock\s+research\s+report\b"
    # "Yuken India Ltd YUKEN" -- a database row rendered as a title, ending in
    # the ticker. Real headlines do not end that way.
    r"|\bltd\.?\s+[A-Z0-9]{3,}\s*$", re.I)


def _query_for(symbol, name):
    """-> the search phrase for a company, or None if it cannot be trusted.

    A bad query is worse than no query. TAKE resolved to no company name -- the
    symbol is not in the current equity master -- so the first live run searched
    the bare ticker "TAKE" and archived an Investopedia explainer on take-profit
    orders, filed against a stock it has nothing to do with. One such row is
    enough to make the whole channel untrustworthy, because nothing downstream
    can tell it from a real headline.

    So a query needs a resolved name that is more than the ticker and carries a
    word long enough to be a real name.
    """
    if not name:
        return None
    q = " ".join(w for w in name.split()
                 if w.lower().strip(".,") not in ("limited", "ltd", "ltd.", "the"))
    q = q.strip()
    # Compare the STRIPPED query against the ticker, not the raw name. "TAKE
    # Limited" is a real registered name and differs from "TAKE", so a check on
    # the raw name passes -- and then "Limited" is removed and the query IS the
    # bare ticker, which is what searched for take-profit orders. The property
    # is that the query must be more specific than the ticker, and only the
    # stripped form can answer that.
    if q.upper().replace(" ", "") == symbol.upper():
        return None
    words = [w for w in re.split(r"[^A-Za-z]+", q) if len(w) >= 4]
    if not words:
        return None
    return q or None


def _publisher(raw, host):
    """-> a publisher name that is the same string every time.

    The first run recorded Moneycontrol three ways -- "Moneycontrol",
    "moneycontrol.com" and "cnbctv18" beside "CNBCTV18" -- plus
    "Morningstar%2c Inc." with its comma still percent-encoded. Counting
    publishers is the main thing this field is for, and three spellings of one
    name is three publishers to anything doing the counting.
    """
    name = urllib.parse.unquote(raw or "").strip()
    if not name:
        name = host
    name = re.sub(r"^www\.", "", name)
    name = re.sub(r"\.(com|in|co\.in|net|org)$", "", name, flags=re.I)
    name = re.sub(r"\s+on\s+MSN$", "", name, flags=re.I)
    name = name.strip() or host
    # Case is the last difference, and lowercasing everything would turn
    # CNBCTV18 into cnbctv18. A small table of the publishers this archive
    # actually sees, keyed on the flattened name; anything unknown keeps the
    # spelling it arrived with rather than being mangled into Title Case.
    return _CANON.get(name.casefold(), name)


_CANON = {
    "moneycontrol": "Moneycontrol",
    "cnbctv18": "CNBCTV18",
    "mint": "Mint", "livemint": "Mint",
    "the economic times": "The Economic Times",
    "economictimes": "The Economic Times", "economic times": "The Economic Times",
    "business standard": "Business Standard",
    "business line": "Business Line", "thehindubusinessline": "Business Line",
    "the financial express": "The Financial Express",
    "financialexpress": "The Financial Express",
    "zee business": "Zee Business", "zeebiz": "Zee Business",
    "hindustan times": "Hindustan Times", "hindustantimes": "Hindustan Times",
    "business today": "Business Today", "businesstoday": "Business Today",
    "reuters": "Reuters", "news18": "News18", "investopedia": "Investopedia",
}


def parse_feed(body, source=""):
    """-> [{source, publisher, title, link, published}] from RSS or Atom."""
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
        real = _unwrap(link)
        if _NOT_A_STORY.search(urllib.parse.urlparse(real).path):
            continue
        if _TITLE_NOT_A_STORY.search(title):
            continue
        # Bing names the publisher in <News:Source>; a plain RSS feed does not,
        # and there the host is the publisher.
        host = urllib.parse.urlparse(real).netloc
        out.append({
            "source": source,
            "publisher": _publisher(_text(it, "Source"), host),
            "title": re.sub(r"\s+", " ", title)[:300],
            "link": real,
            "published": _text(it, "pubDate", "published", "updated"),
        })
    return out


def _escalate(name, url, status, log=print):
    """-> (body, status, via) after retrying a refused feed through Nimble.

    Optional in every sense: with no key configured this returns the original
    refusal unchanged and the caller carries on. A daily job must not start
    failing because an optional key expired.
    """
    if name not in ESCALATE:
        return b"", status, "direct"
    try:
        import nimble
    except Exception:
        return b"", status, "direct"
    if not nimble.available():
        log(f"  {name}: HTTP {status}; no NIMBLE_API_KEY, not escalating")
        return b"", status, "direct"
    page = nimble.extract(url, formats=["html"])
    if not page.ok or not page.html:
        log(f"  {name}: HTTP {status}; nimble also failed ({page.error[:60]})")
        return b"", status, "direct"
    return page.html.encode(), 200, "nimble"


def capture_symbols(symbols, log=print, fetcher=None, pause=2.0):
    """Query per company and append what is new, tagged with its symbol.

    Tagging matters: a general-feed headline has to be matched to a company by
    name, which is fuzzy and drops three-letter tickers entirely. An item that
    arrived FROM a company's own query carries that attribution for free, so
    sentiment.py can read it exactly rather than guessing.
    """
    import time
    import urllib.parse
    from snapshot import fetch
    import sentiment                    # one definition of the company name
    fetcher = fetcher or fetch
    now = datetime.now(timezone.utc)
    day = now.date().isoformat()
    out = NEWS / f"{day}.jsonl"
    NEWS.mkdir(parents=True, exist_ok=True)

    seen = set()
    if out.exists():
        for line in out.read_text().splitlines():
            try:
                seen.add(json.loads(line)["link"])
            except (json.JSONDecodeError, KeyError):
                continue

    added = 0
    with out.open("a") as fh:
        for sym in symbols:
            q = _query_for(sym, sentiment.company_name(sym))
            if q is None:
                log(f"  skip {sym}: no company name to search on "
                    f"(not in the current equity master)")
                continue
            url = SYMBOL_QUERY.format(q=urllib.parse.quote(f'"{q}"'))
            if not robots_ok(url):
                log(f"  skip {sym}: robots.txt disallows")
                continue
            status, body = fetcher(url, timeout=30)
            if status != 200 or not body:
                log(f"  {sym}: HTTP {status}")
                time.sleep(pause)
                continue
            fresh = []
            for i in parse_feed(body, source="bing_news"):
                if i["link"] in seen:
                    continue
                age = _age_days(i["published"], now)
                if age is not None and age > SYMBOL_MAX_AGE_DAYS:
                    continue
                i["symbol"] = sym            # exact attribution, not a guess
                i["captured_at"] = now.isoformat()
                fresh.append(i)
                seen.add(i["link"])
            for i in fresh:
                fh.write(json.dumps(i, ensure_ascii=False) + "\n")
            added += len(fresh)
            log(f"  {sym} ({q}): {len(fresh)} new")
            time.sleep(pause)
    log(f"captured {added} per-company items -> {out.name}")
    return added


def capture(feeds=None, log=print, fetcher=None):
    """Fetch every feed once and append what is new to today's file.

    `captured_at` is OUR timestamp, and it is the one that matters. A feed's own
    pubDate is whatever the publisher chose to put there and cannot be verified;
    the moment we saw an item is the moment it was demonstrably available.
    """
    from snapshot import fetch
    fetcher = fetcher or fetch
    feeds = feeds if feeds is not None else dict(FEEDS)
    # The escalate-only sources are attempted ONLY when a key exists. Adding
    # them unconditionally would mean four requests a day that are known to
    # return 403, which is noise in the log and load on someone else's server
    # for a result already known.
    if feeds is not None and fetcher is None:
        try:
            import nimble
            if nimble.available():
                feeds = dict(feeds, **ESCALATE)
        except Exception:
            pass
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
            via = "direct"
            if (status != 200 or not body) and fetcher is not None:
                # A refusal is not always the end. These sources publish the
                # feed and permit the path; they refuse a urllib user-agent.
                body, status, via = _escalate(name, url, status, log)
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
                i["via"] = via          # how it was obtained, kept with the item
                fh.write(json.dumps(i, ensure_ascii=False) + "\n")
            added += len(new)
            log(f"  {name}: {len(items)} items, {len(new)} new"
                + ("" if via == "direct" else f" (via {via})"))
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

    # --- entity pages are not stories, and publishers get one spelling ------
    ent = b"""<?xml version="1.0"?><rss version="2.0"><channel>
      <item><title>Yuken India Share Price</title>
            <link>https://example.com/a</link></item>
      <item><title>Yuken India Ltd. Share Price Live Today</title>
            <link>https://example.com/b</link></item>
      <item><title>Yuken India bags a Rs 40 crore order</title>
            <link>https://example.com/c</link></item>
    </channel></rss>"""
    got = parse_feed(ent, source="t")
    assert len(got) == 1, [g["title"] for g in got]
    assert "bags a Rs 40 crore order" in got[0]["title"], got

    assert _publisher("moneycontrol.com", "x") == _publisher("Moneycontrol", "x"), \
        "one publisher recorded under two spellings"
    assert _publisher("Morningstar%2c Inc.", "x") == "Morningstar, Inc.", \
        _publisher("Morningstar%2c Inc.", "x")
    assert _publisher("News18 on MSN", "x") == "News18"
    # Assert the PROPERTY -- the host form and the display form of one publisher
    # must agree -- not the literal string. An earlier version asserted
    # "livemint", which the canonical table then correctly changed to "Mint",
    # failing a test for a reason that was an improvement.
    assert _publisher("", "www.livemint.com") == _publisher("Mint", "x"), \
        "a publisher's host form and display form disagree"
    assert _publisher("", "www.moneycontrol.com") == _publisher("Moneycontrol", "x")
    # An unknown publisher keeps the spelling it arrived with rather than being
    # mangled -- Title Case would turn CNBCTV18 into Cnbctv18.
    assert _publisher("SomeNewWire", "x") == "SomeNewWire"

    # --- a query that cannot be trusted is not made ------------------------
    assert _query_for("TAKE", "") is None, "a bare ticker became a search query"
    assert _query_for("TAKE", "TAKE") is None, "name equal to the ticker is not a name"
    # The case that actually happened: a real registered name that REDUCES to
    # the ticker once the generic tail is stripped. Checking the raw name lets
    # this through, and the query then searched the bare word "TAKE".
    assert _query_for("TAKE", "TAKE Limited") is None, \
        "a name that strips down to the bare ticker became a query"
    assert _query_for("TAKE", "Take Solutions Limited") == "Take Solutions"
    assert _query_for("XYZ", "Ltd") is None, "no word long enough to be a name"
    assert _query_for("YUKEN", "Yuken India Limited") == "Yuken India"
    assert _query_for("HAPPYFORGE", "Happy Forging Limited") == "Happy Forging"

    # and the entity-title filter catches the database-row shape
    ent2 = b"""<?xml version="1.0"?><rss version="2.0"><channel>
      <item><title>Yuken India Ltd YUKEN</title><link>https://e.com/a</link></item>
      <item><title>Stock Research Report for Yuken India Ltd</title>
            <link>https://e.com/b</link></item>
      <item><title>Yuken India Ltd wins export order</title>
            <link>https://e.com/c</link></item>
    </channel></rss>"""
    got2 = parse_feed(ent2, source="t")
    assert len(got2) == 1 and "export order" in got2[0]["title"], \
        [g["title"] for g in got2]

    # --- aggregator redirects must be unwrapped to the real publisher --------
    wrapped = ("http://www.bing.com/news/apiclick.aspx?ref=FexRss&aid=&"
               "url=https%3a%2f%2fwww.moneycontrol.com%2fnews%2fx.html&c=1")
    assert _unwrap(wrapped) == "https://www.moneycontrol.com/news/x.html", _unwrap(wrapped)
    assert _unwrap("https://plain.example/story") == "https://plain.example/story"

    # --- escalation is optional and silent when unconfigured ---------------
    # The job must behave identically with no key, which is how it behaves
    # today and how it will behave the day a key expires.
    b, st, via = _escalate("mc_latest", "https://x.example/f", 403,
                           log=lambda *_: None)
    assert via == "direct" and st == 403 and b == b"", (via, st, b)
    # a feed not on the escalate list is never escalated at all
    assert _escalate("et_markets", "https://x.example/f", 500,
                     log=lambda *_: None)[2] == "direct"

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
    elif "--picks" in sys.argv:
        # Per-company news for what the active strategy is actually looking at.
        # Bounded by the bucket's size, so this is ~10 queries a day, not 900.
        import clusters
        import features
        c = features.load_corpus()
        day = sorted({d for s in c.values() for d in s.days})[-1]
        syms = [s for lst in clusters.pick(c, day).values() for s, _ in lst[:5]]
        print(f"newswatch --picks: {len(syms)} candidates as of {day}")
        capture_symbols(syms)
    else:
        # BOTH channels on the daily tick. The general feeds give the market
        # backdrop; the per-company queries are the only reason the archive says
        # anything at all about a microcap -- day one matched ZERO headlines to
        # YUKEN from the general feeds alone.
        #
        # No STRATEGY is set by the scheduler, so the picks are sprout's, which
        # is the live book. That is the right list: capture news for what is
        # actually being looked at.
        print(f"newswatch -> {NEWS}")
        capture()
        try:
            import clusters
            import features
            c = features.load_corpus()
            day = sorted({d for s in c.values() for d in s.days})[-1]
            syms = [s for lst in clusters.pick(c, day).values() for s, _ in lst[:5]]
            print(f"per-company ({paths.STRATEGY}, {len(syms)} candidates):")
            capture_symbols(syms)
        except Exception as e:
            # A failure here must not cost the general capture, which has
            # already been written. The job logs and exits clean.
            print(f"  per-company capture skipped: {type(e).__name__}: {e}")
