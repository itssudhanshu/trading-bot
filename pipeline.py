#!/usr/bin/env python3
"""Autonomous next-step runner: search -> PBO gate -> validate -> report.

Encodes the decision tree rather than re-deciding it each run, so the same rules
apply whether a person or a timer starts it.

WHAT IT WILL NOT DO
-------------------
It does not consult the sealed holdout. That spends a lifetime budget of 50 and
is irreversible; an unattended loop consulting freely would exhaust it in two
sittings and destroy the only defence this project has against overfitting.
The pipeline produces a shortlist and stops. `--consult` exists for a human who
means it, and it caps how many it will spend in one run.

STOP CONDITIONS, pre-registered
-------------------------------
  PBO > 0.5          the selector is not generalising; nothing it promotes is
                     evidence, however good the numbers look (L41)
  budget exhausted   no consultations remain
  nothing promoted   walk-forward rejected everything

Each is a stop, not a warning. A rule obeyed only when convenient is not a rule.
"""
import argparse
import json
import random
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
STATE = ROOT / "data" / "pipeline_state.json"
PBO_STOP = 0.5
MAX_CONSULT_PER_RUN = 3          # even when explicitly allowed


def _load_state():
    return json.loads(STATE.read_text()) if STATE.exists() else {"runs": [], "seeds_used": []}


def _save_state(s):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(s, indent=2))


def next_seed(state):
    """A seed never used before, so a run is always a fresh hypothesis set.
    Re-running a spent seed re-tests identical specs and inflates nothing but
    the clock."""
    used = set(state.get("seeds_used", []))
    for _ in range(10000):
        s = random.randint(1000, 999999)
        if s not in used:
            return s
    raise RuntimeError("no unused seed found")


def run(cmd, log_path, timeout=7200):
    with open(log_path, "w") as f:
        p = subprocess.run(cmd, stdout=f, stderr=subprocess.STDOUT,
                           timeout=timeout, cwd=ROOT)
    return p.returncode


def pbo_of(seed, scorer="min"):
    import cpcv
    f = ROOT / "data" / f"candidates_seed{seed}.jsonl"
    if not f.exists():
        return None, 0
    rows = [json.loads(l) for l in f.read_text().splitlines() if l.strip()]
    d = {r["spec_hash"]: r["block_pnl"] for r in rows if r.get("block_pnl")}
    if len(d) < 2:
        return None, len(d)
    return cpcv.pbo(d, scorer=scorer)["pbo"], len(d)


def one_cycle(n_specs=400, workers=6, consult=False, log=print):
    import judge
    state = _load_state()
    seed = next_seed(state)
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    rec = {"seed": seed, "at": stamp, "stage": "search"}
    log(f"=== cycle {stamp}  seed {seed} ===")

    spent = judge._load()["spent"]
    if spent >= judge.BUDGET:
        rec["stop"] = "budget exhausted"
        log("STOP: holdout budget exhausted")
        state["runs"].append(rec); _save_state(state)
        return rec

    t0 = time.time()
    rc = run([sys.executable, "generator.py", "-n", str(n_specs),
              "--seed", str(seed), "--parallel", str(workers)],
             ROOT / "data" / f"pipe_search_{seed}.log")
    state["seeds_used"] = state.get("seeds_used", []) + [seed]
    if rc != 0:
        rec["stop"] = f"search failed rc={rc}"
        log(rec["stop"]); state["runs"].append(rec); _save_state(state); return rec
    log(f"  search done in {time.time()-t0:.0f}s")

    pbo, n = pbo_of(seed)
    rec.update(stage="pbo", pbo=pbo, n_candidates=n)
    log(f"  PBO(min) = {pbo:.3f} over {n} candidates" if pbo is not None
        else "  PBO: not computable")
    if pbo is None or pbo > PBO_STOP:
        rec["stop"] = f"PBO {pbo} > {PBO_STOP}: selector not generalising"
        log(f"STOP: {rec['stop']}")
        state["runs"].append(rec); _save_state(state); return rec

    rc = run([sys.executable, "validate.py", "--shortlist", "30"],
             ROOT / "data" / f"pipe_validate_{seed}.log")
    promoted = ROOT / "data" / "promoted.jsonl"
    rows = [json.loads(l) for l in promoted.read_text().splitlines()
            if l.strip()] if promoted.exists() else []
    rec.update(stage="validate", promoted=len(rows))
    log(f"  promoted: {len(rows)}")
    if not rows:
        rec["stop"] = "nothing survived walk-forward"
        log(f"STOP: {rec['stop']}")
        state["runs"].append(rec); _save_state(state); return rec

    if not consult:
        rec["stop"] = f"{len(rows)} promoted; consultation is a deliberate act"
        log(f"HOLD: {rec['stop']}  (rerun with --consult to spend budget)")
        state["runs"].append(rec); _save_state(state); return rec

    rec["stage"] = "consult"
    rec["verdicts"] = _consult(rows[:MAX_CONSULT_PER_RUN], log=log)
    state["runs"].append(rec); _save_state(state)
    return rec


def _consult(rows, log=print):
    import dsr, features, judge, report, split
    corpus = features.load_corpus(); bd = features.breadth(corpus)
    days = sorted({d for s in corpus.values() for d in s.days})
    _, ho = split.split_days(days); blks = split.blocks(days)
    trials = dsr.load_trials()
    out = []
    for r in rows:
        sp = r["spec"]
        trades, taken, curve, dd = report.simulate_portfolio(
            sp, corpus, bd, allowed=set(ho))
        st = report.stats(taken, curve, dd)
        if not st["n"]:
            continue
        bp = {l: sum(t.net for t in taken if t.entry_day in set(blks[l]))
              for l in split.HOLDOUT_BLOCKS}
        res = {"n_trades": st["n"], "expectancy_after_costs": st["expectancy"],
               "max_dd": dd, "block_pnl": bp,
               "returns": [t.net / 1_000_000 for t in taken],
               "trial_sharpes": trials}
        v = judge.consult(sp, res)
        log(f"  {r['spec_hash']}  {st['total_return_pct']:+.2f}%  "
            f"{v['verdict']}  budget left {v['budget_remaining']}")
        out.append({"spec_hash": r["spec_hash"], "verdict": v["verdict"],
                    "return_pct": st["total_return_pct"], "block_pnl": bp})
    return out


def _selftest():
    import tempfile
    global STATE
    orig = STATE
    try:
        with tempfile.TemporaryDirectory() as td:
            STATE = Path(td) / "s.json"
            st = _load_state()
            assert st == {"runs": [], "seeds_used": []}
            a = next_seed(st)
            st["seeds_used"] = [a]
            assert next_seed(st) != a, "reused a spent seed"
            _save_state(st)
            assert _load_state()["seeds_used"] == [a]
    finally:
        STATE = orig

    # the stop threshold must be the pre-registered one, not drifted
    assert PBO_STOP == 0.5, PBO_STOP
    assert MAX_CONSULT_PER_RUN <= 5, "an unattended run must not drain the budget"
    src = Path(__file__).read_text()
    assert "if not consult:" in src, "consultation must be opt-in"
    print("pipeline selftest ok")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cycles", type=int, default=1)
    ap.add_argument("-n", "--n-specs", type=int, default=400)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--consult", action="store_true",
                    help="allow spending holdout budget (max %d/run)" % MAX_CONSULT_PER_RUN)
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        _selftest()
    else:
        for c in range(a.cycles):
            r = one_cycle(a.n_specs, a.workers, a.consult)
            if r.get("stop", "").startswith(("budget", "PBO")):
                print("halting further cycles")
                break
