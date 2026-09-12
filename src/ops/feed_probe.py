#!/usr/bin/env python3
"""Is NSE's quarterly-results feed frozen, or are we calling it wrong?

MEASURED (2026-09-12, `xbrl_probe`): 2,021 of 2,120 symbols' indexes stop at
exactly 2024-12-31 and 0 of 2,120 list anything at 2025-06-30 or later, on
indexes force-refetched that same day. Companies do not all stop filing on one
date, and `drop_census` cleared the parser -- 6,548 dropped rows, every one a
null broadCastDate on a 2006-2007 filing, nothing from 2025 at all.

HYPOTHESIS. `fundamentals.INDEX_URL` sends no date range:

    /api/corporates-financial-results?index=equities&symbol=X&period=Quarterly

while this repo's OWN announcements client sends one:

    /api/corporate-announcements?index=equities&from_date=..&to_date=..

If the results endpoint answers an undated request from a default window, a
frozen ceiling is what that looks like -- and the fix is a query parameter, not
a new data source.

ENDPOINT. For each URL variant, on a few large liquid names: HTTP status, rows
returned, and the newest `toDate` in the response. The question is answered by
one number -- does ANY variant return a quarter ending after 2024-12-31.

Two controls, because a negative result has to be attributable:
  - `period=Annual`, the same endpoint by a path this repo already uses;
  - `corporate-announcements` over a recent window, which shares the fetch,
    headers and cookie handling. If that returns 2026 rows and results does
    not, the difference is the endpoint, not our client or our access.

This answers a question. It adopts nothing and changes no rule.
"""
import sys as _sys, pathlib as _pl
_sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[1]))
import paths  # noqa: F401  -- puts the source dirs on sys.path
import json
import sys
from datetime import date, timedelta

import fundamentals as fu
from snapshot import fetch

RESULTS = "https://www.nseindia.com/api/corporates-financial-results"
ANNOUNCE = "https://www.nseindia.com/api/corporate-announcements"

# The ceiling every symbol shares. Any variant that beats it is the answer.
CEILING = date(2024, 12, 31)

SYMBOLS = ("RELIANCE", "TCS", "INFY")


def variants(sym, today=None):
    """-> [(label, url)]. Named so a result can be quoted against its request."""
    today = today or date.today()
    d0, d1 = date(2025, 1, 1), today
    f = "%d-%m-%Y"
    return [
        ("current (no dates)",
         f"{RESULTS}?index=equities&symbol={sym}&period=Quarterly"),
        ("quarterly + from/to",
         f"{RESULTS}?index=equities&symbol={sym}&period=Quarterly"
         f"&from_date={d0.strftime(f)}&to_date={d1.strftime(f)}"),
        ("quarterly, no period arg",
         f"{RESULTS}?index=equities&symbol={sym}"
         f"&from_date={d0.strftime(f)}&to_date={d1.strftime(f)}"),
        ("control: period=Annual",
         f"{RESULTS}?index=equities&symbol={sym}&period=Annual"),
    ]


def newest_todate(body):
    """-> (rows, newest toDate) from a results response. None if unreadable.

    Tolerant about shape: NSE has returned both a bare list and {"data": [...]}
    from this family of endpoints, and a probe that assumes one of them reports
    a live endpoint as dead.
    """
    try:
        doc = json.loads(body)
    except Exception:
        return None, None
    rows = doc if isinstance(doc, list) else (doc.get("data") or doc.get("resultsList"))
    if not isinstance(rows, list):
        return None, None
    dates = [d for d in (fu._dt(r.get("toDate")) for r in rows
                         if isinstance(r, dict)) if d]
    return len(rows), (max(dates) if dates else None)


def probe_one(url, fetcher=None):
    fetcher = (fetcher or fetch)
    status, body = fetcher(url, timeout=30)
    n, newest = newest_todate(body or b"")
    return {"status": status, "bytes": len(body or b""), "rows": n,
            "newest": newest, "beats_ceiling": bool(newest and newest > CEILING)}


def report(symbols=SYMBOLS, fetcher=None):
    print(f"the shared ceiling is {CEILING}; a variant answers this question "
          f"only if it returns something LATER\n")
    win = []
    for sym in symbols:
        print(f"  {sym}")
        for label, url in variants(sym):
            r = probe_one(url, fetcher=fetcher)
            mark = "  <-- BEATS THE CEILING" if r["beats_ceiling"] else ""
            print(f"    {label:<26} HTTP {r['status']:<4} "
                  f"rows={str(r['rows']):<6} newest={r['newest']}{mark}")
            if r["beats_ceiling"]:
                win.append((sym, label, r["newest"], url))
        print()

    # Control: does ANY NSE endpoint serve recent rows through this client?
    today = date.today()
    d0 = today - timedelta(days=7)
    url = (f"{ANNOUNCE}?index=equities&from_date={d0:%d-%m-%Y}"
           f"&to_date={today:%d-%m-%Y}")
    status, body = (fetcher or fetch)(url, timeout=30)
    n, _ = newest_todate(body or b"")
    try:
        doc = json.loads(body or b"[]")
        rows = doc if isinstance(doc, list) else (doc.get("data") or [])
    except Exception:
        rows = []
    print(f"  control: corporate-announcements, last 7 days -- HTTP {status}, "
          f"{len(rows)} row(s)")
    if status == 200 and rows:
        print("    -> this client CAN read current NSE data, so a frozen "
              "results feed is the endpoint, not our access")
    else:
        print("    -> the control is also empty: suspect access (cookies, "
              "headers, rate limiting), NOT a frozen feed")

    print()
    if win:
        print(f"  ANSWER: {len(win)} variant(s) return a quarter after "
              f"{CEILING}. The feed is not frozen; the request was wrong.")
        for sym, label, newest, url in win[:3]:
            print(f"    {sym} via {label}: {newest}\n      {url}")
    else:
        print(f"  ANSWER: no variant beats {CEILING}. Combined with a working "
              f"control, the quarterly feed is frozen at the source and no "
              f"parameter change reaches 2025-2026 data.")
    return win


def _selftest():
    # newest_todate must read BOTH shapes NSE has served. A probe that assumes
    # one of them reports a live endpoint as dead, which would send this
    # project hunting for a new data source it does not need.
    bare = json.dumps([{"toDate": "30-Jun-2026"}, {"toDate": "31-Mar-2026"}])
    n, newest = newest_todate(bare.encode())
    assert (n, newest) == (2, date(2026, 6, 30)), (n, newest)
    wrapped = json.dumps({"data": [{"toDate": "31-Dec-2024"}]})
    assert newest_todate(wrapped.encode()) == (1, date(2024, 12, 31))
    assert newest_todate(b"<html>nope</html>") == (None, None)
    assert newest_todate(b"") == (None, None)
    # a row with an unreadable date must not sink the whole response
    mixed = json.dumps([{"toDate": None}, {"toDate": "30-Sep-2026"}])
    assert newest_todate(mixed.encode()) == (2, date(2026, 9, 30))

    # the ceiling comparison is the entire verdict, so assert both directions
    assert probe_one("u", lambda u, timeout=30: (200, bare.encode()))["beats_ceiling"]
    assert not probe_one("u", lambda u, timeout=30: (200, wrapped.encode()))["beats_ceiling"]
    assert not probe_one("u", lambda u, timeout=30: (404, b""))["beats_ceiling"]

    # every variant must be a distinct, well-formed URL for the symbol asked
    vs = variants("RELIANCE", today=date(2026, 9, 12))
    assert len(vs) == len({u for _, u in vs}), "duplicate variant"
    assert all("symbol=RELIANCE" in u for _, u in vs), vs
    assert any("from_date=01-01-2025" in u for _, u in vs), vs
    print("feed_probe selftest ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    else:
        syms = [a for a in sys.argv[1:] if not a.startswith("-")]
        bad = [a for a in sys.argv[1:] if a.startswith("-")]
        if bad:
            raise SystemExit(f"unknown flag {bad[0]!r}; use --selftest "
                             f"or bare symbols")
        report(tuple(syms) or SYMBOLS)
