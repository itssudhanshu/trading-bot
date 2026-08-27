#!/usr/bin/env python3
"""RSI oversold bounce in an uptrend: does it filter to better entries?

Univest proposes a swing setup where RSI(14) dips to 30-40 and then crosses
back above 40 while the stock remains in a 3-month uptrend (close > SMA60).
The article's rationale is behavioural: a pullback inside an uptrend is
accumulation, not distribution, so a bounce off oversold RSI in that context
carries a higher forward return than the average breakout entry this bucket
currently takes. The setup is familiar, widely taught, and entirely unmeasured
here -- no prior backtest in this repo has conditioned entries on RSI, and no
number in CLAUDE.md rests on it. That is precisely why it is measured first.

Why the prior number is suspect: there is no prior number. The live bucket
ranks on price momentum, delivery and liquidity, gates on 200-DMA and a 20-day
breakout, and buys at the next open unconditionally within those gates. An
RSI bounce rule has not been tested, so no published CAGR, drawdown or
per-trade mean can be quoted for it, and no weight or threshold was set
looking at it. Any resemblance between a future result and past findings would
be coincidence, not confirmation.

What is being questioned: whether refusing entries that do NOT look like an
RSI oversold bounce in an uptrend raises the mean return per trade. The
mechanism predicts the sign: pullback in uptrend -> higher forward return, so
the filtered bucket should beat the unfiltered bucket on per-trade mean. The
null is that it does not -- the filter is noise, or it discards winners as
often as losers.

CONTROL: the live configuration, byte for byte. Stop, target, hold and seats
are read from selection.py at import, never copied, so the control tracks the
live bucket if those constants move. No other rule varies. The variant adds one
entry gate and nothing else; engine.py is untouched and every risk invariant
remains exactly as the live bucket runs it.

VARIANTS (one degree of freedom):
  - control : live rules, no RSI gate (the current bucket)
  - gate    : live rules plus RSI bounce gate -- trade only when
              RSI(14) at signal-1 is 30-40, RSI(14) at signal is >40
              (cross back above 40), and close at signal > SMA60. The fill is
              still the next open; the gate is evaluated on the signal bar, the
              last close the trader could have seen.

DECISION, fixed before running:
  - Per-trade edge is the statistic. Report mean +/- std err and t for each
    arm, and the gap variant-minus-control with its Welch t, overall and
    within each cluster (micro/small) and each regime block (2019-2021,
    2022-2023, 2024-2026) with n beside every figure.
  - |t| > 2 is RESOLVED, otherwise inside the noise, in those words.
  - No adoption unless the overall gap resolves at t > 2 with the predicted
    sign (gate > control). A resolved loss in the opposite direction is also a
    decision: the gate is dropped. Anything else is a null result recorded in
    docs/lessons.md and the gate is dropped -- including if CAGR alone wins
    without the error bar. This file never writes a weight, a threshold or a
    selection rule; a survivor earns one follow-up, not a promotion.
"""

import sys as _sys, pathlib as _pl
_sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[1]))
import paths  # noqa: F401  -- puts the source dirs on sys.path
import multiprocessing as mp
import statistics
import sys
from collections import defaultdict

import analysis
import features
import remeasure
import selection
import simulate

BATCH = "20260827-rsibounce"

# Read, never copied -- the impact_test lesson: a copied hold said 15 for
# three months after the live value moved to 10.
BASE = dict(stop_pct=selection.STOP_PCT, target_pct=selection.TARGET_PCT,
            hold=selection.HOLD_DAYS, max_pos=selection.MAX_POSITIONS,
            refresh=5, trigger=selection.TRIGGER)

# --- RSI bounce gate -------------------------------------------------------
RSI_PERIOD = 14
RSI_LOW = 30.0
RSI_HIGH = 40.0
SMA_PERIOD = 60

_RSI_CACHE = {}
_SMA_CACHE = {}

def _rsi_sma(s):
    """-> (rsi_list, sma_list) for symbol s, cached per symbol."""
    r = _RSI_CACHE.get(s.symbol)
    if r is None:
        r = features.rsi(s.close, RSI_PERIOD)
        _RSI_CACHE[s.symbol] = r
    m = _SMA_CACHE.get(s.symbol)
    if m is None:
        m = features.sma(s.close, SMA_PERIOD)
        _SMA_CACHE[s.symbol] = m
    return r, m


def rsi_bounce_passes(s, sig_idx):
    """Pure condition on signal bar sig_idx -> bool.

    Requires rsi[sig-1] in [30,40], rsi[sig] > 40, close[sig] > sma60[sig].
    Any unknown (None) is a failure -- the gate may only pass on positive
    confirmation, otherwise a variant held together by missing data would
    trade as though it were selective.
    """
    rsi, sma = _rsi_sma(s)
    if sig_idx < 1 or sig_idx >= len(s.close):
        return False
    a = rsi[sig_idx - 1] if sig_idx - 1 < len(rsi) else None
    b = rsi[sig_idx] if sig_idx < len(rsi) else None
    c = sma[sig_idx] if sig_idx < len(sma) else None
    px = s.close[sig_idx] if sig_idx < len(s.close) else None
    if a is None or b is None or c is None or px is None:
        return False
    return (RSI_LOW <= a <= RSI_HIGH) and (b > RSI_HIGH) and (px > c)


def rsi_bounce_tradable(s, i, purpose):
    """-> tradable(s,i,purpose) for simulate.run.

    simulate calls tradable(s, i_fill, "entry") where i_fill is the fill
    bar (signal+1) and tradable(s, i, "exit") for exits. The bounce must be
    judged on the signal bar, the last close the trader could have traded on,
    so entry checks sig = i-1. Exits are never blocked by this rule: an
    entry filter has nothing to say about when to leave, and blocking exits
    would be a risk change (CLAUDE.md: risk invariants are not searchable).
    Unknowable cases are refused for entry (strict), never silently passed.
    """
    if purpose != "entry":
        return True
    sig = i - 1
    # Need at least sig >=1 and sig < len
    if sig < 1 or sig >= len(s.close):
        return False
    # Signal bar must itself be above its SMA and show the RSI cross.
    # If either indicator is still None (warmup) this is a refusal.
    return rsi_bounce_passes(s, sig)


ARMS = [
    ("control (live, no gate)", None),
    ("RSI bounce gate", "rsi"),
]

_C = _D = None


def _one(item):
    label, kind = item
    # Clear per-worker caches so a variant cannot leak into its sibling via
    # entry._CACHE or the RSI/SMA caches (fork gives copy, but be explicit).
    import entry as _entry
    _entry._CACHE.clear()
    _RSI_CACHE.clear()
    _SMA_CACHE.clear()
    kw = dict(BASE)
    if kind == "rsi":
        kw["tradable"] = rsi_bounce_tradable
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
    print(f"RSI-BOUNCE GATE  batch {BATCH}  "
          f"RSI({RSI_PERIOD}) {RSI_LOW:.0f}-{RSI_HIGH:.0f} cross >{RSI_HIGH:.0f} + close>SMA{SMA_PERIOD}  "
          f"{len(_C)} symbols x {len(_D)} sessions")
    print(f"live rules {BASE['stop_pct']:g}/{BASE['target_pct']:g}/"
          f"{BASE['hold']}d trig={BASE['trigger']} max_pos={BASE['max_pos']}\n")

    with mp.get_context("fork").Pool(len(ARMS)) as p:
        res = p.map(_one, ARMS)

    # Header per arm
    print(f"  {'arm':<26}{'CAGR':>9}{'maxDD':>8}{'win':>6}{'n':>6}"
          f"{'per-trade':>12}{'std err':>9}")
    for label, kind, r in res:
        m, se, _n = remeasure.edge(r)
        win = sum(1 for x in r["trades"] if x["ret"] > 0) / max(len(r["trades"]), 1) * 100
        print(f"  {label:<26}{r['cagr']:>+8.2f}%{r['maxdd']:>7.1f}%"
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
    blocks = sorted({ _block(x["day"]) for x in cr["trades"] + vr["trades"] })
    for b in blocks:
        d2, se2, t2 = subset_gap(vr["trades"], cr["trades"],
                                 lambda x, bb=b: _block(x["day"]) == bb)
        print(f"    {b:<10}{d2:>+7.2f}%  +/-{se2:.2f}  t={t2:+.2f}  "
              f"{'RESOLVED' if abs(t2) > 2 else 'inside the noise'}")

    # Also show per-cluster per-arm means for continuity
    print("\n  per cluster mean per trade (for reference):")
    for label, _, r in res:
        pc = analysis.per_cluster(r["trades"])
        print(f"    {label:<26} micro {pc.get('micro', {}).get('avg', float('nan')):+.2f}%"
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

    # Persistence: store both arms via keep (promotion bar) but gate only keeps
    # if it also resolves directionally. Never adopt on CAGR alone.
    for label, _, r in res:
        simulate.store(label, r, batch=BATCH)

    # Promotion bar for the gate (candidate storage is separate from the store
    # above). simulate.keep checks CAGR>5, DD<55, n>=150, win>=30. That is a
    # LEVEL bar; the error bar is t>2. Both must clear to earn storage, and this
    # file still adopts nothing to live trading.
    gate_keep = simulate.keep(v_label, vr,
                              {**BASE, "rsi_bounce": f"RSI{RSI_PERIOD} {RSI_LOW}-{RSI_HIGH} cross>{RSI_HIGH} + SMA{SMA_PERIOD}"},
                              batch=BATCH, track="cluster",
                              note="RSI bounce gate, pre-registered 20260827-rsibounce")
    bar_ok = gate_keep is not None
    directional = (d > 0 and abs(t) > 2)
    # Preserve t for endpoint line even if per-block loop overwrote t last
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
    assert BATCH == "20260827-rsibounce"

    # --- helper to build a Series from closes ---
    def series_from_closes(sym, closes, start=date(2024, 1, 1)):
        days = [start + timedelta(days=i) for i in range(len(closes))]
        s = features.Series(sym, days)
        for px in closes:
            px = float(px)
            s.open.append(px)
            s.high.append(px * 1.001)
            s.low.append(px * 0.999)
            s.close.append(px)
            s.volume.append(10000)
            s.turnover.append(1e6)
            s.deliv_pct.append(40.0)
            s.surveillance_known.append(True)
            s.restricted.append(False)
        return s

    # --- pure logic: RSI 35->42 cross should pass when close>SMA ---
    # Build a synthetic series long enough for SMA60 and RSI to be defined.
    # We will craft closes that produce a known RSI crossing by brute force
    # search: use features.rsi to verify, then adjust.
    _RSI_CACHE.clear(); _SMA_CACHE.clear()

    # Flat then dip then bounce is the intuitive shape; search for a pair that
    # actually yields 30-40 -> >40. Generate many random walks and pick one that
    # hits the condition, rather than hand-deriving Wilder's smoothing.
    import random as _rnd
    _rnd.seed(0)
    hit = None
    for _trial in range(5000):
        # Start around 100, then random walk with slight drift
        closes = [100.0]
        for _ in range(90):
            closes.append(max(1.0, closes[-1] * (1 + _rnd.gauss(0, 0.015))))
        s = series_from_closes("TRY", closes)
        r = features.rsi(s.close, 14)
        sm = features.sma(s.close, 60)
        for sig in range(60, len(s.close)):
            a, b, c = r[sig - 1], r[sig], sm[sig]
            if a is None or b is None or c is None:
                continue
            if 30 <= a <= 40 and b > 40 and s.close[sig] > c:
                hit = (s, sig, a, b, c)
                break
        if hit:
            break
    assert hit is not None, "synthetic search did not find an RSI 30-40->>40 + SMA pass case"
    s_hit, sig_hit, a_hit, b_hit, c_hit = hit
    # The gate must pass on this signal
    _RSI_CACHE.clear(); _SMA_CACHE.clear()
    assert rsi_bounce_passes(s_hit, sig_hit) is True, \
        f"expected pass at sig {sig_hit}: rsi {a_hit:.1f}->{b_hit:.1f} sma {c_hit:.2f} close {s_hit.close[sig_hit]}"
    # And tradable must pass on the fill bar (sig+1, entry)
    _RSI_CACHE.clear(); _SMA_CACHE.clear()
    assert rsi_bounce_tradable(s_hit, sig_hit + 1, "entry") is True
    # Exits are never blocked
    assert rsi_bounce_tradable(s_hit, sig_hit + 1, "exit") is True

    # --- same signal but SMA failure: close below SMA must be refused ---
    # Take the hitting series and make close at sig below its SMA by editing one bar
    _RSI_CACHE.clear(); _SMA_CACHE.clear()
    # Find a sig where cross holds but force SMA failure
    for sig in range(60, len(s_hit.close)):
        r, sm = _rsi_sma(s_hit)
        a, b = r[sig - 1], r[sig]
        if a is None or b is None or sm[sig] is None:
            continue
        if 30 <= a <= 40 and b > 40:
            # push close well below sma
            orig = s_hit.close[sig]
            s_hit.close[sig] = sm[sig] * 0.90
            _RSI_CACHE.clear(); _SMA_CACHE.clear()
            # rsi recomputes from closes, so RSI may shift -- re-evaluate
            # Instead craft isolated SMA failure: keep RSI pass, make SMA fail by
            # constructing a high SMA: we already edited close, but RSI changed.
            # So brute-force a different series that has cross but close<=sma.
            s_hit.close[sig] = orig
            break

    # So find a genuine SMA-fail example by searching
    _RSI_CACHE.clear(); _SMA_CACHE.clear()
    sma_fail = None
    _rnd.seed(1)
    for _trial in range(5000):
        closes = [100.0]
        for _ in range(90):
            closes.append(max(1.0, closes[-1] * (1 + _rnd.gauss(0, 0.015))))
        s = series_from_closes("TRY2", closes)
        r = features.rsi(s.close, 14)
        sm = features.sma(s.close, 60)
        for sig in range(60, len(s.close)):
            a, b, c = r[sig - 1], r[sig], sm[sig]
            if a is None or b is None or c is None:
                continue
            if 30 <= a <= 40 and b > 40 and s.close[sig] <= c:
                sma_fail = (s, sig)
                break
        if sma_fail:
            break
    assert sma_fail is not None, "did not find cross but close<=SMA fail case"
    s_fail, sig_fail = sma_fail
    _RSI_CACHE.clear(); _SMA_CACHE.clear()
    assert rsi_bounce_passes(s_fail, sig_fail) is False, \
        f"expected SMA fail at sig {sig_fail}"
    _RSI_CACHE.clear(); _SMA_CACHE.clear()
    assert rsi_bounce_tradable(s_fail, sig_fail + 1, "entry") is False

    # --- RSI non-cross must be refused (even with SMA pass) ---
    # Find a sig where RSI does NOT cross (e.g., both >50) but SMA passes
    _RSI_CACHE.clear(); _SMA_CACHE.clear()
    non_cross = None
    _rnd.seed(2)
    for _trial in range(2000):
        closes = [100.0 + i * 0.3 for i in range(90)]  # steady uptrend -> RSI high, not oversold
        s = series_from_closes("UP", closes)
        r = features.rsi(s.close, 14)
        sm = features.sma(s.close, 60)
        for sig in range(60, len(s.close)):
            a, b, c = r[sig - 1], r[sig], sm[sig]
            if a is None or b is None or c is None:
                continue
            if not (30 <= a <= 40 and b > 40) and s.close[sig] > c:
                # ensure it's not a bounce but SMA passes -> should be refused
                non_cross = (s, sig, a, b)
                break
        if non_cross:
            break
    assert non_cross is not None
    s_nc, sig_nc, a_nc, b_nc = non_cross
    _RSI_CACHE.clear(); _SMA_CACHE.clear()
    assert rsi_bounce_passes(s_nc, sig_nc) is False, \
        f"non-cross {a_nc:.1f}->{b_nc:.1f} should not pass"
    _RSI_CACHE.clear(); _SMA_CACHE.clear()
    assert rsi_bounce_tradable(s_nc, sig_nc + 1, "entry") is False

    # --- warmup / unknown must be refused for entry, never passed ---
    short = series_from_closes("SHORT", [100.0] * 10)
    _RSI_CACHE.clear(); _SMA_CACHE.clear()
    assert rsi_bounce_tradable(short, 5, "entry") is False
    assert rsi_bounce_tradable(short, 0, "entry") is False
    assert rsi_bounce_tradable(short, 1, "exit") is True  # exits never blocked even when unknown

    # --- ARMS shape ---
    assert ARMS[0][1] is None, "control must carry no gate"
    assert ARMS[1][1] == "rsi"
    assert ARMS[1][0] == "RSI bounce gate"

    # --- imports do not vary engine invariants ---
    import engine as _eng
    assert hasattr(_eng, "IMPACT_C")

    print("rsi_bounce_test selftest ok")


if __name__ == "__main__":
    if "--selftest" in _sys.argv:
        _selftest()
    else:
        main()
