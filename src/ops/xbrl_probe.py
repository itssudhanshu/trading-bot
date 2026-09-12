#!/usr/bin/env python3
"""Why did 1,240 XBRL downloads fail while 40 succeeded?

HYPOTHESIS, stated before the run: the 2026-09-12 forced refresh grew the job
list from ~40,800 filings to 41,919 -- so the fresh indexes DID reveal roughly
1,280 filings that were not previously known -- and `xbrl_ok=40 fail=1240` says
almost every one of those new filings failed to download. If that is right, the
missing filings are concentrated in RECENT quarters, and the corpus is not
merely stale but structurally unable to advance.

ENDPOINT. Two numbers decide it:

  1. the quarter_end distribution of filings with no file on disk. Concentrated
     in the last few quarters -> the failures ARE the new data. Spread evenly
     across 2019-2026 -> they are long-standing gaps and the refresh simply
     found nothing, which is a different problem.
  2. the HTTP status of a sample of them. `snapshot.fetch` returns (status, b"")
     on an HTTPError and (0, b"") on anything else, and `backfill` throws all of
     it into one `xbrl_fail` counter -- so 404, 403, timeout and "body was not
     XBRL" are currently indistinguishable. They have completely different
     fixes.

This answers a question; it adopts nothing and changes no rule.
"""
import sys as _sys, pathlib as _pl
_sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[1]))
import paths  # noqa: F401  -- puts the source dirs on sys.path
import sys
from collections import Counter
from datetime import date

import fundamentals as fu


def missing(symbols, log=None, start=None):
    """-> [(sym, quarter_end, url)] for every in-index filing with no file.

    `start` filters on visible_from exactly as `backfill` does. Without it this
    counts the whole of history and cannot be reconciled with the run it is
    diagnosing: the first version reported 51,250 holes against a job list of
    41,919, because backfill windows from 2019-01-01 and this did not. A
    diagnostic whose number does not tie out to the thing it diagnoses is
    another number to explain, not an explanation.

    Reads the cached index only. No network: the point is to characterise the
    hole before deciding what to do about it.
    """
    out = []
    for i, sym in enumerate(symbols, 1):
        if log and i % 500 == 0:
            log(f"  scanned {i}/{len(symbols)}")
        try:
            rows = fu.build_asof(sym)
        except Exception:
            continue
        for f in rows:
            if not f.get("xbrl"):
                continue
            if start and f["visible_from"] < start:
                continue
            if not fu._xbrl_path(sym, f["quarter_end"]).exists():
                out.append((sym, f["quarter_end"], f["xbrl"]))
    return out


def newest_listed(symbols):
    """-> Counter of each symbol's NEWEST listed quarter_end.

    The question the refresh actually raised: does NSE's index list 2025-2026
    quarters at all? If most symbols' newest listed filing is 2024-12-31 then
    nothing downstream can advance and no amount of refetching will help --
    the data is not being served, rather than being dropped by this repo.
    """
    out = Counter()
    for sym in symbols:
        try:
            rows = fu.build_asof(sym)
        except Exception:
            continue
        if rows:
            out[max(r["quarter_end"] for r in rows)] += 1
    return out


def by_quarter(holes):
    """-> Counter of quarter_end, so concentration is visible rather than asserted."""
    return Counter(q for _, q, _ in holes)


def probe(holes, n=40, fetcher=None):
    """Fetch a sample and report what actually came back, status by status."""
    import random
    fetcher = fetcher or fu.fetch
    sample = random.Random(7).sample(holes, min(n, len(holes)))
    rows = []
    for sym, qe, url in sample:
        status, body = fetcher(url, timeout=40)
        head = (body or b"")[:2000].lower()
        rows.append({"sym": sym, "quarter_end": qe, "url": url,
                     "status": status, "bytes": len(body or b""),
                     "is_xbrl": bool(body) and b"xbrl" in head,
                     "head": (body or b"")[:120]})
    return rows


def report(symbols=None, n=40, start=date(2019, 1, 1)):
    import features
    if symbols is None:
        symbols = sorted(features.load_corpus())

    # ENDPOINT 0, which the first run made the obvious one to ask: how far do
    # the indexes themselves reach? Every hole below is a filing NSE listed. If
    # NSE lists nothing after 2024 then there is no hole to fill and the whole
    # staleness question is about what is being served, not what is stored.
    print(f"scanning {len(symbols)} symbols")
    nl = newest_listed(symbols)
    tot = sum(nl.values())
    print(f"\n  newest quarter each symbol's index LISTS ({tot} with any):")
    for q, c in sorted(nl.items(), reverse=True)[:10]:
        print(f"    {q}  {c:5d} symbol(s)")
    if tot:
        for label, cut in (("2026-03-31", date(2026, 3, 31)),
                           ("2025-06-30", date(2025, 6, 30))):
            c = sum(v for q, v in nl.items() if q >= cut)
            print(f"    {c}/{tot} ({c/tot:.0%}) list a quarter ending "
                  f"{label} or later")

    print(f"\n  filings listed but not stored (visible_from >= {start}, "
          f"the same window backfill uses):")
    holes = missing(symbols, log=print, start=start)
    print(f"\n  {len(holes)} listed filings have no XBRL file on disk")
    if not holes:
        print("  nothing missing; the hole is elsewhere")
        return
    qc = by_quarter(holes)
    print("\n  by quarter_end (top 12) -- ENDPOINT 1:")
    for q, c in sorted(qc.items(), reverse=True)[:12]:
        print(f"    {q}  {c:5d}")
    # date, NOT a string. build_asof returns datetime.date for quarter_end and
    # visible_from; comparing one to an ISO string raises, and in the places
    # where Python does not raise it silently answers the wrong question.
    cut = date(2025, 6, 30)
    assert all(isinstance(q, date) for q in qc), \
        f"quarter_end is not a date: {[type(q) for q in list(qc)[:3]]}"
    recent = sum(c for q, c in qc.items() if q >= cut)
    print(f"\n  {recent}/{len(holes)} ({recent/len(holes):.0%}) are quarters "
          f"ending 2025-06-30 or later")
    print("  -> concentrated: the failures ARE the new data"
          if recent / len(holes) > 0.5 else
          "  -> spread out: these are long-standing gaps, not a refresh failure")

    print(f"\n  fetching {min(n, len(holes))} of them -- ENDPOINT 2:")
    rows = probe(holes, n=n)
    sc = Counter(r["status"] for r in rows)
    for status, c in sc.most_common():
        label = {0: "no HTTP response (timeout/DNS/connection)",
                 200: "200 OK", 403: "403 forbidden", 404: "404 not found"}.get(
                     status, f"HTTP {status}")
        print(f"    {c:3d}  {label}")
    ok = [r for r in rows if r["is_xbrl"]]
    served = [r for r in rows if r["status"] == 200 and not r["is_xbrl"]]
    print(f"    {len(ok)} of {len(rows)} returned something that is XBRL")
    if served:
        print("\n  200 but NOT xbrl -- what the server actually sent:")
        for r in served[:3]:
            print(f"    {r['sym']} {r['quarter_end']}  {r['bytes']}B  {r['head'][:90]}")
    bad = [r for r in rows if r["status"] not in (200,)]
    if bad:
        print("\n  example failing URLs:")
        for r in bad[:3]:
            print(f"    [{r['status']}] {r['url'][:110]}")


def _selftest():
    # by_quarter must count, not assert. A concentration claim is the whole
    # endpoint, so it is computed from the rows rather than eyeballed.
    # date objects, NOT ISO strings. build_asof returns datetime.date, and the
    # first version of this fixture used strings -- so the selftest passed
    # while the real run died on `q >= "2025-06-30"`. A fixture that gets the
    # TYPE wrong tests nothing the caller will ever hit; the same mistake put
    # ISO strings into _fake_series and hid a whole dead channel.
    q1, q2 = date(2026, 6, 30), date(2021, 3, 31)
    holes = [("A", q1, "u1"), ("B", q1, "u2"), ("C", q2, "u3")]
    qc = by_quarter(holes)
    assert qc[q1] == 2 and qc[q2] == 1, qc
    assert all(isinstance(q, date) for q in qc), "fixture drifted back to str"
    assert sum(c for q, c in qc.items() if q >= date(2025, 6, 30)) == 2

    # probe must classify by what came BACK, never by the status alone: NSE
    # serves HTML error pages with HTTP 200, which is the trap bhavcopy_date
    # exists for elsewhere in this repo.
    def fake(url, timeout=40):
        return {"u1": (200, b"<?xml version='1.0'?><xbrl>ok</xbrl>"),
                "u2": (200, b"<html>session expired</html>"),
                "u3": (404, b"")}[url]
    rows = probe(holes, n=3, fetcher=fake)
    got = {r["url"]: r for r in rows}
    assert got["u1"]["is_xbrl"] is True, got["u1"]
    assert got["u2"]["is_xbrl"] is False, "200 with an HTML body counted as XBRL"
    assert got["u2"]["status"] == 200, got["u2"]
    assert got["u3"]["status"] == 404 and got["u3"]["bytes"] == 0, got["u3"]
    print("xbrl_probe selftest ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    else:
        n = 40
        for a in sys.argv[1:]:
            if a.startswith("--n="):
                n = int(a.split("=", 1)[1])
            elif a.startswith("-"):
                raise SystemExit(f"unknown flag {a!r}; use --selftest or --n=N")
        report(n=n)
