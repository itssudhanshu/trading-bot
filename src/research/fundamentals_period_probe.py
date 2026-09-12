#!/usr/bin/env python3
"""Does `features_asof` compare this quarter against the one a year earlier?

Opened 2026-09-12 after the reviewer layer's first live call put two numbers for
the same company on the same page and they disagreed: YUKEN's `fundamental`
channel read `profit_growth -18.82` while its announcement and news channels
both carried "Q1 Results: Net profit rises 29% YoY". One of them is wrong, and
which one matters far beyond one dossier.

THE MECHANISM BEING CHECKED
---------------------------
`features_asof` picks the comparison row BY POSITION:

    seen.sort(key=lambda r: r["visible_from"])
    cur, yr = seen[-1], seen[-5]        # 4 quarters back

Five rows back is four quarters back only if the timeline has no gaps. It can:
`build_parsed` drops a quarter on any of three conditions -- the XBRL file is
not on disk, the index has no entry for that quarter_end, or `quarter_figures`
returns nothing. Each is a `continue`, and none leaves a marker behind.

So a symbol missing one quarter has `seen[-5]` landing FIVE quarters back, and
the growth features then compare Q1 against Q4-of-two-years-ago while reporting
it as year-on-year. Nothing raises. The row carries `quarter_end`, so the
information needed to notice is present and simply unused.

WHAT THIS PROBE MEASURES
------------------------
For every position in every parsed timeline where `features_asof` could compute
(any row with four predecessors), the gap in days between `cur["quarter_end"]`
and `seen[-5]["quarter_end"]`. A true year-on-year comparison is 365 or 366 days
(NSE quarters end on calendar quarter boundaries). `TOLERANCE_DAYS` is
deliberately generous at 20: the question is whether the periods are a year
apart at all, not whether they are exactly so.

HOW TO READ THE RESULT
----------------------
  clean     every computable position is a year apart. The contradiction is
            then NOT this mechanism, and the next suspect is consolidated vs
            standalone -- `build_asof` prefers consolidated and the press
            reports whichever the company led with.
  impure    some positions compare the wrong periods. Then every fundamentals
            figure this repo has published is suspect, INCLUDING the four-feature
            null measured on 1,049 trades: a feature computed from mismatched
            periods is noise, and measuring noise as flat is not the same
            finding as measuring the feature as flat.

Nothing here proposes a fix. What the fix should be depends on which of those
two it is, and choosing it before the number is the move this repo forbids.

    python3 src/research/fundamentals_period_probe.py
    python3 src/research/fundamentals_period_probe.py --symbol YUKEN
    python3 src/research/fundamentals_period_probe.py --selftest
"""
import argparse
import json
import sys
from collections import Counter
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # -> src/
import paths  # noqa: F401

import fundamentals

TOLERANCE_DAYS = 20
YEAR_DAYS = 365
LOOKBACK = 5          # the -5 in features_asof; kept as one name, not retyped


def _d(iso):
    try:
        return date.fromisoformat(str(iso)[:10])
    except (TypeError, ValueError):
        return None


def period_gaps(rows):
    """-> one record per position `features_asof` could compute from.

    Mirrors its selection exactly -- sort by visible_from, take [-1] and
    [-LOOKBACK] of the rows visible at that point -- so a disagreement here is a
    disagreement with the live feature, not with a paraphrase of it.
    """
    ordered = sorted([r for r in rows if r.get("visible_from")],
                     key=lambda r: r["visible_from"])
    out = []
    for i in range(LOOKBACK - 1, len(ordered)):
        cur, yr = ordered[i], ordered[i - (LOOKBACK - 1)]
        qc, qy = _d(cur.get("quarter_end")), _d(yr.get("quarter_end"))
        gap = (qc - qy).days if (qc and qy) else None
        out.append({
            "as_of": cur["visible_from"],
            "cur_quarter": cur.get("quarter_end"),
            "cmp_quarter": yr.get("quarter_end"),
            "gap_days": gap,
            "year_apart": gap is not None and abs(gap - YEAR_DAYS) <= TOLERANCE_DAYS,
        })
    return out


def audit(timelines):
    """-> counts across every symbol. `timelines` is {symbol: rows}."""
    total = clean = 0
    bad_syms, shapes, dupes = Counter(), Counter(), []
    for sym, rows in timelines.items():
        qs = [r.get("quarter_end") for r in rows if r.get("quarter_end")]
        if len(set(qs)) != len(qs):
            dupes.append(sym)          # build_asof should make this impossible
        for rec in period_gaps(rows):
            total += 1
            if rec["year_apart"]:
                clean += 1
            else:
                bad_syms[sym] += 1
                g = rec["gap_days"]
                shapes[("none" if g is None
                        else f"{round(g / 91.3)} quarters")] += 1
    return {"symbols": len(timelines), "positions": total, "clean": clean,
            "mismatched": total - clean,
            "symbols_affected": len(bad_syms),
            "worst": bad_syms.most_common(8),
            "gap_shapes": shapes.most_common(8),
            "duplicate_quarters": dupes}


def _report(a):
    pct = (100 * a["clean"] / a["positions"]) if a["positions"] else 0.0
    print(f"\n{a['symbols']} symbols, {a['positions']} computable positions")
    print(f"  year apart      {a['clean']} ({pct:.2f}%)")
    print(f"  MISMATCHED      {a['mismatched']}  "
          f"across {a['symbols_affected']} symbol(s)")
    if a["gap_shapes"]:
        print("  what the gap actually was:")
        for shape, n in a["gap_shapes"]:
            print(f"    {shape:>14}  {n}")
    if a["worst"]:
        print("  most affected symbols:")
        for sym, n in a["worst"]:
            print(f"    {sym:<12} {n}")
    if a["duplicate_quarters"]:
        print(f"  !! duplicate quarter_end in: "
              f"{', '.join(a['duplicate_quarters'][:8])}")
    print()
    if a["mismatched"]:
        print("  Mismatched positions mean the growth features compared periods")
        print("  that are not a year apart, while reporting year-on-year. Every")
        print("  published fundamentals figure is then suspect, including the")
        print("  four-feature null on 1,049 trades.\n")
    return a["mismatched"] == 0


def _dump(symbol):
    rows = fundamentals.timeline(symbol)
    if not rows:
        print(f"{symbol}: no parsed timeline on disk")
        return False
    print(f"\n{symbol}: {len(rows)} parsed quarters\n")
    print(f"  {'visible_from':<12} {'quarter_end':<12} "
          f"{'revenue':>14} {'net_profit':>14}")
    for r in sorted(rows, key=lambda x: x["visible_from"]):
        print(f"  {r['visible_from']:<12} {r.get('quarter_end', '?'):<12} "
              f"{r.get('revenue', float('nan')):>14,.2f} "
              f"{r.get('net_profit', float('nan')):>14,.2f}")
    print("\n  what features_asof would compare, at each computable position:\n")
    ok = True
    for rec in period_gaps(rows):
        mark = "  " if rec["year_apart"] else "!!"
        ok = ok and rec["year_apart"]
        print(f"  {mark} as of {rec['as_of']}: {rec['cur_quarter']} vs "
              f"{rec['cmp_quarter']}  ({rec['gap_days']}d)")
    print()
    return ok


# --------------------------------------------------------------------------

def _rows(*pairs):
    return [{"visible_from": v, "quarter_end": q, "revenue": 100.0,
             "net_profit": 10.0} for v, q in pairs]


def _selftest():
    # --- a complete timeline is clean --------------------------------------
    full = _rows(("2024-05-10", "2024-03-31"), ("2024-08-10", "2024-06-30"),
                 ("2024-11-10", "2024-09-30"), ("2025-02-10", "2024-12-31"),
                 ("2025-05-10", "2025-03-31"), ("2025-08-10", "2025-06-30"))
    recs = period_gaps(full)
    assert len(recs) == 2, recs
    assert all(r["year_apart"] for r in recs), recs
    assert recs[0]["gap_days"] == 365, recs[0]

    # --- ONE missing quarter makes the comparison five quarters wide -------
    # This is the defect the probe exists to find, planted so the detector is
    # proven to fire rather than merely to pass on clean input.
    holed = [r for r in full if r["quarter_end"] != "2024-09-30"]
    recs = period_gaps(holed)
    assert len(recs) == 1, recs
    assert not recs[0]["year_apart"], recs[0]
    assert recs[0]["cur_quarter"] == "2025-06-30", recs[0]
    assert recs[0]["cmp_quarter"] == "2024-03-31", recs[0]
    assert recs[0]["gap_days"] == 456, recs[0]["gap_days"]

    # --- too short to compute is not a mismatch ----------------------------
    assert period_gaps(full[:4]) == []
    assert period_gaps([]) == []

    # --- an unparseable date is reported, never silently counted clean ------
    bad = _rows(*[(r["visible_from"], r["quarter_end"]) for r in full])
    bad[0]["quarter_end"] = "not-a-date"
    rec = period_gaps(bad)[0]
    assert rec["gap_days"] is None and not rec["year_apart"], rec

    # --- the audit counts, and names the symbol ----------------------------
    a = audit({"CLEAN": full, "HOLED": holed})
    assert a["positions"] == 3 and a["clean"] == 2 and a["mismatched"] == 1, a
    assert a["symbols_affected"] == 1 and a["worst"][0][0] == "HOLED", a
    assert not a["duplicate_quarters"], a
    dup = full + [dict(full[-1])]
    assert audit({"DUP": dup})["duplicate_quarters"] == ["DUP"]

    # --- the mirror is exact: same picks as features_asof -------------------
    # If this drifts, the probe measures a paraphrase and proves nothing.
    seen = sorted(full, key=lambda r: r["visible_from"])
    assert seen[-1]["quarter_end"] == period_gaps(full)[-1]["cur_quarter"]
    assert seen[-LOOKBACK]["quarter_end"] == period_gaps(full)[-1]["cmp_quarter"]

    print("fundamentals_period_probe selftest ok "
          "(detector proven on a planted missing quarter)")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--symbol", help="dump one symbol's timeline and comparisons")
    ap.add_argument("--json", help="write the per-position records here")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return _selftest()

    if not fundamentals.PARSED.exists():
        print(f"no parsed fundamentals at {fundamentals.PARSED} -- this probe "
              f"needs the filing cache.\nThe selftest proves the detector "
              f"without it.")
        raise SystemExit(2)

    if a.symbol:
        raise SystemExit(0 if _dump(a.symbol.upper()) else 1)

    timelines = {}
    for p in sorted(fundamentals.PARSED.glob("*.json")):
        try:
            rows = json.loads(p.read_text())
        except json.JSONDecodeError:
            continue
        if rows:
            timelines[p.stem] = rows
    res = audit(timelines)
    ok = _report(res)
    if a.json:
        Path(a.json).write_text(json.dumps(res, indent=1, default=str))
        print(f"  -> {a.json}")
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
