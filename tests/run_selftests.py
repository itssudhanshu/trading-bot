#!/usr/bin/env python3
"""Run every module's --selftest, then the audit. One command.

This project keeps its checks INSIDE the module they protect -- `python3
src/strategies/breakout/selection.py --selftest` asserts the exit rules, and it
lives next to them so it cannot drift out of sight. That convention is good and
is not changing. What was missing is a way to run them ALL, which meant the
sweep was a hand-typed shell loop, retyped from memory each time, and a module
absent from that loop was a module nobody checked.

So the list is DISCOVERED, not maintained: every .py under src/. A new module is
in the sweep the moment it exists, without anyone remembering to add it.

    python3 tests/run_selftests.py

That includes ops/audit.py, which has no --selftest branch and so ignores the
flag and runs its full check set -- including the one that re-runs the backtest
and compares it against the recorded baseline. It exits 1 on any failure, so it
needs no special handling here and gets none.

Exits non-zero if anything fails, so it can gate a commit.
"""
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import paths

# Modules with no --selftest branch fall through to running main(), which for
# these means a full backtest -- minutes each, and for tg.py a network call. They
# are excluded by NAME so the exclusion is visible and has to be justified,
# rather than being silently skipped by a bare except.
NO_SELFTEST = {
    "impact_test.py": "no --selftest branch; runs the c sensitivity table (~3 min)",
    "trigger_test.py": "no --selftest branch; runs seven triggers (~4 min)",
    "weight_test.py": None,          # has one
    "snapshot.py": "fetches from NSE; nothing to assert offline",
    "live_source.py": None,          # has one; network-free (cached master)
    "restore_orphans.py": "one-shot recovery script, already run",
    "patch_helper.py": None,         # has one
}


def targets():
    """-> every module that should be swept, in a stable order."""
    seen, out = set(), []
    for d in paths.SRC:                       # src/strategies/breakout, src/core, ...
        for p in sorted((paths.ROOT / d).glob("*.py")):
            if p.name.startswith("_") or p.name in seen:
                continue
            seen.add(p.name)
            out.append(p)
    # paths.py sits in src/ itself rather than in one of the SRC dirs, so the
    # glob above cannot see it -- and it is the module the other 27 depend on.
    for p in sorted((paths.ROOT / "src").glob("*.py")):
        if p.name not in seen:
            seen.add(p.name)
            out.append(p)
    return out


def main():
    rows, skipped = [], []
    for p in targets():
        why = NO_SELFTEST.get(p.name)
        if why:
            skipped.append((p.name, why))
            continue
        rel = p.relative_to(paths.ROOT)
        # PYTHONPATH is STRIPPED deliberately. This sweep once reported 26
        # passed while every module was unable to find paths.py, because the
        # shell that launched it exported PYTHONPATH=. and the children
        # inherited it. A check that passes because of the operator's shell is
        # not a check on the code.
        env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
        r = subprocess.run([sys.executable, str(p), "--selftest"],
                           capture_output=True, text=True, cwd=paths.ROOT,
                           env=env, timeout=900)
        tail = (r.stdout.strip().splitlines() or [""])[-1][:70]
        if r.returncode != 0:
            tail = (r.stderr.strip().splitlines() or ["failed"])[-1][:70]
        rows.append((r.returncode == 0, str(rel), tail))
        print(f"  {'ok  ' if r.returncode == 0 else 'FAIL'} {str(rel):<40}{tail}")

    bad = [r for r in rows if not r[0]]
    print(f"\n  {len(rows) - len(bad)} passed, {len(bad)} failed, "
          f"{len(skipped)} have no selftest")
    for name, why in skipped:
        print(f"    - {name}: {why}")
    for _, name, tail in bad:
        print(f"    FAILED {name}: {tail}")
    return 1 if bad else 0


def _selftest():
    t = [p.name for p in targets()]
    # Discovery is the whole point: if it stops finding the strategy's own
    # rules, the sweep silently shrinks to whatever is left.
    for must in ("selection.py", "clusters.py", "entry.py", "engine.py",
                 "features.py", "simulate.py", "audit.py", "daily.py", "tg.py",
                 "agent.py", "overview.py", "paths.py"):
        assert must in t, f"discovery missed {must}: {sorted(t)}"
    assert len(t) == len(set(t)), "a module is swept twice"
    # Every excluded name must actually be a module we found, or the exclusion
    # is stale and is quietly protecting nothing.
    for name, why in NO_SELFTEST.items():
        if why:
            assert name in t, f"{name} is excluded but no longer exists"
    print(f"run_selftests selftest ok ({len(t)} modules discovered)")


if __name__ == "__main__":
    sys.exit(_selftest() or 0 if "--selftest" in sys.argv else main())
