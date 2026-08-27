#!/usr/bin/env python3
"""Pre-earnings drift gate -- pre-registered consequence of the Univest article.

Univest article proposes pre-earnings positioning 7-10 days before results
(enter 10 days before expected results, target 5-8% pre-results, exit before
announcement). The mechanism is pre-earnings drift: informed positioning
and anticipation bid the name up into the print, so entries timed to that
window capture the drift and exit before the announcement variance.

Not measured here. No prior backtest in this repo has conditioned entries on
distance to the next expected filing, no number in CLAUDE.md rests on it,
and no weight or trigger was set looking at it. Any result therefore cannot
confirm a past finding -- it can only be a new, independently measured one.

What is being questioned: whether refusing entries whose signal day is NOT
7-10 days before the expected next filing (from fundamentals.expected_next_filing)
raises the mean return per trade. The mechanism predicts the sign, so the
test is directional: the 7-10d window should beat the unfiltered bucket on
per-trade mean. The null is that it does not -- the window is noise, or it
discards winners as often as losers.

How the date is formed: expected_next_filing(timeline, signal_day_iso) from
src/core/fundamentals.py. That function uses ONLY filings already visible on
the signal day, then last quarter-end +91d + median publication lag for THIS
company. Timeline per symbol is fundamentals.timeline(sym) -- the cached
as-of filing timeline, or [] if never built. No forward calendar is read;
NSE board-meeting schedules would be lookahead in a backtest.

Why per-company lag: 25 days at p10, 71 at p90 across 91,843 filings. A
universe-wide constant would blackout the wrong window for most names, so
the lag is per-company. If a symbol has fewer than two visible filings the
function returns None and the hypothesis has nothing to say.

CONTROL: the live configuration, byte for byte. Stop, target, hold and seats
are read from selection.py at import, never copied, so the control tracks
the live bucket if those constants move. No other rule varies. The variant
adds one entry gate and nothing else; engine.py is untouched and every risk
invariant remains exactly as the live bucket runs it.

VARIANTS (one degree of freedom):
  - control : live rules, no pre-earnings gate (the current bucket)
  - gate 7-10d : live rules plus pre-earnings gate -- trade only when
               7 <= (expected_next_filing - signal_day).days <= 10.
               The fill is still the next open; the gate is evaluated on the
               signal bar, the last close the trader could have seen.

Missing timeline -> REFUSE (strict). Without an expected date the hypothesis
has nothing to say, and allowing it would dilute the gate into the control.
A symbol with <2 visible filings, no parsed timeline, or any None is refused
for entry, never silently passed. This choice is FROZEN before running and
is part of what the selftest asserts. Exits are never blocked by this rule:
an entry filter has nothing to say about when to leave, and blocking exits
would be a risk change (CLAUDE.md: risk invariants are not searchable).

DECISION, fixed before running:
  - Per-trade edge is the statistic. Report mean +/- std err and t for each
    arm, and the gap variant-minus-control with its Welch t, overall and
    within each cluster (micro/small) and each regime block (2019-2021,
    2022-2023, 2024-2026) with n beside every figure.
  - |t| > 2 is RESOLVED, otherwise inside the noise, in those words.
  - No adoption unless the overall gap resolves at t > 2 with the predicted
    sign (gate > control). A resolved loss in the opposite direction is also
    a decision: the gate is dropped. Anything else is a null result recorded
    in docs/lessons.md and the gate is dropped -- including if CAGR alone wins
    without the error bar. This file never writes a weight, a threshold or a
    selection rule; a survivor earns one follow-up, not a promotion.
  - The live bucket (main) remains the record; the gate is a candidate, not
    a replacement, until forward paper trades shrink the error bar.

BATCH = 20260827-preearn. One variant, one threshold window (7-10d), no sweep.
"""

import sys as _sys, pathlib as _pl
_sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[1]))
import paths  # noqa: F401  -- puts the source dirs on sys.path
import multiprocessing as mp
import statistics
import sys
from collections import defaultdict

import features
import fundamentals
import remeasure
import selection
import simulate

BATCH = "20260827-preearn"

# Read, never copied -- the impact_test lesson: a copied hold said 15 for
# three months after the live value moved to 10.
BASE = dict(stop_pct=selection.STOP_PCT, target_pct=selection.TARGET_PCT,
            hold=selection.HOLD_DAYS, max_pos=selection.MAX_POSITIONS,
            refresh=5, trigger=selection.TRIGGER)

# Pre-earnings window: inclusive, in calendar days before expected filing.
PRE_EARN_LOW = 7
PRE_EARN_HIGH = 10

# --- timeline cache --------------------------------------------------------
# fundamentals.timeline reads a json file per symbol; the backtest touches
# the same names repeatedly. Cache here so the gate does not dominate runtime.
_TL_CACHE = {}

def _timeline(sym):
    tl = _TL_CACHE.get(sym)
    if tl is None:
        # fundamentals.timeline returns [] if never built; missing is normal
        try:
            tl = fundamentals.timeline(sym)
        except Exception:
            tl = []
        _TL_CACHE[sym] = tl
    return tl


def preearn_days_until(s, sig_idx):
    """-> days until expected next filing from signal bar sig_idx, or None.

    None means unknown: fewer than 2 visible filings, no timeline, or any
    date parse failure. The gate treats None as a refusal, never as a pass.
    """
    if sig_idx < 0 or sig_idx >= len(s.days):
        return None
    # signal day is the day we ranked on; fill is next open (sig+1)
    sig_day = s.days[sig_idx]
    if sig_day is None:
        return None
    sig_iso = sig_day.isoformat() if hasattr(sig_day, "isoformat") else str(sig_day)
    # timeline per symbol via fundamentals.timeline (cached above)
    tl = _timeline(s.symbol)
    if not tl:
        return None
    try:
        exp = fundamentals.expected_next_filing(tl, sig_iso)
    except Exception:
        return None
    if exp is None:
        return None
    # exp is a date, sig_day is a date
    try:
        return (exp - sig_day).days
    except Exception:
        return None


def preearn_passes(s, sig_idx):
    """Pure condition on signal bar sig_idx -> bool.

    Requires 7 <= days_until <= 10. Any unknown (None) is False -- the gate
    may only pass on positive confirmation, otherwise a variant held together
    by missing data would trade as though it were selective.
    """
    d = preearn_days_until(s, sig_idx)
    if d is None:
        return False
    return PRE_EARN_LOW <= d <= PRE_EARN_HIGH


def preearn_tradable(s, i, purpose):
    """-> tradable(s,i,purpose) for simulate.run.

    simulate calls tradable(s, i_fill, "entry") where i_fill is the fill
    bar (signal+1) and tradable(s, i, "exit") for exits. The drift window
    must be judged on the signal bar, the last close the trader could have
    traded on, so entry checks sig = i-1. Exits are never blocked by this
    rule: an entry filter has nothing to say about when to leave, and
    blocking exits would be a risk change (CLAUDE.md: risk invariants are
    not searchable). Unknowable cases are refused for entry (strict), never
    silently passed.
    """
    if purpose != "entry":
        return True
    sig = i - 1
    if sig < 0 or sig >= len(s.days):
        return False
    # Need at least sig within bounds and fill bar exists
    if i < 0 or i >= len(s.days):
        return False
    return preearn_passes(s, sig)


ARMS = [
    ("control (live, no gate)", None),
    (f"gate {PRE_EARN_LOW}-{PRE_EARN_HIGH}d pre-earn", "preearn"),
]

_C = _D = None


def _one(item):
    label, kind = item
    import entry as _entry
    _entry._CACHE.clear()
    _TL_CACHE.clear()
    kw = dict(BASE)
    if kind == "preearn":
        kw["tradable"] = preearn_tradable
    r = simulate.run(_C, _D, **kw)
    return label, kind, r


def subset_gap(a_trades, b_trades, filt):
    """Welch gap a-b within one subset -> (gap, se, t)."""
    fa = [t["ret"] for t in a_trades if filt(t)]
    fb = [t["ret"] for t in b_trades if filt(t)]
    if len(fa) < 2 or len(fb) < 2:
        return float("nan"), float("nan"), float("nan")
    ma, sa = statistics.fmean(fa), statistics.stdev(fa) / max(len(fa), 1) ** .5
    mb, sb = statistics.fmean(fb), statistics.stdev(fb) / max(len(fb), 1) ** .5
    se = (sa ** 2 + sb ** 2) ** 0.5
    d = ma - mb
    return d, se, (d / se if se else float("nan"))


def _block(day):
    y = int(str(day)[:4])
    return "2019-2021" if y <= 2021 else ("2022-2023" if y <= 2023 else "2024-2026")


def main():
    global _C, _D
    _C = features.load_corpus()
    _D = sorted({d for s in _C.values() for d in s.days})
    print(f"PRE-EARNINGS DRIFT GATE  batch {BATCH}  "
          f"window {PRE_EARN_LOW}-{PRE_EARN_HIGH}d before expected_next_filing  "
          f"{len(_C)} symbols x {len(_D)} sessions")
    print(f"live rules {BASE['stop_pct']:g}/{BASE['target_pct']:g}/"
          f"{BASE['hold']}d trig={BASE['trigger']} max_pos={BASE['max_pos']}\n")

    with mp.get_context("fork").Pool(len(ARMS)) as p:
        res = p.map(_one, ARMS)

    print(f"  {'arm':<32}{'CAGR':>9}{'maxDD':>8}{'win':>6}{'n':>6}"
          f"{'per-trade':>12}{'std err':>9}")
    for label, kind, r in res:
        m, se, _n = remeasure.edge(r)
        win = sum(1 for x in r["trades"] if x["ret"] > 0) / max(len(r["trades"]), 1) * 100
        print(f"  {label:<32}{r['cagr']:>+8.2f}%{r['maxdd']:>7.1f}%"
              f"{win:>5.0f}%{len(r['trades']):>6}{m:>+11.2f}%{se:>8.2f}%")

    (c_label, _, cr), (v_label, _, vr) = res[0], res[1]
    d, se, t = remeasure.gap(vr, cr)
    verdict = "RESOLVED" if abs(t) > 2 else "inside the noise"
    sign = "gate > control" if d > 0 else "gate < control" if d < 0 else "no diff"
    print(f"\n  variant - control: {vr['cagr'] - cr['cagr']:+.2f} CAGR pts"
          f"  {d:+.2f}%/trade  +/-{se:.2f}  t={t:+.2f}  {verdict}  ({sign})"
          f"  -- hypothesis H predicts gate > control")
    print(f"  occupancy: control {cr['occupancy']:.2f} vs gate "
          f"{vr['occupancy']:.2f} avg seats; full-book sessions "
          f"{cr['occ_full']:.0f}% vs {vr['occ_full']:.0f}%")

    print("\n  per cluster (gate - control, per trade):")
    for clu in ("micro", "small"):
        d2, se2, t2 = subset_gap(vr["trades"], cr["trades"],
                                 lambda x, c=clu: x["clu"] == c)
        nv = sum(1 for x in vr["trades"] if x["clu"] == clu)
        nc = sum(1 for x in cr["trades"] if x["clu"] == clu)
        vv = "RESOLVED" if abs(t2) > 2 else "inside the noise"
        print(f"    {clu:<6}{d2:>+7.2f}%  +/-{se2:.2f}  t={t2:+.2f}  {vv}"
              f"   (n {nv} vs {nc})")

    print("\n  per regime block (gate - control, per trade):")
    blocks = sorted({_block(x["day"]) for x in cr["trades"] + vr["trades"]})
    for b in blocks:
        d2, se2, t2 = subset_gap(vr["trades"], cr["trades"],
                                 lambda x, bb=b: _block(x["day"]) == bb)
        print(f"    {b:<10}{d2:>+7.2f}%  +/-{se2:.2f}  t={t2:+.2f}  "
              f"{'RESOLVED' if abs(t2) > 2 else 'inside the noise'}")

    print("\n  per cluster mean per trade (for reference):")
    import analysis
    for label, _, r in res:
        pc = analysis.per_cluster(r["trades"])
        print(f"    {label:<32} micro {pc.get('micro', {}).get('avg', float('nan')):+.2f}%"
              f" (n={pc.get('micro', {}).get('n', 0)})  small "
              f"{pc.get('small', {}).get('avg', float('nan')):+.2f}%"
              f" (n={pc.get('small', {}).get('n', 0)})")

    print("\n  exit mix, gate:")
    mix = defaultdict(int)
    for x in vr["trades"]:
        mix[x["why"]] += 1
    print("    " + ", ".join(f"{k} {v}" for k, v in sorted(mix.items())) if mix else "    no trades")
    print("  exit mix, control:")
    mixc = defaultdict(int)
    for x in cr["trades"]:
        mixc[x["why"]] += 1
    print("    " + ", ".join(f"{k} {v}" for k, v in sorted(mixc.items())) if mixc else "    no trades")

    for label, _, r in res:
        simulate.store(label, r, batch=BATCH)

    gate_keep = simulate.keep(v_label, vr,
                              {**BASE, "preearn_window": f"{PRE_EARN_LOW}-{PRE_EARN_HIGH}d"},
                              batch=BATCH, track="cluster",
                              note="pre-earnings drift gate 7-10d, pre-registered 20260827-preearn")
    bar_ok = gate_keep is not None
    directional = (d > 0 and abs(t) > 2)
    _, _, t_overall = remeasure.gap(vr, cr)
    print(f"\n  promotion bar (keep): {'CLEARED' if bar_ok else 'NOT cleared'}; "
          f"error bar: {'RESOLVED' if abs(t_overall) > 2 else 'inside the noise'}"
          f"  directional (gate>control): {'yes' if directional else 'no'}")
    if bar_ok and directional:
        print("  ENDPOINT: candidate stored -- forward paper trades decide next")
    else:
        print("  ENDPOINT: NULL RESULT -- gate dropped, nothing adopted")
    print(f"\n  {analysis.trades_needed(analysis.BACKTEST_EDGE)} trades are needed"
          f" to resolve a {analysis.BACKTEST_EDGE:.1f}%/trade edge; control n={len(cr['trades'])}"
          f" gate n={len(vr['trades'])}")


def _selftest():
    from datetime import date, timedelta

    # --- live constants are READ, not copied ---
    assert BASE["stop_pct"] == selection.STOP_PCT
    assert BASE["target_pct"] == selection.TARGET_PCT
    assert BASE["hold"] == selection.HOLD_DAYS
    assert BASE["max_pos"] == selection.MAX_POSITIONS
    assert BASE["trigger"] == selection.TRIGGER
    assert BATCH == "20260827-preearn"
    assert PRE_EARN_LOW == 7 and PRE_EARN_HIGH == 10

    # --- synthetic timeline for expected_next_filing -----------------------
    # 3 visible filings, last quarter_end 2024-06-30, median lag 25d
    # => expected = 2024-06-30 +91d +25d = 2024-10-24
    # Any signal day after 2024-07-25 but before that date yields same expected,
    # because no new filing has become visible.
    tl = [{"visible_from": "2024-01-25", "quarter_end": "2023-12-31"},
          {"visible_from": "2024-04-24", "quarter_end": "2024-03-31"},
          {"visible_from": "2024-07-25", "quarter_end": "2024-06-30"}]
    exp = fundamentals.expected_next_filing(tl, "2024-08-01")
    assert exp is not None and exp.isoformat() == "2024-10-24", exp
    # also from fundamentals selftest invariant
    assert fundamentals.expected_next_filing(tl, "2024-10-16").isoformat() == "2024-10-24"

    # Helper to build a Series that has a signal bar at `sig_date` and fill bar
    # at sig_date+1 (both must exist in s.days). The tradable is checked at
    # fill index i = sig_idx+1.
    def _series_for_window(sig_date, sym="SYNTH"):
        # Build a minimal series spanning sig_date-1 .. sig_date+3 so that
        # indices are well-defined and tradable can be called.
        start = sig_date - timedelta(days=2)
        days = [start + timedelta(days=k) for k in range(7)]
        s = features.Series(sym, days)
        for _ in days:
            s.open.append(100.0); s.high.append(101.0); s.low.append(99.0)
            s.close.append(100.0); s.volume.append(10000); s.turnover.append(1e6)
            s.deliv_pct.append(40.0); s.surveillance_known.append(True)
            s.restricted.append(False)
        return s

    # Monkey-patch _timeline for synthetic symbol to return our tl
    _orig = _TL_CACHE.copy()
    try:
        _TL_CACHE.clear()
        # Put synthetic timelines into cache so _timeline returns them without
        # touching fundamentals.timeline files.
        # We need per-symbol timelines: use SYMBOL -> tl mapping
        def _prep(sym, tl_data):
            _TL_CACHE[sym] = tl_data

        # --- 8d before expected (2024-10-16) -> should PASS -----------------
        sig8 = date(2024, 10, 16)  # 2024-10-24 - 2024-10-16 = 8
        s8 = _series_for_window(sig8, "SYNTH8")
        _prep("SYNTH8", tl)
        sig_idx8 = s8.days.index(sig8)
        fill_idx8 = sig_idx8 + 1
        assert preearn_days_until(s8, sig_idx8) == 8, preearn_days_until(s8, sig_idx8)
        assert preearn_passes(s8, sig_idx8) is True
        assert preearn_tradable(s8, fill_idx8, "entry") is True
        assert preearn_tradable(s8, fill_idx8, "exit") is True  # exits never blocked

        # --- 5d before expected (2024-10-19) -> should FAIL -----------------
        sig5 = date(2024, 10, 19)  # 5 days before 10-24
        s5 = _series_for_window(sig5, "SYNTH5")
        _prep("SYNTH5", tl)
        sig_idx5 = s5.days.index(sig5)
        fill_idx5 = sig_idx5 + 1
        assert preearn_days_until(s5, sig_idx5) == 5, preearn_days_until(s5, sig_idx5)
        assert preearn_passes(s5, sig_idx5) is False
        assert preearn_tradable(s5, fill_idx5, "entry") is False
        assert preearn_tradable(s5, fill_idx5, "exit") is True

        # --- 12d before expected (2024-10-12) -> should FAIL ----------------
        sig12 = date(2024, 10, 12)  # 12 days before
        s12 = _series_for_window(sig12, "SYNTH12")
        _prep("SYNTH12", tl)
        sig_idx12 = s12.days.index(sig12)
        fill_idx12 = sig_idx12 + 1
        assert preearn_days_until(s12, sig_idx12) == 12
        assert preearn_passes(s12, sig_idx12) is False
        assert preearn_tradable(s12, fill_idx12, "entry") is False

        # --- boundary 7d and 10d -> should PASS ------------------------------
        for sig, expect in [(date(2024, 10, 17), 7), (date(2024, 10, 14), 10)]:
            s = _series_for_window(sig, f"SYNTH{expect}")
            _prep(f"SYNTH{expect}", tl)
            idx = s.days.index(sig)
            assert preearn_days_until(s, idx) == expect, (sig, preearn_days_until(s, idx))
            assert preearn_passes(s, idx) is True, f"{sig} {expect}d should pass"
            assert preearn_tradable(s, idx + 1, "entry") is True

        # --- outside boundaries 6d and 11d -> should FAIL --------------------
        for sig, expect in [(date(2024, 10, 18), 6), (date(2024, 10, 13), 11)]:
            s = _series_for_window(sig, f"OUT{expect}")
            _prep(f"OUT{expect}", tl)
            idx = s.days.index(sig)
            assert preearn_days_until(s, idx) == expect
            assert preearn_passes(s, idx) is False, f"{sig} {expect}d should fail"
            assert preearn_tradable(s, idx + 1, "entry") is False

        # --- missing timeline -> REFUSE (strict) -----------------------------
        s_missing = _series_for_window(date(2024, 10, 16), "MISSING")
        _prep("MISSING", [])  # empty timeline
        idx_m = s_missing.days.index(date(2024, 10, 16))
        assert preearn_days_until(s_missing, idx_m) is None
        assert preearn_passes(s_missing, idx_m) is False
        assert preearn_tradable(s_missing, idx_m + 1, "entry") is False
        # exits still pass even when timeline missing
        assert preearn_tradable(s_missing, idx_m + 1, "exit") is True

        # --- single filing (<2) -> None -> REFUSE ---------------------------
        one = [{"visible_from": "2024-01-25", "quarter_end": "2023-12-31"}]
        s_one = _series_for_window(date(2024, 10, 16), "ONE")
        _prep("ONE", one)
        idx_one = s_one.days.index(date(2024, 10, 16))
        assert preearn_days_until(s_one, idx_one) is None
        assert preearn_passes(s_one, idx_one) is False
        assert preearn_tradable(s_one, idx_one + 1, "entry") is False

        # --- unknowable indices refuse for entry, never for exit -------------
        s_edge = _series_for_window(date(2024, 10, 16), "EDGE")
        _prep("EDGE", tl)
        assert preearn_tradable(s_edge, 0, "entry") is False  # sig = -1 out of bounds
        assert preearn_tradable(s_edge, 0, "exit") is True

        # --- ARMS shape ------------------------------------------------------
        assert ARMS[0][1] is None, "control must carry no gate"
        assert ARMS[1][1] == "preearn"
        assert "7-10" in ARMS[1][0]

        # --- subset_gap sanity -----------------------------------------------
        import random as _rnd
        _rnd.seed(3)
        xs = [_rnd.gauss(1.0, 16) for _ in range(300)]
        ta = [{"ret": x, "clu": "micro"} for x in xs[:150]]
        tb = [{"ret": x, "clu": "micro"} for x in xs[150:]]
        d, _, t = subset_gap(tb, ta, lambda x: x["clu"] == "micro")
        assert abs(t) < 2, (d, t)
        tc = [{"ret": x + 4, "clu": "micro"} for x in xs[:150]]
        d, _, t = subset_gap(tc, ta, lambda x: x["clu"] == "micro")
        assert t > 2 and d > 3, (d, t)

        # --- imports do not vary engine invariants ---------------------------
        import engine as _eng
        assert hasattr(_eng, "IMPACT_C")

    finally:
        _TL_CACHE.clear()
        _TL_CACHE.update(_orig)

    print("preearn_test selftest ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    else:
        main()
