#!/usr/bin/env python3
"""Do UNADJUSTED CORPORATE ACTIONS distort the live equity book?

PRE-REGISTERED BEFORE RUNNING (batch 20260824-splitaudit1).

WHY THIS EXISTS. Raw bhavcopy closes carry no adjustment for splits, bonuses
or NAV-style resets: GOLDBEES prints a -99% day on 2019-12-19 and NIFTYBEES
-90% the same day (L70's scan). The fund book was removed partly because such
series are not shares; but the EQUITY universe keeps printing them too -- 751
split-like equity days were counted in that scan. Two exposure paths for the
live bucket:

  trades     a name held across its action day books a PHANTOM move: a 1:5
             split reads as -80% and hits the -10% stop that was never really
             hit. Economically the holder lost nothing; the ledger says -80%.
  features   `rs` and `near_high` span 125 sessions, the trend gate 200. An
             action inside those windows corrupts the score of every candidate
             carrying one -- usually excluding it, sometimes ranking it.

AMENDMENT (same batch, before any result was stored -- first run crashed on a
missing trade field and never printed numbers). Two clauses tightened:

  gap guard    the histogram of detected ratios showed hundreds of x0.66-0.75
               events. Real actions sit on simple fractions (0.5, 0.2, 0.1);
               that cluster is PRICE GAPS ACROSS NON-TRADED STRETCHES --
               suspensions or missing snapshots stitched into one "daily" move
               by the corpus builder. An event whose previous bar is more than
               7 calendar days earlier is therefore NOT an action and MUST NOT
               be adjusted across: doing so would invent a continuous price
               history that the exchange never offered.
  fill index   simulate.run's CLOSED trades carry no entry_day. `held` counts
               sessions strictly after the fill up to and including the exit,
               so fill_index = exit_index - held reconstructs it exactly.

DETECTOR (fixed here, before results). A day with |close/close_prev - 1| > 25%
-- NSE EQ price bands cap real single-day moves at 20% -- AND persistence: the
NEXT close within 25% of the new basis, AND the PREVIOUS bar inside bands (a
print-pair's retrace leg is itself out-of-band, so it cannot open an event).
Persistence rejects bad-print pairs (a +50% spike that retraces -33% the next
day is two bad rows, not an event); a genuine crash cannot trip it, because
circuits make crashes STAIRCASES of -20% days, never one >25% bar. Each
detection carries its ratio; real actions cluster on simple fractions (0.5,
0.2, 0.1, 2.0...), which the report prints so nonsense shows itself.

ARMS.
  LIVE       simulate.run(**remeasure.LIVE) on the raw corpus. Sanity gate:
             this must land near the recorded reference (+2.42% CAGR, 193
             trades, batch 20260820-nonequity3). If it misses materially the
             delta columns are void -- say so, do not interpret them.
  ADJUSTED   the identical run on backward-adjusted OHLC (every bar before an
             action multiplied by that action's ratio; volume/turnover/deliv
             untouched -- rupee turnover IS continuous across a split).
             Breakout's entry._CACHE is cleared between arms: its indicators
             are keyed by symbol and would serve raw-price arrays otherwise.

DECISION FRAMING (differs from a knob test, stated in advance). This is a data
correction in the L58/L61 family: a phantom stop-out is wrong at ANY
t-statistic, so error bars decide nothing here -- they are reported as context.
If the arms diverge materially, levels move the way the guard and the ETF fix
moved them; recording the finding and deliberately re-baselining remain
separate operator steps.

REPORTED. Action census (count, ratio histogram, worst symbols); live trades
whose holding window contains their symbol's action day, with booked returns;
live entries whose scoring windows contained an action (feature corruption
count); LIVE vs ADJUSTED full-book table with Welch edge, mean +/- std err.

    python3 src/research/split_audit.py            # the measurement
    python3 src/research/split_audit.py --selftest # mechanics on fixtures
"""

import sys as _sys, pathlib as _pl
_sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[1]))
import paths  # noqa: F401  -- puts the source dirs on sys.path

import json
import statistics
import sys
from collections import Counter

import features
import selection
import simulate

BATCH = "20260824-splitaudit1"

SPLIT_JUMP_PCT = 25.0     # > any band-legal daily move; see docstring
PERSIST_PCT = 25.0        # next close must stay near the new basis
MAX_GAP_DAYS = 7          # prev bar this close = gap, not an action
MOM_WINDOW = 125          # rs / near_high lookback (clusters.py)
TREND_WINDOW = 200        # trend-gate SMA length

# The live bucket, byte-for-byte remeasure.LIVE (read, never copied).
LIVE = dict(stop_pct=selection.STOP_PCT, target_pct=selection.TARGET_PCT,
            hold=selection.HOLD_DAYS, max_pos=selection.MAX_POSITIONS,
            take_per_cluster=dict(selection.TAKE_PER_CLUSTER),
            trigger=selection.TRIGGER)

# The recorded reference the LIVE arm must roughly reproduce (L61).
REF_CAGR, REF_N = 2.42, 193


def find_actions(s):
    """-> {index: ratio} of corporate-action bars, per the pre-registered rule."""
    out = {}
    n = len(s)
    prev_move = 0.0
    for j in range(1, n - 1):
        prev = s.close[j - 1]
        move = abs(s.close[j] / prev - 1.0) * 100 if prev else 0.0
        gap_ok = (s.days[j] - s.days[j - 1]).days <= MAX_GAP_DAYS
        if (move > SPLIT_JUMP_PCT and prev_move <= SPLIT_JUMP_PCT
                and gap_ok):
            nxt = s.close[j + 1]
            if nxt and abs(nxt / s.close[j] - 1.0) * 100 <= PERSIST_PCT:
                out[j] = s.close[j] / prev
        prev_move = move
    return out


def adjusted_copy(s, actions):
    """-> new Series, OHLC backward-adjusted across every action.

    adjusted[i] = raw[i] * prod(ratio_j for every action j > i), so the series
    is continuous through the event without touching history AFTER it.
    """
    n = len(s)
    mult = [1.0] * n
    run = 1.0
    for i in range(n - 1, -1, -1):
        mult[i] = run
        if i in actions:
            run *= actions[i]
    t = features.Series(s.symbol)
    t.days = s.days
    t.turnover = s.turnover
    t.volume = s.volume
    t.deliv_pct = s.deliv_pct
    t.surveillance_known = s.surveillance_known
    t.restricted = s.restricted
    for src, dst in ((s.open, []), (s.high, []), (s.low, []), (s.close, [])):
        del dst[:]
    t.open = [v * m for v, m in zip(s.open, mult)]
    t.high = [v * m for v, m in zip(s.high, mult)]
    t.low = [v * m for v, m in zip(s.low, mult)]
    t.close = [v * m for v, m in zip(s.close, mult)]
    return t


def holds_action(trade, actions_by_sym):
    """-> (index, ratio) of the FIRST action inside the holding window.

    The fill index is exit_index - held: `held` counts sessions strictly after
    the fill up to and including the exit bar (simulate.run's definition), and
    closed trades carry no entry_day to look up.
    """
    acts = actions_by_sym.get(trade["sym"])
    if not acts:
        return None
    s = trade["_series"]
    hi = s.index_of(trade["day"])
    if hi is None or trade["held"] is None:
        return None
    lo = hi - trade["held"]
    for j in sorted(acts):
        if lo <= j <= hi:
            return j, acts[j]
    return None


def scored_through_action(trade, actions_by_sym):
    """True if an action sat inside rs(125)/trend(200) windows at the signal bar.

    The signal bar is the session BEFORE the entry fill: that is what build()
    saw when it ranked the name. Fill index reconstructed as in holds_action.
    """
    acts = actions_by_sym.get(trade["sym"])
    if not acts:
        return False
    s = trade["_series"]
    hi = s.index_of(trade["day"])
    if hi is None or trade["held"] is None:
        return False
    i_fill = hi - trade["held"]
    if i_fill < 1:
        return False
    i_sig = i_fill - 1
    return any(i_sig - TREND_WINDOW <= j < i_sig for j in acts)


def _edge(rets):
    if len(rets) < 2:
        return float("nan"), float("nan"), len(rets)
    return (statistics.fmean(rets),
            statistics.stdev(rets) / len(rets) ** 0.5, len(rets))


def _welch(a, b):
    ma, sa, na = _edge(a)
    mb, sb, nb = _edge(b)
    se = (sa ** 2 + sb ** 2) ** 0.5
    return ma - mb, se, (ma - mb) / se if se else float("nan")


def run_arms(corpus, adj_corpus, days):
    import entry as breakout_entry
    out = {}
    for name, corp in (("LIVE", corpus), ("ADJUSTED", adj_corpus)):
        breakout_entry._CACHE.clear()      # indicators key on SYMBOL only
        out[name] = simulate.run(corp, days, **LIVE)
    return out


def main(argv):
    if "--selftest" in argv:
        _selftest()
        return 0

    corpus = features.load_corpus()
    days = sorted({d for s in corpus.values() for d in s.days})

    actions_by_sym = {}
    ratios = []
    for sym, s in corpus.items():
        acts = find_actions(s)
        if acts:
            actions_by_sym[sym] = acts
            ratios.extend(round(r, 2) for r in acts.values())
    adj_corpus = dict(corpus)
    for sym, acts in actions_by_sym.items():
        adj_corpus[sym] = adjusted_copy(corpus[sym], acts)

    n_events = sum(len(v) for v in actions_by_sym.values())
    hist = Counter(ratios)
    print(f"SPLIT AUDIT  batch {BATCH}")
    print(f"symbols with actions: {len(actions_by_sym)} / {len(corpus)}   "
          f"events: {n_events}   days {days[0]}..{days[-1]}")
    print("ratio histogram (top 12): "
          + ", ".join(f"{r}:x{n}" for r, n in hist.most_common(12)))

    # attach series refs for the window scans (not used by simulate.run)
    def with_series(trades):
        out = []
        for t in trades:
            t = dict(t)
            t["_series"] = corpus[t["sym"]]
            out.append(t)
        return out

    out = run_arms(corpus, adj_corpus, days)
    live_tr = with_series(out["LIVE"]["trades"])

    crossing = [(t, holds_action(t, actions_by_sym)) for t in live_tr]
    crossing = [(t, hit) for t, hit in crossing if hit]
    scored_bad = [t for t in live_tr if scored_through_action(t, actions_by_sym)]

    print(f"\nLIVE trades total: {len(live_tr)}")
    print(f"  holding window crosses an action day: {len(crossing)}")
    for t, (j, r) in sorted(crossing, key=lambda kv: kv[0]["ret"]):
        print(f"    {t['sym']:<14} exit {t['why']:<7} ret {t['ret']:+7.2f}%  "
              f"held {t['held']:>2}d  action x{r:.2f} @ {t['_series'].days[j]}")
    print(f"  entries scored THROUGH an action (125/200-bar windows): "
          f"{len(scored_bad)}")

    la, aa = out["LIVE"], out["ADJUSTED"]
    ea, eb = _edge([t["ret"] for t in live_tr]), _edge([t["ret"] for t in aa["trades"]])
    w = _welch([t["ret"] for t in live_tr],
               [t["ret"] for t in aa["trades"]])
    print(f"\n{'arm':<10}{'CAGR':>9}{'maxDD':>8}{'n':>6}{'win':>6}"
          f"{'per trade':>20}")
    for name, r, e in (("LIVE", la, ea), ("ADJUSTED", aa, eb)):
        win = (sum(1 for t in r["trades"] if t["ret"] > 0)
               / max(len(r["trades"]), 1) * 100)
        print(f"{name:<10}{r['cagr']:>+8.2f}%{r['maxdd']:>7.1f}%"
              f"{len(r['trades']):>6}{win:>5.0f}%"
              f"{e[0]:>+10.2f} +/- {e[1]:.2f}")
    print(f"\nEDGE adjusted-live: {w[0]:+.2f}% +/- {w[1]:.2f}%  t = {w[2]:+.2f}")

    drift_ok = (abs(la["cagr"] - REF_CAGR) < 1.0
                and abs(len(la["trades"]) - REF_N) <= 15)
    print(f"\nsanity: LIVE vs recorded reference ({REF_CAGR}%, n={REF_N}): "
          f"{'OK' if drift_ok else 'DRIFTED -- deltas above are void, investigate first'}")

    log = paths.DATA / "research"
    log.mkdir(exist_ok=True)
    row = {"batch": BATCH,
           "symbols_with_actions": len(actions_by_sym),
           "events": n_events,
           "crossing": len(crossing),
           "scored_through": len(scored_bad),
           "live": {"cagr": round(la["cagr"], 2), "dd": round(la["maxdd"], 1),
                    "n": len(la["trades"])},
           "adjusted": {"cagr": round(aa["cagr"], 2), "dd": round(aa["maxdd"], 1),
                        "n": len(aa["trades"]),
                        "per_trade": (round(eb[0], 2), round(eb[1], 2))},
           "sanity_ok": bool(drift_ok)}
    (log / "split_audit.jsonl").open("a").write(json.dumps(row) + "\n")
    print(f"appended summary to {log / 'split_audit.jsonl'}")
    return 0


def _selftest():
    """Mechanics, not markets: detector, adjustment arithmetic, scans."""
    from datetime import date, timedelta
    d0 = date(2024, 1, 1)
    n = 260
    days = [d0 + timedelta(days=k) for k in range(n)]

    def mk(px_fn):
        s = features.Series("X")
        for k, d in enumerate(days):
            px = px_fn(k)
            s.days.append(d)
            s.open.append(px)
            s.high.append(px * 1.001)
            s.low.append(px * 0.999)
            s.close.append(px)
            s.volume.append(1000)
            s.turnover.append(1e6)
            s.deliv_pct.append(50.0)
            s.surveillance_known.append(True)
            s.restricted.append(False)
        return s

    # 1:5 split at k=150: 100 -> 20. Detector must find ratio 0.2; adjustment
    # must make the series continuous WITHOUT touching anything after the bar.
    spl = mk(lambda k: 100.0 + 0.05 * k if k < 150 else 20.0 + 0.01 * k)
    acts = find_actions(spl)
    assert list(acts) == [150] and abs(acts[150] - 0.2) < 1e-3, acts
    adj = adjusted_copy(spl, acts)
    assert abs(adj.close[149] - adj.close[150]) < 1e-6, \
        (adj.close[149], adj.close[150])
    assert adj.close[150:] == spl.close[150:], "post-action bars must be untouched"

    # Bad print pair: +50% spike retracing -33.3% next day -> NOT an action.
    noisy = mk(lambda k: 100.0 if k != 60 else 150.0)
    assert find_actions(noisy) == {}, find_actions(noisy)

    # A price gap across a non-traded stretch (suspension / missing snapshot)
    # is NOT an action and must never be adjusted across.
    gapped = features.Series("GAP")
    for k, d in enumerate(days):
        if 100 <= k < 120:
            continue
        px = 100.0 if k < 120 else 60.0
        gapped.days.append(d)
        gapped.open.append(px)
        gapped.high.append(px * 1.001)
        gapped.low.append(px * 0.999)
        gapped.close.append(px)
        gapped.volume.append(1000)
        gapped.turnover.append(1e6)
        gapped.deliv_pct.append(50.0)
        gapped.surveillance_known.append(True)
        gapped.restricted.append(False)
    assert find_actions(gapped) == {}, find_actions(gapped)

    # A circuit staircase (-20% x 3) is a real crash and must NOT be an action.
    crash = mk(lambda k: max(100.0 * (0.8 ** max(0, min(k - 100, 6))), 26.21))
    assert find_actions(crash) == {}, find_actions(crash)

    # Holding-window scan: an action INSIDE [fill..exit] is caught, outside is
    # not. held = exit_index - fill_index, simulate.run's definition.
    t = {"sym": "X", "day": days[160], "held": 20}
    t["_series"] = spl
    assert holds_action(t, {"X": acts}) == (150, acts[150])
    t_out = {"sym": "X", "day": days[175], "held": 20}
    t_out["_series"] = spl
    assert holds_action(t_out, {"X": acts}) is None

    # Scoring-window scan: signal bar = fill-1; an entry AFTER the split ranks
    # on windows containing it, one before does not.
    t_post = {"sym": "X", "day": days[175], "held": 20}
    t_post["_series"] = spl
    assert scored_through_action(t_post, {"X": acts})
    t_pre = {"sym": "X", "day": days[130], "held": 10}
    t_pre["_series"] = spl
    assert not scored_through_action(t_pre, {"X": acts})

    # Welch helper sanity.
    d, se, tt = _welch([1.0, 2.0, 1.0], [3.0, 4.0, 3.0])
    assert abs(d + 2.0) < 1e-9 and se > 0 and tt < 0
    print("split_audit selftest ok")


if __name__ == "__main__":
    sys.exit(main(sys.argv))
