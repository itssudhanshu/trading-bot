#!/usr/bin/env python3
"""Sealed holdout judge. The only component allowed to see holdout results.

Two rules exist so that an automated generator cannot overfit its way past them:

  1. Returns PASS/FAIL and budget remaining. Never a metric. If it returned a
     Sharpe, agents would hill-climb on it across runs and leak the holdout one
     decimal at a time.
  2. Fixed lifetime consultation budget. Every distinct hypothesis costs one.
     Re-testing an identical spec is free and returns the cached verdict --
     otherwise a retry loop burns the budget, and re-rolling the same spec
     hoping for a different answer is exactly the behaviour we are preventing.

Run as a subprocess by the agent loop. The holdout data must live outside any
path the agents can read -- prompts are not a security boundary, the filesystem
is. Enforced here by refusing to run if the holdout sits inside the repo.
"""
import hashlib
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
import split

# Epoch 1 used a contiguous "last 12 months" holdout, which confounded regime
# with edge (lessons L19). Its ledger is preserved unchanged as a record: five
# consultations, five FAILs. Those five specs are RETIRED, not re-testable --
# their behaviour in that period is known, and two of its blocks are in the new
# holdout.
LEDGER_EPOCH1 = ROOT / "data" / "judge_ledger.json"
LEDGER = ROOT / "data" / f"judge_ledger_epoch{split.EPOCH}.json"

# The sealed set is now blocks, not a cutoff date -- see split.HOLDOUT_BLOCKS.
HOLDOUT_BLOCKS = split.HOLDOUT_BLOCKS


def is_holdout(d):
    return split.is_holdout(d)

BUDGET = 50          # lifetime holdout consultations
MIN_TRADES = 30      # swing frequency is low; below this it is not evidence
MAX_DD = 0.25


def _load(path=None):
    p = path or LEDGER
    if p.exists():
        return json.loads(p.read_text())
    return {"spent": 0, "verdicts": {}, "log": []}


def _save(state, path=None):
    p = path or LEDGER
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, indent=2, sort_keys=True))


def spec_hash(spec: dict) -> str:
    """Stable identity for a hypothesis, independent of key ordering."""
    return hashlib.sha256(
        json.dumps(spec, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:16]


def _verdict(result: dict) -> bool:
    """Deterministic. Never an LLM -- a judge that can be argued with is not a judge."""
    return (result.get("n_trades", 0) >= MIN_TRADES
            and result.get("expectancy_after_costs", 0) > 0
            and result.get("max_dd", 1.0) <= MAX_DD)


def consult(spec: dict, result: dict, ledger_path=None) -> dict:
    """-> {'verdict': 'PASS'|'FAIL'|'REFUSED', 'budget_remaining': int}

    Deliberately the entire return surface. Do not add metrics to it.
    """
    state = _load(ledger_path)
    h = spec_hash(spec)

    if h in state["verdicts"]:                       # same hypothesis, already paid for
        return {"verdict": state["verdicts"][h], "budget_remaining": BUDGET - state["spent"]}

    if state["spent"] >= BUDGET:
        return {"verdict": "REFUSED", "budget_remaining": 0}

    verdict = "PASS" if _verdict(result) else "FAIL"
    state["spent"] += 1
    state["verdicts"][h] = verdict
    state["log"].append({"spec_hash": h, "verdict": verdict,
                         "at": datetime.now(timezone.utc).isoformat()})
    _save(state, ledger_path)
    return {"verdict": verdict, "budget_remaining": BUDGET - state["spent"]}


def _selftest():
    import tempfile
    passing = {"n_trades": 40, "expectancy_after_costs": 120.0, "max_dd": 0.10}
    failing = {"n_trades": 40, "expectancy_after_costs": -50.0, "max_dd": 0.10}
    thin = {"n_trades": 12, "expectancy_after_costs": 500.0, "max_dd": 0.05}

    with tempfile.TemporaryDirectory() as td:
        led = Path(td) / "ledger.json"

        r = consult({"setup": "vcp", "n": 1}, passing, led)
        assert r["verdict"] == "PASS" and r["budget_remaining"] == BUDGET - 1, r

        # the return surface must never carry a metric
        assert set(r) == {"verdict", "budget_remaining"}, r
        blob = json.dumps(r)
        for leak in ("expectancy", "sharpe", "n_trades", "max_dd", "120"):
            assert leak not in blob, f"metric leaked to agent: {leak}"

        # re-consulting an identical spec is free and stable
        again = consult({"n": 1, "setup": "vcp"}, failing, led)   # key order differs
        assert again["verdict"] == "PASS", "cached verdict must not be re-rolled"
        assert again["budget_remaining"] == BUDGET - 1, "cache must not burn budget"

        assert consult({"setup": "vcp", "n": 2}, failing, led)["verdict"] == "FAIL"
        assert consult({"setup": "vcp", "n": 3}, thin, led)["verdict"] == "FAIL", \
            "too few trades is not evidence, however good the numbers look"

        # budget is a hard stop
        state = _load(led)
        state["spent"] = BUDGET
        _save(state, led)
        out = consult({"setup": "vcp", "n": 999}, passing, led)
        assert out == {"verdict": "REFUSED", "budget_remaining": 0}, out
    print("judge selftest ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    elif "--status" in sys.argv:
        s = _load()
        print(f"spent {s['spent']}/{BUDGET}, {len(s['verdicts'])} hypotheses tested")
    else:
        # agent entrypoint: judge.py <spec.json> <result.json>
        spec = json.loads(Path(sys.argv[1]).read_text())
        result = json.loads(Path(sys.argv[2]).read_text())
        print(json.dumps(consult(spec, result)))
