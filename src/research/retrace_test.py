#!/usr/bin/env python3
"""H15: does the depth of the pullback before a breakout carry anything?

PRE-REGISTERED 2026-08-26, after the detectors were frozen in their own commit
(367f0ab9) and before any return was computed against any arm.

THE HYPOTHESIS
--------------
Third family from the operator's chart-pattern review, and the first where the
literatures genuinely OPPOSE each other. Fibonacci practice buys deep
retracements -- the 61.8% "golden" discount. Trend-following practice prefers
shallow pullbacks -- the high-tight-flag reading. Both are tested as gates on
the live breakout trigger; neither is testable standalone in this book (a
long-only momentum bucket cannot buy "price is 30% off its high" on its own).

THE DIRECTION THAT CARRIES THE ADOPTION PATH, STATED BEFORE THE RUN
-------------------------------------------------------------------
`pb_shallow` is the PRIMARY because this book is momentum by construction:
200-DMA gate, new-high trigger, score built on relative strength. The
deep-discount reading asks to buy weakness inside a strength system; if it
won anyway, adopting it would require its own fresh pre-registration, not a
quiet swap after seeing which side won. That is why `pb_deep` runs as
description only even though the family is two-sided.

THE CONTROL, NAMED EXPLICITLY
-----------------------------
`breakout` -- the incumbent trigger, read from selection.TRIGGER, never typed.

THE MECHANISM ARM
-----------------
Every arm TIGHTENS the trigger. The coin has now priced that confound at ~zero
twice (L74 +0.09%, L75 -0.02%) and runs again at matched rate for one fork --
"it read zero twice" stays an assumption until it is measured a third time.
Rate matched from firing COUNTS only.

THE ARMS
--------
  pb_shallow   PRIMARY: breakout AND pullback depth < 15%. Depth = decline
               from the trailing PB_WINDOW=40-bar high to its deepest later
               low, signal bar excluded so today's pop cannot erase its own
               dip. A high on the newest bar is depth zero -- nothing yet to
               recover from, the extreme shallow case.
  pb_deep      description only: breakout AND depth >= 30% -- the Fibonacci
               flush reading.
  coin         mechanism reference, no adoption path ever.

Thresholds 15/30 are round numbers fixed in the freeze commit: 15% is the
scale of this book's own stop, 30% is unambiguously a real flush. Not tuned.

ENDPOINT AND BAR
----------------
Mean per-trade return, arm minus control, +/- std err and t, per size group
and per half-year block, n beside every figure. Family = THREE variant
comparisons against the control; Bonferroni two-sided alpha = 0.05/3 = 0.0167
would give |t| >= 2.39, and BAR stays 2.6 -- tightened, never loosened,
consistent with H5/H13/H14.

ADOPTION requires ALL of, for the PRIMARY only:
  1. edge over breakout clears |t| >= 2.6
  2. edge exceeds the coin arm's edge (information beyond mechanical tightening)
  3. both size groups positive
  4. worst half-year block no worse than breakout's

    STRATEGY=patterns python3 src/research/retrace_test.py
"""
import sys as _sys, pathlib as _pl
_sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[1]))
import paths  # noqa: F401  -- puts the source dirs on sys.path
import multiprocessing as mp
import statistics
import sys
from collections import defaultdict

import entry
import features
import selection
import simulate

BATCH = "20260826-retrace-h15"
BAR = 2.6

# Read the live rules; never copy them.
BASE = dict(stop_pct=selection.STOP_PCT, target_pct=selection.TARGET_PCT,
            hold=selection.HOLD_DAYS, max_pos=selection.MAX_POSITIONS,
            refresh=5)

CONTROL = selection.TRIGGER              # "breakout" -- read, not typed
PRIMARY = "pb_shallow"
DESCRIPTIVE = ("pb_deep",)
MECHANISM = "coin"
VARIANTS = (PRIMARY,) + DESCRIPTIVE + (MECHANISM,)

_C = _D = None


def _rates(corpus):
    """Firing rates of every gated arm among breakout-signal bars.

    RATES ONLY, as in candle_test/fvg_test: counts cannot leak an outcome
    into the design, which is what lets the coin's probability match honestly.
    """
    tot = 0
    hit = {nm: 0 for nm in VARIANTS if nm != MECHANISM}
    for s in corpus.values():
        entry._CACHE.clear()
        for i in range(21, len(s.days)):
            if not entry.breakout(s, i):
                continue
            tot += 1
            for nm in hit:
                if entry.TRIGGERS[nm](s, i):
                    hit[nm] += 1
    return tot, {k: (v / tot if tot else float("nan")) for k, v in hit.items()}


def _block(day):
    return f"{day.year}-H{1 if day.month <= 6 else 2}"


def _stats(trades):
    r = [t["ret"] for t in trades]
    if len(r) < 2:
        return {"n": len(r), "mean": float("nan"), "sd": float("nan")}
    return {"n": len(r), "mean": statistics.fmean(r), "sd": statistics.stdev(r)}


def _diff(a, b):
    """-> edge (b minus a), std err, t."""
    if a["n"] < 2 or b["n"] < 2:
        return float("nan"), float("nan"), float("nan")
    se = (a["sd"] ** 2 / a["n"] + b["sd"] ** 2 / b["n"]) ** 0.5
    edge = b["mean"] - a["mean"]
    return edge, se, (edge / se if se else float("nan"))


def _verdict(t):
    if t != t:
        return "not enough trades"
    return "RESOLVED" if abs(t) >= BAR else "inside the noise"


def _one(name):
    entry._CACHE.clear()
    r = simulate.run(_C, _D, trigger=name, **BASE)
    t = r["trades"]
    by = defaultdict(float)
    for x in t:
        by[_block(x["day"])] += x["ret"]
    return {"name": name, "cagr": r["cagr"], "maxdd": r["maxdd"],
            "n": len(t), "worst": min(by.values()) if by else float("nan"),
            "_r": r}


def main():
    global _C, _D
    if paths.STRATEGY != "patterns":
        print(f"this test belongs to patterns; STRATEGY={paths.STRATEGY}.")
        print("run:  STRATEGY=patterns python3 src/research/retrace_test.py")
        return 1

    _C = features.load_corpus()
    _D = sorted({d for s in _C.values() for d in s.days})
    print(f"batch {BATCH}   control '{CONTROL}'   bar |t| >= {BAR}\n")

    tot, rate = _rates(_C)
    print(f"breakout-signal bars in corpus: {tot}")
    for nm, p in rate.items():
        print(f"  {nm:<12} {p * 100:.1f}% of breakout bars")
    entry.set_coin_rate(rate[PRIMARY])
    print(f"  coin rate set to primary's: P={rate[PRIMARY]:.4f}\n")

    names = (CONTROL,) + VARIANTS
    with mp.get_context("fork").Pool(min(len(names), mp.cpu_count())) as pool:
        res = {x["name"]: x for x in pool.map(_one, names)}

    ctrl = _stats(res[CONTROL]["_r"]["trades"])
    print(f"{'arm':<16}{'CAGR':>8}{'maxDD':>7}{'n':>6}{'per trade':>11}"
          f"{'vs ctrl':>10}{'t':>7}   verdict")
    for name in names:
        st = _stats(res[name]["_r"]["trades"])
        row = (f"  {name:<14}{res[name]['cagr']:>8.2f}{res[name]['maxdd']:>7.1f}"
               f"{st['n']:>6}{st['mean']:>10.2f}%")
        if name == CONTROL:
            print(f"{row}{'--':>10}{'--':>7}   control (the incumbent)")
            continue
        edge, se, t = _diff(ctrl, st)
        tag = _verdict(t)
        if name in DESCRIPTIVE:
            tag = "description only -- no adoption path"
        elif name == MECHANISM:
            tag = "mechanism reference -- no adoption path"
        print(f"{row}{edge:>+9.2f}%{t:>7.2f}   {tag}")

    prim = res[PRIMARY]
    coin = res[MECHANISM]
    edge_p, se_p, t_p = _diff(ctrl, _stats(prim["_r"]["trades"]))
    edge_c, se_c, t_c = _diff(ctrl, _stats(coin["_r"]["trades"]))

    print("\nper size group (arm minus breakout):")
    for clu in ("micro", "small"):
        a = _stats([x for x in res[CONTROL]["_r"]["trades"] if x["clu"] == clu])
        for label, arm in (("pb_shallow", prim), ("coin", coin)):
            b = _stats([x for x in arm["_r"]["trades"] if x["clu"] == clu])
            e, s_, tt = _diff(a, b)
            print(f"  {clu:<8} breakout {a['mean']:+6.2f}% (n={a['n']:3d})   "
                  f"{label:<12} {b['mean']:+6.2f}% (n={b['n']:3d})   "
                  f"edge {e:+6.2f}%  t {tt:+5.2f}   {_verdict(tt)}")

    print("\nworst half-year block:")
    for name in names:
        print(f"  {name:<14}{res[name]['worst']:>+8.1f}%")

    # Descriptive companion: outcome by depth third at signal time. The
    # entry session is the exit-day index minus held sessions -- both real
    # fields of simulate's closed-trade dict (ret, clu, day, sym, held, ...);
    # the signal bar is one before that. Description only, never an adoption
    # condition.
    buckets = defaultdict(list)
    skipped = 0
    for tr in res[CONTROL]["_r"]["trades"]:
        s = _C.get(tr["sym"])
        xi = s.index_of(tr["day"]) if s else None
        if s is None or xi is None or xi - tr["held"] - 1 < 0:
            skipped += 1
            continue
        d = entry.pullback_pct(s, xi - tr["held"] - 1)
        if d is None:
            skipped += 1
            continue
        b = "<15%" if d < 15 else ("15-30%" if d < 30 else ">=30%")
        buckets[b].append(tr["ret"])
    print("\ndepth profile at signal time (control trades):")
    for b in ("<15%", "15-30%", ">=30%"):
        v = buckets.get(b, [])
        if len(v) >= 2:
            m = statistics.fmean(v)
            see = statistics.stdev(v) / len(v) ** 0.5
            print(f"  {b:<8} n={len(v):4d}   mean {m:+6.2f}% +/- {see:.2f}")
        else:
            print(f"  {b:<8} n={len(v):4d}   not enough trades")
    if skipped:
        print(f"  ({skipped} trades unresolvable -- symbol or dates absent)")

    print("\nadoption check (all four required, primary only):")
    c1 = t_p == t_p and abs(t_p) >= BAR
    print(f"  1  edge clears |t| >= {BAR}        {edge_p:+.2f}% +/- {se_p:.2f}, "
          f"t {t_p:+.2f}   {'PASS' if c1 else 'FAIL'}")
    c2 = edge_p == edge_p and edge_c == edge_c and edge_p > edge_c
    print(f"  2  beats the coin at matched tightness   "
          f"pb_shallow {edge_p:+.2f}% vs coin {edge_c:+.2f}% "
          f"(t {t_c:+.2f})   {'PASS' if c2 else 'FAIL'}")
    grp = {c: _stats([x for x in prim["_r"]["trades"] if x["clu"] == c])["mean"]
           for c in ("micro", "small")}
    c3 = all(v == v and v > 0 for v in grp.values())
    print(f"  3  both size groups positive     micro {grp['micro']:+.2f}%, "
          f"small {grp['small']:+.2f}%   {'PASS' if c3 else 'FAIL'}")
    c4 = res[PRIMARY]["worst"] >= res[CONTROL]["worst"]
    print(f"  4  worst block no worse          {res[PRIMARY]['worst']:+.1f}% vs "
          f"{res[CONTROL]['worst']:+.1f}%   {'PASS' if c4 else 'FAIL'}")

    ok = c1 and c2 and c3 and c4
    print(f"\n  -> {'ADOPT' if ok else 'DO NOT ADOPT'}: "
          f"{_verdict(t_p) if not c1 else 'a condition failed'}")
    if not ok:
        print("     TRIGGER stays 'breakout'.")
    return 0


def _selftest():
    """The arithmetic, the rate matcher and the registration -- no backtest."""
    import math

    a = {"n": 100, "mean": 0.0, "sd": 10.0}
    b = {"n": 100, "mean": 2.0, "sd": 10.0}
    e, se, t = _diff(a, b)
    assert abs(e - 2.0) < 1e-9 and abs(se - math.sqrt(2)) < 1e-9
    assert _diff(b, a)[0] < 0 < e, "the subtraction is the wrong way round"
    assert _diff({"n": 1, "mean": 0.0, "sd": 0.0}, b)[2] != _diff(
        {"n": 1, "mean": 0.0, "sd": 0.0}, b)[2], "one trade produced a real t"
    assert BAR >= 2.6 - 1e-9, "the bar may be tightened, never relaxed below H13's"
    assert _verdict(BAR) == "RESOLVED" and _verdict(-BAR) == "RESOLVED"
    assert _verdict(BAR - 0.01) == "inside the noise"

    # The control must be READ from the live rule, never typed here.
    assert CONTROL == selection.TRIGGER, "control drifted from the live trigger"
    assert CONTROL != "none", "controlling on 'none' would be a straw man"

    # Rate matcher and registration need patterns's module; under another
    # strategy the TABLE is checked from a child instead (paths isolates
    # strategies deliberately).
    if paths.STRATEGY == "patterns":
        from datetime import date, timedelta
        d0 = date(2024, 1, 1)
        n_bars = 45                       # > PB_WINDOW, so depth is measured
        days = [d0 + timedelta(days=k) for k in range(n_bars)]
        s = features.Series("P", list(days))
        for k in range(n_bars - 1):
            px = 90.0
            s.open.append(px); s.high.append(px + 0.5); s.low.append(px - 0.5)
            s.close.append(px); s.volume.append(1000)
            s.turnover.append(1e6); s.deliv_pct.append(50.0)
        # last bar breaks out of the flat base
        s.open.append(91); s.high.append(92); s.low.append(90.7)
        s.close.append(91.5); s.volume.append(1000)
        s.turnover.append(1e6); s.deliv_pct.append(50.0)
        i = n_bars - 1
        entry._CACHE.clear()
        # flat base: window high sits on the newest bar -> depth exactly 0
        assert entry.pullback_pct(s, i) == 0.0
        assert entry.pb_shallow(s, i) and not entry.pb_deep(s, i)

        saved = entry.P_COIN
        try:
            tot_seen, rate = _rates({"P": s})
            assert tot_seen == 1, tot_seen
            assert rate["pb_shallow"] == 1.0, rate
            entry.set_coin_rate(rate["pb_shallow"])
            assert entry.P_COIN == 1.0
            assert entry.coin(s, i) is True
            entry.set_coin_rate(0.0)
            assert entry.coin(s, i) is False
        finally:
            entry.P_COIN = saved

        for nm in VARIANTS:
            assert nm in entry.TRIGGERS, f"{nm} is not registered"
    else:
        import os
        import subprocess
        env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
        env["STRATEGY"] = "patterns"
        r = subprocess.run(
            [sys.executable, "-c",
             "import sys; sys.path.insert(0, 'src'); import paths, entry;"
             " print(' '.join(entry.TRIGGERS))"],
            cwd=paths.ROOT, env=env, capture_output=True, text=True, timeout=120)
        got = set(r.stdout.split())
        assert got, f"could not read patterns's triggers: {r.stderr[-300:]}"
        for nm in VARIANTS:
            assert nm in got, f"{nm} is not registered in patterns: {sorted(got)}"

    # Condition 2 must stay consulted -- a check that exists but is no longer
    # read is worse than none, because it reads as protection.
    _src = open(__file__).read()
    assert "beats the coin at matched tightness" in _src, \
        "adoption condition 2 is gone"
    assert "edge_c" in _src, "condition 2 no longer reads the mechanism arm"
    print("retrace_test selftest ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    else:
        sys.exit(main())
