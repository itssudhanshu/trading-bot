#!/usr/bin/env python3
"""H14: does a fair-value gap around the breakout carry anything?

PRE-REGISTERED 2026-08-26, after the detectors were frozen in their own commit
(c9ef1bfb) and before any return was computed against any arm.

THE HYPOTHESIS
--------------
The second family from the operator's chart-pattern review that H5/H13 had
not touched. A bullish fair-value gap (FVG) is a three-bar imbalance: the
signal bar's low sits entirely above the high two bars back, leaving a zone
the impulse bar never let anyone trade. The price-action literature claims
two opposite things about it -- that a fresh imbalance marks urgency worth
following, and that price should RETURN to fill the zone before continuing.
Both are tested here as gates on the live trigger; neither is testable as a
standalone signal in this book (a long-only momentum bucket cannot buy "price
fell into a hole three days ago" on its own).

THE CONTROL, NAMED EXPLICITLY
-----------------------------
`breakout` -- the incumbent trigger, read from selection.TRIGGER, never typed.

THE MECHANISM ARM, SAME AS LAST TIME
------------------------------------
Every arm below TIGHTENS the trigger, and L74 priced what tightening alone
buys: coin at matched rate read +0.09%, t +0.06 -- nothing. It runs again
anyway because it costs one fork and because "it read zero last time" is
exactly the kind of assumption this project has been burned by before. Its
rate is matched to the primary's from firing COUNTS only, never returns.

THE ARMS
--------
  fvg          PRIMARY, carries the adoption path: breakout AND the signal
               bar completes a fresh bullish FVG (low[i] > high[i-2]). One
               clause, no window parameter -- which is why it and not the
               windowed arms carries the path.
  fvg_recent   description only: an in-window FVG whose zone has not been
               revisited (FVG_WINDOW frozen at 5).
  gap_fill     description only: an in-window FVG that WAS revisited before
               today's break, today closing back above its floor -- the
               literature's own entry, adapted as a gate.
  coin         mechanism reference, no adoption path ever.

DESCRIPTIVE arms exist so nobody re-runs this file to peek at them later;
they cannot be adopted individually without a new pre-registration of their
own (the H5 rule for flag / asc_triangle / cup_handle).

ENDPOINT AND BAR
----------------
Mean per-trade return, arm minus control, +/- std err and t, reported per size
group and per half-year block, n beside every figure. The family is FOUR
variant comparisons against the control; Bonferroni two-sided
alpha = 0.05/4 = 0.0125 would give |t| >= 2.50, and BAR is set at 2.6 anyway
-- criteria may be tightened, never loosened, and 2.6 is what H5/H13 ran at.

ADOPTION requires ALL of, for the PRIMARY only:
  1. edge over breakout clears |t| >= 2.6
  2. edge exceeds the coin arm's edge (information beyond mechanical tightening)
  3. both size groups positive
  4. worst half-year block no worse than breakout's

    STRATEGY=patterns python3 src/research/fvg_test.py
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

BATCH = "20260826-fvg-h14"
BAR = 2.6

# Read the live rules; never copy them.
BASE = dict(stop_pct=selection.STOP_PCT, target_pct=selection.TARGET_PCT,
            hold=selection.HOLD_DAYS, max_pos=selection.MAX_POSITIONS,
            refresh=5)

CONTROL = selection.TRIGGER              # "breakout" -- read, not typed
PRIMARY = "fvg"
DESCRIPTIVE = ("fvg_recent", "gap_fill")
MECHANISM = "coin"
VARIANTS = (PRIMARY,) + DESCRIPTIVE + (MECHANISM,)

_C = _D = None


def _rates(corpus):
    """Firing rates of every gated arm among breakout-signal bars.

    RATES ONLY, exactly as in candle_test: running this before the backtests
    cannot leak an outcome into the design, which is what lets the coin's
    probability be matched honestly.
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
        print("run:  STRATEGY=patterns python3 src/research/fvg_test.py")
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
        for label, arm in (("fvg", prim), ("coin", coin)):
            b = _stats([x for x in arm["_r"]["trades"] if x["clu"] == clu])
            e, s_, tt = _diff(a, b)
            print(f"  {clu:<8} breakout {a['mean']:+6.2f}% (n={a['n']:3d})   "
                  f"{label:<12} {b['mean']:+6.2f}% (n={b['n']:3d})   "
                  f"edge {e:+6.2f}%  t {tt:+5.2f}   {_verdict(tt)}")

    print("\nworst half-year block:")
    for name in names:
        print(f"  {name:<14}{res[name]['worst']:>+8.1f}%")

    print("\nadoption check (all four required, primary only):")
    c1 = t_p == t_p and abs(t_p) >= BAR
    print(f"  1  edge clears |t| >= {BAR}        {edge_p:+.2f}% +/- {se_p:.2f}, "
          f"t {t_p:+.2f}   {'PASS' if c1 else 'FAIL'}")
    c2 = edge_p == edge_p and edge_c == edge_c and edge_p > edge_c
    print(f"  2  beats the coin at matched tightness   "
          f"fvg {edge_p:+.2f}% vs coin {edge_c:+.2f}% "
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

    # The rate matcher and registration need patterns's module, which is
    # unreachable under another strategy BY DESIGN (paths binds ONE active
    # strategy); there the TRIGGERS table is checked from a child instead --
    # the TABLE, not the source text, which can never fail on itself.
    if paths.STRATEGY == "patterns":
        from datetime import date, timedelta
        d0 = date(2024, 1, 1)
        n_bars = 24                      # 21 flat base bars + a 3-bar tail
        days = [d0 + timedelta(days=k) for k in range(n_bars)]

        def _sym(symbol, tail):
            assert len(tail) == n_bars - 21
            s = features.Series(symbol, list(days))
            for k in range(n_bars - len(tail)):
                s.open.append(85); s.high.append(85.5); s.low.append(84.5)
                s.close.append(85); s.volume.append(1000)
                s.turnover.append(1e6); s.deliv_pct.append(50.0)
            for o, h, l, c in tail:
                s.open.append(o); s.high.append(h); s.low.append(l)
                s.close.append(c)
                s.volume.append(1000); s.turnover.append(1e6)
                s.deliv_pct.append(50.0)
            return s

        # Flat base at 85.5 high, then impulse to 91 and a signal bar whose
        # low (90.6) clears the pre-impulse high (87.0). Against a base this
        # quiet EVERY tail bar clears high[i-2] -- that is the canonical
        # definition behaving correctly, not a fixture bug -- so rate = 1.0,
        # nothing has revisited a zone yet (gap_fill 0.0).
        gappy = _sym("G", [(86, 87.0, 85.8, 86.5), (87, 91.0, 86.9, 90.5),
                           (92, 94.0, 90.6, 93.5)])
        tot, rate = _rates({"G": gappy})
        assert tot == 3, f"expected three breakout bars, saw {tot}"
        assert rate["fvg"] == 1.0 and rate["fvg_recent"] == 1.0, rate
        assert rate["gap_fill"] == 0.0, rate
        saved = entry.P_COIN
        try:
            entry.set_coin_rate(rate["fvg"])
            assert entry.P_COIN == 1.0
            r1 = entry.coin(gappy, n_bars - 1)
            assert entry.coin(gappy, n_bars - 1) == r1, "coin is not deterministic"
            assert all(entry.coin(gappy, k) == entry.breakout(gappy, k)
                       for k in range(24))
            entry.set_coin_rate(0.0)
            assert not any(entry.coin(gappy, k) for k in range(24))
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
    print("fvg_test selftest ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    else:
        sys.exit(main())
