#!/usr/bin/env python3
"""Backfill historical bhavcopy (daily OHLCV + delivery %).

Only bhavcopy is retrievable historically. ASM/GSM/F&O-ban are published for
today only, so backfilled dates carry no surveillance state -- universe.load()
marks those bars surveillance_known=False.

Writes into the same data/raw/<date>/ layout snapshot.py uses, so universe.load()
reads backfilled and live-collected days identically.

    ./backfill.py --years 4
    ./backfill.py --from 2024-01-01 --to 2024-12-31
"""

import sys as _sys, pathlib as _pl
_sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[1]))
import paths  # noqa: F401  -- puts the source dirs on sys.path
import argparse
import json
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path

from snapshot import RAW, SOURCES, bhavcopy_date, fetch

# NSE's delivery bhavcopy archive starts here. Verified by probe: 2019-09-16
# returns 404, 2019-10-01 returns 200. Requests before this date 404 for every
# session, and fetch_day would record each one as a holiday -- silently poisoning
# holidays.json with hundreds of real trading days and breaking gap detection.
ARCHIVE_START = date(2019, 10, 1)

HOLIDAYS = RAW.parent / "holidays.json"
WORKERS = 6          # polite against nsearchives; it is a static file host
_lock = threading.Lock()


def weekdays(start: date, end: date):
    d = start
    while d <= end:
        if d.weekday() < 5:
            yield d
        d += timedelta(days=1)


def _load_holidays() -> set:
    if HOLIDAYS.exists():
        return set(json.loads(HOLIDAYS.read_text()))
    return set()


def _save_holidays(days: set):
    HOLIDAYS.parent.mkdir(parents=True, exist_ok=True)
    HOLIDAYS.write_text(json.dumps(sorted(days), indent=0))


def have(day: date) -> bool:
    return (RAW / day.isoformat() / "bhavcopy_delivery.csv").exists()


def fetch_day(day: date) -> str:
    """-> 'have' | 'ok' | 'holiday' | 'fail'. Never raises."""
    if have(day):
        return "have"
    url = SOURCES["bhavcopy_delivery"][0].format(d=day)
    status, body = fetch(url, timeout=40)
    if status == 404:
        return "holiday"                     # market shut; not an error
    if status == 200 and body:
        # NSE serves the previous session's file with 200 on holidays. Trust the
        # content, never the URL -- otherwise the prior day is duplicated here.
        if bhavcopy_date(body) != day:
            return "holiday"
        out = RAW / day.isoformat()
        out.mkdir(parents=True, exist_ok=True)
        (out / "bhavcopy_delivery.csv").write_bytes(body)
        return "ok"
    return "fail"


def backfill(start: date, end: date, workers=WORKERS) -> dict:
    if start < ARCHIVE_START:
        print(f"clamping start {start} -> {ARCHIVE_START} (archive floor)")
        start = ARCHIVE_START
    holidays = _load_holidays()
    todo = [d for d in weekdays(start, end)
            if d.isoformat() not in holidays and not have(d)]
    tally = {"have": 0, "ok": 0, "holiday": 0, "fail": 0}
    tally["have"] = sum(1 for d in weekdays(start, end) if have(d))
    failed = []

    def work(d):
        r = fetch_day(d)
        with _lock:
            tally[r] += 1
            if r == "holiday":
                holidays.add(d.isoformat())
            elif r == "fail":
                failed.append(d)
            done = tally["ok"] + tally["holiday"] + tally["fail"]
            if done % 25 == 0:
                print(f"  {done}/{len(todo)}  ok={tally['ok']} "
                      f"holiday={tally['holiday']} fail={tally['fail']}", flush=True)

    if todo:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            list(pool.map(work, todo))
    _save_holidays(holidays)
    tally["failed_dates"] = [d.isoformat() for d in sorted(failed)]
    return tally


def _selftest():
    import tempfile
    import backfill as B
    original_raw, original_hol = B.RAW, B.HOLIDAYS
    calls = []

    def fake(url, **kw):
        calls.append(url)
        # 15 Aug 2025 (Independence Day): NSE 200s with the PRIOR session's file.
        # 14 Aug 2025: honest 404. Both must be classified as holidays.
        if "15082025" in url:
            return 200, b"SYMBOL, SERIES, DATE1\nX, EQ, 14-Aug-2025\n"
        if "14082025" in url:
            return 404, b""
        d = url.split("_")[-1].removesuffix(".csv")
        stamp = f"{d[:2]}-{['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'][int(d[2:4])-1]}-{d[4:]}"
        return 200, f"SYMBOL, SERIES, DATE1\nX, EQ, {stamp}\n".encode()

    try:
        with tempfile.TemporaryDirectory() as td:
            B.RAW = Path(td) / "raw"
            B.HOLIDAYS = Path(td) / "holidays.json"
            B.fetch = fake

            t = B.backfill(date(2025, 8, 13), date(2025, 8, 18), workers=2)
            # 13,14,15,18 are weekdays; 16-17 is the weekend
            assert t["ok"] == 2 and t["holiday"] == 2, t   # 14 = 404, 15 = stale 200
            assert t["fail"] == 0, t
            assert not (B.RAW / "2025-08-16").exists(), "weekend must not be fetched"
            assert not (B.RAW / "2025-08-15" / "bhavcopy_delivery.csv").exists(), \
                "stale 200 must not be stored -- that duplicates the prior session"
            assert json.loads(B.HOLIDAYS.read_text()) == ["2025-08-14", "2025-08-15"]

            n = len(calls)
            t2 = B.backfill(date(2025, 8, 13), date(2025, 8, 18), workers=2)
            assert len(calls) == n, "resume refetched existing days"
            assert t2["have"] == 2, t2

            # requests before the archive floor must be clamped, never fetched --
            # otherwise every pre-archive session is recorded as a holiday
            before = len(calls)
            B.backfill(date(2015, 1, 5), date(2015, 1, 9), workers=2)
            assert len(calls) == before, "fetched below the archive floor"
            hols = set(json.loads(B.HOLIDAYS.read_text()))
            assert not any(h.startswith("2015") for h in hols), \
                f"pre-archive dates poisoned the holiday cache: {hols}"
    finally:
        B.RAW, B.HOLIDAYS, B.fetch = original_raw, original_hol, fetch
    print("backfill selftest ok")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--years", type=float, default=4.0)
    ap.add_argument("--from", dest="start", type=date.fromisoformat)
    ap.add_argument("--to", dest="end", type=date.fromisoformat, default=date.today())
    ap.add_argument("--workers", type=int, default=WORKERS)
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()

    if a.selftest:
        return _selftest()

    start = a.start or (a.end - timedelta(days=int(365.25 * a.years)))
    print(f"backfill {start} -> {a.end}")
    t = backfill(start, a.end, a.workers)
    print(f"\ndone: {t['ok']} fetched, {t['have']} already had, "
          f"{t['holiday']} non-trading, {t['fail']} failed")
    if t["failed_dates"]:
        print(f"failed: {', '.join(t['failed_dates'][:10])}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
