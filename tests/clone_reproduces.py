#!/usr/bin/env python3
"""A cloned strategy must reproduce sprout's baseline EXACTLY, with knobs off.

This is the gate every new strategy passes before any finding built on it means
anything. thicket and trellis are born as behavioural clones of sprout, with
every new rule shipped off by default. If a clone with all knobs off produces
anything other than sprout's recorded +7.59% / 31.0% / 195, then the fork is
wrong -- and every number measured against it afterwards would be describing a
bucket nobody designed, while looking entirely plausible.

Not approximately. The corpus is the same, the rules are the same, the seed is
the same; an identical configuration has no licence to drift by 0.01. A
tolerance here would be a place for a real difference to hide.

    STRATEGY=thicket python3 tests/clone_reproduces.py
    STRATEGY=trellis python3 tests/clone_reproduces.py

Run with STRATEGY unset it says so and exits clean: sprout is not a clone of
itself, and audit.py already owns the question of whether sprout has moved.

WHY IT COMPARES AGAINST A LIVE RUN, NOT A STORED NUMBER
-------------------------------------------------------
It used to compare the clone against data/sprout/baseline.json. That broke the
first time it mattered: the daily agent fetched a new session mid-afternoon, the
corpus grew from 1698 to 1699, and the check would have failed on a number that
has nothing to do with whether the fork is clean.

A stored baseline answers "does this match what sprout did on the corpus as it
stood in July". The question worth asking is "does this match what sprout does
RIGHT NOW, on the same bars". So sprout is re-run in a child process with
STRATEGY=sprout and the two are compared directly. Slower -- two full backtests
-- and it cannot drift, which for the gate that licenses every downstream number
is the right trade.

The recorded baseline is still printed, as information. audit.py owns the
question of whether sprout has moved against it.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import paths  # noqa: E402

SPROUT_BASELINE = ROOT / "data" / "sprout" / "baseline.json"


MEASURE = """
import sys; sys.path.insert(0, "src")
import paths, features, selection, simulate, json
c = features.load_corpus()
d = sorted({x for s in c.values() for x in s.days})
r = simulate.run(c, d, stop_pct=selection.STOP_PCT,
                 target_pct=selection.TARGET_PCT, hold=selection.HOLD_DAYS,
                 max_pos=selection.MAX_POSITIONS, trigger=selection.TRIGGER,
                 refresh=5)
print("RESULT" + json.dumps({"sessions": len(d), "cagr": round(r["cagr"], 2),
      "n": len(r["trades"]), "maxdd": round(r["maxdd"], 1)}))
"""


def measure(strategy):
    """-> the headline for `strategy`, measured in a child process.

    A child, because paths binds the active strategy at import: one process
    cannot hold two strategies' rules, which is the isolation working as
    designed. PYTHONPATH is stripped for the same reason the sweep strips it.
    """
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env["STRATEGY"] = strategy
    r = subprocess.run([sys.executable, "-c", MEASURE], cwd=ROOT, env=env,
                       capture_output=True, text=True, timeout=1800)
    for line in r.stdout.splitlines():
        if line.startswith("RESULT"):
            return json.loads(line[6:])
    raise RuntimeError(f"{strategy} produced no result:\n{r.stderr[-800:]}")


def main():
    import selection

    print(f"strategy: {paths.STRATEGY}")
    print(f"rules:    {Path(selection.__file__).parent.relative_to(ROOT)}")
    print(f"data:     {paths.SDATA.relative_to(ROOT)}\n")

    if paths.STRATEGY == "sprout":
        print("STRATEGY is sprout; there is no clone to check.")
        print("run:  STRATEGY=thicket python3 tests/clone_reproduces.py")
        return 0

    print("measuring sprout and the clone on the SAME corpus "
          "(two backtests, a few minutes)...\n")
    want = measure("sprout")
    got = measure(paths.STRATEGY)

    print(f"{'':12} {'sprout now':>12} {'this clone':>12}")
    bad = []
    for k in ("sessions", "cagr", "n", "maxdd"):
        ok = got[k] == want[k]
        bad += [] if ok else [k]
        print(f"  {k:<10} {str(want[k]):>12} {str(got[k]):>12}  "
              f"{'ok' if ok else '<-- DIFFERS'}")

    if SPROUT_BASELINE.exists():
        rec = json.loads(SPROUT_BASELINE.read_text())
        drift = [k for k in ("sessions", "cagr", "n", "maxdd")
                 if rec.get(k) != want[k]]
        print(f"\n  recorded baseline: {rec.get('cagr')} / n={rec.get('n')} / "
              f"{rec.get('sessions')} sessions"
              + (f"   (sprout has since moved on: {', '.join(drift)} -- "
                 f"audit.py's question, not this one)" if drift else "   (unchanged)"))

    if bad:
        print(f"\nFAIL: {paths.STRATEGY} does not reproduce sprout on {', '.join(bad)}.")
        print("The fork differs from sprout in behaviour, not just in name.")
        print("Do NOT record a baseline for it and do NOT measure anything")
        print("against it until the difference is found and is deliberate.")
        return 1
    print(f"\n{paths.STRATEGY} reproduces sprout exactly. The fork is clean.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
