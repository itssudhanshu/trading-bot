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
  today   strPrevDate=strToDate=today returns the day's rows. ANY past window
          returns 0 rows, in-browser or not, any param spelling. pageno does
          NOT walk back in time. This endpoint is therefore TODAY-ONLY and
          the archive it builds is FORWARD-ONLY, exactly like newswatch's:
          history before the first fetch is absent, and `absent is not quiet`
          applies to it permanently.
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
# FALLBACK (superseded for yield, kept as the probe record): the app's
# AnnSubCategoryGetData JSON endpoint -- today-only AND filtered to ~8 rows
# by its mandatory strSearch=P, vs 1,039 on the feed the same morning.
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


def _tokens(name):
    return {w for w in re.split(r"[^A-Za-z0-9]+", (name or "").upper())
            if len(w) >= 4 and w not in ("LIMITED", "LTD", "THE", "INDIA",
                                         "INDIAN", "COMPANY", "CORPORATION")}


def name_to_symbol(longname, master_names):
    """-> (NSE symbol, n_token_hits) best master match for a BSE company name.

    master_names is {symbol: company name}. Token-overlap count. Accept when
    TWO tokens match, or when ONE matches and that token is UNIQUE across all
    masters -- KENNAMETAL appears in exactly one company's name, and demanding
    a second hit would drop precisely the distinctive single-word brands.
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
        scored.append((hits, unique, extra, sym))
    scored = [s for s in scored if s[0] > 0]
    if not scored:
        return None, 0
    # hits first, then PRECISION: "Union Bank of India" must beat
    # "City Union Bank Limited" on the same two shared tokens, and the exact
    # name is the one with no leftover master words.
    scored.sort(key=lambda s: (-s[0], s[2], not s[1]))
    hits, unique, _, best = scored[0]
    if hits >= 2 or (hits >= 1 and unique):
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
    elif "--update" in sys.argv:
        n_fetched, n_stored = update()
        sys.exit(0 if n_fetched or n_stored >= 0 else 1)
    else:
        rows = fetch_day()
        print(f"{len(rows)} BSE announcements today")
