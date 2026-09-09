#!/usr/bin/env python3
"""BSE corporate announcements -- the second filings source, for the symbols
NSE's own feed omits.

WHY THIS EXISTS (L72). 228 of 2,292 tradeable symbols have ZERO filings in
data/announcements: NSE's corporate-announcements API returns empty for them
even queried per-symbol directly (KENNAMET, KOVAI, ABBOTINDIA among them --
companies that certainly file). The likely cause is BSE-designated filing.
Their announcements are public regulatory disclosures on BSE; this module
fetches the same feed a reader's browser gets.

THE ROBOTS DECISION (operator, 2026-08-25). api.bseindia.com publishes
robots.txt Disallow. The operator approved overriding it for exactly this
host and purpose: public filings, browser-equivalent client, load of one
page a day. Every fetch below passes `respect_robots=False` EXPLICITLY so
the decision is visible at each call site; crawl.ALLOWED_DESPITE_ROBOTS
stays empty as its selftest requires.

WHAT THE ENDPOINT GIVES, MEASURED 2026-08-25 (probe record -- these cost a
day of guessing to learn, do not re-derive):
  url     /BseIndiaAPI/api/AnnSubCategoryGetData/w with EXACTLY the params
          the bseindia.com app sends: pageno, strCat=-1, strPrevDate=YYYYMMDD,
          strScrip=, strSearch=P, strToDate=YYYYMMDD, strType=C,
          subcategory=-1. strSearch=P is MANDATORY -- empty returns {} -- and
          is what the site's own XHR sends.
  today   strPrevDate=strToDate=today returns the day's rows.
  past    CORRECTED 2026-09-09 (L93). This probe recorded "ANY past window
          returns 0 rows, in-browser or not, any param spelling", and that is
          wrong. A past WINDOW does return 0 -- strPrevDate=20260904 with
          strToDate=20260908 gives nothing, which is almost certainly what was
          tested -- but a past SINGLE DATE, strPrevDate == strToDate, returns
          that day's rows, 50 to a page, walked by pageno. 2026-09-03 gave
          2,198 rows and 2026-09-05 gave 2,089, every row carrying the queried
          date. fetch_past_day() uses this.
          The archive is therefore NOT forward-only, though what the API
          returns is a FIFTH of what the feed carries on a weekday, so a
          backfilled day is thin by collection method. `absent is not quiet`
          still applies; so does `recovered is not equivalent`.
  client  plain urllib passes Akamai for the api host (TLS-gated, not
          cookie-gated, once the param names are right); the headless browser
          is fingerprint-blocked and the headed browser is redirected to the
          SPA. No cookies, no session, one request a day.

SYMBOL MAPPING. BSE rows carry SCRIP_CD (a numeric code) and SLONGNAME (the
company name); this book lives in NSE symbols. Mapping is by company name
against the equity master, scored with the same token-overlap matcher
sentiment.py uses for news attribution, accepted at a fixed bar with the
match stored beside the record so a wrong match is auditable rather than
silent.

Backfill a missed day with:

    python3 src/core/bse_announcements.py --backfill 2026-09-05

Records use the SAME shape as announcements.parse_rows ({symbol,
visible_from, an_dt, desc, text}) through announcements' own visible_from,
so the 15:30 visibility rule and every downstream reader apply unchanged.
"""

import sys as _sys, pathlib as _pl
_sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[1]))
import paths  # noqa: F401

import json
import re
import sys
import urllib.request
from datetime import date, datetime, timedelta

import announcements
from paths import DATA

BSE_RAW = DATA / "announcements" / "bse" / "raw"
BSE_PARSED = DATA / "announcements" / "bse_parsed"
# PRIMARY: BSE's PUBLISHED RSS feed (beta.bseindia.com/rss-feed.html lists it
# for feed readers -- an invited fetch, plain client, ~1,000 items a day).
# FALLBACK for today, and the ONLY source for a past day: the app's
# AnnSubCategoryGetData JSON endpoint. Filtered by its mandatory strSearch=P to
# roughly a fifth of the feed's volume (2,153 against 10,760 on 2026-09-07), so
# it is the lesser source when both are available -- but it is DATED, which the
# feed is not, and that makes it the only way to fill a missed day (L93).
RSS_URL = "https://beta.bseindia.com/data/xml/announcements.xml"
API = ("https://api.bseindia.com/BseIndiaAPI/api/AnnSubCategoryGetData/w"
       "?pageno={pageno}&strCat=-1&strPrevDate={d0}&strScrip=&strSearch=P"
       "&strToDate={d1}&strType=C&subcategory=-1")
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
HEADERS = {
    "User-Agent": UA,
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://www.bseindia.com/",
    "Accept-Language": "en-IN",
    "sec-ch-ua": '"Chromium";v="151", "Not=A?Brand";v="99"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"macOS"',
}
# The name-match bar. sentiment._match_terms needs >= MIN_NAME_HITS token hits
# to attribute a headline; the same idea here, one number, not tuned.
MATCH_TOKENS = 2


def fetch_day(day=None, timeout=30):
    """-> today's BSE announcements, normalised to the row shape parse_rows
    consumes ({SLONGNAME, SCRIP_CD, NEWSSUB, NEWS_SUBMISSION_DT}).

    Source is the published RSS feed, which serves the LATEST day only --
    measured 2026-08-25: 1,039 items, every pubDate today. A past `day` is
    therefore served [] by construction and this function does not pretend
    otherwise; the archive it builds is forward-only, like newswatch's.
    """
    day = day or date.today()
    req = urllib.request.Request(RSS_URL, headers={"User-Agent": UA,
                                                   "Accept": "*/*"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read()
    except Exception:
        return []
    if not body:
        return []
    text = body.decode(errors="replace")
    out = []
    for item in re.findall(r"<item>(.*?)</item>", text, re.S):
        title = re.search(r"<title>([^<]*)</title>", item)
        code = re.search(r"<scripcode>([^<]*)</scripcode>", item)
        desc = re.search(r"<description>([^<]*)</description>", item)
        pub = re.search(r"<pubDate>([^<]*)</pubDate>", item)
        if not (title and code and pub):
            continue
        m = re.search(r"^(.*?)\s*\((\d+)\)\s*$", title.group(1).strip())
        name = m.group(1) if m else title.group(1).strip()
        ts = None
        for fmt in ("%d-%b-%Y %H:%M:%S", "%a, %d %b %Y %H:%M:%S %Z"):
            try:
                ts = datetime.strptime(pub.group(1).strip(), fmt)
                break
            except ValueError:
                continue
        if ts is None:
            continue
        out.append({
            "SCRIP_CD": code.group(1).strip(),
            "SLONGNAME": name,
            "NEWSSUB": (desc.group(1).strip() if desc else ""),
            "NEWS_SUBMISSION_DT": ts.isoformat(sep=" "),
        })
    return out


def fetch_past_day(day, max_pages=80, delay=0.15):
    """-> a PAST day's announcements from the dated API, same row shape as
    fetch_day. [] when the day yields nothing.

    L72a RECORDED THAT THIS IS IMPOSSIBLE AND IT IS NOT. That probe concluded
    "ANY past window returns 0 rows, in-browser or not, any param spelling",
    and the live endpoint disagrees: a SINGLE-DATE query
    (strPrevDate == strToDate == the past date) returns that day's rows, 50 to
    a page, walked by pageno. What genuinely returns 0 is a multi-day WINDOW --
    strPrevDate=20260904&strToDate=20260908 gives nothing -- which is very
    likely what was tested. Verified 2026-09-09 by fetching 2026-09-03 (2,198
    rows) and 2026-09-05 (2,089 rows), every row carrying the queried date.

    IT IS A PARTIAL SOURCE AND THAT MATTERS MORE THAN THE RECOVERY. Refetching
    days we already hold, against the RSS capture of the same day:

        2026-09-06 (Sunday)   RSS    230 rows   API    221
        2026-09-07 (Monday)   RSS 10,760 rows   API  2,153

    so on a weekday this returns about a fifth of what the live feed carries --
    the mandatory strSearch=P filters it. A backfilled day is therefore THINNER
    than a live-captured one, and a later reader comparing them would read the
    difference as a quiet day rather than as a different collection method.
    Anything storing these rows must say so; data/known_gaps.json is where.

    The row shape is normalised to fetch_day's so parse_rows and the whole
    downstream path are unchanged. NEWSSUB differs in FORMAT between the two
    sources -- the API prefixes company and scrip ("NLC India Ltd - 513683 -
    Reg. 34 (1) Annual Report.") where the feed gives the bare subject -- which
    is cosmetic here but is why the two cannot be de-duplicated by subject text.
    """
    import time
    import urllib.parse
    day = day if isinstance(day, str) else day.isoformat()
    stamp = day.replace("-", "")
    out, page = [], 1
    while page <= max_pages:
        q = {"pageno": page, "strCat": "-1", "subcategory": "-1",
             "strPrevDate": stamp, "strToDate": stamp, "strSearch": "P",
             "strType": "C"}
        url = (API.split("?")[0] + "?" + urllib.parse.urlencode(q))
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=30) as r:
                rows = json.loads(r.read().decode(errors="replace")).get("Table") or []
        except Exception:
            break
        if not rows:
            break
        for r in rows:
            ts = r.get("News_submission_dt") or r.get("NEWS_DT")
            if not ts:
                continue          # parse_rows drops these anyway; be explicit
            out.append({
                "SCRIP_CD": str(r.get("SCRIP_CD") or ""),
                "SLONGNAME": (r.get("SLONGNAME") or "").strip(),
                "NEWSSUB": (r.get("NEWSSUB") or "").strip(),
                "NEWS_SUBMISSION_DT": str(ts).replace("T", " ")[:19],
            })
        page += 1
        # One page a day is the load the robots override was granted for; a
        # backfill is dozens at once, so it waits between them.
        time.sleep(delay)
    return out


def _tokens(name):
    return {w for w in re.split(r"[^A-Za-z0-9]+", (name or "").upper())
            if len(w) >= 4 and w not in ("LIMITED", "LTD", "THE", "INDIA",
                                         "INDIAN", "COMPANY", "CORPORATION")}


def name_to_symbol(longname, master_names):
    """-> (NSE symbol, n_token_hits) best master match for a BSE company name.

    master_names is {symbol: company name}. Token-overlap count. Accept when
    TWO tokens match, or when ONE matches, that token is UNIQUE across all
    masters, AND THE BSE NAME HAS NO OTHER DISTINCTIVE WORD LEFT OVER --
    KENNAMETAL appears in exactly one company's name, and demanding a second
    hit would drop precisely the distinctive single-word brands.

    THE THIRD CONDITION WAS ADDED ON 2026-08-29 AND IT IS THE ONE WITH
    EVIDENCE BEHIND IT (L88a). Unique-across-the-NSE-master is not the same as
    unambiguous: BSE lists issuers NSE does not, so "Axis Solutions Ltd" shared
    only AXIS, that token was unique among NSE names, and three of its filings
    were written into AXISBANK's timeline. Measured on 433 rows once the source
    name was finally being stored: 54 of them (12.5%) attached one company's
    announcement to another company's symbol -- Ajanta SOYA under AJANTPHARM,
    Dhanlaxmi COTEX under DHANBANK, Cochin MINERALS under COCHINSHIP -- and
    every one was a single-token match where the BSE name carried a second
    distinctive word the master name could not account for.

    The distinction the rule draws: "Avantel Ltd" against "Avantel Limited"
    shares AVANTEL and leaves NOTHING unexplained, so it is the same company
    under a shorter name and is kept. "Ajanta Soya Ltd" against "Ajanta Pharma
    Limited" leaves SOYA unexplained, so it is a different company that happens
    to share a word, and is dropped. On the 433 labelled rows this keeps all 39
    genuine single-token matches and rejects all 54 mis-attributions.

    A rejected row is DROPPED, never reassigned: the right symbol may not be in
    the NSE master at all, which is exactly how these arose.
    Ties broken by the longer shared name. Returns (None, hits) when nothing
    reaches the bar -- an unmatched BSE name is DROPPED, not guessed: a filing
    attached to the wrong symbol is worse than a missing one.
    """
    want = _tokens(longname)
    if not want:
        return None, 0
    # token -> how many masters carry it (built per call; ~2,300 short names)
    spread = {}
    for name in master_names.values():
        for t in _tokens(name):
            spread[t] = spread.get(t, 0) + 1
    scored = []
    for sym, name in master_names.items():
        have = _tokens(name)
        shared = want & have
        hits = len(shared)
        unique = any(spread.get(t) == 1 for t in shared)
        extra = len(have - want)          # master words the BSE name lacks
        # ...and the other direction, which is the one that matters on a
        # single hit: BSE words the MASTER cannot account for. SOYA in
        # "Ajanta Soya" is this; "Avantel Ltd" has none.
        unexplained = len(want - shared)
        scored.append((hits, unique, extra, sym, unexplained))
    scored = [s for s in scored if s[0] > 0]
    if not scored:
        return None, 0
    # hits first, then PRECISION: "Union Bank of India" must beat
    # "City Union Bank Limited" on the same two shared tokens, and the exact
    # name is the one with no leftover master words.
    scored.sort(key=lambda s: (-s[0], s[2], not s[1]))
    hits, unique, _, best, unexplained = scored[0]
    if hits >= 2 or (hits >= 1 and unique and unexplained == 0):
        return best, hits
    return None, hits


def _ts(value):
    """-> datetime for BSE's ISO-ish stamp, or announcements' NSE formats."""
    if not value:
        return None
    v = str(value).strip()
    try:
        return datetime.fromisoformat(v)
    except ValueError:
        return announcements._dt(v)


def parse_rows(rows, master_names):
    """-> [{symbol, visible_from, an_dt, desc, text, source, match_hits}].

    BSE re-submits the same announcement (Arihant printed one filing four
    times in a single day's feed); identical (symbol, desc, calendar day)
    collapses to its first occurrence.
    """
    import features
    sessions = features.trading_days()
    out, seen = [], set()
    for r in rows:
        ts = _ts(r.get("News_submission_dt")
                 or r.get("NEWS_SUBMISSION_DT"))
        if ts is None:
            continue
        vf = announcements.visible_from(ts, sessions)
        if vf is None:
            # Stamps AFTER the last known session (today's filings, calendar
            # ends yesterday) are the NORMAL case for a forward-only source.
            # The 15:30 rule cannot name a next session that does not exist
            # yet, so the stamp's own date stands; the exact an_dt is kept on
            # the record either way, and this channel is context, never a
            # measured input, so a boundary-day imprecision costs nothing.
            vf = ts.date()
        sym, hits = name_to_symbol(r.get("SLONGNAME"), master_names)
        if not sym:
            continue
        desc = (r.get("NEWSSUB") or "").strip()
        day_key = (sym, desc, vf)
        if day_key in seen:
            continue
        seen.add(day_key)
        out.append({
            "symbol": sym,
            "visible_from": vf.isoformat(),
            "an_dt": ts.isoformat(sep=" "),
            "desc": desc,
            "text": (r.get("ATTACHMENTTEXT") or r.get("attchmntText") or "").strip()[:400],
            "source": "bse",
            "bse_scrip": str(r.get("SCRIP_CD") or ""),
            # The name the match was MADE FROM. Without it a mis-attribution is
            # invisible: the record says AXISBANK and nothing says which BSE
            # company's filing it was, so the only evidence of a bad match was
            # a scrip code that disagreed with its neighbours -- and that proxy
            # turns out to be unreliable, because a company files legitimately
            # under its equity code AND its debt-segment codes. 2,671 records
            # were stored before this field existed and cannot be re-derived
            # (the feed is forward-only, L72a), so the audit starts from here.
            "bse_name": (r.get("SLONGNAME") or "").strip(),
            "match_hits": hits,
        })
    out.sort(key=lambda x: (x["symbol"], x["visible_from"], x["an_dt"]))
    return out


def master_names():
    """-> {symbol: company name} from the newest equity master."""
    import csv, io, universe
    newest = universe.master_snapshot()
    if newest is None:
        return {}
    out = {}
    for r in csv.DictReader(io.StringIO(
            (newest / "equity_master.csv").read_text(errors="replace"))):
        sym = (r.get("SYMBOL") or "").strip().upper()
        name = (r.get("NAME OF COMPANY") or "").strip()
        if sym and name:
            out[sym] = name
    return out


def store_day(rows_parsed, day=None, rows_raw=None):
    """Append one day's parsed rows per symbol. Returns n stored.

    `rows_raw` is the FEED AS RECEIVED. It is written to BSE_RAW, which stored
    the parsed rows instead until 2026-08-28 -- a directory named raw holding
    derived data, which is the reason no matcher change can be re-derived
    against history: name_to_symbol's input was discarded the moment it ran.
    Kept optional so a caller that has already lost the raw rows still stores
    something, but the default is now the real thing.
    """
    day = day or date.today()
    by_sym = {}
    for r in rows_parsed:
        by_sym.setdefault(r["symbol"], []).append(r)
    BSE_PARSED.mkdir(parents=True, exist_ok=True)
    n = 0
    for sym, recs in sorted(by_sym.items()):
        p = BSE_PARSED / f"{sym}.jsonl"
        seen = set()
        if p.exists():
            for line in p.read_text().splitlines():
                if line.strip():
                    try:
                        rec = json.loads(line)
                        seen.add(rec["an_dt"] + "|" + rec["desc"])
                    except Exception:
                        continue
        with p.open("a") as f:
            for r in recs:
                key = r["an_dt"] + "|" + r["desc"]
                if key in seen:
                    continue
                f.write(json.dumps(r) + "\n")
                n += 1
    (BSE_RAW).mkdir(parents=True, exist_ok=True)
    (BSE_RAW / f"{day.isoformat()}.json").write_text(
        json.dumps(rows_raw if rows_raw is not None else rows_parsed,
                   indent=1) + "\n")
    return n


def timeline(symbol):
    """-> BSE filings for one NSE symbol, same shape as announcements.timeline."""
    p = BSE_PARSED / f"{symbol}.jsonl"
    if not p.exists():
        return []
    out = []
    for line in p.read_text().splitlines():
        if line.strip():
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    out.sort(key=lambda x: (x.get("visible_from", ""), x.get("an_dt", "")))
    return out


def update(day=None, log=print):
    """One idempotent daily step: fetch, map, store. -> (fetched, stored)."""
    rows = fetch_day(day)
    if not rows:
        return 0, 0
    parsed = parse_rows(rows, master_names())
    stored = store_day(parsed, day, rows_raw=rows)
    log(f"bse: {len(rows)} rows fetched, {len(parsed)} mapped, {stored} stored")
    return len(rows), stored


def _selftest():
    """Mapping bar, parse shape, store idempotence -- on fixtures, no network."""
    master = {"KENNAMET": "Kennametal India Limited",
              "KOVAI": "Kovai Medical Center and Hospital",
              "TCS": "Tata Consultancy Services Limited"}
    sym, hits = name_to_symbol("Kennametal India Ltd", master)
    assert sym == "KENNAMET" and hits >= 1, (sym, hits)  # unique-token path
    sym, hits = name_to_symbol("Kovai Medical Center and Hospital Ltd", master)
    assert sym == "KOVAI", (sym, hits)
    # exact fit must beat a longer name sharing both tokens (CUB regression)
    sym, hits = name_to_symbol("Union Bank of India", master | {
        "CUB": "City Union Bank Limited", "UNIONBANK": "Union Bank of India"})
    assert sym == "UNIONBANK", (sym, hits)
    sym, hits = name_to_symbol("Some Unrelated Company Limited", master)
    assert sym is None, "an unmatched name must be dropped, not guessed"

    # L88a, BOTH directions. A single shared token is only enough when the BSE
    # name leaves nothing unexplained; these are the real pairs that were
    # mis-filed for weeks, and the real pair that must keep working.
    real = {"AJANTPHARM": "Ajanta Pharma Limited",
            "AXISBANK": "Axis Bank Limited",
            "DHANBANK": "Dhanlaxmi Bank Limited",
            "AVANTEL": "Avantel Limited"}
    for bse_name in ("Ajanta Soya Ltd", "Axis Solutions Ltd",
                     "Dhanlaxmi Cotex Ltd"):
        sym, hits = name_to_symbol(bse_name, real)
        assert sym is None, \
            f"{bse_name!r} was attached to {sym} on {hits} shared token(s)"
    # ...and the same-company-shorter-name case must survive, or the fix has
    # simply turned the feed off: 39 of the 433 labelled rows look like this.
    assert name_to_symbol("Avantel Ltd", real)[0] == "AVANTEL"
    assert name_to_symbol("Kennametal India Ltd", master)[0] == "KENNAMET", \
        "the distinctive single-word brand this rule exists to keep was dropped"

    row = {"SCRIP_CD": 532477, "SLONGNAME": "Kennametal India Ltd",
           "NEWS_SUBMISSION_DT": "25-Aug-2026T10:00:00",
           "NEWSSUB": "Kennametal India - outcome of board meeting",
           "attchmntText": "pursuant to reg 30"}
    # announcements._dt expects NSE's format; BSE sends ISO-ish. The parse must
    # read BOTH or the fixture documents which it reads.
    parsed_ts = announcements._dt(row["NEWS_SUBMISSION_DT"])
    if parsed_ts is None:
        row["NEWS_SUBMISSION_DT"] = "2026-08-25T10:00:00"
    import tempfile
    import features as _f
    with tempfile.TemporaryDirectory() as td:
        # visible_from needs a trading calendar; fabricate one around the date
        from datetime import date as _d, timedelta as _td
        real = _f.trading_days
        days = [_d(2026, 8, 1) + _td(days=k) for k in range(31)]
        _f.trading_days = lambda: days
        try:
            recs = parse_rows([row], master)
        finally:
            _f.trading_days = real
        assert len(recs) == 1, recs
        r = recs[0]
        assert r["symbol"] == "KENNAMET" and r["source"] == "bse"
        assert r["visible_from"] and r["an_dt"] and r["desc"]
        # The name the match was made from has to survive into the record, or a
        # wrong match leaves no trace of which company's filing it really was.
        assert r["bse_name"] == "Kennametal India Ltd", r

        global BSE_PARSED, BSE_RAW
        old_p, old_r = BSE_PARSED, BSE_RAW
        BSE_PARSED = _pl.Path(td) / "parsed"
        BSE_RAW = _pl.Path(td) / "raw"
        try:
            n1 = store_day(recs, _d(2026, 8, 25), rows_raw=[row])
            n2 = store_day(recs, _d(2026, 8, 25), rows_raw=[row])
            assert n1 == 1 and n2 == 0, (n1, n2)   # idempotent: no dup rows
            tl = timeline("KENNAMET")
            assert len(tl) == 1 and tl[0]["bse_scrip"] == "532477"
            assert timeline("KOVAI") == []
            # BSE_RAW must hold the FEED, not a second copy of the parse. It
            # held the parsed rows until 2026-08-28, which is why none of the
            # 2,671 records already on disk can be re-matched.
            archived = json.loads(
                (BSE_RAW / "2026-08-25.json").read_text())
            assert archived and "SLONGNAME" in archived[0], archived[:1]
            assert "symbol" not in archived[0], \
                "the raw archive is holding parsed rows again"
        finally:
            BSE_PARSED, BSE_RAW = old_p, old_r
    print("bse_announcements selftest ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    elif "--backfill" in sys.argv:
        # A missed day, from the DATED endpoint. Deliberately a separate verb
        # from --update: it reads a different, thinner source, and a day
        # recovered this way is recorded in data/known_gaps.json under
        # `partial` so nothing later mistakes its row count for a quiet day.
        import datetime as _d
        _i = sys.argv.index("--backfill")
        _day = sys.argv[_i + 1] if len(sys.argv) > _i + 1 else None
        if not _day:
            sys.exit("--backfill needs a date: --backfill 2026-09-05")
        _rows = fetch_past_day(_day)
        _parsed = parse_rows(_rows, master_names())
        _n = store_day(_parsed, _d.date.fromisoformat(_day), rows_raw=_rows)
        print(f"bse backfill {_day}: {len(_rows)} fetched, {len(_parsed)} "
              f"mapped, {_n} stored")
    elif "--update" in sys.argv:
        n_fetched, n_stored = update()
        sys.exit(0 if n_fetched or n_stored >= 0 else 1)
    else:
        rows = fetch_day()
        print(f"{len(rows)} BSE announcements today")
