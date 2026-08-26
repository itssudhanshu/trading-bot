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

import sys as _sys, pathlib as _pl
_sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[1]))
import paths  # noqa: F401  -- puts the source dirs on sys.path
import json
import re
import sys
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

from snapshot import fetch

from paths import ROOT      # one definition; see paths.py
RAW = ROOT / "data" / "fundamentals"
INDEX_URL = ("https://www.nseindia.com/api/corporates-financial-results"
             "?index=equities&symbol={sym}&period=Quarterly")

XBRLI = "{http://www.xbrl.org/2003/instance}"
WANTED = {                       # xbrl local-name -> our field
    "RevenueFromOperations": "revenue",
    "ProfitLossForPeriod": "net_profit",
    "ProfitBeforeTax": "pbt",
    "Expenses": "expenses",
    # Earnings-quality inputs. Feasibility scan on 400 random filings: these
    # four appear in 100% of them; cash-flow statements appear in 0%, so the
    # accruals family stays impossible rather than silently empty.
    "OtherIncome": "other_income",
    "ProfitBeforeExceptionalItemsAndTax": "pbt_before_exceptional",
    "ExceptionalItemsBeforeTax": "exceptional_items",
    "FinanceCosts": "finance_cost",
    # Cash-flow facts exist ONLY in the ANNUAL feed (L79): quarterly files
    # never carry them. Read by build_parsed_annual, never by build_parsed.
    "CashFlowsFromUsedInOperatingActivities": "ocf",
}

# Balance-sheet facts live in INSTANT contexts (one date, no span), which the
# flow parser skips by design. Kept separate so every existing caller and the
# as-of flow logic stay untouched; matched against the filing's own toDate.
INSTANT_WANTED = {
    "TradeReceivables": "receivables",
    "Inventories": "inventories",
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


def parse_instants(data: bytes) -> dict:
    """-> {instant_date_iso: {field: float}} for balance-sheet facts.

    One date, no span; segment-filtered contexts dropped like everywhere else.
    First value wins per (date, field): later duplicates are restatements or
    consolidated/standalone repeats, same rule as the flow parser.
    """
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        return {}
    ctx_date = {}
    for ctx in root.iter(f"{XBRLI}context"):
        if ctx.find(f".//{{http://xbrl.org/2006/xbrldi}}explicitMember") is not None:
            continue
        per = ctx.find(f"{XBRLI}period")
        if per is None:
            continue
        ins = per.find(f"{XBRLI}instant")
        if ins is not None and (ins.text or "").strip():
            ctx_date[ctx.get("id")] = ins.text.strip()
    if not ctx_date:
        return {}
    out = {}
    for el in root.iter():
        field = INSTANT_WANTED.get(el.tag.rsplit("}", 1)[-1])
        if not field or not (el.text or "").strip():
            continue
        date = ctx_date.get(el.get("contextRef"))
        if date is None:
            continue
        try:
            val = float(el.text)
        except ValueError:
            continue
        out.setdefault(date, {}).setdefault(field, val)
    return out


def quarter_figures(meta: dict, xbrl: bytes) -> dict:
    """Figures for the quarter the filing REPORTS, matched on its own dates."""
    want = (_dt(meta.get("fromDate")), _dt(meta.get("toDate")))
    if not all(want):
        return {}
    fig = {}
    for (sd, ed), fields in parse_xbrl(xbrl).items():
        try:
            if (datetime.strptime(sd, "%Y-%m-%d").date(),
                    datetime.strptime(ed, "%Y-%m-%d").date()) == want:
                fig = fields
                break
        except ValueError:
            continue
    if not fig:
        return {}
    # Stocks as at this filing's own quarter close: printed WITH it, never
    # later. Partial coverage by design -- missing concepts stay missing
    # instead of silently falling back to some other date's balance sheet.
    ins = parse_instants(xbrl).get(want[1].isoformat())
    if ins:
        fig.update({k: v for k, v in ins.items() if fig.get(k) is None})
    return fig


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
      <f:OtherIncome contextRef="Q">4</f:OtherIncome>
      <f:FinanceCosts contextRef="YTD">9</f:FinanceCosts>
      <xbrli:context id="FY"><xbrli:period>
        <xbrli:startDate>2024-01-01</xbrli:startDate>
        <xbrli:endDate>2024-12-31</xbrli:endDate></xbrli:period></xbrli:context>
      <xbrli:context id="HY"><xbrli:period>
        <xbrli:startDate>2024-10-01</xbrli:startDate>
        <xbrli:endDate>2024-12-31</xbrli:endDate></xbrli:period></xbrli:context>
      <xbrli:context id="BS"><xbrli:period>
        <xbrli:instant>2024-12-31</xbrli:instant></xbrli:period></xbrli:context>
      <f:Inventories contextRef="BS">55</f:Inventories>
      <f:TradeReceivables contextRef="BS">77</f:TradeReceivables>
      <f:CashFlowsFromUsedInOperatingActivities contextRef="FY">500</f:CashFlowsFromUsedInOperatingActivities>
      <f:CashFlowsFromUsedInOperatingActivities contextRef="HY">120</f:CashFlowsFromUsedInOperatingActivities>
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
    # quality fields follow the SAME quarter-matching discipline
    assert q["other_income"] == 4, q
    assert "finance_cost" not in q, "the YTD finance cost leaked into Q"
    # and the balance sheet attaches only at the filing's own instant
    assert q["inventories"] == 55 and q["receivables"] == 77, q
    assert parse_instants(xml) == {
        "2024-12-31": {"inventories": 55.0, "receivables": 77.0}}
    # annual cash flow: the FULL-YEAR span wins over same-date shorter ones
    fy = pick_fy_span(parse_xbrl(xml), _dt("31-Dec-2024"))
    assert fy["ocf"] == 500, fy
    assert pick_fy_span(parse_xbrl(xml), _dt("31-Mar-2020")) == {}, \
        "a year with no context must yield nothing, never the nearest"
    assert quarter_figures({"fromDate": "01-Jan-2020", "toDate": "31-Mar-2020"},
                           xml) == {}, "a stale balance sheet leaked"

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

    # expected_next_filing: from the company's OWN lag, never a forward calendar
    hist = [{"visible_from": "2024-01-25", "quarter_end": "2023-12-31"},
            {"visible_from": "2024-04-24", "quarter_end": "2024-03-31"},
            {"visible_from": "2024-07-25", "quarter_end": "2024-06-30"}]
    nxt = expected_next_filing(hist, "2024-08-01")
    assert nxt is not None
    # last quarter end 2024-06-30 + 91d + median lag (25d) = 2024-10-24
    assert nxt.isoformat() == "2024-10-24", nxt
    # it must never see filings that were not yet visible
    assert expected_next_filing(hist, "2024-01-26") == expected_next_filing(
        hist[:1] + hist[1:], "2024-01-26"), "used a future filing"
    assert expected_next_filing(hist[:1], "2024-02-01") is None, "needs >=2 filings"
    assert expected_next_filing([], "2024-02-01") is None

    # visible(): the as-of rule again, plus lookback measured in QUARTERS
    rows = [{"visible_from": "2024-01-20", "quarter_end": "2023-12-31", "revenue": 100.0},
            {"visible_from": "2024-04-25", "quarter_end": "2024-03-31", "revenue": 110.0},
            {"visible_from": "2025-01-18", "quarter_end": "2024-12-31", "revenue": 130.0}]
    assert visible(rows, "2024-01-19") is None, "filing seen before it was broadcast"
    assert visible(rows, "2024-01-20")["revenue"] == 100.0
    assert visible(rows, "2025-01-17")["revenue"] == 110.0, "future filing leaked"
    assert visible(rows, "2025-01-18")["revenue"] == 130.0
    assert visible(rows, "2025-01-18", back=1)["revenue"] == 110.0
    assert visible(rows, "2024-01-20", back=1) is None, "history does not reach back"
    print("fundamentals selftest ok")


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


# --- parsed cache + as-of timeline ----------------------------------------

PARSED = RAW / "parsed"
PARSED_ANNUAL = RAW / "parsed_annual"
INDEX_ANNUAL_DIR = RAW / "index_annual"


def build_parsed(symbol, force=False) -> list:
    """-> [{visible_from, quarter_end, revenue, net_profit, ...}] by visibility.

    XBRL is 57 KB of XML per filing and there are ~90,000 of them; parsing on
    demand would dominate every backtest. Parsed once into a compact cache.
    """
    out = PARSED / f"{symbol}.json"
    if out.exists() and not force:
        return json.loads(out.read_text())
    rows = []
    for entry in build_asof(symbol):
        p = _xbrl_path(symbol, entry["quarter_end"])
        if not p.exists():
            continue
        idx = fetch_index(symbol)
        m = next((x for x in idx
                  if _dt(x.get("toDate")) == entry["quarter_end"]), None)
        if not m:
            continue
        fig = quarter_figures(m, p.read_bytes())
        if not fig:
            continue
        rows.append({"visible_from": entry["visible_from"].isoformat(),
                     "quarter_end": entry["quarter_end"].isoformat(), **fig})
    rows.sort(key=lambda r: r["visible_from"])
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rows))
    return rows


def timeline(symbol):
    """Cached as-of timeline, or [] if never built."""
    p = PARSED / f"{symbol}.json"
    return json.loads(p.read_text()) if p.exists() else []


def pick_fy_span(parsed: dict, year_end):
    """-> fields of the LONGEST context ending on `year_end`.

    Annual XBRL carries half-year and quarter spans that share the fiscal
    year-end date; the cash-flow statement belongs to the FULL year. Longest
    span wins is a rule about the construct (annual flow), fixed before any
    spread was measured -- not a tunable.
    """
    best, best_days = {}, -1
    for (sd, ed), fields in parsed.items():
        try:
            d_ed = datetime.strptime(ed, "%Y-%m-%d").date()
            d_sd = datetime.strptime(sd, "%Y-%m-%d").date()
        except ValueError:
            continue
        if d_ed != year_end:
            continue
        days = (d_ed - d_sd).days
        if days > best_days:
            best, best_days = fields, days
    return best


def build_parsed_annual(symbol, force=False) -> list:
    """Annual-filing timeline -> [{visible_from, year_end, ocf, ...}].

    Reads bytes harvested into xbrl_annual/ plus the broadcast-dated index
    written by backfill_annual.py. Deliberately SEPARATE from the quarterly
    timeline: features_asof counts rows in QUARTERS, and interleaving annual
    rows would silently corrupt every growth feature built on that arithmetic.
    """
    out = PARSED_ANNUAL / f"{symbol}.json"
    if out.exists() and not force:
        return json.loads(out.read_text())
    idx_path = INDEX_ANNUAL_DIR / f"{symbol}.json"
    if not idx_path.exists():
        return []
    rows = []
    for m in json.loads(idx_path.read_text()):
        p = RAW / "xbrl_annual" / symbol / f"{m['quarter_end']}.xml"
        if not p.exists():
            continue
        qe = _dt(m["quarter_end"])
        if qe is None:
            continue
        try:
            fig = pick_fy_span(parse_xbrl(p.read_bytes()), qe)
        except Exception:
            continue
        if fig.get("ocf") is None:
            continue
        row = {"visible_from": m["visible_from"], "year_end": m["quarter_end"],
               "ocf": fig["ocf"]}
        for k in ("revenue", "net_profit"):
            if fig.get(k) is not None:
                row[k] = fig[k]
        rows.append(row)
    rows.sort(key=lambda r: r["visible_from"])
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rows))
    return rows


def timeline_annual(symbol):
    """Cached ANNUAL as-of timeline, or [] if never built."""
    p = PARSED_ANNUAL / f"{symbol}.json"
    return json.loads(p.read_text()) if p.exists() else []


def expected_next_filing(rows, day_iso):
    """Predicted date of the NEXT results, from this company's own past pattern.

    A blackout needs to know results are coming, and the only honest source is
    history: the last visible quarter end, plus one quarter, plus THIS company's
    median publication lag. NSE does publish forward board-meeting calendars,
    but using one in a backtest is lookahead -- on any past date I would be
    reading a schedule that had not been announced.

    Companies differ enormously: 25 days at p10, 71 at p90 across 91,843
    filings. A universe-wide constant would blackout the wrong window for most
    names, so the lag is per-company.
    """
    from datetime import date as _date, timedelta as _td
    seen = [r for r in rows if r["visible_from"] <= day_iso]
    if len(seen) < 2:
        return None
    lags = []
    for r in seen:
        qe = _date.fromisoformat(r["quarter_end"])
        vf = _date.fromisoformat(r["visible_from"])
        lags.append((vf - qe).days)
    lags.sort()
    median_lag = lags[len(lags) // 2]
    last_qe = _date.fromisoformat(seen[-1]["quarter_end"])
    return last_qe + _td(days=91) + _td(days=median_lag)


def growth_yoy(rows, day_iso, back=0):
    """Revenue growth vs the same quarter a year earlier, as a fraction.

    `back` shifts the whole comparison earlier, so back=1 gives LAST quarter's
    YoY growth -- which is what acceleration needs.
    """
    now, then = visible(rows, day_iso, back=back), visible(rows, day_iso, back=back + 4)
    if not now or not then:
        return None
    a, b = now.get("revenue"), then.get("revenue")
    if a is None or not b or b <= 0:
        return None
    return a / b - 1.0


def growth_accel(rows, day_iso):
    """Change in YoY growth rate: is the company getting better FASTER?

    A threshold on growth ("grew >10%") is a static quality check and most
    companies pass it in a good year. The change in growth rate is the momentum
    signal -- and it is what distinguishes a business accelerating from one
    merely large.
    """
    now, prev = growth_yoy(rows, day_iso), growth_yoy(rows, day_iso, back=1)
    return None if now is None or prev is None else now - prev


def visible(rows, day_iso, back=0):
    """The filing visible on `day_iso`, or `back` quarters earlier. None if the
    history does not reach that far -- never the nearest available."""
    seen = [r for r in rows if r["visible_from"] <= day_iso]
    if len(seen) <= back:
        return None
    return seen[-1 - back]


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    elif "--parse" in sys.argv:
        import features
        corpus = features.load_corpus()
        n = ok = 0
        for sym in sorted(corpus):
            n += 1
            if build_parsed(sym):
                ok += 1
            if n % 250 == 0:
                print(f"  parsed {n}  with data {ok}", flush=True)
        print(f"done: {ok}/{n} symbols have parsed fundamentals")
    elif "--parse-annual" in sys.argv:
        import features
        corpus = features.load_corpus()
        n = ok = 0
        for sym in sorted(corpus):
            n += 1
            if build_parsed_annual(sym):
                ok += 1
            if n % 250 == 0:
                print(f"  annual {n}  with ocf {ok}", flush=True)
        print(f"done: {ok}/{n} symbols have annual ocf timelines")
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


# ---------------------------------------------------------------- as-of features
def features_asof(rows, day_iso):
    """-> company-momentum features visible on `day_iso`, or {} if not enough.

    Only filings whose `visible_from` has passed are used. A quarter that ended
    before this date but had not been PUBLISHED is invisible -- median lag is 42
    days, p90 is 71, so using quarter_end here would hand the backtest results
    it could not have read. That single distinction is the difference between a
    fundamentals test and a lookahead test.
    """
    seen = [r for r in rows if r.get("visible_from") and r["visible_from"] <= day_iso]
    if len(seen) < 5:
        return {}                       # need this quarter and the year-ago one
    seen.sort(key=lambda r: r["visible_from"])
    cur, yr = seen[-1], seen[-5]        # 4 quarters back
    out = {}

    def growth(a, b):
        if a is None or b is None or b == 0:
            return None
        return (a - b) / abs(b) * 100

    out["rev_growth"] = growth(cur.get("revenue"), yr.get("revenue"))
    out["profit_growth"] = growth(cur.get("net_profit"), yr.get("net_profit"))
    rev, np_ = cur.get("revenue"), cur.get("net_profit")
    out["margin"] = (np_ / rev * 100) if rev and np_ is not None and rev != 0 else None
    prev_rev, prev_np = yr.get("revenue"), yr.get("net_profit")
    prev_margin = ((prev_np / prev_rev * 100)
                   if prev_rev and prev_np is not None and prev_rev != 0 else None)
    out["margin_change"] = (out["margin"] - prev_margin
                            if out["margin"] is not None and prev_margin is not None
                            else None)
    return {k: v for k, v in out.items() if v is not None}


def _selftest_features():
    rows = [{"visible_from": f"2024-0{i}-01", "quarter_end": f"2023-0{i}-01",
             "revenue": 1000.0 * i, "net_profit": 100.0 * i} for i in range(1, 6)]
    f = features_asof(rows, "2024-06-01")
    assert abs(f["rev_growth"] - 400.0) < 1e-6, f      # 5000 vs 1000
    assert abs(f["margin"] - 10.0) < 1e-6, f
    assert abs(f["margin_change"]) < 1e-6, f           # margin flat at 10%
    # a filing not yet published must be invisible
    assert features_asof(rows, "2024-01-15") == {}, "used an unpublished filing"
    later = features_asof(rows, "2024-04-15")
    assert later == {}, "needs 5 visible filings, only 4 by then"
    print("fundamentals.features_asof selftest ok")
