#!/usr/bin/env python3
"""breakout must not move while sentiment and patterns are built.

That is the operator's one rule for this work, and a rule stated in a design
document is not a rule -- it is an intention. This enforces it.

Four checks. The first three are structure and run in under a second; the
fourth is behaviour and is the one that actually matters:

  1  breakout's four rule files are byte-identical to their recorded hashes,
     as are its learned weights and its recorded headline
  2  no strategy or research module can reach the live order book
  3  a non-breakout strategy cannot resolve its data directory inside data/breakout
  4  breakout's recorded baseline still reproduces: +7.59% CAGR, n=195

Check 4 is NOT repeated here. `audit.py` already re-runs the backtest and
compares it against `data/breakout/baseline.json`, and the selftest sweep runs
the audit -- doing it twice would add two minutes to every sweep to learn the
same thing. What check 4 catches that 1-3 cannot: a SHARED module edited in a
way that changes what breakout buys. Such an edit leaves every file in the
manifest untouched and every import clean, and shows up only as a different
number at the end of a backtest. That is why the audit is the real guard and
these are the cheap ones that fail fast.

    python3 tests/breakout_untouched.py

If breakout legitimately changes -- the learning loop moves its weights, or a
rule is deliberately edited -- re-record the manifest in its own commit, the
way `audit.py --rebaseline` is a deliberate separate step:

    python3 tests/breakout_untouched.py --record

Never re-record to make a red test go green. The question a failure asks is
"did I move breakout, or did breakout move?", and only a person can answer it.
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

MANIFEST = ROOT / "tests" / "breakout_manifest.json"
BASELINE = ROOT / "data" / "breakout" / "baseline.json"

# An import statement, not a mention. selection.py has a COMMENT saying orders
# "are queued into positions.db by daily.py", which is true, documentary, and
# must not trip this check.
_IMPORTS_ORDER_BOOK = re.compile(r"^\s*(?:import\s+positions|from\s+positions\s+import)",
                                 re.MULTILINE)

# What is actually TOUCHED matters more than whether the module was imported.
# This check originally failed anything that imported `positions` at all, which
# was right when nothing did and became wrong the moment the forward-trading
# work landed: learning.py imports it to read the string constant
# positions.MAIN and filter its OWN ledger, and never opens the database.
#
# So the rule is now about reach, not about imports. Reading a label cannot
# touch the order book; anything else can.
_ATTR = re.compile(r"\bpositions\.([A-Za-z_][A-Za-z0-9_]*)")
_LABELS = {"MAIN", "POOL", "BUCKETS"}     # plain strings/tuples naming a book

# Modules allowed to reach further, each with the reason it is allowed. Named
# individually so an exception has to be defended in writing rather than
# happening quietly -- the same idiom run_selftests.py uses for NO_SELFTEST.
_MAY_READ_ORDER_BOOK = {
    "src/research/forward_test.py":
        "the live forward run IS its subject; it reads the order book and "
        "never writes to it",
}

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
    for p in sorted((ROOT / "src/strategies/breakout").glob("*.py")):
        man[str(p.relative_to(ROOT))] = _hash(p)
    for extra in ("data/breakout/weights.json", "data/breakout/baseline.json"):
        if (ROOT / extra).exists():
            man[extra] = _hash(ROOT / extra)
    MANIFEST.write_text(json.dumps(man, indent=1, sort_keys=True) + "\n")
    print(f"recorded {len(man)} files -> {MANIFEST.relative_to(ROOT)}")


def check_rules_unchanged():
    """1 -- breakout's rules, weights and headline are byte-identical."""
    man = json.loads(MANIFEST.read_text())
    moved, missing = [], []
    for rel, want in man.items():
        p = ROOT / rel
        if not p.exists():
            missing.append(rel)
        elif _hash(p) != want:
            moved.append(rel)
    check("breakout's rules, weights and headline are unchanged",
          not moved and not missing,
          f"moved: {moved or 'none'}; missing: {missing or 'none'}")

    # A new .py appearing in breakout/ is also a change to breakout, and hashing
    # only what the manifest lists would never notice it.
    on_disk = {str(p.relative_to(ROOT))
               for p in (ROOT / "src/strategies/breakout").glob("*.py")}
    listed = {k for k in man if k.startswith("src/strategies/breakout/")}
    check("no file was added to or removed from breakout's rules",
          on_disk == listed,
          f"unlisted: {sorted(on_disk - listed) or 'none'}")


def check_order_book_unreachable():
    """2 -- research and strategy code cannot open the live order book.

    positions.db is the one mutable artefact that is NOT scoped per strategy
    (positions.py: DB = ROOT/"data"/"positions.db"), so it is the single place
    a second strategy could corrupt breakout's live bucket. Backtests have no
    business there, and this asserts they cannot get there by accident.
    """
    offenders, labelled = [], []
    for d in ("src/strategies", "src/research"):
        for p in sorted((ROOT / d).rglob("*.py")):
            rel = str(p.relative_to(ROOT))
            src = p.read_text(encoding="utf-8", errors="replace")
            if not _IMPORTS_ORDER_BOOK.search(src):
                continue
            if rel in _MAY_READ_ORDER_BOOK:
                labelled.append(rel)
                continue
            # "positions.db" in prose is the FILENAME, not an attribute; it is
            # lowercase and no such attribute exists, so it cannot match a label
            # and would be a false positive. Excluded by name.
            reach = {a for a in _ATTR.findall(src)
                     if a not in _LABELS and a != "db"}
            if reach:
                offenders.append(f"{rel} -> {sorted(reach)}")
    check("no strategy or research module can reach the live order book",
          not offenders,
          f"reaching: {offenders or 'none'}; "
          f"allowed by name: {labelled or 'none'}")


def check_data_scoped():
    """3 -- a non-breakout strategy cannot write into data/breakout.

    Run in a CHILD with STRATEGY set, because paths.SDATA is bound at import
    and this process already imported it as breakout. PYTHONPATH is stripped for
    the same reason the sweep strips it: a check that passes because of the
    parent's environment is not a check.
    """
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    for name in ("sentiment", "patterns"):
        env["STRATEGY"] = name
        r = subprocess.run(
            [sys.executable, "-c",
             "import sys; sys.path.insert(0, 'src'); import paths;"
             " print(paths.SDATA)"],
            cwd=ROOT, env=env, capture_output=True, text=True, timeout=60)
        got = r.stdout.strip()
        ok = got.endswith(f"data/{name}") and "breakout" not in got
        check(f"STRATEGY={name} writes to its own directory, not breakout's",
              ok, f"paths.SDATA -> {got or r.stderr.strip()[:120]}")


def main():
    if "--record" in sys.argv:
        record()
        return 0
    print("breakout isolation\n")
    check_rules_unchanged()
    check_order_book_unreachable()
    check_data_scoped()
    # READ the baseline; never print a copy of it. A hardcoded "+7.59% / n=195"
    # sat here and was already wrong within a day of being typed, which is L60
    # in miniature -- and wrong in the one place a reader goes to check whether
    # breakout has moved.
    _b = json.loads(BASELINE.read_text()) if BASELINE.exists() else {}
    print(f"\n  behavioural check (recorded baseline "
          f"{_b.get('cagr', '?')}% / n={_b.get('n', '?')}) is audit.py's, and"
          f"\n  runs in the sweep: python3 tests/run_selftests.py\n")
    if _fails:
        print(f"{len(_fails)} FAILED: {', '.join(_fails)}")
        return 1
    print("breakout is untouched")
    return 0


if __name__ == "__main__":
    sys.exit(main())
