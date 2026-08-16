#!/usr/bin/env python3
"""Point-in-time fundamentals from NSE quarterly filings.

THE ENTIRE POINT IS THE AS-OF DATE. A filing describes a quarter that ended
weeks before it was published -- RELIANCE's Q3 FY25 covers Oct-Dec 2024 and was
broadcast 16-Jan-2025, a 16-day gap. Using those figures on 01-Jan-2025 because
"it is the Q3 quarter" is lookahead: trading on numbers nobody had yet. It does
not announce itself; the backtest simply gets better.

NSE gives the publication timestamp explicitly (`broadCastDate`), so the rule is
mechanical: a filing becomes visible on the day it was broadcast, never on the
day its quarter ended.

Two-stage, because the cheap stage carries the dates:
  1. metadata index per symbol  -- dates, flags, XBRL link. No figures at all.
  2. XBRL per filing            -- the actual numbers, ~57 KB each.

XBRL numbers are context-scoped: the SAME tag carries the quarter, the
year-to-date and prior-year comparatives. RELIANCE Q3 revenue appears as both
128,260 cr (quarter) and 396,645 cr (nine-month YTD). Selecting the wrong
context yields a plausible wrong number, so periods are matched explicitly
against the filing's own fromDate/toDate rather than by tag order.
"""
import json
import re
import sys
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

from snapshot import fetch

ROOT = Path(__file__).resolve().parent
RAW = ROOT / "data" / "fundamentals"
INDEX_URL = ("https://www.nseindia.com/api/corporates-financial-results"
             "?index=equities&symbol={sym}&period=Quarterly")

XBRLI = "{http://www.xbrl.org/2003/instance}"
WANTED = {                       # xbrl local-name -> our field
    "RevenueFromOperations": "revenue",
    "ProfitLossForPeriod": "net_profit",
    "ProfitBeforeTax": "pbt",
    "Expenses": "expenses",
}


def _dt(s):
    """NSE mixes '16-Jan-2025 20:20:21' and '16-Jan-2025 20:20'."""
    if not s:
        return None
    for fmt in ("%d-%b-%Y %H:%M:%S", "%d-%b-%Y %H:%M", "%d-%b-%Y"):
        try:
            return datetime.strptime(s.strip(), fmt).date()
        except ValueError:
            continue
    return None


def fetch_index(symbol, force=False):
    """Metadata for every quarterly filing. Raw bytes stored, parsed later."""
    out = RAW / "index" / f"{symbol}.json"
    if out.exists() and not force:
        return json.loads(out.read_text())
    status, body = fetch(INDEX_URL.format(sym=symbol), timeout=30)
    if status != 200 or not body:
        return []
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(body)
    return json.loads(body)


def parse_xbrl(data: bytes) -> dict:
    """-> {(start_date, end_date): {field: float}} for every period context.

    Contexts carrying an xbrldi segment are dropped: those are per-segment
    breakdowns, not the consolidated line item.
    """
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        return {}

    periods = {}
    for ctx in root.iter(f"{XBRLI}context"):
        cid = ctx.get("id")
        if ctx.find(f".//{{http://xbrl.org/2006/xbrldi}}explicitMember") is not None:
            continue                                  # segment breakdown
        per = ctx.find(f"{XBRLI}period")
        if per is None:
            continue
        sd, ed = per.find(f"{XBRLI}startDate"), per.find(f"{XBRLI}endDate")
        if sd is None or ed is None:
            continue                                  # instant context (balance sheet)
        periods[cid] = (sd.text, ed.text)

    out = {}
    for el in root.iter():
        local = el.tag.rsplit("}", 1)[-1]
        field = WANTED.get(local)
        if not field:
            continue
        span = periods.get(el.get("contextRef"))
        if span is None or not (el.text or "").strip():
            continue
        try:
            val = float(el.text)
        except ValueError:
            continue
        # First value wins for a given (period, field): later ones are
        # restatements or duplicate consolidated/standalone blocks.
        out.setdefault(span, {}).setdefault(field, val)
    return out


def quarter_figures(meta: dict, xbrl: bytes) -> dict:
    """Figures for the quarter the filing REPORTS, matched on its own dates."""
    want = (_dt(meta.get("fromDate")), _dt(meta.get("toDate")))
    if not all(want):
        return {}
    for (sd, ed), fields in parse_xbrl(xbrl).items():
        try:
            if (datetime.strptime(sd, "%Y-%m-%d").date(),
                    datetime.strptime(ed, "%Y-%m-%d").date()) == want:
                return fields
        except ValueError:
            continue
    return {}


# Consolidated vs standalone: NSE files BOTH for the same quarter, same
# broadcast date. Consolidated includes subsidiaries and is the economically
# meaningful figure for a group; standalone is the parent alone. Mixing them
# across a symbol's history silently changes what is being measured.
#
# Rule: prefer Consolidated, fall back to standalone. FIXED HERE BEFORE any
# predicate reads it -- choosing after seeing which backtests better is the
# wrong order, and it is the kind of choice that never looks like a choice
# afterwards.
PREFER_CONSOLIDATED = True


def build_asof(symbol, force=False) -> list:
    """-> filings sorted by broadcast date, one per quarter, as-of dated.

    Each entry: {visible_from, quarter_end, consolidated, xbrl}. `visible_from`
    is the BROADCAST date -- the first day the numbers existed publicly.
    """
    idx = fetch_index(symbol, force=force)
    by_quarter = {}
    for m in idx:
        bc, qe = _dt(m.get("broadCastDate")), _dt(m.get("toDate"))
        if not bc or not qe:
            continue
        cons = (m.get("consolidated") or "").strip().lower().startswith("consolidated")
        cur = by_quarter.get(qe)
        # Prefer consolidated; among equals prefer the EARLIER broadcast, since
        # a later filing for the same quarter is a revision and was not visible
        # on the original date.
        better = (cur is None
                  or (cons and not cur["consolidated"])
                  or (cons == cur["consolidated"] and bc < cur["visible_from"]))
        if better:
            by_quarter[qe] = {"visible_from": bc, "quarter_end": qe,
                              "consolidated": cons, "xbrl": m.get("xbrl")}
    return sorted(by_quarter.values(), key=lambda r: r["visible_from"])


def as_of(filings: list, day):
    """Most recent filing VISIBLE on `day`. None if nothing was published yet.

    Strictly `visible_from <= day`. Returning the nearest filing regardless of
    direction is the lookahead bug this module exists to prevent.
    """
    seen = [f for f in filings if f["visible_from"] <= day]
    return seen[-1] if seen else None


def _selftest():
    import tempfile

    # as-of dating: the property everything else depends on
    assert _dt("16-Jan-2025 20:20:21").isoformat() == "2025-01-16"
    assert _dt("16-Jan-2025 20:20").isoformat() == "2025-01-16"
    assert _dt("") is None and _dt("garbage") is None

    xml = b"""<?xml version="1.0"?>
    <xbrl xmlns:xbrli="http://www.xbrl.org/2003/instance"
          xmlns:xbrldi="http://xbrl.org/2006/xbrldi"
          xmlns:f="http://x">
      <xbrli:context id="Q"><xbrli:period>
        <xbrli:startDate>2024-10-01</xbrli:startDate>
        <xbrli:endDate>2024-12-31</xbrli:endDate></xbrli:period></xbrli:context>
      <xbrli:context id="YTD"><xbrli:period>
        <xbrli:startDate>2024-04-01</xbrli:startDate>
        <xbrli:endDate>2024-12-31</xbrli:endDate></xbrli:period></xbrli:context>
      <xbrli:context id="SEG"><xbrli:period>
        <xbrli:startDate>2024-10-01</xbrli:startDate>
        <xbrli:endDate>2024-12-31</xbrli:endDate></xbrli:period>
        <xbrldi:explicitMember>seg</xbrldi:explicitMember></xbrli:context>
      <f:RevenueFromOperations contextRef="Q">100</f:RevenueFromOperations>
      <f:RevenueFromOperations contextRef="YTD">300</f:RevenueFromOperations>
      <f:RevenueFromOperations contextRef="SEG">7</f:RevenueFromOperations>
      <f:ProfitLossForPeriod contextRef="Q">10</f:ProfitLossForPeriod>
    </xbrl>"""

    p = parse_xbrl(xml)
    assert ("2024-10-01", "2024-12-31") in p and ("2024-04-01", "2024-12-31") in p
    assert p[("2024-10-01", "2024-12-31")]["revenue"] == 100
    assert p[("2024-04-01", "2024-12-31")]["revenue"] == 300, "YTD must stay separate"
    assert all("7" not in str(v) for v in p.values()), "segment context leaked in"

    # the quarter is selected by the filing's OWN dates, never by tag order
    meta = {"fromDate": "01-Oct-2024", "toDate": "31-Dec-2024"}
    q = quarter_figures(meta, xml)
    assert q["revenue"] == 100, f"picked the wrong context: {q}"
    assert q["net_profit"] == 10

    # a filing whose dates match nothing yields nothing, never a stale guess
    assert quarter_figures({"fromDate": "01-Jan-2020", "toDate": "31-Mar-2020"}, xml) == {}
    assert parse_xbrl(b"not xml") == {}

    # --- as-of dating: nothing from the future, ever ----------------------
    from datetime import date as _d
    fil = [{"visible_from": _d(2024, 10, 14), "quarter_end": _d(2024, 9, 30),
            "consolidated": True, "xbrl": "a"},
           {"visible_from": _d(2025, 1, 16), "quarter_end": _d(2024, 12, 31),
            "consolidated": True, "xbrl": "b"}]
    assert as_of(fil, _d(2024, 10, 13)) is None, "returned a filing before it existed"
    assert as_of(fil, _d(2024, 10, 14))["xbrl"] == "a", "broadcast day itself must count"
    assert as_of(fil, _d(2025, 1, 15))["xbrl"] == "a", "Q3 leaked before broadcast"
    assert as_of(fil, _d(2025, 1, 16))["xbrl"] == "b"
    assert as_of([], _d(2025, 1, 1)) is None
    print("fundamentals selftest ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    elif "--backfill" in sys.argv:
        import features
        from datetime import date
        corpus = features.load_corpus()
        syms = sorted(corpus)
        print(f"backfilling fundamentals for {len(syms)} symbols")
        backfill(syms, start=date(2019, 1, 1), end=date.today())
    else:
        sym = sys.argv[1] if len(sys.argv) > 1 else "RELIANCE"
        idx = fetch_index(sym)
        print(f"{sym}: {len(idx)} quarterly filings")
        for m in idx[:3]:
            bc = _dt(m.get("broadCastDate"))
            print(f"  {m['fromDate']} -> {m['toDate']}  broadcast {bc}  "
                  f"lag {(bc - _dt(m['toDate'])).days}d  {m.get('consolidated','')}")


# --- bulk backfill ---------------------------------------------------------

def _xbrl_path(symbol, quarter_end):
    return RAW / "xbrl" / symbol / f"{quarter_end}.xml"


def backfill(symbols, start=None, end=None, workers=6, log=print):
    """Metadata for every symbol, then XBRL for filings visible in the window.

    Resumable: anything already on disk is skipped, so a killed run costs only
    what it had not finished. Raw bytes are stored and parsed later -- a parser
    bug must never cost a refetch of 70,000 files.
    """
    import threading
    from concurrent.futures import ThreadPoolExecutor
    lock = threading.Lock()
    tally = {"idx_ok": 0, "idx_fail": 0, "xbrl_ok": 0, "xbrl_have": 0, "xbrl_fail": 0}

    def do_index(sym):
        try:
            got = bool(fetch_index(sym))
        except Exception:
            got = False
        with lock:
            tally["idx_ok" if got else "idx_fail"] += 1
            n = tally["idx_ok"] + tally["idx_fail"]
            if n % 100 == 0:
                log(f"  index {n}/{len(symbols)}  ok={tally['idx_ok']} fail={tally['idx_fail']}")

    log(f"stage 1: metadata for {len(symbols)} symbols")
    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(do_index, symbols))
    log(f"  done: ok={tally['idx_ok']} fail={tally['idx_fail']}")

    jobs = []
    for sym in symbols:
        try:
            for f in build_asof(sym):
                if start and f["visible_from"] < start:
                    continue
                if end and f["visible_from"] > end:
                    continue
                if f.get("xbrl"):
                    jobs.append((sym, f["quarter_end"], f["xbrl"]))
        except Exception:
            continue

    log(f"stage 2: {len(jobs)} XBRL filings in window")

    def do_xbrl(job):
        sym, qe, url = job
        p = _xbrl_path(sym, qe)
        if p.exists():
            with lock:
                tally["xbrl_have"] += 1
            return
        status, body = fetch(url, timeout=40)
        ok = status == 200 and body and b"xbrl" in body[:2000].lower()
        if ok:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(body)
        with lock:
            tally["xbrl_ok" if ok else "xbrl_fail"] += 1
            n = tally["xbrl_ok"] + tally["xbrl_fail"] + tally["xbrl_have"]
            if n % 500 == 0:
                log(f"  xbrl {n}/{len(jobs)}  ok={tally['xbrl_ok']} "
                    f"have={tally['xbrl_have']} fail={tally['xbrl_fail']}")

    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(do_xbrl, jobs))
    log(f"  done: {tally}")
    return tally
