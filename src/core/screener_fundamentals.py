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

    Annual columns only. visible_from is Ann. Date column, year_end is Mar 31 YYYY of header.
    Sorted by visible_from ascending.
    """
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html_bytes, "lxml")
    # This is a stub for Task 1 — full implementation in Task 2
    return []


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

    # parser stub
    assert parse_screener(b"<html></html>") == []
    print("screener_fundamentals selftest ok")


if __name__ == "__main__":
    if "--selftest" in _sys.argv:
        _selftest()
    else:
        print("usage: python3 screener_fundamentals.py --selftest")
