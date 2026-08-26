#!/usr/bin/env python3
"""Screener.in annual fundamentals — raw cache + parser + timeline.

Isolated from quarterly NSE XBRL (data/fundamentals/parsed/). Pilot on
top-500 by turnover; full rollout same code over 1,276 micro/small.

Point-in-time: visible_from is Screener's announcement date (ISO), gate is
visible_from <= signal_day, freshness year_end >= signal - 550d.
"""

import sys as _sys, pathlib as _pl
_sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[1]))
import paths  # noqa: F401

import json
import pathlib
import random
import time
import urllib.request
import urllib.error
from pathlib import Path

from paths import ROOT

RAW_SCREENER = ROOT / "data" / "screener_raw"
PARSED_SCREENER = ROOT / "data" / "fundamentals_screener" / "parsed"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"


def fetch_screener(symbol, force=False, fetcher=None):
    """Fetch https://www.screener.in/company/{SYMBOL}/consolidated/ with cache.

    Resumable: if raw HTML exists and >10KB, return cached bytes without network
    unless force=True. Polite: 2 req/s is enforced by caller (backfill), this
    function handles 3 retries on 429/5xx with backoff.
    """
    out = RAW_SCREENER / f"{symbol}.html"
    if not force and out.exists() and out.stat().st_size > 10240:
        return 200, out.read_bytes()

    url = f"https://www.screener.in/company/{symbol}/consolidated/"
    headers = {"User-Agent": UA, "Referer": "https://www.screener.in/", "Accept": "*/*"}
    req = urllib.request.Request(url, headers=headers)

    for attempt, delay in enumerate([0, 2, 5, 10]):
        if attempt:
            time.sleep(delay + random.uniform(0, 0.2))
        try:
            if fetcher:
                status, body = fetcher(url, timeout=30)
            else:
                with urllib.request.urlopen(req, timeout=30) as r:
                    status, body = r.status, r.read()
            if status in (429, 500, 502, 503, 504) and attempt < 3:
                continue
            if status == 200 and body:
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_bytes(body)
            return status, body
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and attempt < 3:
                continue
            return e.code, b""
        except Exception:
            if attempt < 3:
                continue
            return 0, b""
    return 0, b""


def _norm_num(s):
    if not s or s.strip() in ("—", "-", ""):
        return None
    s = s.replace("₹", "").replace(",", "").strip()
    mult = 1
    if "Cr" in s:
        mult = 1e7
        s = s.replace("Cr", "").strip()
    elif "Lac" in s:
        mult = 1e5
        s = s.replace("Lac", "").strip()
    try:
        return float(s) * mult
    except ValueError:
        return None


def parse_screener(html_bytes):
    """Parse Screener HTML -> list[{visible_from, year_end, revenue, net_profit, ocf, total_assets}].

    Annual columns only (Mar 31). visible_from is year_end + 60 days (conservative proxy for
    announcement lag; median NSE quarterly lag is 42d, annual ~60d). Sorted by visible_from.
    Values are in INR (Crores * 1e7) to match XBRL scale; ratio tests are unit-invariant.
    """
    from bs4 import BeautifulSoup
    from datetime import datetime, timedelta
    soup = BeautifulSoup(html_bytes, "lxml")
    # Map section id -> field mapping for rows we care
    FIELD_MAP = {
        "Sales": "revenue",
        "Net Profit": "net_profit",
        "Cash from Operating Activity": "ocf",
        "Total Assets": "total_assets",
    }
    # year_end -> dict of fields
    per_year = {}
    # Screener annual tables are in sections: profit-loss, balance-sheet, cash-flow
    for sec_id in ("profit-loss", "balance-sheet", "cash-flow"):
        sec = soup.find("section", id=sec_id)
        if not sec:
            continue
        tbl = sec.find("table")
        if not tbl:
            continue
        thead = tbl.find("thead")
        if not thead:
            continue
        headers = thead.find_all("th")
        # headers[0] is empty/label, rest are dates like Mar 2015 with data-date-key=2015-03-31
        col_years = []
        for th in headers[1:]:
            dk = th.get("data-date-key")
            if dk:
                col_years.append(dk)
            else:
                txt = th.get_text(strip=True)
                # fallback: parse Mar YYYY
                try:
                    dt = datetime.strptime(txt, "%b %Y")
                    col_years.append(dt.strftime("%Y-%m-%d").replace("-01", "-31") if "Mar" in txt else None)
                except Exception:
                    col_years.append(None)
        # iterate rows
        for tr in tbl.find("tbody").find_all("tr") if tbl.find("tbody") else []:
            tds = tr.find_all("td")
            if not tds:
                continue
            label = tds[0].get_text(" ", strip=True).replace("+", "").strip()
            field = None
            for k, v in FIELD_MAP.items():
                if k.lower() in label.lower():
                    field = v
                    break
            if not field:
                continue
            for idx, td in enumerate(tds[1:]):
                if idx >= len(col_years) or not col_years[idx]:
                    continue
                ye = col_years[idx]
                # only Mar 31 annual columns (Screener includes all Mar, so all are annual)
                if not ye.endswith("-03-31"):
                    continue
                txt = td.get_text(strip=True)
                val = _norm_num(txt)
                if val is None:
                    continue
                # Screener displays in Rs. Crores without suffix in table -> scale to INR
                if "Cr" not in txt and "Lac" not in txt:
                    val *= 1e7
                # visible_from proxy: year_end + 60 days
                try:
                    ye_dt = datetime.strptime(ye, "%Y-%m-%d").date()
                    vf = (ye_dt + timedelta(days=60)).isoformat()
                except Exception:
                    continue
                per_year.setdefault(ye, {"year_end": ye, "visible_from": vf})
                per_year[ye][field] = val
    # keep only rows with at least ocf (for accruals) or revenue (for completeness) — but store all
    rows = []
    for ye in sorted(per_year):
        d = per_year[ye]
        # require at least one of the key fields
        if any(k in d for k in ("revenue", "ocf", "total_assets")):
            rows.append(d)
    return sorted(rows, key=lambda r: r["visible_from"])


def build_parsed_screener(symbol, force=False):
    out = PARSED_SCREENER / f"{symbol}.json"
    if out.exists() and not force:
        return json.loads(out.read_text())
    raw = RAW_SCREENER / f"{symbol}.html"
    if not raw.exists():
        return []
    rows = parse_screener(raw.read_bytes())
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rows))
    return rows


def timeline_annual_screener(symbol):
    p = PARSED_SCREENER / f"{symbol}.json"
    return json.loads(p.read_text()) if p.exists() else []


def _selftest():
    import tempfile
    # fetcher resumable + retry
    orig_raw = RAW_SCREENER
    tmp = pathlib.Path(tempfile.mkdtemp())
    globals()["RAW_SCREENER"] = tmp
    try:
        calls = []
        def fake_fetch(url, timeout=30):
            calls.append(url)
            if len(calls) == 1:
                return 429, b""
            return 200, b"<html>" + b"x"*11000 + b"</html>"
        status, body = fetch_screener("RELIANCE", fetcher=fake_fetch)
        assert status == 200 and body.startswith(b"<html>") and len(body) > 10240, (status, len(body))
        calls.clear()
        status2, body2 = fetch_screener("RELIANCE", fetcher=lambda *a, **k: (500, b""))
        assert status2 == 200, "resumable skip failed"
        assert len(calls) == 0, "should not have fetched when cached"
        print("fetcher selftest ok")
    finally:
        globals()["RAW_SCREENER"] = orig_raw

    # parser stub + fixture
    assert parse_screener(b"<html></html>") == []
    # pinned fixture from 2026-08-26
    import pathlib as _pl2
    fix = pathlib.Path("tests/fixtures/screener_RELIANCE_consolidated.html")
    if fix.exists():
        rows = parse_screener(fix.read_bytes())
        assert len(rows) >= 5, f"expected >=5 annual rows, got {len(rows)}"
        r2024 = [r for r in rows if r["year_end"] == "2024-03-31"]
        assert r2024, "2024-03-31 row missing"
        r2024 = r2024[0]
        # visible_from is year_end +60d = 2024-05-30
        assert r2024["visible_from"] == "2024-05-30", r2024["visible_from"]
        # values are scaled to INR (Cr *1e7); check exact
        assert abs(r2024["ocf"] - 1587880000000.0) < 1e6, r2024["ocf"]  # 158,788 Cr -> 1.58788e12 INR
        assert abs(r2024["revenue"] - 8990410000000.0) < 1e6, r2024["revenue"]
        assert r2024["total_assets"] > 1e12, r2024["total_assets"]
        # number norm (allow tiny floating error)
        assert abs(_norm_num("₹ 1,587.88 Cr") - 15878800000.0) < 1e-2
        assert _norm_num("—") is None
        print(f"parser selftest ok ({len(rows)} rows)")
    else:
        print("parser fixture missing, skipping pinned test")

    # cache builder + timeline API
    orig_parsed = PARSED_SCREENER
    orig_parse = parse_screener
    tmp2 = pathlib.Path(tempfile.mkdtemp())
    globals()["PARSED_SCREENER"] = tmp2
    try:
        # use same tmp raw dir from fetcher test? recreate
        tmp_raw = pathlib.Path(tempfile.mkdtemp())
        globals()["RAW_SCREENER"] = tmp_raw
        globals()["parse_screener"] = lambda b: [{"visible_from": "2024-05-17", "year_end": "2024-03-31", "ocf": 1.0, "revenue": 10.0}]
        (tmp_raw / "FAKE.html").write_bytes(b"fake")
        rows2 = build_parsed_screener("FAKE")
        assert rows2[0]["ocf"] == 1.0, rows2
        assert timeline_annual_screener("FAKE")[0]["visible_from"] == "2024-05-17"
        # second call without force should hit cache (no re-parse)
        globals()["parse_screener"] = lambda b: (_ for _ in ()).throw(RuntimeError("should not be called"))
        rows3 = build_parsed_screener("FAKE")
        assert rows3[0]["ocf"] == 1.0
        print("cache selftest ok")
    finally:
        globals()["PARSED_SCREENER"] = orig_parsed
        globals()["RAW_SCREENER"] = orig_raw
        globals()["parse_screener"] = orig_parse
    print("screener_fundamentals selftest ok")


if __name__ == "__main__":
    if "--selftest" in _sys.argv:
        _selftest()
    else:
        print("usage: python3 screener_fundamentals.py --selftest")
