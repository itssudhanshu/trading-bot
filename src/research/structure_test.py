#!/usr/bin/env python3
"""H16: does intact swing structure into a breakout carry anything?

PRE-REGISTERED 2026-08-26, after the detectors were frozen in their own commit
(ece430d8) and before any return was computed against any arm. Two definitional
questions were settled in that freeze, both caught by fixtures: a CHoCH is a
close through the newest higher low THE MOMENT IT PRINTS (not gated on pivot
confirmation -- damage you can see is damage), and plateau troughs register
once rather than as adjacent duplicate pivots.

THE HYPOTHESIS
--------------
Last family from the operator's chart-pattern review. The structure reading:
an uptrend is higher highs and higher lows; a break below the newest higher
low is a change of character. Claim under test: a 20-day-high breakout that
arrives with intact higher-low structure outperforms one arriving after
structure broke. Note what the gate REJECTS by design: monotonic runs with no
pullbacks have no readable structure -- excluding them IS part of the
hypothesis, and H15 measured that excluded class as fine (+1.32%/trade), so
this arm pays a known price to buy its information.

THE CONTROL, NAMED EXPLICITLY
-----------------------------
`breakout` -- the incumbent trigger, read from selection.TRIGGER, never typed.

THE MECHANISM ARM
-----------------
Coin at matched rate, third-and-a-half measurement of the tightening
confound (L74 +0.09%, L75 -0.02%, L76 -0.54% -- all noise so far). One fork;
rate from firing COUNTS only.

THE ARMS
--------
  hl_intact   PRIMARY: breakout AND >=2 confirmed swing lows in the window
              (SWING_FRINGE=3, STRUCT_LOOKBACK=60, frozen), newer ABOVE older,
              no close since the newest trough back through its level.
  hh_hl       description only: hl_intact AND ascending confirmed swing highs
              too -- the stricter full-structure reading.
  coin        mechanism reference, no adoption path ever.

ENDPOINT AND BAR
----------------
Mean per-trade return, arm minus control, +/- std err and t, per size group
and per half-year block, n beside every figure. Family = THREE variant
comparisons; BAR stays |t| >= 2.6 (tightened relative to the Bonferroni
requirement of ~2.39, consistent with H5/H13/H14/H15).

ADOPTION requires ALL of, for the PRIMARY only:
  1. edge over breakout clears |t| >= 2.6
  2. edge exceeds the coin arm's edge (information beyond mechanical tightening)
  3. both size groups positive
  4. worst half-year block no worse than breakout's

    STRATEGY=patterns python3 src/research/structure_test.py
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

BATCH = "20260826-structure-h16"
BAR = 2.6

# Read the live rules; never copy them.
BASE = dict(stop_pct=selection.STOP_PCT, target_pct=selection.TARGET_PCT,
            hold=selection.HOLD_DAYS, max_pos=selection.MAX_POSITIONS,
            refresh=5)

CONTROL = selection.TRIGGER              # "breakout" -- read, not typed
PRIMARY = "hl_intact"
DESCRIPTIVE = ("hh_hl",)
MECHANISM = "coin"
VARIANTS = (PRIMARY,) + DESCRIPTIVE + (MECHANISM,)

_C = _D = None


def _rates(corpus):
    """Firing rates of every gated arm among breakout-signal bars.

    RATES ONLY, as in every test in this family: counts cannot leak an
    outcome into the design.
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
        print("run:  STRATEGY=patterns python3 src/research/structure_test.py")
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
        for label, arm in (("hl_intact", prim), ("coin", coin)):
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
          f"hl_intact {edge_p:+.2f}% vs coin {edge_c:+.2f}% "
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
        path = ([90.0] * 6 + [88.0, 86.0, 88.0]      # trough one
                + [90.0] * 8
                + [89.0, 87.0, 89.0]                  # trough two, higher
                + [91.0] * 5 + [93.0, 95.0, 97.0])    # rise into the break
        bars = []
        prev = path[0]
        for c in path[1:]:
            bars.append((prev, max(prev, c) + 0.3, min(prev, c) - 0.3, c))
            prev = c
        days = [date(2024, 1, 1) + timedelta(days=k) for k in range(len(bars) + 1)]
        s = features.Series("S", list(days))
        for o, h, l, c in bars:
            s.open.append(o); s.high.append(h); s.low.append(l); s.close.append(c)
            s.volume.append(1000); s.turnover.append(1e6); s.deliv_pct.append(50.0)
        s.open.append(98); s.high.append(98.8); s.low.append(97.4)
        s.close.append(98.5); s.volume.append(1000)
        s.turnover.append(1e6); s.deliv_pct.append(50.0)
        i = len(days) - 1
        entry._CACHE.clear()
        assert len(entry._confirmed_swing_lows(s, i)) >= 2, "fixture has no pivots"
        assert entry.hl_intact(s, i), "fixture staircase read as broken"
        saved = entry.P_COIN
        try:
            tot_seen, rate = _rates({"S": s})
            assert tot_seen >= 2, tot_seen
            assert rate["hl_intact"] == 1.0, rate
            entry.set_coin_rate(rate["hl_intact"])
            r1 = entry.coin(s, i)
            assert entry.coin(s, i) == r1, "coin is not deterministic"
            entry.set_coin_rate(1.0)
            assert entry.coin(s, i) == entry.breakout(s, i)
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
    print("structure_test selftest ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    else:
        sys.exit(main())
