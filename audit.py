#!/usr/bin/env python3
"""Cross-check every load-bearing property of the system.

Not a selftest: selftests run on fixtures and prove a function does what its
author meant. This runs on the REAL corpus and the REAL book, and checks the
properties that would cost money if they were quietly wrong -- lookahead,
survivorship, cost arithmetic, gap fills, and whether the headline number
still reproduces.

Every check prints PASS or FAIL with the number behind it. A check that cannot
run prints SKIP and says why; it never silently passes.
"""
import statistics
import sys
from datetime import date, timedelta

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, ok))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
    if detail:
        print(f"         {detail}")
    return ok


def skip(name, why):
    RESULTS.append((name, None))
    print(f"  [SKIP] {name}")
    print(f"         {why}")


def section(t):
    print(f"\n{t}\n{'-' * len(t)}")


def main():
    import clusters, engine, features, learning, pbook, portfolio, simulate, universe

    corpus = features.load_corpus()
    days = sorted({d for s in corpus.values() for d in s.days})
    as_of = days[-1]

    # ---------------------------------------------------------------- DATA
    section("DATA INTEGRITY")

    hol = set()
    p = features.ROOT / "data" / "holidays.json"
    if p.exists():
        import json
        hol = {str(x) for x in json.loads(p.read_text())}
    have, gaps, d = set(days), [], days[0]
    while d <= days[-1]:
        if d.weekday() < 5 and d not in have and d.isoformat() not in hol:
            gaps.append(d)
        d += timedelta(days=1)
    check("no unexplained weekday gaps", not gaps,
          f"{len(days)} sessions {days[0]}..{days[-1]}, {len(gaps)} gaps")

    # every stored bhavcopy must carry the date of the folder it sits in
    import snapshot
    bad_dates = []
    for folder in sorted((features.ROOT / "data" / "raw").iterdir())[-40:]:
        f = folder / "bhavcopy_delivery.csv"
        if not f.exists():
            continue
        inside = snapshot.bhavcopy_date(f.read_bytes())
        if inside and inside.isoformat() != folder.name:
            bad_dates.append((folder.name, str(inside)))
    check("stored bhavcopy matches its folder date", not bad_dates,
          f"last 40 snapshots checked; {len(bad_dates)} mismatched "
          f"(this is the holiday stale-serve trap)")

    # survivorship: symbols that stop trading must remain in history
    ended = [s for s in corpus.values() if s.days[-1] < days[-40]]
    check("delisted symbols retained in history", len(ended) > 0,
          f"{len(ended)} symbols stop before the last 40 sessions and are kept")

    # no bar may hold a price from the future
    fwd = [s.symbol for s in corpus.values() if s.days and s.days[-1] > date.today()]
    check("no bar dated in the future", not fwd, f"{len(fwd)} offenders")

    # ---------------------------------------------------------- SELECTION
    section("SELECTION")

    bands = clusters.size_clusters(corpus, as_of)
    sizes = {k: len(v) for k, v in bands.items()}
    overlap = set(bands["micro"]) & set(bands["small"])
    check("clusters are disjoint", not overlap, f"{sizes}, {len(overlap)} overlap")

    ranked = sorted(corpus, key=lambda s: statistics.median(
        [x for x in corpus[s].turnover if x > 0] or [0]))
    allc = set(bands["micro"]) | set(bands["small"])
    check("most liquid names are excluded", ranked[-1] not in allc,
          f"top-turnover name {ranked[-1]} is not tradeable; "
          f"{len(allc)} of {len(corpus)} are ({len(allc)/len(corpus)*100:.0f}%)")

    picks = clusters.pick(corpus, as_of)
    check("only tradeable clusters are scored", set(picks) == set(clusters.CLUSTERS),
          f"scored: {sorted(picks)}")

    # the 200-DMA gate must EXCLUDE, never merely score down
    below = 0
    for sym, _ in [x for v in picks.values() for x in v]:
        s = corpus[sym]
        i = s.index_of(as_of)
        sma = features.sma(s.close, 200)
        if i is not None and sma[i] and s.close[i] < sma[i]:
            below += 1
    check("no pick sits below its 200-day average", below == 0,
          f"{below} of {sum(len(v) for v in picks.values())} picks below the gate")

    # surveillance must bind where it is known
    rows = portfolio.build(corpus, as_of)
    bad = []
    for r in rows:
        s = corpus[r["symbol"]]
        i = s.index_of(as_of)
        if (i is not None and i < len(s.restricted) and s.restricted[i]
                and s.surveillance_known[i]):
            bad.append(r["symbol"])
    check("no restricted stock is a candidate", not bad, f"{bad or 'none'}")

    # the bucket must honour the configured mix and never exceed it
    sel = portfolio.allocate(rows)
    from collections import Counter
    mix = Counter(r["cluster"] for r in sel)
    ok = all(mix[c] <= k for c, k in portfolio.TAKE_PER_CLUSTER.items())
    check("bucket never exceeds its per-cluster quota", ok,
          f"selected {dict(mix)} against {portfolio.TAKE_PER_CLUSTER}")
    check("bucket never exceeds MAX_POSITIONS", len(sel) <= portfolio.MAX_POSITIONS,
          f"{len(sel)} of {portfolio.MAX_POSITIONS}")

    # every selected name must have actually triggered
    untrig = [r["symbol"] for r in sel if not r.get("triggered")]
    check("every selected name has triggered", not untrig, f"{untrig or 'none'}")

    # ------------------------------------------------------------- MONEY
    section("MONEY")

    per = portfolio.CAPITAL * portfolio.DEPLOY_PCT / 100 / portfolio.MAX_POSITIONS
    q, risk = portfolio.position_size(portfolio.CAPITAL, 100.0)
    check("position size matches the deployment cap", abs(q * 100.0 - per) < 100,
          f"Rs {q*100:,.0f} per stock vs cap Rs {per:,.0f}")
    check("a full book stays inside the deployment cap",
          per * portfolio.MAX_POSITIONS <= portfolio.CAPITAL * portfolio.DEPLOY_PCT / 100 + 1,
          f"Rs {per*portfolio.MAX_POSITIONS:,.0f} of Rs {portfolio.CAPITAL:,}")
    big, _ = portfolio.position_size(portfolio.CAPITAL, 100.0, mult=99)
    check("the risk rule caps any single position", big * 100.0 < portfolio.CAPITAL * 0.25,
          f"hard cap Rs {big*100:,.0f} = {big*100/portfolio.CAPITAL*100:.0f}% of capital")

    # cost arithmetic, checked by hand
    c = engine.Costs()
    v = 100_000.0
    manual = (20 + v*0.001 + v*0.0000297 + v*1e-6
              + 0.18*(20 + v*0.0000297 + v*1e-6) + v*0.00015)
    check("buy-side cost matches a hand calculation",
          abs(c.charge(v, "BUY") - manual) < 0.01,
          f"Rs {c.charge(v,'BUY'):.2f} vs Rs {manual:.2f} computed independently")
    # Independent of c.dp_sell. The earlier version compared the sell charge
    # against a formula that itself read c.dp_sell, so setting DP to zero moved
    # both sides together and the check passed while the charge had vanished --
    # a test that could not fail for the reason it claimed to test.
    EXPECT_DP = 15.93
    sell_minus_buy = c.charge(v, "SELL") - c.charge(v, "BUY")
    check("sell side pays DP and no stamp duty",
          abs(sell_minus_buy - (EXPECT_DP - v * 0.00015)) < 0.01,
          f"sell - buy = Rs {sell_minus_buy:+.2f}; expected "
          f"Rs {EXPECT_DP - v*0.00015:+.2f} (DP {EXPECT_DP} less stamp "
          f"Rs {v*0.00015:.2f})")
    check("the DP charge is actually applied", c.charge(v, "SELL") - c.charge(v, "BUY")
          + v * 0.00015 > 10.0,
          f"DP component Rs {sell_minus_buy + v*0.00015:.2f}, must be non-zero")

    # ------------------------------------------------------------- FILLS
    section("FILLS AND EXITS")

    import tempfile, shutil
    from pathlib import Path
    tmp = Path(tempfile.mkdtemp())
    orig_db, orig_led = pbook.DB, learning.LEDGER
    pbook.DB, learning.LEDGER = tmp / "p.db", tmp / "l.jsonl"
    try:
        conn = pbook.db()
        sym = sel[0]["symbol"] if sel else rows[0]["symbol"]
        pbook.queue([{"symbol": sym, "cluster": "small", "qty": 10,
                      "stop": 1.0, "target": 999.0}], as_of, conn)
        filled, _ = pbook.step(corpus, as_of, conn)
        check("an order cannot fill on its own signal day", not filled,
              f"queued and stepped on {as_of}; filled {len(filled)}")
    finally:
        pbook.DB, learning.LEDGER = orig_db, orig_led
        shutil.rmtree(tmp, ignore_errors=True)

    # gap handling, on the real simulator
    r = simulate.run(corpus, days, stop_pct=10.0, target_pct=20.0, hold=15,
                     max_pos=5, refresh=5, trigger="breakout", impact_c=1.0)
    t = r["trades"]
    stops = [x["ret"] for x in t if x["why"] == "stop"]
    check("gapped stops fill worse than the nominal stop",
          bool(stops) and statistics.fmean(stops) < -10.0,
          f"{len(stops)} stops, mean {statistics.fmean(stops) if stops else 0:+.2f}% "
          f"against a -10% nominal")
    holds = [x["why"] for x in t]
    check("every trade exits by stop, target or time",
          set(holds) <= {"stop", "target", "time"}, f"{Counter(holds)}")

    # -------------------------------------------------------- REPRODUCES
    section("HEADLINE NUMBER")
    check("the recorded baseline still reproduces",
          abs(r["cagr"] - 13.57) < 0.01 and len(t) == 217,
          f"CAGR {r['cagr']:+.2f}% (recorded +13.57), n={len(t)} (recorded 217), "
          f"maxDD {r['maxdd']:.1f}%")

    # ------------------------------------------------------------ SUMMARY
    section("SUMMARY")
    p_ = sum(1 for _, o in RESULTS if o is True)
    f_ = sum(1 for _, o in RESULTS if o is False)
    s_ = sum(1 for _, o in RESULTS if o is None)
    print(f"  {p_} passed, {f_} failed, {s_} skipped")
    if f_:
        print("\n  FAILED:")
        for n, o in RESULTS:
            if o is False:
                print(f"    - {n}")
    return 1 if f_ else 0


if __name__ == "__main__":
    sys.exit(main())
