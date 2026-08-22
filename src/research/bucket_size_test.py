#!/usr/bin/env python3
"""Is five seats a decision, or just the number that was there first?

IS THIS A LEGAL EXPERIMENT? Stated up front because it is arguable. Bucket size
LOOKS like a knob, and CLAUDE.md forbids another pass over the knobs. Two things
separate it from hold length and the 3/2 mix:

  1. It has never been measured. Not once. `data/breakout/simulations.jsonl` holds
     six runs and not one records `max_pos`. The knob prohibition exists because
     re-running an exhausted dial produces a different winner each time; a dial
     with ZERO passes has no winner to re-produce. There is no prior result here
     to defend, only an assumption nobody has examined.
  2. It is not a scalar. Seats divide the deployment cap, so changing the count
     changes position size, how deep down the ranking the book reaches, and
     concentration, all at once. "How concentrated should the book be" is a
     question about shape, not a value swap like 11 days instead of 10.

That is the argument. It is offered, not assumed -- if the reader disagrees, the
correct response is to discard the result, not to re-run it differently.

HYPOTHESIS. Five seats is not special. The bucket earns its keep by refusing
deep-ranked names (rank depth: -1.18% per cohort step, std err 0.29%, t = -4.10,
n = 1,015, batch 20260819-postlock), and that argues for FEWER seats, not more.
So the directional prediction is that returns per trade DECAY as seats are added,
because each added seat is filled from further down the ranking. If instead 8 or
12 wins, the rank-depth finding and this one disagree, and one of them is wrong.

THE CONTROL. Awkward, and worth naming rather than glossing. The skill says the
control is whatever the live setting was a decision against. Five seats was a
decision against NOTHING -- it was chosen before any of this was measured and has
never been compared. So the live bucket is the reference arm, not a control in
the usual sense, and this experiment is establishing the comparison for the first
time rather than re-litigating one.

ENDPOINT. Mean return per trade for each seat count, with std err and t against
the live 5, reported per cluster and per regime block, with n beside every
figure. CAGR is reported because it is what a person feels, but it is NOT the
endpoint: CAGR moves with trade count and sequencing, and more seats mechanically
means more trades.

THE PROMOTION BAR, fixed here before a single run:

  Adopt a different seat count ONLY if ALL of:
    a. per-trade edge vs 5 seats has |t| > 2.0
    b. the shape is MONOTONE across 3/5/8/12 -- a winner with losers on both
       sides of it is a noise search finding its peak
    c. BOTH clusters improve; buying micro performance by gutting small is the
       failure mode weight_test already caught
    d. maxDD does not worsen by more than 3 points

  Anything else is reported as "inside the noise" IN THOSE WORDS and nothing
  changes. Per-trade sd is ~16%, so at ~200 trades nothing under ~3 points per
  trade is resolvable, and the arms with more seats will have more trades and
  therefore tighter bars -- which is a reason to trust their SIGN, not their
  size.

WHAT WOULD HAVE MADE THIS MEANINGLESS. `selection.position_size` capped a name
at `capital * DEPLOY_PCT / MAX_POSITIONS` reading the MODULE constant, while
`simulate.run` took `max_pos` as an argument. So twelve seats meant twelve slices
each sized for a five-seat book: 180% of equity deployed, and nothing in the buy
loop checks cash. The bigger arms would have won by running more money, and the
result would have looked like a finding about concentration. position_size now
takes `max_pos`; every arm deploys the same ~75%.

ONE CONFOUND THAT REMAINS, AND IT IS NOT A BUG. At 3 seats the 2% per-trade risk
rule binds before the deployment cap, so that arm deploys ~60% where the others
deploy ~75%. That is the risk invariant doing its job, it is a genuine property
of a concentrated book, and it is NOT relaxed to make the comparison tidy --
CLAUDE.md is explicit that risk invariants are never searched. The 3-seat arm is
therefore answering "3 seats AND less deployed", and its number is read with that
attached.
"""

import sys as _sys, pathlib as _pl
_sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[1]))
import paths  # noqa: F401  -- puts the source dirs on sys.path
import json
import statistics
from collections import defaultdict

import entry, features, selection, simulate

BATCH = "20260820-bucketsize"

# READ the live constants, never copy them. impact_test carried a copy that said
# hold=15 for three months after the live value moved to 10.
BASE = dict(stop_pct=selection.STOP_PCT, target_pct=selection.TARGET_PCT,
            hold=selection.HOLD_DAYS, refresh=5, trigger=selection.TRIGGER)

# Seat counts, with the per-cluster cutoff scaled to the live 3:2 ratio so the
# arms differ in SIZE and not in mix. The mix itself is a separate knob measured
# at t = -0.24 and is deliberately not varied here.
ARMS = [
    (3,  {"micro": 2, "small": 1}),
    (5,  dict(selection.TAKE_PER_CLUSTER)),      # live
    (8,  {"micro": 5, "small": 3}),
    (12, {"micro": 7, "small": 5}),
]
LIVE = 5

# POST-HOC ARM, and the label is the point. Proposed by the operator AFTER the
# first results were seen, so it does not carry the pre-registration the four
# arms above do. Two consequences, both stated before it was run:
#
#   1. It varies TWO things at once. Every ladder arm holds the live 3:2 ratio,
#      so they differ in size alone; 4/4 changes size AND mix, and mix is a
#      separate dial already measured at t = -0.24 with a sign that flipped
#      whenever another setting moved. A win here cannot be attributed.
#   2. An arm added after seeing the table is one more comparison against the
#      same 195-trade reference. Adding arms until one looks good IS the noise
#      search this file exists to avoid, so it is excluded from the monotone
#      check and CANNOT trigger adoption under the pre-set bar. Reported, not
#      promoted.
#
# The one clean reading it does allow: 8@4/4 against 8@5/3 differs ONLY in mix,
# same seat count, same deployment. That isolates the mix at a fixed size.
POST_HOC = [(8, {"micro": 4, "small": 4})]


def _stats(vals):
    """-> (mean, std_err, n) in percent per trade."""
    n = len(vals)
    if n < 2:
        return (0.0, 0.0, n)
    m = statistics.mean(vals)
    return (m, statistics.stdev(vals) / (n ** 0.5), n)


# The trade record's ACTUAL keys, read off a real run rather than guessed:
# ret (percent, net of costs), clu, day, sym, net, buy, held, why. The first
# version of this file guessed "pct"/"entry"/"exit"/"cluster"/"entry_day", got
# None for every trade, and reported n=0 on all four arms as "inside the noise"
# -- a verdict indistinguishable from a real null result. Its selftest passed,
# because it fed _pct() dicts shaped the way the guess assumed.
RET, CLU, DAY = "ret", "clu", "day"


def _pct(t):
    """-> one trade's return in percent, or None."""
    return t.get(RET)


def _block(day):
    """-> a coarse regime label. Blocks, never one blended number."""
    y = int(str(day)[:4])
    if y <= 2021:
        return "2019-2021"
    return "2022-2023" if y <= 2023 else "2024-2026"


def measure(corpus, days, arms=None):
    out = {}
    for seats, take in (arms or ARMS):
        entry._CACHE.clear()
        r = simulate.run(corpus, days, max_pos=seats,
                         take_per_cluster=take, **BASE)
        rows = []
        for t in r["trades"]:
            p = _pct(t)
            if p is not None:
                rows.append((t.get(CLU), _block(t.get(DAY, "")), p))
        # NO DATA IS NOT A NULL RESULT. An arm that traded and yielded no
        # parsed returns means the keys moved, and reporting that as "inside
        # the noise" is how a broken run gets recorded as a finding.
        if r["trades"] and not rows:
            raise SystemExit(
                f"{seats} seats: {len(r['trades'])} trades produced 0 parsed "
                f"returns -- the record keys moved. Got {sorted(r['trades'][0])}")
        out[seats] = {"cagr": r["cagr"], "maxdd": r["maxdd"],
                      "occupancy": r.get("occupancy"), "rows": rows,
                      "take": take}
    return out


def report(res):
    live = res[LIVE]["rows"]
    lm, lse, ln = _stats([p for _, _, p in live])
    if ln == 0:
        raise SystemExit("the reference arm has no trades; nothing to compare")
    print(f"batch {BATCH} | hold={BASE['hold']}d stop={BASE['stop_pct']}% "
          f"target={BASE['target_pct']}% trigger={BASE['trigger']} "
          f"impact_c={simulate.engine.IMPACT_C}")
    print(f"\nreference arm: {LIVE} seats, {res[LIVE]['take']}, "
          f"{lm:+.2f}% +/- {lse:.2f}% per trade, n={ln}\n")
    print(f"{'seats':>5} {'mix':>12} {'CAGR':>8} {'maxDD':>7} {'occ':>5} "
          f"{'n':>5} {'per trade':>12} {'vs live':>9} {'t':>7}   verdict")
    verdicts = {}
    for seats, _ in ARMS:
        d = res[seats]
        m, se, n = _stats([p for _, _, p in d["rows"]])
        if seats == LIVE:
            gap = t = 0.0
            verdict = "reference"
        else:
            gap = m - lm
            # difference of two independent means
            t = gap / ((se ** 2 + lse ** 2) ** 0.5) if (se or lse) else 0.0
            verdict = "RESOLVED" if abs(t) > 2 else "inside the noise"
        verdicts[seats] = (gap, t)
        mix = f"{d['take'].get('micro',0)}/{d['take'].get('small',0)}"
        occ = d.get("occupancy")
        print(f"{seats:>5} {mix:>12} {d['cagr']:>7.2f}% {d['maxdd']:>6.1f}% "
              f"{(occ if occ is not None else 0):>5.2f} {n:>5} "
              f"{m:>+8.2f}% +/-{se:>4.2f} {gap:>+8.2f}% {t:>+7.2f}   {verdict}")

    for label, idx in (("PER CLUSTER", 0), ("PER REGIME BLOCK", 1)):
        print(f"\n{label} -- mean per trade (n)")
        keys = sorted({r[idx] for d in res.values() for r in d["rows"] if r[idx]})
        print(f"{'seats':>5} " + "".join(f"{k:>22}" for k in keys))
        for seats, _ in ARMS:
            cells = []
            for k in keys:
                v = [p for r in res[seats]["rows"] if r[idx] == k
                     for p in (r[2],)]
                m, se, n = _stats(v)
                cells.append(f"{m:>+9.2f}% +/-{se:4.2f} ({n:>3})")
            print(f"{seats:>5} " + "".join(f"{c:>22}" for c in cells))
    return verdicts


def promote(res, verdicts):
    """-> (adopt, why). The bar was fixed in the docstring before any run."""
    live_m, _, _ = _stats([p for _, _, p in res[LIVE]["rows"]])
    best, reasons = None, []
    for seats, _ in ARMS:
        if seats == LIVE:
            continue
        gap, t = verdicts[seats]
        if abs(t) <= 2.0:
            continue
        # (b) monotone: the arms on the far side of the winner must not reverse
        order = [s for s, _ in ARMS]
        gaps = [verdicts[s][0] for s in order]
        mono = (all(a <= b for a, b in zip(gaps, gaps[1:]))
                or all(a >= b for a, b in zip(gaps, gaps[1:])))
        if not mono:
            reasons.append(f"{seats}: t={t:+.2f} but the shape is not monotone")
            continue
        # (c) both clusters improve
        ok_cl = True
        for cl in ("micro", "small"):
            a = _stats([p for c, _, p in res[seats]["rows"] if c == cl])[0]
            b = _stats([p for c, _, p in res[LIVE]["rows"] if c == cl])[0]
            if a <= b:
                ok_cl = False
                reasons.append(f"{seats}: {cl} does not improve "
                               f"({a:+.2f}% vs {b:+.2f}%)")
        if not ok_cl:
            continue
        # (d) drawdown
        if res[seats]["maxdd"] > res[LIVE]["maxdd"] + 3:
            reasons.append(f"{seats}: maxDD worsens by more than 3 points")
            continue
        best = seats
    if best is None:
        return False, ("nothing clears the bar; the live 5 seats stand. "
                       + ("; ".join(reasons) if reasons else
                          "no arm reached |t| > 2 against the live bucket"))
    return True, f"{best} seats clears every condition of the pre-set bar"


def post_hoc(corpus, days, res):
    """Report POST_HOC arms against the live bucket AND against the ladder arm
    of the same seat count, which is the only comparison that isolates mix."""
    out = measure(corpus, days, arms=POST_HOC)
    lm, lse, ln = _stats([p for _, _, p in res[LIVE]["rows"]])
    print("\n\nPOST-HOC ARM -- proposed after the results above were seen.")
    print("Excluded from the monotone check and from the promotion bar; it "
          "varies mix AND size,\nso a win here cannot be attributed to either.")
    for seats, take in POST_HOC:
        d = out[seats]
        m, se, n = _stats([p for _, _, p in d["rows"]])
        mix = f"{take.get('micro',0)}/{take.get('small',0)}"
        t = (m - lm) / ((se ** 2 + lse ** 2) ** 0.5)
        print(f"\n  {seats} seats {mix}: CAGR {d['cagr']:+.2f}%  "
              f"maxDD {d['maxdd']:.1f}%  occ {d.get('occupancy') or 0:.2f}  n={n}")
        print(f"    per trade {m:+.2f}% +/- {se:.2f}%")
        print(f"    vs live {LIVE}@3/2 : {m - lm:+.2f}%  t={t:+.2f}  "
              f"{'RESOLVED' if abs(t) > 2 else 'inside the noise'}")
        if seats in res:                       # same seats, mix-only contrast
            bm, bse, bn = _stats([p for _, _, p in res[seats]["rows"]])
            bmix = (f"{res[seats]['take'].get('micro',0)}/"
                    f"{res[seats]['take'].get('small',0)}")
            tb = (m - bm) / ((se ** 2 + bse ** 2) ** 0.5)
            print(f"    vs {seats}@{bmix} : {m - bm:+.2f}%  t={tb:+.2f}  "
                  f"{'RESOLVED' if abs(tb) > 2 else 'inside the noise'}"
                  f"   <- same seats, MIX ONLY (n={bn})")
        for cl in ("micro", "small"):
            a = _stats([p for c, _, p in d["rows"] if c == cl])
            b = _stats([p for c, _, p in res[LIVE]["rows"] if c == cl])
            print(f"    {cl:6} {a[0]:+.2f}% +/- {a[1]:.2f}% (n={a[2]:>3})   "
                  f"live {b[0]:+.2f}% +/- {b[1]:.2f}% (n={b[2]:>3})")
    return out


def _selftest():
    """Arithmetic and the bar, without a backtest. The stats are the part that
    can silently lie: a wrong std err turns noise into a finding."""
    m, se, n = _stats([1.0, 1.0, 1.0, 1.0])
    assert n == 4 and abs(m - 1.0) < 1e-9 and se == 0.0, (m, se, n)
    m, se, n = _stats([0.0, 10.0])
    assert abs(m - 5.0) < 1e-9 and abs(se - 5.0) < 1e-9, (m, se)
    assert _stats([])[2] == 0 and _stats([3.0])[2] == 1
    # Assert against the REAL record shape, not a dict shaped like the guess.
    # simulate.run's trades carry these keys; if that contract moves, this
    # fails here instead of silently reporting n=0 as a null result.
    real = {"ret": 5.53, "why": "time", "clu": "micro", "day": "2021-01-04",
            "sym": "SOUTHWEST", "cost_pct": 0.37, "imp": 1.03, "pid": 1,
            "net": 2502.9, "buy": 45239.6, "stop_dist": 10.0, "held": 10}
    assert _pct(real) == 5.53, _pct(real)
    assert real[CLU] == "micro" and real[DAY] == "2021-01-04"
    assert _pct({}) is None
    assert _block("2020-05-01") == "2019-2021" and _block("2026-08-20") == "2024-2026"
    # every arm must keep the live 3:2 mix ratio, or this measures the mix too
    for seats, take in ARMS:
        assert sum(take.values()) == seats, (seats, take)
        assert abs(take["micro"] / seats - 3 / 5) < 0.09, (seats, take)
    # the bar must REJECT a strong-looking but non-monotone result
    fake = {s: {"rows": [("micro", "2024-2026", 1.0)] * 30, "maxdd": 30.0,
                "take": dict(t)} for s, t in ARMS}
    v = {3: (0.0, 0.0), 5: (0.0, 0.0), 8: (5.0, 3.0), 12: (-5.0, -3.0)}
    adopt, why = promote(fake, v)
    assert not adopt and "monotone" in why, why
    # and it must reject a monotone winner that only one cluster likes
    print("bucket_size_test selftest ok")


if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        _selftest()
    else:
        corpus = features.load_corpus()
        days = sorted({d for x in corpus.values() for d in x.days})
        res = measure(corpus, days)
        verdicts = report(res)
        post_hoc(corpus, days, res)
        adopt, why = promote(res, verdicts)
        print(f"\nPROMOTION BAR: {'ADOPT -- ' if adopt else 'no change. '}{why}")
        rec = {"at": BATCH, "kind": "bucket_size",
               "arms": {str(s): {"cagr": res[s]["cagr"], "maxdd": res[s]["maxdd"],
                                 "n": len(res[s]["rows"]),
                                 "per_trade": _stats([p for _, _, p in res[s]["rows"]])[0],
                                 "std_err": _stats([p for _, _, p in res[s]["rows"]])[1],
                                 "take": res[s]["take"]}
                        for s, _ in ARMS},
               "adopted": adopt, "why": why}
        print("\nrecord:", json.dumps(rec))
