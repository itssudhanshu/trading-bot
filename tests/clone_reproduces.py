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

Run with STRATEGY unset it checks sprout against its own baseline, which is what
audit.py already does -- harmless, and it means the command is never wrong.

WHY THIS IS NOT audit.py
------------------------
audit.py compares the ACTIVE strategy against ITS OWN recorded baseline
(paths.SDATA/"baseline.json"). A clone has no recorded baseline yet, and
recording one before it has been shown to match would record the drift as
correct. This compares against SPROUT's baseline specifically, which is the only
number a clone is allowed to be born with.
"""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import paths  # noqa: E402

SPROUT_BASELINE = ROOT / "data" / "sprout" / "baseline.json"


def main():
    if not SPROUT_BASELINE.exists():
        print("no sprout baseline recorded; nothing to reproduce")
        return 1
    want = json.loads(SPROUT_BASELINE.read_text())

    import features
    import selection
    import simulate

    print(f"strategy: {paths.STRATEGY}")
    print(f"rules:    {Path(selection.__file__).parent.relative_to(ROOT)}")
    print(f"data:     {paths.SDATA.relative_to(ROOT)}\n")

    corpus = features.load_corpus()
    days = sorted({d for s in corpus.values() for d in s.days})

    # Read the live constants; never copy them. impact_test.py carried a copy
    # that said hold=15 for three months after the live value moved to 10.
    r = simulate.run(corpus, days,
                     stop_pct=selection.STOP_PCT,
                     target_pct=selection.TARGET_PCT,
                     hold=selection.HOLD_DAYS,
                     max_pos=selection.MAX_POSITIONS,
                     trigger=selection.TRIGGER,
                     refresh=5)
    got = {"sessions": len(days), "cagr": round(r["cagr"], 2),
           "n": len(r["trades"]), "maxdd": round(r["maxdd"], 1)}

    print(f"{'':12} {'recorded':>10} {'this clone':>12}")
    bad = []
    for k in ("sessions", "cagr", "n", "maxdd"):
        ok = got[k] == want.get(k)
        bad += [] if ok else [k]
        print(f"  {k:<10} {str(want.get(k)):>10} {str(got[k]):>12}  "
              f"{'ok' if ok else '<-- DIFFERS'}")

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
