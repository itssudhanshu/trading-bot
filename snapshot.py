#!/usr/bin/env python3
"""Daily NSE reference-data snapshot. Stores raw bytes; parses nothing.

Point-in-time ASM/GSM/F&O-ban state is not retrievable from NSE after the fact --
they publish today's list only. So this runs daily and keeps payloads verbatim.
Parsing happens downstream: a parser bug must never cost us data we cannot refetch.

    ./snapshot.py              # today
    ./snapshot.py --date 2026-08-14
    ./snapshot.py --selftest
"""
import argparse
import hashlib
import json
import sys
import urllib.error
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RAW = ROOT / "data" / "raw"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")

# name -> (url template, extension). {d} is the snapshot date.
SOURCES = {
    "bhavcopy_delivery": ("https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_{d:%d%m%Y}.csv", "csv"),
    "fo_secban":         ("https://nsearchives.nseindia.com/content/fo/fo_secban.csv", "csv"),
    "asm":               ("https://www.nseindia.com/api/reportASM", "json"),
    "gsm":               ("https://www.nseindia.com/api/reportGSM", "json"),
    "indices":           ("https://www.nseindia.com/api/allIndices", "json"),
    "nifty500":          ("https://nsearchives.nseindia.com/content/indices/ind_nifty500list.csv", "csv"),
    "equity_master":     ("https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv", "csv"),
}

# Absent these, the day's snapshot is not usable for point-in-time backtests.
CRITICAL = ("bhavcopy_delivery", "asm", "gsm")


def fetch(url, timeout=30, retries=1):
    """-> (http_status, body). Never raises; a dead source must not kill the run."""
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Referer": "https://www.nseindia.com/",
        "Accept": "*/*",
    })
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, b""
        except Exception:
            if attempt == retries:
                return 0, b""
    return 0, b""


def bhavcopy_date(body: bytes):
    """Trade date from the file's first data row, or None if unparseable.

    NSE's archive serves the PREVIOUS session's file with HTTP 200 when you ask
    for a holiday, rather than 404. So the URL is not evidence of what the file
    contains -- storing it under the requested date duplicates the prior session
    and injects a phantom zero-range bar into every series built from it.
    """
    from datetime import datetime
    try:
        row = body.decode(errors="replace").splitlines()[1]
        return datetime.strptime(row.split(",")[2].strip(), "%d-%b-%Y").date()
    except (IndexError, ValueError):
        return None


def snapshot(day, force=False, fetcher=fetch):
    outdir = RAW / day.isoformat()
    outdir.mkdir(parents=True, exist_ok=True)
    mpath = outdir / "manifest.json"
    manifest = json.loads(mpath.read_text()) if mpath.exists() else {}

    for name, (tpl, ext) in SOURCES.items():
        if not force and manifest.get(name, {}).get("status") == 200:
            continue
        url = tpl.format(d=day)
        status, body = fetcher(url)
        if name == "bhavcopy_delivery" and status == 200 and body:
            if bhavcopy_date(body) != day:
                status, body = 404, b""      # stale serve => market was shut
        if status == 200 and body:
            (outdir / f"{name}.{ext}").write_bytes(body)
        manifest[name] = {
            "url": url,
            "status": status,
            "bytes": len(body),
            "sha256": hashlib.sha256(body).hexdigest() if body else None,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }
        # Rewrite each iteration so a crash mid-run leaves an accurate manifest.
        mpath.write_text(json.dumps(manifest, indent=2, sort_keys=True))
    return manifest


def _selftest():
    import tempfile
    global RAW
    original, calls = RAW, []

    good_bhav = b"SYMBOL, SERIES, DATE1\nX, EQ, 14-Aug-2026\n"

    def fake(url, **kw):
        calls.append(url)
        return 200, (good_bhav if "sec_bhavdata_full" in url else b"hello")

    try:
        with tempfile.TemporaryDirectory() as td:
            RAW = Path(td)
            d = date(2026, 8, 14)
            m = snapshot(d, fetcher=fake)

            assert any("sec_bhavdata_full_14082026.csv" in u for u in calls), \
                f"bhavcopy date must be DDMMYYYY, got {calls}"
            assert m["asm"]["sha256"] == hashlib.sha256(b"hello").hexdigest()
            assert (RAW / "2026-08-14" / "bhavcopy_delivery.csv").read_bytes() == good_bhav

            n = len(calls)
            snapshot(d, fetcher=fake)
            assert len(calls) == n, "second run refetched; not idempotent"

            snapshot(d, force=True, fetcher=fake)
            assert len(calls) == n + len(SOURCES), "--force did not refetch"

            # holiday: NSE 200s with the prior session's file. Must not be stored.
            hol = date(2026, 8, 17)
            m2 = snapshot(hol, fetcher=fake)      # fake still returns 14-Aug content
            assert m2["bhavcopy_delivery"]["status"] == 404, m2["bhavcopy_delivery"]
            assert not (RAW / hol.isoformat() / "bhavcopy_delivery.csv").exists(), \
                "stale-dated bhavcopy stored under the wrong date"

            # gap detection: a day with bhavcopy but no asm.json is an
            # unrecoverable surveillance hole, not a missing-data problem
            from datetime import timedelta
            probe = date(2026, 8, 14) + timedelta(days=3)      # Mon 17 Aug
            (RAW / "2026-08-14").mkdir(parents=True, exist_ok=True)
            mb, ms = gaps(lookback_days=3, today=probe)
            assert date(2026, 8, 14) not in mb, "day with bhavcopy is not missing"
            assert date(2026, 8, 14) not in ms, "asm.json present, so no surv gap"
            # a later day missing asm.json IS a gap, once collection has started
            (RAW / "2026-08-18").mkdir(parents=True, exist_ok=True)
            (RAW / "2026-08-18" / "bhavcopy_delivery.csv").write_bytes(good_bhav)
            probe2 = date(2026, 8, 19)
            mb2, ms2 = gaps(lookback_days=5, today=probe2)
            assert date(2026, 8, 18) in ms2, "post-collection gap must be reported"
            assert date(2026, 8, 18) not in mb2, "surveillance gap is not a bhavcopy gap"

            # ...but a day BEFORE the first asm.json is backfill, not a gap
            (RAW / "2026-08-10").mkdir(parents=True, exist_ok=True)
            (RAW / "2026-08-10" / "bhavcopy_delivery.csv").write_bytes(good_bhav)
            mb3, ms3 = gaps(lookback_days=12, today=probe2)
            assert date(2026, 8, 10) not in ms3, \
                "backfilled history predates collection; not a gap"
    finally:
        RAW = original
    print("selftest ok")


def gaps(lookback_days=45, today=None):
    """-> (missing_bhavcopy, missing_surveillance) over recent weekdays.

    Bhavcopy gaps are recoverable -- NSE archives it. Surveillance gaps are NOT:
    ASM/GSM/ban are published for the current day only, so a day the machine
    slept through is gone permanently. The two are reported separately because
    only one of them can be fixed.
    """
    from datetime import timedelta
    today = today or date.today()
    holidays = set()
    hol_file = RAW.parent / "holidays.json"
    if hol_file.exists():
        holidays = set(json.loads(hol_file.read_text()))

    # Backfilled history has no surveillance by design (NSE publishes today only),
    # so absence before the collector's first run is expected, not a gap. Anchor
    # on the earliest asm.json: a detector that cries wolf over known-missing data
    # gets ignored, which costs more than not having it.
    collected = [p.parent.name for p in RAW.glob("*/asm.json")]
    since = min(collected) if collected else None

    missing_bhav, missing_surv = [], []
    for k in range(1, lookback_days + 1):
        d = today - timedelta(days=k)
        if d.weekday() >= 5 or d.isoformat() in holidays:
            continue
        day_dir = RAW / d.isoformat()
        if not (day_dir / "bhavcopy_delivery.csv").exists():
            missing_bhav.append(d)
        elif since and d.isoformat() >= since and not (day_dir / "asm.json").exists():
            # bhavcopy present so it WAS a trading day, but surveillance is absent
            missing_surv.append(d)
    return missing_bhav, missing_surv


def catchup(lookback_days=45):
    """Recover what can be recovered; report loudly what cannot."""
    import backfill
    missing_bhav, missing_surv = gaps(lookback_days)
    if missing_bhav:
        print(f"backfilling {len(missing_bhav)} missing bhavcopy days...")
        backfill.backfill(min(missing_bhav), max(missing_bhav))
        missing_bhav, missing_surv = gaps(lookback_days)

    if missing_bhav:
        print(f"still missing bhavcopy: {[str(d) for d in missing_bhav]}")
    if missing_surv:
        print(f"UNRECOVERABLE: {len(missing_surv)} trading days have no ASM/GSM/ban "
              f"snapshot. Those days can never be used for point-in-time backtests.")
        print(f"  {', '.join(str(d) for d in missing_surv[:10])}")
    if not missing_bhav and not missing_surv:
        print(f"no gaps in the last {lookback_days} days")
    return missing_bhav, missing_surv


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--date", type=date.fromisoformat, default=date.today())
    ap.add_argument("--force", action="store_true", help="refetch sources already stored")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--catchup", action="store_true",
                    help="backfill recoverable gaps, report unrecoverable ones")
    ap.add_argument("--gaps", action="store_true", help="report gaps, change nothing")
    a = ap.parse_args()

    if a.selftest:
        return _selftest()
    if a.gaps:
        mb, ms = gaps()
        print(f"missing bhavcopy ({len(mb)}, recoverable): {[str(d) for d in mb[:10]]}")
        print(f"missing surveillance ({len(ms)}, NOT recoverable): {[str(d) for d in ms[:10]]}")
        return
    if a.catchup:
        return catchup()

    m = snapshot(a.date, force=a.force)
    for name in SOURCES:
        e = m[name]
        print(f"  {name:20} {e['status']:>4}  {e['bytes']:>9,} B")

    # No bhavcopy => market was shut. Surveillance lists still move, so we keep them.
    if m["bhavcopy_delivery"]["status"] == 404:
        print(f"{a.date}: no bhavcopy, non-trading day")
        return

    failed = [n for n in CRITICAL if m[n]["status"] != 200]
    if failed:
        print(f"FAILED (critical): {', '.join(failed)}", file=sys.stderr)
        sys.exit(1)
    print(f"{a.date}: ok -> {RAW / a.date.isoformat()}")


if __name__ == "__main__":
    main()
