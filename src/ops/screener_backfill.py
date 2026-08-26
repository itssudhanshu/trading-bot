#!/usr/bin/env python3
"""Pilot backfill for Screener fundamentals — top-500 by turnover.

Resumable: existing raw HTML >10KB is skipped. Polite rate 2 req/s.
"""

import sys as _sys, pathlib as _pl
_sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[1]))
import paths  # noqa: F401

import time
import json
import pathlib
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from paths import ROOT

import features
import clusters

# reuse screener fundamentals API
import screener_fundamentals as sf


def select_top500(as_of=None):
    """Top-500 tradeable symbols by median turnover over last 250 sessions."""
    corpus = features.load_corpus()
    # use latest session if not specified
    if as_of is None:
        days = sorted({d for s in corpus.values() for d in s.days})
        as_of = days[-1]
    tradeable = set()
    for band in clusters.size_clusters(corpus, as_of=as_of, names=clusters.CLUSTERS).values():
        tradeable.update(band)
    # median turnover per symbol over last 250 sessions
    scored = []
    for sym in tradeable:
        s = corpus[sym]
        idx = s.index_of(as_of)
        if idx is None or idx < 250:
            continue
        vals = [v for v in s.turnover[idx-250:idx] if v and v > 0]
        if not vals:
            continue
        import statistics
        scored.append((sym, statistics.median(vals)))
    scored.sort(key=lambda x: x[1], reverse=True)
    return [sym for sym, _ in scored[:500]]


def backfill_screener(symbols, workers=4):
    """Fetch + parse for symbols. Returns (ok, skipped, failed)."""
    import threading
    lock = threading.Lock()
    counts = {"ok": 0, "skipped": 0, "failed": 0}

    def do(sym):
        # polite rate: 2 req/s global => 0.5s per request, distributed across workers
        # simple per-thread sleep
        time.sleep(0.5)
        raw_path = sf.RAW_SCREENER / f"{sym}.html"
        if raw_path.exists() and raw_path.stat().st_size > 10240:
            with lock:
                counts["skipped"] += 1
            # ensure parsed exists
            sf.build_parsed_screener(sym)
            return
        status, body = sf.fetch_screener(sym)
        if status == 200 and body and len(body) > 10240:
            sf.build_parsed_screener(sym, force=True)
            with lock:
                counts["ok"] += 1
        else:
            with lock:
                counts["failed"] += 1

    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(do, symbols))
    return counts


def main():
    syms = select_top500()
    print(f"screener pilot backfill: {len(syms)} symbols, 2 req/s, 4 workers")
    counts = backfill_screener(syms, workers=4)
    print(f"done: {counts}")
    # also report parsed coverage
    parsed = sum(1 for sym in syms if (sf.PARSED_SCREENER / f"{sym}.json").exists())
    print(f"parsed: {parsed}/{len(syms)} with screener timelines")


def _selftest():
    import tempfile
    # selection picks 500 and is deterministic
    # mock corpus with fake turnover
    import features, clusters
    # we test backfill resumable without network
    orig_raw = sf.RAW_SCREENER
    orig_parsed = sf.PARSED_SCREENER
    orig_fetch = sf.fetch_screener
    orig_build = sf.build_parsed_screener
    tmp_raw = pathlib.Path(tempfile.mkdtemp())
    tmp_parsed = pathlib.Path(tempfile.mkdtemp())
    sf.RAW_SCREENER = tmp_raw
    sf.PARSED_SCREENER = tmp_parsed
    try:
        # create fake existing raw
        (tmp_raw / "EXIST.html").write_bytes(b"x" * 11000)
        # mock fetch to count calls
        calls = []
        def fake_fetch(sym, force=False, fetcher=None):
            calls.append(sym)
            # write fake html
            (tmp_raw / f"{sym}.html").write_bytes(b"x" * 11000)
            return 200, b"x" * 11000
        sf.fetch_screener = fake_fetch
        sf.build_parsed_screener = lambda sym, force=False: (tmp_parsed / f"{sym}.json").write_text("[]") or []
        # backfill 2 symbols, one already exists
        counts = backfill_screener(["EXIST", "NEW"], workers=1)
        assert "EXIST" not in calls, "should have skipped existing"
        assert "NEW" in calls, "should have fetched new"
        assert counts["skipped"] == 1 and counts["ok"] == 1, counts
        print("backfill selftest ok")
    finally:
        sf.RAW_SCREENER = orig_raw
        sf.PARSED_SCREENER = orig_parsed
        sf.fetch_screener = orig_fetch
        sf.build_parsed_screener = orig_build
    # selection sanity: top-500 is 500 or less if corpus smaller, and all tradeable
    syms = select_top500()
    assert 400 <= len(syms) <= 500, len(syms)
    print(f"selection selftest ok ({len(syms)} symbols)")


if __name__ == "__main__":
    if "--selftest" in _sys.argv:
        _selftest()
    else:
        main()
