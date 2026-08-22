#!/usr/bin/env python3
"""Where things live. One definition, imported by everything.

Fourteen modules each derived the data directory from their own file location
(`Path(__file__).resolve().parent / "data"`). That works only while every file
sits in the repo root -- move one into a subdirectory and it silently points at
a `data/` that does not exist, and because these paths are created on demand it
would not error, it would create a fresh empty one and the live bucket would look
empty. That made the layout unchangeable, so it is fixed here first.

Also puts the source directories on sys.path, so `import features` keeps
working from anywhere. That preserves the project's convention that any module
can be run directly for its selftest:

    python3 src/strategies/breakout/clusters.py --selftest

THIS FILE LIVES IN src/ AND THAT IS LOAD-BEARING. Every module bootstraps with

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # -> src/
    import paths

so `parents[1]` from src/core, src/bucket, src/research and src/ops must land on
the directory holding this file. When this file sat at the repo root and the
source moved under src/, all of those lines silently pointed one level too
shallow -- and every selftest still passed, because the shell that ran them had
PYTHONPATH=. exported. Putting paths.py in src/ fixes them all at once;
src/strategies/breakout sits one deeper and uses parents[2].
"""
import os
import sys
from pathlib import Path

# parents[1], not parent: this file is src/paths.py, and ROOT is the repo.
ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
RAW = DATA / "raw"

# --- which strategy is live ------------------------------------------------
# A strategy is a directory under strategies/ holding the RULES: what to rank,
# what to buy, when to sell. Everything else -- price data, the fill and cost
# engine, the backtest harness, the order book, the bot -- is shared and knows
# nothing about any particular strategy.
#
# Only the ACTIVE strategy goes on sys.path. That is the isolation: a second
# strategy also defines `selection`, and if both were importable, `import
# selection` would resolve to whichever directory came first and every result
# after that would describe a book nobody chose. One active at a time, named
# out loud, and the wrong one cannot be reached by accident.
#
#     STRATEGY=other python3 src/ops/audit.py
STRATEGY = os.environ.get("STRATEGY", "breakout")
STRATEGIES = ROOT / "src" / "strategies"
SDIR = STRATEGIES / STRATEGY

# Strategy-scoped data: weights, the recorded baseline, the trade ledger, the
# stored results. These are OUTPUTS of one strategy and a second one must not
# append to them -- a mixed strategies.jsonl cannot be un-mixed afterwards.
SDATA = DATA / STRATEGY

# Source directories, in import-resolution order. The strategy comes FIRST so
# its rules win over anything shared, and root is last so a stray name there
# cannot shadow a real module.
SRC = (f"src/strategies/{STRATEGY}", "src/core", "src/bucket", "src/research",
       "src/ops")

# Root-relative path to a source file, for the places that SPAWN a script rather
# than import it -- agent.py runs `python3 <this> ` with cwd=ROOT. Those were
# plain strings ("ops/snapshot.py") and the src/ move broke the scheduler
# silently: subprocess reports rc=2 into a log nobody reads.
def script(rel):
    """-> "src/ops/snapshot.py" for script("ops/snapshot.py")."""
    for d in SRC:
        if rel.startswith(d.split("/", 1)[1] + "/"):
            return f"src/{rel}"
    return rel

for _d in SRC:
    _p = str(ROOT / _d)
    if _p not in sys.path:
        sys.path.insert(0, _p)
# src/ last, so `import paths` keeps working for anything that arrives here
# without having bootstrapped, and so a name in src/ cannot shadow a real module.
if str(ROOT / "src") not in sys.path:
    sys.path.append(str(ROOT / "src"))


def _selftest():
    assert ROOT.is_dir() and (ROOT / "src" / "paths.py").exists()
    assert DATA.name == "data" and DATA.parent == ROOT
    # The bootstrap every module copies. If this file ever moves again, those
    # `parents[1]` lines are what breaks, and they break SILENTLY under a shell
    # that happens to export PYTHONPATH -- so assert the invariant here rather
    # than trusting the next sweep to notice.
    assert Path(__file__).resolve().parent == ROOT / "src", \
        "paths.py must live in src/; every module's parents[1] bootstrap targets it"
    for _m in (ROOT / "src" / "core" / "features.py",
               ROOT / "src" / "ops" / "audit.py"):
        assert _m.resolve().parents[1] == ROOT / "src", _m
    assert RAW == DATA / "raw"
    # every source dir must be importable, or a moved module cannot be found
    for d in SRC:
        assert str(ROOT / d) in sys.path, d
    # The active strategy must exist and must be the ONLY one importable. A
    # typo in STRATEGY would otherwise fall through to whatever is on sys.path
    # next and run the shared modules against no rules at all.
    assert SDIR.is_dir(), f"no such strategy: {SDIR}"
    assert (SDIR / "selection.py").exists(), f"{STRATEGY} defines no selection.py"
    others = [p for p in STRATEGIES.iterdir()
              if p.is_dir() and p.name != STRATEGY and not p.name.startswith(("_", "."))]
    for p in others:
        assert str(p) not in sys.path, f"{p.name} is importable while {STRATEGY} is live"
    assert SDATA.parent == DATA, SDATA
    assert SDATA.name == STRATEGY, "strategy data is not scoped to the strategy"
    print(f"paths selftest ok (strategy: {STRATEGY}, {len(others)} inactive)")


if __name__ == "__main__":
    _selftest() if "--selftest" in sys.argv else print(f"ROOT={ROOT}\nDATA={DATA}")
