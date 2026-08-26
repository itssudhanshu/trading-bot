#!/usr/bin/env python3
"""Harvest ANNUAL-results XBRL from NSE into data/fundamentals/xbrl_annual/.

Why: quarterly results XBRL carries NO cash-flow statement (0% of 400 scanned,
batch 20260826-quality session) -- but the Annual feed does:
CashFlowsFromUsedInOperatingActivities et al., verified in live samples.
This stores raw bytes ONLY, resumable like fundamentals.backfill; parsing and
any factor work happen later and get their own pre-registration.

No rule, weight or selection input changes here. Recording infrastructure.
"""

import sys as _sys, pathlib as _pl
_sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[1]))
import paths  # noqa: F401  -- puts the source dirs on sys.path

import sys
import threading
from concurrent.futures import ThreadPoolExecutor

import features
import paths as P
from snapshot import fetch

URL = ("https://www.nseindia.com/api/corporates-financial-results"
       "?index=equities&symbol={sym}&period=Annual")
OUT = P.ROOT / "data" / "fundamentals" / "xbrl_annual"
IDIR = P.ROOT / "data" / "fundamentals" / "index_annual"


def pick(rows):
    """-> best filing per quarter-end, preferring consolidated then earliest
    broadcast (same rule as fundamentals.build_asof, kept local so the core
    module stays untouched)."""
    from fundamentals import _dt
    by_qe = {}
    for m in rows:
        bc, qe = _dt(m.get("broadCastDate")), _dt(m.get("toDate"))
        if not bc or not qe or not m.get("xbrl"):
            continue
        cons = (m.get("consolidated") or "").strip().lower().startswith(
            "consolidated")
        cur = by_qe.get(qe)
        better = (cur is None
                  or (cons and not cur["cons"])
                  or (cons == cur["cons"] and bc < cur["bc"]))
        if better:
            by_qe[qe] = {"bc": bc, "cons": cons, "url": m["xbrl"]}
    return by_qe


def dest(sym, qe):
    return OUT / sym / f"{qe}.xml"


def harvest(symbols, start, end, workers=6, log=print):
    """-> tally dict. Resumable: files on disk are skipped, index misses are
    not cached so they retry on the next run."""
    lock = threading.Lock()
    tally = {"idx_ok": 0, "idx_fail": 0, "ok": 0, "have": 0, "fail": 0}

    def do(sym):
        try:
            st, body = fetch(URL.format(sym=sym), timeout=30)
            rows = __import__("json").loads(body) if st == 200 and body else []
            picked = pick(rows)
            jobs = list(picked.items())
        except Exception:
            picked, jobs = {}, []
        # Persist the broadcast-dated index so parsing never re-fetches NSE:
        # the bytes alone cannot be dated without it.
        if picked:
            try:
                IDIR.mkdir(parents=True, exist_ok=True)
                (IDIR / f"{sym}.json").write_text(__import__("json").dumps(
                    [{"visible_from": f["bc"].isoformat(),
                      "quarter_end": qe.isoformat()}
                     for qe, f in sorted(picked.items())]))
            except OSError:
                pass
        got_idx = bool(jobs)
        with lock:
            tally["idx_ok" if got_idx else "idx_fail"] += 1
        for qe, f in jobs:
            if f["bc"] < start or f["bc"] > end:
                continue
            p = dest(sym, qe)
            if p.exists():
                with lock:
                    tally["have"] += 1
                continue
            s2, b2 = fetch(f["url"], timeout=60)
            good = (s2 == 200 and b2
                    and b"xbrl" in b2[:2000].lower()
                    and b"CashFlow" in b2)
            if good:
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(b2)
            with lock:
                tally["ok" if good else "fail"] += 1
                n = sum(tally[k] for k in ("ok", "have", "fail"))
                if n % 500 == 0:
                    log(f"  annual xbrl {n}  ok={tally['ok']} "
                        f"have={tally['have']} fail={tally['fail']}")

    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(do, symbols))
    return tally


def main(log=print):
    from datetime import date
    corpus = features.load_corpus()
    syms = sorted(corpus)
    log(f"annual XBRL harvest: {len(syms)} symbols, 2019-01-01..today")
    t = harvest(syms, date(2019, 1, 1), date.today(), log=log)
    log(f"done: {t}")
    return t


def _selftest():
    # pick(): consolidated preferred, then EARLIEST broadcast among equals;
    # rows missing dates/urls dropped.
    rows = [
        {"broadCastDate": "02-May-2024 10:00", "toDate": "31-Mar-2024",
         "consolidated": "Standalone", "xbrl": "u-standalone"},
        {"broadCastDate": "03-May-2023 10:00", "toDate": "31-Mar-2023",
         "consolidated": "Consolidated", "xbrl": "u-2023"},
        {"broadCastDate": "01-May-2024 09:00", "toDate": "31-Mar-2024",
         "consolidated": "Consolidated", "xbrl": "u-cons-late-bc"},
        {"broadCastDate": "05-May-2024 09:00", "toDate": "31-Mar-2024",
         "consolidated": "Consolidated", "xbrl": "u-cons-later"},
        {"broadCastDate": "", "toDate": "31-Mar-2023", "xbrl": "u-nobc"},
        {"broadCastDate": "01-Jun-2024", "toDate": "31-Mar-2023",
         "xbrl": ""},
    ]
    got = pick(rows)
    assert len(got) == 2, got
    assert got[__import__("datetime").date(2024, 3, 31)]["url"] == \
        "u-cons-late-bc", "must take consolidated with earliest broadcast"
    assert got[__import__("datetime").date(2023, 3, 31)]["url"] == "u-2023"
    assert dest("X/Y", __import__("datetime").date(2024, 3, 31)) == \
        OUT / "X" / "Y" / "2024-03-31.xml"
    print("backfill_annual selftest ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    else:
        main()
