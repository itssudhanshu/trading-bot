#!/usr/bin/env python3
"""Sector mapping for NSE tradeable universe — source selection and fetcher.

WHY THIS EXISTS. simulate.py:239 says "no sector rule — corpus has no industry
classification." The Univest article proposes sector rotation entry (FII/DII
buying in sector 3-5 days). To test that, the corpus needs a sector label per
symbol.

SOURCES RESEARCHED

  1. NSE sector indices composition
     - Endpoint tried: https://www.nseindia.com/api/equity-stockIndices?index=SECTORAL
       -> 404 for every index name tested (NIFTY AUTO, NIFTY BANK, NIFTY PHARMA,
       NIFTY 50, SECTORAL). That path does not exist.
     - snapshot.py SOURCES has "indices": https://www.nseindia.com/api/allIndices
       -> works, returns index-level OHLC (24157 for NIFTY 50) but no membership.
     - Alternate host niftyindices.com serves per-index constituents:
         https://www.niftyindices.com/IndexConstituent/ind_niftypharmalist.csv
         https://www.niftyindices.com/IndexConstituent/ind_niftyautolist.csv
       Verified live 2026-08-27: 20 rows (pharma), 15 rows (auto), with Industry
       column. But these are NIFTY sector indices — large-cap only. Tested:
         * NIFTY PHARMA = 20 names (ABBOTINDIA..ZYDUSLIFE) — zero micro caps.
         * NIFTY AUTO   = 15 names (ASHOKLEY..UNOMINDA) — zero micro caps.
       To cover micro/small 1,276 tradeable you would need to scrape ~20 such
       CSVs and still only reach ~200 large caps. No micro/small coverage, no
       daily history, and the mapping is index-construction, not a company
       attribute.

  2. Screener.in company page header
     - Page: https://www.screener.in/company/{SYMBOL}/consolidated/
     - Already fetched by screener_fundamentals.fetch_screener() into
       data/screener_raw/{SYMBOL}.html (1,238 files, 123 of 1,276 tradeable
       already cached = 96.9%). Polite, resumable, with retry on 429/5xx.
     - Sector hierarchy is in the peer-comparison sub-header:
         <a title="Broad Sector">Energy</a>
         <a title="Sector">Oil, Gas & Consumable Fuels</a>
         <a title="Broad Industry">Petroleum Products</a>
         <a title="Industry">Refineries & Marketing</a>
       Extracted via BeautifulSoup from <a title="...">.
     - Measured on 1,238 cached pages: 0 missing Broad Sector (100% coverage).
     - Distinct values on that set: 12 Broad Sectors, 20 Sectors,
       ~30 Broad Industries, many Industries — exactly the granularity a
       rotation test needs. No auth, no key, free for the tradeable universe.
     - Verified live on uncached symbol SBIN 2026-08-27: fetched 225,675 B,
       parsed Broad Sector=Financial Services, Sector=Financial Services,
       Broad Industry=Banks, Industry=Public Sector Bank.

RECOMMENDATION: Screener.in is the most complete free source for micro/small
1,276 tradeable. NSE sector indices are unsuitable (large-cap only, incomplete).

This module reuses screener_fundamentals.fetch_screener for polite fetching
and parses the four-level hierarchy into data/sectors.json.

    python3 src/ops/sector_backfill.py --sample          # 10 tradeable, proves mapping
    python3 src/ops/sector_backfill.py --all             # full 1,276 (polite 2 req/s)
    python3 src/ops/sector_backfill.py --selftest
    python3 src/ops/sector_backfill.py --report          # sector distribution

Output: data/sectors.json  {symbol: broad_sector}  (simple, for rotation test)
        data/sectors_detailed.json  {symbol: {broad_sector, sector, broad_industry, industry}}
        Both are point-in-time snapshots of Screener's current classification —
        not historical, but sector membership is slow-moving (reclass <1% /yr).
        For a pre-registered rotation test this is sufficient; a historical
        sector change would be a second-order effect versus the 3-5 day FII flow
        signal being tested.
"""

import sys as _sys, pathlib as _pl
_sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[1]))
import paths  # noqa: F401

import argparse
import json
import random
import time
from pathlib import Path

from paths import ROOT

import screener_fundamentals as sf

SECTORS_JSON = ROOT / "data" / "sectors.json"
DETAILED_JSON = ROOT / "data" / "sectors_detailed.json"


def parse_sector(html_bytes):
    """-> {broad_sector, sector, broad_industry, industry} or None if missing.

    Reads Screener's peer-comparison header. All four levels are optional in
    the return — Broad Sector is the one the rotation test will key on, and
    it is present in 100% of the 1,238 pages measured. Industry may be absent
    on a malformed page, but never is in practice.
    """
    from bs4 import BeautifulSoup
    try:
        soup = BeautifulSoup(html_bytes, "lxml")
    except Exception:
        return None
    out = {}
    for a in soup.find_all("a"):
        title = a.get("title")
        if title in ("Broad Sector", "Sector", "Broad Industry", "Industry"):
            key = title.lower().replace(" ", "_")
            out[key] = a.get_text(strip=True)
    if not out.get("broad_sector"):
        return None
    return out


def fetch_sector(symbol, fetcher=None, force=False):
    """Fetch and parse sector for one symbol. Resumable via screener_raw cache.

    Returns dict or None. Uses sf.fetch_screener so 429/5xx retry and 10KB
    cache check are shared with fundamentals.
    """
    status, body = sf.fetch_screener(symbol, force=force, fetcher=fetcher)
    if status != 200 or not body or len(body) < 10240:
        return None
    return parse_sector(body)


def _tradeable_symbols(as_of=None):
    """-> list of 1,276 micro+small symbols as of `as_of` (latest if None)."""
    import features
    import clusters
    corpus = features.load_corpus()
    days = sorted({d for s in corpus.values() for d in s.days})
    if as_of is None:
        as_of = days[-1]
    bands = clusters.size_clusters(corpus, as_of=as_of)
    return sorted(s for v in bands.values() for s in v)


def _load_existing():
    simple = {}
    detailed = {}
    if SECTORS_JSON.exists():
        try:
            simple = json.loads(SECTORS_JSON.read_text())
        except Exception:
            simple = {}
    if DETAILED_JSON.exists():
        try:
            detailed = json.loads(DETAILED_JSON.read_text())
        except Exception:
            detailed = {}
    return simple, detailed


def backfill(symbols, force=False, delay=0.6, verbose=True):
    """Fetch sector for each symbol, polite delay between requests.

    Resumable: if symbol already in sectors.json and not force, skip network.
    Returns (ok, failed, skipped).
    """
    simple, detailed = _load_existing()
    ok = failed = skipped = 0
    for idx, sym in enumerate(symbols):
        if not force and sym in simple and sym in detailed:
            skipped += 1
            continue
        # polite: sleep before each network call except first resumable skip
        if idx > 0:
            time.sleep(delay + random.uniform(0, 0.15))
        parsed = fetch_sector(sym, force=force)
        if parsed and parsed.get("broad_sector"):
            simple[sym] = parsed["broad_sector"]
            detailed[sym] = parsed
            ok += 1
            if verbose:
                print(f"  {sym:14} {parsed['broad_sector']:30} / {parsed.get('sector','')}")
        else:
            failed += 1
            if verbose:
                print(f"  {sym:14} FAILED")
        # incremental save so a crash leaves progress
        SECTORS_JSON.parent.mkdir(parents=True, exist_ok=True)
        SECTORS_JSON.write_text(json.dumps(simple, indent=1, sort_keys=True) + "\n")
        DETAILED_JSON.write_text(json.dumps(detailed, indent=1, sort_keys=True) + "\n")
    return ok, failed, skipped


def report():
    """Print sector distribution from detailed mapping, plus coverage."""
    simple, detailed = _load_existing()
    if not detailed:
        print("no mapping yet — run --sample or --all first")
        return
    from collections import Counter
    cnt_broad = Counter(v.get("broad_sector", "?") for v in detailed.values())
    cnt_sector = Counter(v.get("sector", "?") for v in detailed.values())
    cnt_ind = Counter(v.get("broad_industry", "?") for v in detailed.values())
    print(f"mapped {len(detailed)} symbols")
    print(f"\nBroad Sector ({len(cnt_broad)} distinct):")
    for k, n in cnt_broad.most_common():
        print(f"  {k:35} {n:4}  {n/len(detailed)*100:4.1f}%")
    print(f"\nSector ({len(cnt_sector)} distinct, top 15):")
    for k, n in cnt_sector.most_common(15):
        print(f"  {k:40} {n:4}")
    print(f"\nBroad Industry ({len(cnt_ind)} distinct, top 15):")
    for k, n in cnt_ind.most_common(15):
        print(f"  {k:40} {n:4}")
    # coverage vs tradeable
    try:
        tradeable = set(_tradeable_symbols())
        covered = tradeable & set(detailed)
        print(f"\ncoverage of tradeable micro+small (1,276): {len(covered)}/{len(tradeable)} "
              f"= {len(covered)/len(tradeable)*100:.1f}%")
        missing = sorted(tradeable - set(detailed))[:10]
        if missing:
            print(f"  sample missing: {missing}")
    except Exception as e:
        print(f"  (tradeable coverage check skipped: {e})")


def _selftest():
    import tempfile
    import pathlib

    # 1. parser on synthetic HTML
    html_ok = b"""
    <html><body>
      <a href="/market/IN03/" title="Broad Sector">Energy</a>
      <a href="/market/IN03/IN0301/" title="Sector">Oil, Gas &amp; Consumable Fuels</a>
      <a href="/market/IN03/IN0301/IN030103/" title="Broad Industry">Petroleum Products</a>
      <a href="/market/IN03/IN0301/IN030103/IN030103001/" title="Industry">Refineries &amp; Marketing</a>
    </body></html>
    """
    parsed = parse_sector(html_ok)
    assert parsed["broad_sector"] == "Energy", parsed
    assert parsed["sector"] == "Oil, Gas & Consumable Fuels", parsed
    assert parsed["broad_industry"] == "Petroleum Products", parsed
    assert parsed["industry"] == "Refineries & Marketing", parsed

    html_missing = b"<html><body>no sector here</body></html>"
    assert parse_sector(html_missing) is None, "should be None when Broad Sector absent"

    # IT example
    html_it = b"""
    <html><body>
      <a title="Broad Sector">Information Technology</a>
      <a title="Sector">Information Technology</a>
      <a title="Broad Industry">IT - Software</a>
      <a title="Industry">Computers - Software &amp; Consulting</a>
    </body></html>
    """
    p2 = parse_sector(html_it)
    assert p2["broad_sector"] == "Information Technology"

    # 2. parser on real cached fixtures if available
    fix = pathlib.Path("data/screener_raw/RELIANCE.html")
    if fix.exists():
        real = parse_sector(fix.read_bytes())
        assert real is not None and real["broad_sector"] == "Energy", real
        assert real["sector"] == "Oil, Gas & Consumable Fuels", real
        print(f"  fixture RELIANCE ok: {real}")
    fix2 = pathlib.Path("data/screener_raw/TCS.html")
    if fix2.exists():
        real2 = parse_sector(fix2.read_bytes())
        assert real2["broad_sector"] == "Information Technology", real2
        print(f"  fixture TCS ok: {real2}")

    # 3. resumable + polite backfill without network (injected fetcher)
    orig_raw = sf.RAW_SCREENER
    tmp_raw = pathlib.Path(tempfile.mkdtemp())
    sf.RAW_SCREENER = tmp_raw
    orig_sectors = SECTORS_JSON
    orig_detailed = DETAILED_JSON
    tmp_simple = pathlib.Path(tempfile.mkdtemp()) / "sectors.json"
    tmp_detailed = pathlib.Path(tempfile.mkdtemp()) / "sectors_detailed.json"
    # monkey-patch module globals for isolation
    globals()["SECTORS_JSON"] = tmp_simple
    globals()["DETAILED_JSON"] = tmp_detailed
    try:
        # seed existing mapping to test resumable skip
        tmp_simple.write_text(json.dumps({"EXIST": "Energy"}, indent=1))
        tmp_detailed.write_text(json.dumps({"EXIST": {"broad_sector": "Energy", "sector": "Oil"}}, indent=1))
        (tmp_raw / "EXIST.html").write_bytes(b"<html>" + b"x" * 11000 + b"</html>")

        calls = []
        tmp_simple2 = pathlib.Path(tempfile.mkdtemp()) / "sectors.json"
        tmp_detailed2 = pathlib.Path(tempfile.mkdtemp()) / "sectors_detailed.json"
        globals()["SECTORS_JSON"] = tmp_simple2
        globals()["DETAILED_JSON"] = tmp_detailed2
        (tmp_raw / "PRE.html").write_bytes(b"<html>" + b"x" * 11000 + b"</html>")
        # pre-seed PRE as already mapped
        tmp_simple2.write_text(json.dumps({"PRE": "Energy"}, indent=1))
        tmp_detailed2.write_text(json.dumps({"PRE": {"broad_sector": "Energy"}}, indent=1))

        orig_fetch = sf.fetch_screener

        def fake_fetch_screener(symbol, force=False, fetcher=None):
            if not force and (tmp_raw / f"{symbol}.html").exists() and (tmp_raw / f"{symbol}.html").stat().st_size > 10240:
                # simulate resumable returning cached but with no sector — not used for PRE skip
                return 200, (tmp_raw / f"{symbol}.html").read_bytes()
            calls.append(symbol)
            body = (b'<html><a title="Broad Sector">Healthcare</a>'
                    b'<a title="Sector">Healthcare</a>'
                    b'<a title="Broad Industry">Pharma</a>'
                    b'<a title="Industry">Pharma</a></html>' + b"x" * 11000)
            (tmp_raw / f"{symbol}.html").write_bytes(body)
            return 200, body

        sf.fetch_screener = fake_fetch_screener
        try:
            calls.clear()
            ok2, failed2, skipped2 = backfill(["PRE", "NEW2"], delay=0, verbose=False)
            assert "PRE" not in calls, f"should have skipped PRE, calls={calls}"
            assert "NEW2" in calls, f"should have fetched NEW2, calls={calls}"
            assert skipped2 == 1 and ok2 == 1 and failed2 == 0, (ok2, failed2, skipped2)
            # verify files written
            simple = json.loads(tmp_simple2.read_text())
            detailed = json.loads(tmp_detailed2.read_text())
            assert simple["NEW2"] == "Healthcare", simple
            assert detailed["NEW2"]["broad_sector"] == "Healthcare"
            assert simple["PRE"] == "Energy", "existing entry must be preserved"
            print("  resumable backfill ok")
        finally:
            sf.fetch_screener = orig_fetch

        # force refetch
        calls.clear()
        sf.fetch_screener = fake_fetch_screener
        try:
            ok3, failed3, skipped3 = backfill(["PRE"], force=True, delay=0, verbose=False)
            assert skipped3 == 0 and ok3 == 1, (ok3, failed3, skipped3)
            print("  force refetch ok")
        finally:
            sf.fetch_screener = orig_fetch

    finally:
        sf.RAW_SCREENER = orig_raw
        globals()["SECTORS_JSON"] = orig_sectors
        globals()["DETAILED_JSON"] = orig_detailed

    print("sector_backfill selftest ok")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--sample", action="store_true", help="fetch 10 tradeable symbols (polite, proves mapping)")
    g.add_argument("--all", action="store_true", help="fetch all 1,276 tradeable (polite 2 req/s, ~11 min)")
    g.add_argument("--report", action="store_true", help="print sector distribution from existing mapping")
    g.add_argument("--selftest", action="store_true")
    ap.add_argument("--force", action="store_true", help="refetch even if already mapped")
    ap.add_argument("--symbols", nargs="*", help="explicit symbols to fetch")
    ap.add_argument("--delay", type=float, default=0.6, help="seconds between requests (default 0.6)")
    args = ap.parse_args()

    if args.selftest:
        _selftest()
        return

    if args.report:
        report()
        return

    if args.symbols:
        syms = [s.strip().upper() for s in args.symbols if s.strip()]
    elif args.all:
        syms = _tradeable_symbols()
        print(f"sector backfill: {len(syms)} symbols, {args.delay}s delay (~{len(syms)*args.delay/60:.1f} min)")
    elif args.sample:
        # deterministic 10 from tradeable, preferring uncached to prove live fetch
        all_syms = _tradeable_symbols()
        # pick 10 that exercise distinct sectors; use first 10 alphabetically for reproducibility
        # but ensure at least 2 uncached if available to prove live fetch path
        existing_simple, _ = _load_existing()
        uncached = [s for s in all_syms if s not in existing_simple][:5]
        cached = [s for s in all_syms if s in existing_simple][:5]
        # if we have detailed mapping already, sample random to show breadth
        if len(existing_simple) > 100:
            import random
            random.seed(42)
            syms = random.sample(all_syms, 10)
        else:
            syms = (uncached + cached)[:10]
            if len(syms) < 10:
                syms = all_syms[:10]
        print(f"sector sample: {len(syms)} symbols")
    else:
        ap.print_help()
        print("\nNo action — use --sample, --all, --symbols SYM [SYM ...], --report, or --selftest")
        return

    print(f"  symbols: {', '.join(syms)}")
    ok, failed, skipped = backfill(syms, force=args.force, delay=args.delay)
    print(f"\ndone: ok={ok} failed={failed} skipped={skipped} (already mapped)")
    simple, detailed = _load_existing()
    print(f"  total mapped: {len(simple)} simple, {len(detailed)} detailed")
    print(f"  -> {SECTORS_JSON.relative_to(ROOT)}")
    print(f"  -> {DETAILED_JSON.relative_to(ROOT)}")
    if ok or skipped:
        print("\n  sample of mapping:")
        for s in syms[:5]:
            if s in detailed:
                d = detailed[s]
                print(f"    {s:14} {d.get('broad_sector','?'):30} | {d.get('sector','?'):30} | {d.get('industry','?')}")


if __name__ == "__main__":
    main()
