#!/usr/bin/env python3
"""H12: what is the market impact constant, measured rather than assumed?

PRE-REGISTERED 2026-08-25, before the forward book had enough closed trades to
say anything. Nothing in this file ran against a comparison before that
statement; the module's first execution prints "too early" and stops.

WHY THIS EXISTS
---------------
engine.IMPACT_C = 1.0 is an admitted guess (engine.py:41 -- "no data here to
fit it"). STATE.md has wanted calibration since 2026-08-17: "the real prize is
not paper trading, it is calibration". Every CAGR quoted by this project sits
on top of that guess, and the sensitivity table shows it moves the headline
from +11.90% (c=0) to +4.17% (c=3).

The forward paper book is quietly manufacturing the one dataset that can pin
it. A paper fill happens at the REAL opening price, so its realised net return
embeds TRUE market impact. A simulated trade at constant c embeds MODELED
impact. Comparing the two distributions across a grid of c asks: which modelled
world does reality look like?

THE COHORT IS MAIN AND ONLY MAIN
--------------------------------
The pooled book holds the same names constantly (CLAUDE.md), so pooling both
books' trades would put one price path into the sample twice. The bucket is the
record; the pool's trades are shown and never counted. Cohort membership
reuses forward_test.FORWARD_FROM verbatim so the two files always describe the
same trades.

THE COMPARISON
--------------
  paper   closed main trades entered on/after FORWARD_FROM,
          per-trade return = net / (entry_px * qty), the FULL cost stack
          included exactly as the simulation includes it.
  sims    simulate.run at the live rules with impact_c in
          {0.0, 0.5, 1.0, 2.0, 3.0}, fork-set per worker so no constant can
          leak between arms. engine.IMPACT_C itself is never written.
  stat    z(c) = (paper_mean - sim_mean_c) / sqrt(se_paper^2 + se_sim_c^2).
          A constant is CONSISTENT while |z| <= 1.96 and EXCLUDED outside it.

WHAT n BUYS, STATED BEFORE ANY OF IT ARRIVES
--------------------------------------------
Per-trade sd is ~15%, so se_paper ~ 15/sqrt(n); the simulations add ~1.1 each
in quadrature. Minimum detectable gap at |z| = 1.96:

    n=25   -> ~6.5%   nothing realistic is excludable; descriptive only
    n=60   -> ~4.0%   the c=0-vs-c=3 ends start to become visible
    n=100  -> ~3.1%   end constants excludable, the middle is not
    n=195  -> ~2.2%   adjacent-grid resolution begins

At the backtested rate of 2.86 main trades/month these gates arrive in months,
a year, two years and 5.7 years respectively. That is not a defect of the
design; it is the size of the noise on the thing being measured, and pretending
otherwise is how this project manufactured phantom findings twice (L58, L61).

GATES, in the shape of forward_test.py:
  below 25 closed   -> every arm reads "too early". No z table at all: a table
                       nobody can act on is still a table people act on.
  25..99            -> the z table prints under a DESCRIPTIVE banner. No word
                       in it may be quoted as a finding.
  >= 100            -> JUDGE: exclusions named, the consistent band stated,
                       and the live c=1.0 flagged loudly if it falls OUTSIDE
                       the band.

ADOPTION PATH: NONE. This file never flips a constant. If the band excludes
1.0, the output says so and the operator decides whether the baseline and
engine move -- a deliberate separate step, like every rebaseline before it. The
simulations here are also NOT stored via simulate.keep(): they are yardsticks,
not candidates.

    python3 src/research/impact_calibrate_test.py
"""
import sys as _sys, pathlib as _pl
_sys.path.insert(0, str(_pl.Path(__file__ ).resolve().parents[1]))
import paths  # noqa: F401  -- puts the source dirs on sys.path
import multiprocessing as mp
import sqlite3
import statistics
import sys

import engine, features, forward_test, positions, remeasure, selection, simulate

BATCH = "20260825-h12-calibrate"

GRID = (0.0, 0.5, 1.0, 2.0, 3.0)
Z_BAR = 1.96
N_DESCRIBE = 25
N_JUDGE = 100

BASE = dict(stop_pct=selection.STOP_PCT, target_pct=selection.TARGET_PCT,
            hold=selection.HOLD_DAYS, max_pos=selection.MAX_POSITIONS,
            refresh=5, trigger=selection.TRIGGER)


def paper_rows(conn):
    """-> [(ret_pct, ...)] for closed MAIN trades in the forward cohort."""
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM pos WHERE bucket='main' AND status='closed'")]
    conn.row_factory = None
    out = []
    for r in rows:
        stamp = r.get("entry_day") or r.get("queued_on")
        if not stamp or str(stamp) < forward_test.FORWARD_FROM:
            continue
        if not r.get("entry_px") or not r.get("qty"):
            continue
        denom = float(r["entry_px"]) * float(r["qty"])
        if denom <= 0 or r.get("net") is None:
            continue
        out.append(float(r["net"]) / denom * 100.0)
    return out


def _one(c):
    """Run one simulated arm at impact constant `c`, in a forked worker."""
    corpus = features.load_corpus()
    days = sorted({d for s in corpus.values() for d in s.days})
    r = simulate.run(corpus, days, impact_c=c, **BASE)
    m, se, n = remeasure.edge(r)
    return {"c": c, "mean": m, "se": se, "n": n}


def compare(paper, arms):
    """-> [(c, sim_mean, sim_se, z)] with z = paper vs that arm."""
    pm = statistics.fmean(paper)
    ps = statistics.stdev(paper) / len(paper) ** 0.5
    out = []
    for a in arms:
        se_tot = (ps ** 2 + a["se"] ** 2) ** 0.5
        z = (pm - a["mean"]) / se_tot if se_tot else float("nan")
        out.append((a["c"], a["mean"], a["se"], z))
    return out


def judge(paper, table):
    """-> (mode, lines). mode in too early / descriptive / JUDGE."""
    n = len(paper)
    if n == 0:
        return "too early", ["paper: no closed main trades in the cohort yet",
            "too early: nothing has closed since FORWARD_FROM."]
    pm = statistics.fmean(paper)
    ps = statistics.stdev(paper) / n ** 0.5
    head = f"paper: {pm:+.2f}% +/- {ps:.2f} on n={n}"
    if n < N_DESCRIBE:
        return "too early", [head,
            f"too early: {n} closed; the first descriptive table waits for "
            f"{N_DESCRIBE}, a verdict for {N_JUDGE}."]
    lines = [head]
    excluded, consistent = [], []
    for c, sm, se, z in table:
        tag = "EXCLUDED" if abs(z) > Z_BAR else "consistent"
        (excluded if abs(z) > Z_BAR else consistent).append(c)
        lines.append(f"  c={c:<3} sim {sm:+.2f}% +/- {se:.2f}   "
                     f"z={z:+.2f}   {tag}")
    if n < N_JUDGE:
        return "descriptive", lines + [
            f"descriptive only: {n} closed, judging needs {N_JUDGE}. "
            f"Minimum detectable gap ~{2 * Z_BAR * (ps ** 2 + 1.1 ** 2) ** 0.5:.1f}%."]
    lines.append(f"JUDGE: consistent band "
                 f"[{min(consistent) if consistent else '--'}.."
                 f"{max(consistent) if consistent else '--'}], "
                 f"excluded {sorted(excluded)}")
    if 1.0 in excluded:
        lines.append("  THE LIVE CONSTANT c=1.0 IS EXCLUDED BY THE FORWARD BOOK.")
        lines.append("  Explain before quoting any backtested number again; "
                     "any rebaseline is the operator's deliberate step.")
    else:
        lines.append("  live c=1.0 sits inside the consistent band.")
    return "JUDGE", lines


def main():
    conn = positions.db()
    paper = paper_rows(conn)
    print(f"H12 impact calibration -- batch {BATCH}, live rules "
          f"{BASE['stop_pct']:g}/{BASE['target_pct']:g}/{BASE['hold']}d, "
          f"cohort from {forward_test.FORWARD_FROM}, main only\n")

    mode, lines = judge(paper, [])
    if mode == "too early":
        print("\n".join(lines))
        print("\nNothing is compared before there is something to compare. "
              "This is the gate\nworking, not a failure -- see the n-table in "
              "the docstring for the dates\nthese gates arrive at the "
              "backtested trade rate.")
        return 0

    with mp.get_context("fork").Pool(min(len(GRID), mp.cpu_count() or 1)) as p:
        arms = p.map(_one, list(GRID))
    arms.sort(key=lambda a: a["c"])
    table = compare(paper, arms)
    mode, lines = judge(paper, table)
    print("\n".join(lines))
    print(f"\n{mode}: adoption path is none; engine.IMPACT_C is unchanged by "
          f"this file.")
    return 0


def _selftest():
    import tempfile
    # cohort discipline: pre-FORWARD rows, other buckets, and unclosed rows
    # are all kept out of the paper sample
    _odb = positions.DB
    with tempfile.TemporaryDirectory() as td:
        positions.DB = f"{td}/p.db"
        try:
            c = positions.db()
            c.execute("INSERT INTO pos(symbol,cluster,status,bucket,queued_on,"
                      "entry_day,entry_px,qty,net) VALUES("
                      "'OLD','micro','closed','main','2026-08-01',"
                      "'2026-08-02',100,10,-50)")
            c.execute("INSERT INTO pos(symbol,cluster,status,bucket,queued_on,"
                      "entry_day,entry_px,qty,net) VALUES("
                      "'POOL','small','closed','pooled','2026-09-01',"
                      "'2026-09-02',200,5,40)")
            c.execute("INSERT INTO pos(symbol,cluster,status,bucket,queued_on,"
                      "entry_day,entry_px,qty,net) VALUES("
                      "'NEW','micro','open','main','2026-09-01',"
                      "'2026-09-02',100,10,NULL)")
            got = paper_rows(c)
            assert got == [], f"cohort leaked: {got}"
            # the one row that qualifies is measured on net / outlay
            c.execute("INSERT INTO pos(symbol,cluster,status,bucket,queued_on,"
                      "entry_day,entry_px,qty,net) VALUES("
                      "'IN','micro','closed','main','2026-09-01',"
                      "'2026-09-02',100,10,50)")
            got = paper_rows(c)
            assert got == [5.0], f"net/outlay wrong: {got}"
            c.close()
        finally:
            positions.DB = _odb

    # z arithmetic on synthetic arms
    paper = [2.0 + _e for _e in [1, -1, 3, -3, 2, -2, 0, 1, -1, 0]]  # sd ~1.8
    arms = [{"c": 0.0, "mean": 2.0, "se": 0.0, "n": 9},
            {"c": 3.0, "mean": 12.0, "se": 0.0, "n": 9}]
    tbl = dict((r[0], r[3]) for r in compare(paper, arms))
    assert abs(tbl[0.0]) < 1.0, tbl
    assert tbl[3.0] < -Z_BAR, "an arm 10 points away must be excluded"

    # gates: too early below 25, descriptive below 100, JUDGE at 100+
    assert judge([], [])[0] == "too early", "an empty cohort must not crash"
    few = [0.1] * 24
    assert judge(few, [])[0] == "too early"
    desc = [0.1] * 30
    assert judge(desc, [(1.0, 1.0, 0.1, 0.0)])[0] == "descriptive"
    many = [0.1] * N_JUDGE
    mode, lines = judge(many, [(0.0, 1.0, 0.1, -(statistics.fmean(many) - 1.0)
                                / ((statistics.stdev(many) / 10) ** 2
                                   + 0.01) ** 0.5),
                               (1.0, statistics.fmean(many), 0.0, 0.0)])
    assert mode == "JUDGE", mode
    assert any("inside the consistent band" in l for l in lines)

    # the grid must contain the constant the engine actually ships with, or
    # the comparison could never speak about the live configuration
    assert engine.IMPACT_C in GRID, GRID
    # live rules are READ, never copied (L60's stale-copy shape)
    assert BASE["hold"] == selection.HOLD_DAYS
    # cohort alignment with the structural monitor is load-bearing
    assert forward_test.FORWARD_FROM == "2026-08-21"
    # this file stores no candidates: the sims are yardsticks, not promotions
    src = open(__file__).read().replace("NOT stored via simulate", "")
    assert "simulate." + "keep(" not in src, "keep() crept back in"
    print("impact_calibrate_test selftest ok")


if __name__ == "__main__":
    if "--selftest" in _sys.argv:
        _selftest()
    else:
        sys.exit(main())
