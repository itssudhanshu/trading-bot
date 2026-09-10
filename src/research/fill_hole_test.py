#!/usr/bin/env python3
"""Does the backtest fill entries at a bar printed after the day it records?

PRE-REGISTERED BEFORE ANY TREATMENT ARM RAN (batch 20260911-fillhole1). Same
family as L58 (the circuit-lock guard), L69 (delisted funds in the universe) and
L71's own residue -- "the engine would also mis-fill a stop THROUGH a suspension
gap ... same family, not yet measured". That was the EXIT side. This is the
ENTRY side, and it is the one L71 did not name.

THE DEFECT, in two lines of `simulate.run`:

    e = s.open[i + 1]              # the SYMBOL's next printed bar
    "entry_day": days[di + 1]      # the CALENDAR's next session

Those are the same date only while the symbol trades on both sessions. When a
symbol has a bar on session d and none on d+1 -- suspension, illiquidity, a
delisting followed by a relisting -- the price comes from the far side of the
hole while the position is booked as entered the next morning. Call that a
**fill hole**: the trade's recorded entry day is a session the symbol did not
trade on.

WHAT IS ALREADY KNOWN, MEASURED BEFORE REGISTRATION (descriptive, not the
endpoint; the CONTROL arm is today's code, so running it first changes nothing
that can be decided after the fact):

  corpus       2,815,629 symbol bar-pairs, 4,819 (0.17%) where the symbol's
               next bar is later than the next session. The tail is severe:
               UNITECH 2019-11-20 -> 2025-10-07 (1,453 sessions skipped),
               SICAGEN 2021-07-20 -> 2026-04-20 (1,168), AHLWEST (1,098),
               ARIHANT (1,034). Re-derived by this module, not quoted.
  live config  2 of 195 closed trades (1.0%): MBAPL signalled 2022-05-27, booked
               as entered 2022-05-30, actually filled at the open of 2023-02-06
               -- 172 sessions later -- and SIMPLEXINF, 5 sessions.

WHY THE DIRECTION IS NOT PREDICTED, and saying so is the point. L58 and L69 both
removed fills that FLATTERED the record, and it would be easy to write that
expectation down here and then read the result as confirming it. This defect has
two effects that pull opposite ways:

  against the book   the seat and its capital are occupied from the recorded
                     entry day until the symbol trades again -- 172 sessions for
                     MBAPL -- during which the bucket runs four seats, not five.
                     That is a penalty today's code already pays.
  for the book       the fill price is chosen with information the signal day
                     could not have had. A resumption print is not a price
                     anyone could have bought at on the morning the trade claims.

So: **no directional prediction.** Registered instead --

  R1  the headline moves by less than 1.0 CAGR point;
  R2  the per-trade edge gap does not clear |t| > 2 (2 directly affected trades
      out of 195 cannot resolve a per-trade difference at a ~16% per-trade sd);
  R3  the corrected arm takes FEWER OR EQUAL trades than the control at the
      fill step, since the rule only ever refuses fills.

R1 and R2 are predictions about magnitude, not about sign, and neither one
decides adoption -- see DECISION FRAMING.

ARMS (the last three had not been run when this docstring was written):

  LEGACY      today's code, `fill_gap="legacy"`. The CONTROL: it is what the
              recorded baseline was measured on. Gated -- it must reproduce
              `simulate.run(**LIVE)` with the argument omitted, exactly, or the
              hook is not inert and every delta below is void.
  NEXT_SESSION  the registered fix. A fill is taken only if the symbol has a bar
              on `days[di + 1]`; otherwise no fill, and THE SEAT IS CONSUMED.
              Consumed, not passed down the ranking: at the signal close the
              book queues one order per free seat and cannot know which of them
              will fail to fill tomorrow, so reaching deeper would be choosing
              today's substitute with tomorrow's news. This also subsumes the
              existing `i + 1 >= len(s)` case -- a signal on a symbol's last
              print ever -- which today falls through and buys a worse name.
  NEXT_SESSION_FALLTHROUGH  the same test, but the loop reaches deeper down the
              ranking to refill the seat. A SENSITIVITY, reported and never
              adopted: it is the lookahead the paragraph above rejects, and it
              is here only to say how much of the primary arm's move is the
              refused fill and how much is the cash.
  TRUE_DAY    keep the fill, relabel it: `entry_day` becomes the bar the price
              actually came from. A DIAGNOSTIC. It is what the forward book
              does mechanically -- `positions.step` fills a pending order at the
              open of whatever session the symbol next trades and stamps
              `entry_day` with THAT date -- but no book leaves a market order
              live for 172 sessions on a signal that expired after 10, so it is
              not a candidate rule. It separates the mislabelling from the fill.

CONTROL. `LEGACY`, because taking the symbol's next print regardless of date was
a decision against requiring the calendar's next session -- not against doing
nothing.

AMENDMENT 1 (after the first run, before anything was adopted). R3's PROSE and
R3's CHECK were not the same prediction. The sentence says "takes FEWER OR EQUAL
trades than the control"; the code compares HOLE counts. Both are now reported
and scored separately, and the prose one FAILED: refusing a fill releases the
seat for a later signal, so the corrected arm takes MORE trades, not fewer. The
sentence was wrong about the mechanism, which is worth more than quietly
rewriting it to match the code would have been.

AMENDMENT 2 (after `simulate.FILL_GAP` moved to "next_session"). The inertness
gate asserted `fill_gap="legacy"` equals the argument omitted -- true only while
legacy WAS the default. Re-derived, not overwritten: the gate now asserts the
omitted argument equals the arm named by `simulate.FILL_GAP`, AND asserts the
property the fix exists for (the default arm books no trade on a day its symbol
did not trade; the legacy arm books at least one). Nothing about the arms, the
registered predictions or the decision framing moved.

DECISION FRAMING. This is an L58-family data correction, and error bars do not
get a vote on it. A trade recorded as entered on a day its symbol did not trade
is wrong at t = 0 exactly as it is wrong at t = 3; the noise discipline exists to
stop this project preferring rule A over rule B on one path's arithmetic, and
that is not the question here. The number is the CONSEQUENCE, reported with its
error bar so the consequence is not overstated. Adoption of NEXT_SESSION does not
depend on the sign or the size of the move.

REPORTING. Per cluster and per regime block, never one blended number, with n
beside every figure -- and every fill hole the control arm bought, named.

AFTERWARDS, and as separate deliberate steps: re-record the baseline only with
`python3 src/ops/audit.py --rebaseline`, and write the finding into
docs/lessons.md with its sample size.

    python3 src/research/fill_hole_test.py             # the measurement
    python3 src/research/fill_hole_test.py --selftest  # mechanics on fixtures
"""

import sys as _sys, pathlib as _pl
_sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[1]))
import paths  # noqa: F401  -- puts the source dirs on sys.path

import json
import statistics
import sys

import features
import selection
import simulate

BATCH = "20260911-fillhole1"

# Read the live constants, never copy them: impact_test carried a hardcoded
# hold=15 for three months after the live value moved to 10.
LIVE = dict(stop_pct=selection.STOP_PCT, target_pct=selection.TARGET_PCT,
            hold=selection.HOLD_DAYS, max_pos=selection.MAX_POSITIONS,
            take_per_cluster=dict(selection.TAKE_PER_CLUSTER),
            trigger=selection.TRIGGER)

ARMS = [("LEGACY", "legacy"),
        ("NEXT_SESSION", "next_session"),
        ("NEXT_SESSION_FALLTHROUGH", "next_session_fallthrough"),
        ("TRUE_DAY", "true_day")]


def corpus_prevalence(corpus, days):
    """-> (bar_pairs, holes, worst) over the WHOLE corpus, not the ledger.

    Descriptive: it says how much of the price data can express the defect, not
    how much of it the bucket ever bought. Those differ by three orders of
    magnitude and conflating them would overstate this finding badly.
    """
    pos = {d: k for k, d in enumerate(days)}
    pairs = holes = 0
    worst = []
    for s in corpus.values():
        for k in range(len(s.days) - 1):
            pairs += 1
            a, b = s.days[k], s.days[k + 1]
            ia, ib = pos.get(a), pos.get(b)
            if ia is None or ib is None or ib == ia + 1:
                continue
            holes += 1
            worst.append((ib - ia - 1, s.symbol, a, b))
    worst.sort(reverse=True)
    return pairs, holes, worst[:8]


def _edge(rets):
    if len(rets) < 2:
        return float("nan"), float("nan"), len(rets)
    return (statistics.fmean(rets),
            statistics.stdev(rets) / len(rets) ** 0.5, len(rets))


def _fmt_edge(rets):
    m, se, n = _edge(rets)
    if n < 2:
        return f"{'--':>10}  n={n}"
    return f"{m:>+9.2f} +/- {se:4.2f}  n={n:>3}"


def run_arms(corpus, days):
    import entry as breakout_entry
    out = {}
    for name, policy in ARMS:
        # entry._CACHE keys on symbol and would serve one arm's indicators to
        # the next; suspension_probe learned this the same way.
        breakout_entry._CACHE.clear()
        out[name] = simulate.run(corpus, days, fill_gap=policy, **LIVE)
    breakout_entry._CACHE.clear()
    out["_DEFAULT"] = simulate.run(corpus, days, **LIVE)
    return out


def blocks(trades, days, n=4):
    """Four equal calendar blocks, by exit day. A total is not a finding when
    one period supplied all of it."""
    cuts = [days[int(len(days) * k / n)] for k in range(1, n)] + [days[-1]]
    out, lo = [], days[0]
    for hi in cuts:
        out.append((lo, hi, [t for t in trades if lo <= t["day"] <= hi]))
        lo = hi
    return out


def main(argv):
    if "--selftest" in argv:
        _selftest()
        return 0

    corpus = features.load_corpus()
    days = sorted({d for s in corpus.values() for d in s.days})
    print(f"FILL HOLE TEST  batch {BATCH}")
    print(f"corpus {len(corpus)} symbols, {len(days)} sessions "
          f"{days[0]} .. {days[-1]}\n")

    pairs, holes, worst = corpus_prevalence(corpus, days)
    print(f"CORPUS PREVALENCE  {holes:,} of {pairs:,} bar-pairs "
          f"({holes / max(pairs, 1) * 100:.2f}%) skip at least one session")
    for gap, sym, a, b in worst:
        print(f"  {sym:<14} {a} -> {b}   {gap:>5} sessions skipped")

    out = run_arms(corpus, days)
    ctl = out["LEGACY"]
    dfl = out["_DEFAULT"]

    # GATE, re-derived when the default moved (amendment 2). Two halves: the
    # omitted argument really is the policy `simulate.FILL_GAP` names, and the
    # policies are not decorative -- one of them books trades on days their
    # symbols did not trade and the other does not.
    live_arm = next(n for n, p in ARMS if p == simulate.FILL_GAP)
    same = out[live_arm]
    matched = (round(same["cagr"], 6) == round(dfl["cagr"], 6)
               and len(same["trades"]) == len(dfl["trades"])
               and round(same["equity"], 6) == round(dfl["equity"], 6))
    mislabelled = {n: [t for t in out[n]["trades"]
                       if corpus[t["sym"]].index_of(t["entry_day"]) is None]
                   for n, _ in ARMS}
    works = not mislabelled[live_arm] and bool(mislabelled["LEGACY"])
    ok = matched and works
    print(f"\nGATE  the omitted argument == {live_arm} "
          f"(simulate.FILL_GAP = {simulate.FILL_GAP!r}): "
          f"{'OK' if matched else 'MISMATCH'}")
    print(f"GATE  trades booked on a day the symbol did not trade: "
          f"LEGACY {len(mislabelled['LEGACY'])}, {live_arm} "
          f"{len(mislabelled[live_arm])}: "
          f"{'OK' if works else 'the policy is doing nothing'}")
    if not ok:
        print("      every delta below is void")
    inert = ok

    # Every fill hole the control arm actually bought, named.
    gaps = ctl["fill_gaps"]
    print(f"\nLIVE CONFIG: the control took {len(gaps)} fill hole(s) at the "
          f"fill step")
    pos = {d: k for k, d in enumerate(days)}
    for sig, sym, fill in gaps:
        skipped = (pos[fill] - pos[sig] - 1) if fill in pos else None
        print(f"  {sym:<14} signal {sig}  booked-entry {days[pos[sig] + 1]}  "
              f"actual-fill {fill}  ({skipped} sessions skipped)")
    # ...and which of them survived into the closed ledger.
    held = [t for t in ctl["trades"]
            if corpus[t["sym"]].index_of(t["entry_day"]) is None]
    print(f"  of which reached the closed ledger: {len(held)} of "
          f"{len(ctl['trades'])} trades ({len(held) / max(len(ctl['trades']), 1) * 100:.1f}%)")
    for t in held:
        print(f"    {t['sym']:<14} exit {t['day']} {t['why']:<8} "
              f"ret {t['ret']:+7.2f}%  cluster {t['clu']}")

    print(f"\n{'arm':<26}{'CAGR':>9}{'maxDD':>8}{'n':>6}{'win':>6}"
          f"{'per trade':>24}{'holes':>7}")
    ce = _edge([t["ret"] for t in ctl["trades"]])
    for name, _ in ARMS:
        r = out[name]
        rets = [t["ret"] for t in r["trades"]]
        win = sum(1 for x in rets if x > 0) / max(len(rets), 1) * 100
        print(f"{name:<26}{r['cagr']:>+8.2f}%{r['maxdd']:>7.1f}%"
              f"{len(rets):>6}{win:>5.0f}%{_fmt_edge(rets):>24}"
              f"{len(r['fill_gaps']):>7}")

    print(f"\n{'arm':<26}{'edge vs LEGACY':>18}{'t':>8}   verdict")
    for name, _ in ARMS[1:]:
        e = _edge([t["ret"] for t in out[name]["trades"]])
        se = (ce[1] ** 2 + e[1] ** 2) ** 0.5
        d = e[0] - ce[0]
        t = d / se if se else float("nan")
        print(f"{name:<26}{d:>+13.2f} +/- {se:4.2f}{t:>+8.2f}   "
              f"{'RESOLVED' if abs(t) > 2 else 'inside the noise'}")

    print("\nPER CLUSTER (mean per trade +/- std err)")
    print(f"  {'arm':<26}{'micro':>26}{'small':>26}")
    for name, _ in ARMS:
        row = f"  {name:<26}"
        for clu in ("micro", "small"):
            row += f"{_fmt_edge([t['ret'] for t in out[name]['trades'] if t['clu'] == clu]):>26}"
        print(row)

    print("\nPER REGIME BLOCK (mean per trade +/- std err, by exit day)")
    blk = blocks(ctl["trades"], days)
    print(f"  {'block':<26}" + "".join(f"{f'{lo}..{hi}':>26}"
                                       for lo, hi, _ in blk))
    for name, _ in ARMS:
        row = f"  {name:<26}"
        for lo, hi, _ in blk:
            row += f"{_fmt_edge([t['ret'] for t in out[name]['trades'] if lo <= t['day'] <= hi]):>26}"
        print(row)

    # Registered predictions, scored.
    fix = out["NEXT_SESSION"]
    r1 = abs(fix["cagr"] - ctl["cagr"]) < 1.0
    fe = _edge([t["ret"] for t in fix["trades"]])
    fse = (ce[1] ** 2 + fe[1] ** 2) ** 0.5
    ft = (fe[0] - ce[0]) / fse if fse else float("nan")
    r2 = abs(ft) <= 2
    r3 = len(fix["fill_gaps"]) <= len(ctl["fill_gaps"])
    r3_prose = len(fix["trades"]) <= len(ctl["trades"])
    print("\nREGISTERED PREDICTIONS")
    print(f"  R1 headline moves < 1.0 CAGR point: "
          f"{'HELD' if r1 else 'FAILED'} "
          f"({ctl['cagr']:+.2f}% -> {fix['cagr']:+.2f}%, "
          f"moved {abs(fix['cagr'] - ctl['cagr']):.2f})")
    print(f"  R2 per-trade gap stays inside the noise: "
          f"{'HELD' if r2 else 'FAILED'} (t = {ft:+.2f})")
    print(f"  R3 as CHECKED -- the rule only ever refuses fills: "
          f"{'HELD' if r3 else 'FAILED'} "
          f"({len(fix['fill_gaps'])} holes <= {len(ctl['fill_gaps'])})")
    print(f"  R3 as WRITTEN -- fewer or equal trades: "
          f"{'HELD' if r3_prose else 'FAILED'} "
          f"({len(fix['trades'])} vs {len(ctl['trades'])}; a refused fill "
          f"releases the seat for a later signal, so the count can RISE)")

    bf = paths.SDATA / "baseline.json"
    if bf.exists():
        old = json.loads(bf.read_text())
        grew = len(days) - old["sessions"]
        print(f"\nRECORDED BASELINE  {old['cagr']:+.2f}% / {old['maxdd']}% DD / "
              f"n={old['n']} at {old['sessions']} sessions; the control reads "
              f"{ctl['cagr']:+.2f}% / {ctl['maxdd']:.1f}% / n={len(ctl['trades'])} "
              f"at {len(days)} ({grew:+d} sessions of ordinary drift)")

    log = paths.DATA / "research"
    log.mkdir(exist_ok=True)
    row = {"batch": BATCH, "sessions": len(days),
           "corpus_pairs": pairs, "corpus_holes": holes,
           "gate_inert": bool(inert),
           "arms": {name: {"cagr": round(out[name]["cagr"], 2),
                           "dd": round(out[name]["maxdd"], 1),
                           "n": len(out[name]["trades"]),
                           "holes": len(out[name]["fill_gaps"])}
                    for name, _ in ARMS},
           "predictions": {"R1": bool(r1), "R2": bool(r2), "R3": bool(r3),
                           "R3_as_written": bool(r3_prose)},
           "control_holes": [[str(a), b, str(c)] for a, b, c in gaps]}
    (log / "fill_hole_test.jsonl").open("a").write(json.dumps(row) + "\n")
    print(f"\nappended summary to {log / 'fill_hole_test.jsonl'}")
    return 0


def _selftest():
    """Mechanics on a fixture: inertness, that the hole is DETECTED, that each
    policy does what its name says, and that the seat is consumed rather than
    passed down the ranking.

    `max_pos=3` against a 3 micro / 2 small allocation is not decoration. The
    allocator returns at most `sum(TAKE_PER_CLUSTER)` = 5 rows, so with all five
    seats free there is nothing below the list to fall through TO and the two
    policies are identical by construction. The difference only exists while the
    bucket is partly occupied -- which is most of the time, at 3.09/5 occupancy.
    """
    from datetime import date, timedelta
    d0 = date(2024, 1, 1)
    days = [d0 + timedelta(days=k) for k in range(420)]
    # The first pick session is 301, so the hole opens on 302 -- the session its
    # fills are stamped with. A hole further along would never be reached: the
    # seats are all taken by then.
    HOLE = set(range(302, 313))

    def _corpus(prefix, hole_names=()):
        out = {}
        for j in range(30):
            sym = f"{prefix}{j:02d}"
            s = features.Series(sym)
            for k in range(420):
                if sym in hole_names and k in HOLE:
                    continue
                px = 100.0 + j * 0.001 * k
                if k >= 301:
                    px = 100.0 + min(k - 300, 10) * 3.0
                if k >= 311:
                    px = max(130.0 - (k - 310), 60.0)
                s.days.append(days[k])
                s.open.append(px)
                s.high.append(px * 1.001)
                s.low.append(px * 0.999)
                s.close.append(px); s.volume.append(1000)
                s.turnover.append(1e6 * (j + 1)); s.deliv_pct.append(40.0 + j)
                s.surveillance_known.append(True); s.restricted.append(False)
            out[sym] = s
        return out

    kw = dict(start_idx=301, trigger="none", impact_c=0.0, stop_pct=10.0,
              target_pct=100.0, hold=60, refresh=1, max_pos=3)

    clean = _corpus("C")
    base = simulate.run(clean, days, **kw)
    assert base["trades"], "control took no trades; the fixture is broken"
    assert base["fill_gaps"] == [], \
        f"a gapless corpus reported fill holes: {base['fill_gaps']}"
    # ...and the guard must change nothing when there is nothing to guard.
    assert simulate.run(clean, days, fill_gap="next_session",
                        **kw)["equity"] == base["equity"], \
        "the guard moved a corpus that has no holes"

    # 27 of 30 names miss 302..312, so the pick made on session 301 has no bar
    # on 302 and today's code fills it at the open of 313 -- eleven sessions
    # later. Three names keep trading throughout: a corpus where EVERY name
    # vanishes for eleven sessions has no market at all on those days, and
    # clusters.score cannot rank an empty universe.
    holed = _corpus("H", hole_names={f"H{j:02d}" for j in range(27)})
    legacy = simulate.run(holed, days, fill_gap="legacy", **kw)
    default = simulate.run(holed, days, **kw)
    # Re-derived when the default moved from "legacy" to "next_session" (L98):
    # the omitted argument must be the policy `simulate.FILL_GAP` NAMES, and on
    # a corpus that HAS holes the old policy must differ -- otherwise the
    # parameter is decoration and the arms above compared nothing.
    named = simulate.run(holed, days, fill_gap=simulate.FILL_GAP, **kw)
    assert named["equity"] == default["equity"] and \
        len(named["trades"]) == len(default["trades"]), \
        f"the omitted argument is not simulate.FILL_GAP ({simulate.FILL_GAP!r})"
    assert legacy["equity"] != default["equity"], \
        "legacy and the live policy agree on a corpus full of holes; the " \
        "fill_gap parameter is doing nothing"
    assert legacy["fill_gaps"], \
        "the fixture has an 11-session hole and nothing detected it"
    # The counter MEASURES without acting: legacy detected them and filled anyway.
    bad = [t for t in legacy["trades"]
           if holed[t["sym"]].index_of(t["entry_day"]) is None]
    assert bad, "the fixture never produced a mislabelled fill to correct"

    fixed = simulate.run(holed, days, fill_gap="next_session", **kw)
    # THE PROPERTY, not the trade count: every position the fixed arm holds has
    # a bar on the day it is booked as entered.
    for t in fixed["trades"]:
        assert holed[t["sym"]].index_of(t["entry_day"]) is not None, \
            f"{t['sym']} booked an entry on {t['entry_day']}, a day it did " \
            f"not trade"

    # TRUE_DAY keeps the fill and moves the label onto the bar it came from.
    trued = simulate.run(holed, days, fill_gap="true_day", **kw)
    assert trued["trades"], "true_day took no trades; it must still fill"
    for t in trued["trades"]:
        assert holed[t["sym"]].index_of(t["entry_day"]) is not None, \
            f"true_day left {t['sym']} on {t['entry_day']}"

    # The seat. Holes in half the names, so session 301 ranks a mixture: the
    # holed ones must EAT a seat under the live policy and be REPLACED under the
    # sensitivity one.
    half = _corpus("M", hole_names={f"M{j:02d}" for j in range(0, 30, 2)})
    consumed = simulate.run(half, days, fill_gap="next_session", **kw)
    fell = simulate.run(half, days, fill_gap="next_session_fallthrough", **kw)
    on = lambda r: {t["sym"] for t in r["trades"] if t["entry_day"] == days[302]}
    assert len(on(consumed)) < len(on(fell)), \
        (f"the seat was not consumed: {sorted(on(consumed))} vs "
         f"{sorted(on(fell))} -- or the fixture ranked no holed name in the "
         f"top {kw['max_pos']}")
    assert on(consumed) <= on(fell), (sorted(on(consumed)), sorted(on(fell)))
    assert consumed["fill_gaps"] == fell["fill_gaps"], \
        "the two policies disagreed about WHICH fills are holes"

    assert corpus_prevalence(clean, days)[1] == 0
    assert corpus_prevalence(holed, days)[1] > 0
    print("fill_hole_test selftest ok")


if __name__ == "__main__":
    sys.exit(main(sys.argv))
