#!/usr/bin/env python3
"""sprout must not move while thicket and trellis are built.

That is the operator's one rule for this work, and a rule stated in a design
document is not a rule -- it is an intention. This enforces it.

Four checks. The first three are structure and run in under a second; the
fourth is behaviour and is the one that actually matters:

  1  sprout's four rule files are byte-identical to their recorded hashes,
     as are its learned weights and its recorded headline
  2  no strategy or research module can reach the live order book
  3  a non-sprout strategy cannot resolve its data directory inside data/sprout
  4  sprout's recorded baseline still reproduces: +7.59% CAGR, n=195

Check 4 is NOT repeated here. `audit.py` already re-runs the backtest and
compares it against `data/sprout/baseline.json`, and the selftest sweep runs
the audit -- doing it twice would add two minutes to every sweep to learn the
same thing. What check 4 catches that 1-3 cannot: a SHARED module edited in a
way that changes what sprout buys. Such an edit leaves every file in the
manifest untouched and every import clean, and shows up only as a different
number at the end of a backtest. That is why the audit is the real guard and
these are the cheap ones that fail fast.

    python3 tests/sprout_untouched.py

If sprout legitimately changes -- the learning loop moves its weights, or a
rule is deliberately edited -- re-record the manifest in its own commit, the
way `audit.py --rebaseline` is a deliberate separate step:

    python3 tests/sprout_untouched.py --record

Never re-record to make a red test go green. The question a failure asks is
"did I move sprout, or did sprout move?", and only a person can answer it.
"""
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import paths  # noqa: E402

MANIFEST = ROOT / "tests" / "sprout_manifest.json"

# An import statement, not a mention. selection.py has a COMMENT saying orders
# "are queued into positions.db by daily.py", which is true, documentary, and
# must not trip this check.
_IMPORTS_ORDER_BOOK = re.compile(r"^\s*(?:import\s+positions|from\s+positions\s+import)",
                                 re.MULTILINE)

_fails = []


def check(name, ok, detail=""):
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
    if detail:
        print(f"         {detail}")
    if not ok:
        _fails.append(name)


def _hash(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def record():
    """Re-record the manifest. Deliberate, and its own step."""
    man = {}
    for p in sorted((ROOT / "src/strategies/sprout").glob("*.py")):
        man[str(p.relative_to(ROOT))] = _hash(p)
    for extra in ("data/sprout/weights.json", "data/sprout/baseline.json"):
        if (ROOT / extra).exists():
            man[extra] = _hash(ROOT / extra)
    MANIFEST.write_text(json.dumps(man, indent=1, sort_keys=True) + "\n")
    print(f"recorded {len(man)} files -> {MANIFEST.relative_to(ROOT)}")


def check_rules_unchanged():
    """1 -- sprout's rules, weights and headline are byte-identical."""
    man = json.loads(MANIFEST.read_text())
    moved, missing = [], []
    for rel, want in man.items():
        p = ROOT / rel
        if not p.exists():
            missing.append(rel)
        elif _hash(p) != want:
            moved.append(rel)
    check("sprout's rules, weights and headline are unchanged",
          not moved and not missing,
          f"moved: {moved or 'none'}; missing: {missing or 'none'}")

    # A new .py appearing in sprout/ is also a change to sprout, and hashing
    # only what the manifest lists would never notice it.
    on_disk = {str(p.relative_to(ROOT))
               for p in (ROOT / "src/strategies/sprout").glob("*.py")}
    listed = {k for k in man if k.startswith("src/strategies/sprout/")}
    check("no file was added to or removed from sprout's rules",
          on_disk == listed,
          f"unlisted: {sorted(on_disk - listed) or 'none'}")


def check_order_book_unreachable():
    """2 -- research and strategy code cannot open the live order book.

    positions.db is the one mutable artefact that is NOT scoped per strategy
    (positions.py: DB = ROOT/"data"/"positions.db"), so it is the single place
    a second strategy could corrupt sprout's live bucket. Backtests have no
    business there, and this asserts they cannot get there by accident.
    """
    offenders = []
    for d in ("src/strategies", "src/research"):
        for p in sorted((ROOT / d).rglob("*.py")):
            if _IMPORTS_ORDER_BOOK.search(p.read_text(encoding="utf-8", errors="replace")):
                offenders.append(str(p.relative_to(ROOT)))
    check("no strategy or research module imports the live order book",
          not offenders, f"offenders: {offenders or 'none'}")


def check_data_scoped():
    """3 -- a non-sprout strategy cannot write into data/sprout.

    Run in a CHILD with STRATEGY set, because paths.SDATA is bound at import
    and this process already imported it as sprout. PYTHONPATH is stripped for
    the same reason the sweep strips it: a check that passes because of the
    parent's environment is not a check.
    """
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    for name in ("thicket", "trellis"):
        env["STRATEGY"] = name
        r = subprocess.run(
            [sys.executable, "-c",
             "import sys; sys.path.insert(0, 'src'); import paths;"
             " print(paths.SDATA)"],
            cwd=ROOT, env=env, capture_output=True, text=True, timeout=60)
        got = r.stdout.strip()
        ok = got.endswith(f"data/{name}") and "sprout" not in got
        check(f"STRATEGY={name} writes to its own directory, not sprout's",
              ok, f"paths.SDATA -> {got or r.stderr.strip()[:120]}")


def main():
    if "--record" in sys.argv:
        record()
        return 0
    print("sprout isolation\n")
    check_rules_unchanged()
    check_order_book_unreachable()
    check_data_scoped()
    print(f"\n  behavioural check (baseline +7.59% / n=195) is audit.py's, and"
          f"\n  runs in the sweep: python3 tests/run_selftests.py\n")
    if _fails:
        print(f"{len(_fails)} FAILED: {', '.join(_fails)}")
        return 1
    print("sprout is untouched")
    return 0


if __name__ == "__main__":
    sys.exit(main())
