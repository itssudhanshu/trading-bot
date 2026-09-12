#!/usr/bin/env python3
"""Is the equal-weight benchmark usable as H12's baseline? Five checks, declared first.

PRE-REGISTERED BEFORE ANY ARM WAS RUN (batch 20260912-benchmarkprobe1).

`market.equal_weight_return` was written to give H12 an alpha endpoint, because
raw return per trade cannot tell a good pick from a rising tape. It has never
been run on the real corpus. The spec says, in as many words, that **a benchmark
nobody has looked at is not a benchmark** -- this file is the looking, and the
bars below are set before the numbers exist so that "usable" cannot be decided
after seeing them.

Nothing here proposes a rule. A benchmark is measurement apparatus: it changes
what future results MEAN, never what the bucket buys.

THE FIVE CHECKS, AND THE BAR EACH MUST CLEAR
--------------------------------------------
C1  LOOKAHEAD.  Recomputing a window against a corpus truncated at `end` must
    give a byte-identical result. **Bar: identical to every digit.** This is the
    only check with a prediction of exactly zero, which makes it the strongest
    one here: a single changed digit refutes it. It is also the family that has
    cost this project most -- L58, L69 and L98 were all a fill or a filter
    reading something it could not have read.

C2  COVERAGE.  `n_used / n_asked` per window. A benchmark over 40 of 900 names
    and one over 880 are different claims. **Bar: median >= 0.90, and no single
    window below 0.50.** Below that the proxy is not measuring the universe, it
    is measuring whoever happened to print.

C3  TRUNCATION.  `n_truncated / n_used` -- the share carried at a stale last
    print by the survivorship rule. **Bar: median <= 0.05.** Above it the carry
    rule is doing more work than it was designed for and every figure quoting
    this benchmark has to report it.

C4  SURVIVORSHIP DIRECTION.  The carry rule exists on an argument, so the
    argument is now testable. **Registered prediction: dropping truncated names
    gives a HIGHER mean return than carrying them, and the gap is wider in the
    worst decile of windows than across all windows.** That is what survivorship
    bias looks like; if the gap is zero or runs the other way, the carry rule is
    solving a problem this corpus does not have and should be reported as
    unnecessary rather than quietly kept.

C5  SHAPE.  Mean against median per window, and the p5/p95 of per-symbol
    returns. **No bar** -- this one is descriptive on purpose. It is here so the
    eventual alpha figure can be read knowing whether the benchmark is a
    consensus of many names or a mean dragged by a few, and inventing a
    threshold for it after the fact is exactly the move this protocol forbids.

WHAT A FAILURE MEANS
--------------------
C1 failing stops everything: the benchmark is reading the future and no figure
built on it counts. C2 or C3 failing means the proxy is reported with its
coverage attached, or not used. C4 failing means the carry rule is re-argued in
writing, not silently dropped -- criteria may be tightened, never loosened.

    python3 src/research/benchmark_probe.py                # the real corpus
    python3 src/research/benchmark_probe.py --windows 12   # faster
    python3 src/research/benchmark_probe.py --selftest
"""
import argparse
import json
import statistics
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # -> src/
import paths

import features
import market

BATCH = "20260912-benchmarkprobe1"
HOLD = 10                # sessions -- the live hold, so the windows are the book's
WINDOWS = 24             # sampled evenly across the history
COVERAGE_MEDIAN_BAR = 0.90
COVERAGE_FLOOR = 0.50
TRUNCATION_MEDIAN_BAR = 0.05


def _truncate(corpus, end):
    """-> a copy of `corpus` holding no bar after `end`.

    The instrument for C1. Slicing every parallel column rather than filtering
    `days` alone, because a Series whose columns disagree in length is a bug
    that would show up as a wrong price rather than an error.
    """
    end_iso = market._iso(end)
    out = {}
    for sym, s in corpus.items():
        i = market._idx_asof(s, end)
        if i is None:
            continue
        cut = i + 1
        out[sym] = features.Series(
            symbol=s.symbol, days=list(s.days[:cut]), open=list(s.open[:cut]),
            high=list(s.high[:cut]), low=list(s.low[:cut]),
            close=list(s.close[:cut]), volume=list(s.volume[:cut]),
            turnover=list(s.turnover[:cut]),
            deliv_pct=list(s.deliv_pct[:cut]),
            surveillance_known=list(s.surveillance_known[:cut]),
            restricted=list(s.restricted[:cut]), rs={}, fund=[])
        assert not out[sym].days or market._iso(out[sym].days[-1]) <= end_iso
    return out


def _drop_truncated_return(corpus, symbols, start, end):
    """The C4 control arm: the same return with truncated names DROPPED.

    Deliberately a separate function rather than a flag on
    `market.equal_weight_return`. A benchmark that can quietly change its own
    survivorship rule is one edit away from being tuned, and the live path
    should have exactly one behaviour.
    """
    end_iso = market._iso(end)
    rets = []
    for sym in symbols:
        s = corpus.get(sym)
        if s is None:
            continue
        i0, i1 = market._idx_asof(s, start), market._idx_asof(s, end)
        if i0 is None or i1 is None or i1 <= i0 or not s.close[i0]:
            continue
        if market._iso(s.days[i1]) < end_iso:
            continue                      # the drop
        rets.append(s.close[i1] / s.close[i0] - 1.0)
    if not rets:
        return None
    return round(100 * statistics.fmean(rets), 4)


def _universe(corpus, as_of):
    """-> the symbols the live strategy would rank, or None if it cannot say."""
    try:
        import clusters
        return [s for band in clusters.size_clusters(corpus, as_of).values()
                for s in band]
    except Exception as e:                       # noqa: BLE001 -- reported, not raised
        print(f"  ! universe unavailable on {as_of}: {type(e).__name__}: {e}")
        return None


def probe(corpus, days, n_windows=WINDOWS, hold=HOLD, universe=None):
    """-> one row per sampled window, with every figure each check needs.

    `universe(corpus, as_of) -> [symbols]` is injectable, and that is not a
    convenience. Defaulting to the live `clusters.size_clusters` made the
    selftest depend on the active strategy producing non-empty bands from a
    synthetic corpus -- which it does not, so `probe` returned zero rows and the
    selftest's assertions sat behind `if rows:` and never executed. A test that
    passes because its body was skipped is the same defect as a check that
    passes because of the operator's shell.
    """
    universe = universe or _universe
    usable = [i for i in range(len(days) - hold)]
    if not usable:
        return []
    step = max(1, len(usable) // n_windows)
    rows = []
    for i in usable[::step][:n_windows]:
        start, end = days[i], days[i + hold]
        syms = universe(corpus, start)
        if not syms:
            continue
        ew = market.equal_weight_return(corpus, syms, start, end)
        if not ew:
            continue
        # C1 on this window, against a corpus that cannot see past `end`.
        ew_cut = market.equal_weight_return(_truncate(corpus, end), syms, start, end)
        # C5 shape, from the per-symbol returns the mean was built from.
        per = []
        for sym in syms:
            s = corpus.get(sym)
            if s is None:
                continue
            i0, i1 = market._idx_asof(s, start), market._idx_asof(s, end)
            if i0 is None or i1 is None or i1 <= i0 or not s.close[i0]:
                continue
            per.append(100 * (s.close[i1] / s.close[i0] - 1.0))
        per.sort()
        rows.append({
            "start": ew["start"], "end": ew["end"],
            "return_pct": ew["return_pct"], "median_pct": ew["median_pct"],
            "n_used": ew["n_used"], "n_asked": ew["n_asked"],
            "n_truncated": ew["n_truncated"],
            "coverage": round(ew["n_used"] / ew["n_asked"], 4) if ew["n_asked"] else 0.0,
            "trunc_rate": round(ew["n_truncated"] / ew["n_used"], 4) if ew["n_used"] else 0.0,
            "cut_return_pct": (ew_cut or {}).get("return_pct"),
            "drop_return_pct": _drop_truncated_return(corpus, syms, start, end),
            "p5": round(per[len(per) // 20], 2) if len(per) >= 20 else None,
            "p95": round(per[-max(1, len(per) // 20)], 2) if len(per) >= 20 else None,
        })
    return rows


def verdicts(rows):
    """-> {check: (passed, line)} against the bars declared in the docstring."""
    out = {}
    if not rows:
        return {"C0": (False, "no windows produced a result")}

    bad = [r for r in rows if r["cut_return_pct"] != r["return_pct"]]
    out["C1 lookahead"] = (
        not bad,
        f"{len(rows) - len(bad)}/{len(rows)} windows identical against a corpus "
        f"truncated at `end`" + (f" -- MISMATCH on {bad[0]['start']}: "
                                 f"{bad[0]['return_pct']} vs {bad[0]['cut_return_pct']}"
                                 if bad else ""))

    cov = [r["coverage"] for r in rows]
    med_cov, min_cov = statistics.median(cov), min(cov)
    out["C2 coverage"] = (
        med_cov >= COVERAGE_MEDIAN_BAR and min_cov >= COVERAGE_FLOOR,
        f"median {med_cov:.3f} (bar {COVERAGE_MEDIAN_BAR}), "
        f"worst {min_cov:.3f} (floor {COVERAGE_FLOOR}), "
        f"n_asked median {statistics.median(r['n_asked'] for r in rows):.0f}")

    tr = [r["trunc_rate"] for r in rows]
    med_tr = statistics.median(tr)
    out["C3 truncation"] = (
        med_tr <= TRUNCATION_MEDIAN_BAR,
        f"median {med_tr:.4f} (bar {TRUNCATION_MEDIAN_BAR}), worst {max(tr):.4f}")

    pairs = [(r["drop_return_pct"], r["return_pct"]) for r in rows
             if r["drop_return_pct"] is not None]
    trunc_total = sum(r["n_truncated"] for r in rows)
    if not pairs:
        out["C4 survivorship"] = (False, "no comparable windows")
    elif trunc_total == 0:
        # Three states, not two. With nothing truncated anywhere, the two arms
        # are the same arithmetic and the gap is 0.0 by construction -- that is
        # an absence of evidence about the carry rule, not evidence against it.
        # Scoring it FAIL would report a prediction as refuted by a sample that
        # could not have tested it.
        out["C4 survivorship"] = (
            None, "not applicable -- 0 truncated names in any sampled window, "
                  "so the carry rule never engaged and the arms are identical "
                  "by construction")
    else:
        gaps = [d - c for d, c in pairs]
        worst = sorted(rows, key=lambda r: r["return_pct"])[:max(1, len(rows) // 10)]
        wgaps = [r["drop_return_pct"] - r["return_pct"] for r in worst
                 if r["drop_return_pct"] is not None]
        mean_gap = statistics.fmean(gaps)
        wmean = statistics.fmean(wgaps) if wgaps else 0.0
        out["C4 survivorship"] = (
            mean_gap > 0 and wmean > mean_gap,
            f"drop-minus-carry {mean_gap:+.4f}pp overall, {wmean:+.4f}pp in the "
            f"worst decile over {trunc_total} truncated name-windows -- "
            f"prediction was positive and wider in the tail")

    spreads = [r["return_pct"] - r["median_pct"] for r in rows]
    out["C5 shape"] = (
        True,
        f"mean-minus-median {statistics.fmean(spreads):+.3f}pp; "
        f"p5/p95 of per-symbol returns "
        f"{statistics.median(r['p5'] for r in rows if r['p5'] is not None):.1f}"
        f" / {statistics.median(r['p95'] for r in rows if r['p95'] is not None):.1f}"
        if any(r["p5"] is not None for r in rows) else "too few names for p5/p95")
    return out


def _report(rows, res):
    print(f"\nbenchmark_probe  batch {BATCH}  windows {len(rows)}  hold {HOLD}\n")
    ok = True
    for name, (passed, line) in res.items():
        if name.startswith("C5") or passed is None:
            mark = "----"          # no bar, or a bar this sample cannot test
        elif passed:
            mark = "PASS"
        else:
            mark = "FAIL"
            ok = False
        print(f"  [{mark}] {name:18} {line}")
    print()
    if not ok:
        print("  At least one bar was not cleared. C1 stops everything; C2/C3 mean")
        print("  the benchmark travels with its coverage or is not used; C4 means")
        print("  the carry rule is re-argued in writing, never quietly dropped.\n")
    return ok


# --------------------------------------------------------------------------

def _selftest():
    # The whole probe runs on a synthetic cross-section, so C1 -- the check that
    # matters most and the one that cannot be deferred -- is PROVEN here rather
    # than left for a machine that has data/raw.
    corpus, days = market._fake_corpus(n_sym=14, n_bar=200)

    # --- _truncate really truncates ----------------------------------------
    cut = _truncate(corpus, days[100])
    for s in cut.values():
        assert market._iso(s.days[-1]) <= market._iso(days[100]), s.days[-1]
        n = len(s.days)
        assert len({n, len(s.close), len(s.high), len(s.low), len(s.open),
                    len(s.turnover), len(s.deliv_pct)}) == 1, "columns disagree"
    assert len(cut["SYM00"].days) == 101, len(cut["SYM00"].days)

    # --- C1 holds on a window, and the instrument can detect a breach -------
    syms = sorted(corpus)
    a = market.equal_weight_return(corpus, syms, days[50], days[60])
    b = market.equal_weight_return(_truncate(corpus, days[60]), syms, days[50], days[60])
    assert a["return_pct"] == b["return_pct"], (a, b)

    # A future bar must not be able to change a past window. If it can, C1 is
    # not measuring anything -- so prove the check has teeth by planting one.
    import copy
    tampered = copy.deepcopy(corpus)
    for s in tampered.values():
        for k in range(61, len(s.close)):
            s.close[k] *= 5.0
    c = market.equal_weight_return(tampered, syms, days[50], days[60])
    assert c["return_pct"] == a["return_pct"], \
        "a bar after `end` changed a past window -- equal_weight_return reads ahead"

    # --- the probe produces rows and every check renders --------------------
    # The universe is injected, so this exercises the real body. It previously
    # sat behind `if rows:` with the live clusters returning nothing on a
    # synthetic corpus, so none of it ran and the selftest passed anyway.
    every = lambda c, d: sorted(c)          # noqa: E731 -- the whole fixture
    rows = probe(corpus, days, n_windows=6, hold=10, universe=every)
    assert len(rows) == 6, f"the probe body did not run: {len(rows)} rows"
    assert all(r["cut_return_pct"] == r["return_pct"] for r in rows), rows[0]
    assert all(r["n_asked"] == 14 for r in rows), rows[0]
    res = verdicts(rows)
    assert set(res) >= {"C1 lookahead", "C2 coverage", "C3 truncation",
                        "C4 survivorship", "C5 shape"}, list(res)
    assert res["C1 lookahead"][0], res["C1 lookahead"]
    assert res["C2 coverage"][0], res["C2 coverage"]
    for _, line in res.values():
        assert isinstance(line, str) and line, "a verdict with no line"

    # --- a C1 breach must FAIL the verdict, not just be detectable ----------
    broken = [dict(r) for r in rows]
    broken[2]["cut_return_pct"] = broken[2]["return_pct"] + 0.01
    assert not verdicts(broken)["C1 lookahead"][0], \
        "a mismatched window must fail C1, or C1 reports nothing"

    # --- an empty result is a verdict, not a crash --------------------------
    v = verdicts([])
    assert v and not next(iter(v.values()))[0], v

    # --- C4 is inapplicable, not failed, when nothing was truncated ---------
    assert res["C4 survivorship"][0] is None, res["C4 survivorship"]
    assert "not applicable" in res["C4 survivorship"][1]
    # ...and it still reports a real verdict when the carry rule DID engage.
    engaged = [dict(r) for r in rows]
    engaged[0]["n_truncated"] = 2
    engaged[0]["drop_return_pct"] = engaged[0]["return_pct"] + 1.0
    assert verdicts(engaged)["C4 survivorship"][0] is not None, "C4 went silent"

    # --- the drop arm is a DIFFERENT arm ------------------------------------
    short = copy.deepcopy(corpus)
    s = short["SYM00"]
    s.days, s.close = s.days[:55], s.close[:55]
    s.open, s.high, s.low = s.open[:55], s.high[:55], s.low[:55]
    s.turnover, s.deliv_pct = s.turnover[:55], s.deliv_pct[:55]
    s.volume, s.surveillance_known = s.volume[:55], s.surveillance_known[:55]
    s.restricted = s.restricted[:55]
    carry = market.equal_weight_return(short, syms, days[50], days[60])
    drop = _drop_truncated_return(short, syms, days[50], days[60])
    assert carry["n_truncated"] == 1, carry
    assert drop is not None and drop != carry["return_pct"], (drop, carry)

    print("benchmark_probe selftest ok (C1 proven on synthetic data, "
          "including a planted future bar the check catches)")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--windows", type=int, default=WINDOWS)
    ap.add_argument("--hold", type=int, default=HOLD)
    ap.add_argument("--json", default=None, help="write the rows to this path")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return _selftest()

    if not features.RAW.exists():
        print(f"no corpus at {features.RAW} -- this probe needs the price history.\n"
              f"Run it on a checkout that has data/raw/; the selftest above "
              f"proves the lookahead check itself without one.")
        raise SystemExit(2)

    corpus = features.load_corpus()
    days = features.trading_days()
    print(f"corpus {len(corpus)} symbols, {len(days)} sessions")
    rows = probe(corpus, days, n_windows=a.windows, hold=a.hold)
    ok = _report(rows, verdicts(rows))
    if a.json:
        Path(a.json).write_text(json.dumps(
            {"batch": BATCH, "hold": a.hold, "rows": rows}, indent=1))
        print(f"  rows -> {a.json}")
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
