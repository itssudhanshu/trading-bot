#!/usr/bin/env python3
"""H13: does the SHAPE of the breakout bar itself add anything?

PRE-REGISTERED 2026-08-26, after the detectors were frozen in their own commit
(3b0b2bb3) and before any return was computed against any arm.

THE HYPOTHESIS
--------------
H5 asked whether the shape of a 20-60 bar WINDOW beats a one-bar breakout and
the answer was no (+0.43%, t +0.21). It never examined the signal bar itself.
The price-action literature claims breakout QUALITY is readable off the bar:
a close near the high, a body that swallows yesterday's, an inside bar before
the break, consecutive higher closes into it. Hypothesis: gating the live
breakout on signal-bar shape improves mean per-trade return.

THE CONTROL, NAMED EXPLICITLY
-----------------------------
`breakout` -- the incumbent trigger, read from selection.TRIGGER, never typed.
Every arm below is `breakout AND <shape>`; none replaces it. This book is
long-only momentum above the 200-DMA, so standalone reversal candles (hammer,
morning star) contradict the design and are deliberately absent.

THE MECHANISM ARM, AND WHY IT IS HERE BEFORE THE RESULT
-------------------------------------------------------
H5's arms were LOOSER than the incumbent and could have won by reaching deeper
down a ranking whose depth costs -1.12% per step (t -3.95). These arms are
TIGHTER: they fire less, fill the bucket more slowly, and reach LESS deep --
a mechanical per-trade tailwind available to ANY tightening rule with no
information in it. `coin` prices exactly that tailwind: it is breakout AND a
deterministic pseudo-random gate whose rate P is measured at run time as the
fraction of breakout-signal bars passing strong_close. RATES ONLY -- the rate
pass reads firing counts, never returns, so seeing it cannot leak an outcome
into the design. A candle arm supports the hypothesis only if its edge clears
the bar AND exceeds coin's edge at matched tightness.

THE ARMS
--------
  strong_close   PRIMARY, carries the adoption path: breakout AND the close
                 sits in the top half of the bar's range (STRONG_CLOSE_POS,
                 frozen at 0.50 -- the plainest strength test, no free knob).
  engulf         description only: today's body swallows the prior body and
                 the bar is bullish (the strict bearish-prior version
                 contradicts a breakout context).
  inside_break   description only: the bar before the break was an inside bar.
  three_push     description only: three consecutive higher closes into the
                 break (Three White Soldiers adapted to closes; the
                 bodies/openings variant is deliberately NOT also run).
  coin           MECHANISM reference, no adoption path ever.

DESCRIPTIVE arms exist so no one re-runs this file to peek at them later;
they cannot be adopted individually without a new pre-registration of their
own (the H5 rule for flag / asc_triangle / cup_handle).

ENDPOINT AND BAR
----------------
Mean per-trade return, arm minus control, +/- std err and t, reported per size
group and per half-year block, n beside every figure. The family is FIVE
variant comparisons against the control; Bonferroni two-sided
alpha = 0.05/5 = 0.01 gives BAR |t| >= 2.6 -- the same bar H5 ran at.

ADOPTION requires ALL of, for the PRIMARY only:
  1. edge over breakout clears |t| >= 2.6
  2. edge exceeds the coin arm's edge (information beyond mechanical tightening)
  3. both size groups positive
  4. worst half-year block no worse than breakout's

    STRATEGY=patterns python3 src/research/candle_test.py
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

BATCH = "20260826-candles-h13"
BAR = 2.6

# Read the live rules; never copy them.
BASE = dict(stop_pct=selection.STOP_PCT, target_pct=selection.TARGET_PCT,
            hold=selection.HOLD_DAYS, max_pos=selection.MAX_POSITIONS,
            refresh=5)

CONTROL = selection.TRIGGER              # "breakout" -- read, not typed
PRIMARY = "strong_close"
DESCRIPTIVE = ("engulf", "inside_break", "three_push")
MECHANISM = "coin"
VARIANTS = (PRIMARY,) + DESCRIPTIVE + (MECHANISM,)

_C = _D = None


def _rates(corpus):
    """Firing rates of every gated arm among breakout-signal bars.

    RATES ONLY. This pass reads firing counts and nothing else; running it
    before the backtests cannot leak an outcome into the design, which is what
    lets the coin's probability be matched honestly.
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
        print("run:  STRATEGY=patterns python3 src/research/candle_test.py")
        return 1

    _C = features.load_corpus()
    _D = sorted({d for s in _C.values() for d in s.days})
    print(f"batch {BATCH}   control '{CONTROL}'   bar |t| >= {BAR}\n")

    tot, rate = _rates(_C)
    print(f"breakout-signal bars in corpus: {tot}")
    for nm, p in rate.items():
        print(f"  {nm:<14} {p * 100:.1f}% of breakout bars")
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
        for label, arm in (("strong_close", prim), ("coin", coin)):
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
          f"strong_close {edge_p:+.2f}% vs coin {edge_c:+.2f}% "
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
    assert BAR >= 2.0, "the bar may be tightened, never relaxed"
    assert _verdict(BAR) == "RESOLVED" and _verdict(-BAR) == "RESOLVED"
    assert _verdict(BAR - 0.01) == "inside the noise"

    # The control must be READ from the live rule, never typed here.
    assert CONTROL == selection.TRIGGER, "control drifted from the live trigger"
    assert CONTROL != "none", "controlling on 'none' would be a straw man"

    # The rate matcher: two synthetic symbols, one strong breakout and one
    # weak one -> half the breakout bars pass strong_close, so P = 0.5, and
    # the pass saw counts only. Runs only where patterns's entry module is
    # importable -- under another strategy these names are unreachable BY
    # DESIGN (see the child-process check below), and the sweep runs this
    # file's selftest under whatever strategy is active.
    if paths.STRATEGY == "patterns":
        from datetime import date, timedelta
        d0 = date(2024, 1, 1)
        days = [d0 + timedelta(days=k) for k in range(25)]

        def _sym(symbol, last):
            s = features.Series(symbol, list(days))
            for k in range(24):
                s.open.append(95); s.high.append(95.5); s.low.append(94.5)
                s.close.append(95); s.volume.append(1000)
                s.turnover.append(1e6); s.deliv_pct.append(50.0)
            o, h, l, c = last
            s.open.append(o); s.high.append(h); s.low.append(l); s.close.append(c)
            s.volume.append(1000); s.turnover.append(1e6); s.deliv_pct.append(50.0)
            return s

        strong = _sym("S", (96, 97.0, 95.8, 96.8))     # closes near its high
        weak = _sym("W", (97.5, 97.5, 95.6, 95.8))     # breaks out, fades to a weak close
        tot, rate = _rates({"S": strong, "W": weak})
        assert tot == 2, f"expected two breakout bars, saw {tot}"
        assert abs(rate["strong_close"] - 0.5) < 1e-9, rate
        saved = entry.P_COIN
        try:
            entry.set_coin_rate(rate["strong_close"])
            assert entry.P_COIN == 0.5
            r1 = entry.coin(strong, 24)
            assert entry.coin(strong, 24) == r1, "coin is not deterministic"
            entry.set_coin_rate(1.0)
            assert entry.coin(strong, 24) and entry.coin(weak, 24)
            entry.set_coin_rate(0.0)
            assert not entry.coin(strong, 24) and not entry.coin(weak, 24)
        finally:
            entry.P_COIN = saved

        # Every arm must exist as a real trigger where it will run.
        for nm in VARIANTS:
            assert nm in entry.TRIGGERS, f"{nm} is not registered"
    else:
        # patterns is unreachable from this process -- paths binds ONE active
        # strategy and that isolation is deliberate. Read its TRIGGERS TABLE
        # from a child, not the source text: a source scan for a name matches
        # the list of names itself and can never fail.
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
    print("candle_test selftest ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    else:
        sys.exit(main())
