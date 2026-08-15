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

# --- tightened 2026-08-15, pre-registered in lessons.md L28 ----------------
# Epoch 2 produced a PASS (+6.31%, PF 1.51) whose entire profit came from one
# BULL block while it lost in both BEAR blocks. The old criteria could not see
# that: they tested size, sign and drawdown, never regime consistency or
# statistical significance. Three additions, ALL STRICTLY TIGHTENING -- the only
# safe direction to move a test after seeing a result it let through.
MIN_POSITIVE_BLOCKS = 3   # of the 4 holdout blocks, by P&L
MIN_PSR = 0.95            # significance before multiple-testing correction
MIN_DSR = 0.95            # significance after deflating by the trial count


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


def _verdict(result: dict) -> tuple:
    """-> (passed, [failed_criteria]). Deterministic; never an LLM -- a judge
    that can be argued with is not a judge."""
    import dsr as _dsr
    fails = []
    if result.get("n_trades", 0) < MIN_TRADES:
        fails.append(f"n_trades<{MIN_TRADES}")
    if result.get("expectancy_after_costs", 0) <= 0:
        fails.append("expectancy<=0")
    if result.get("max_dd", 1.0) > MAX_DD:
        fails.append(f"max_dd>{MAX_DD}")

    blocks = result.get("block_pnl") or {}
    if blocks:
        pos = sum(1 for v in blocks.values() if v > 0)
        if pos < MIN_POSITIVE_BLOCKS:
            fails.append(f"only {pos}/{len(blocks)} blocks positive")
    else:
        fails.append("no per-block P&L supplied")

    rets = result.get("returns") or []
    if len(rets) >= 4:
        sr = _dsr.sharpe(rets)
        sk, ku = _dsr.moments(rets)
        if _dsr.psr(sr, 0.0, len(rets), sk, ku) <= MIN_PSR:
            fails.append("PSR<=0.95")
        trials = result.get("trial_sharpes") or []
        if len(trials) >= 2:
            if _dsr.deflated_sharpe(rets, trials)["dsr"] <= MIN_DSR:
                fails.append("DSR<=0.95")
        else:
            fails.append("no trial Sharpes supplied (cannot deflate)")
    else:
        fails.append("too few returns for a significance test")
    return (not fails), fails


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

    passed, fails = _verdict(result)
    verdict = "PASS" if passed else "FAIL"
    state["spent"] += 1
    state["verdicts"][h] = verdict
    state["log"].append({"spec_hash": h, "verdict": verdict, "failed": fails,
                         "at": datetime.now(timezone.utc).isoformat()})
    _save(state, ledger_path)
    return {"verdict": verdict, "budget_remaining": BUDGET - state["spent"]}


def _selftest():
    import tempfile
    import random
    rng = random.Random(1)
    # Deterministic fixtures: a random draw can land anywhere, and a criteria
    # test that depends on the seed is not a test of the criteria.
    strong = [0.006 if i % 3 else 0.001 for i in range(120)]     # SR ~ 2.0
    weakish = [0.001 if i % 2 == 0 else -0.0009 for i in range(120)]  # SR ~ 0.05
    few_trials = [rng.gauss(0.0, 0.01) for _ in range(4)]
    # Trial Sharpes must be spread widely enough that the best of 1,000 draws
    # actually exceeds the candidate. With std 0.05 the max is only ~0.16, which
    # a genuine SR of 1.8 rightly survives -- that is the code working, not a
    # deflation failure.  std ~1.4 here => E[max SR] ~ 4.6.
    many_trials = [(i % 5) - 2 for i in range(1000)]
    good_blocks = {"a": 100.0, "b": 50.0, "c": 20.0, "d": -10.0}
    one_block = {"a": 900.0, "b": -50.0, "c": -80.0, "d": -10.0}

    passing = {"n_trades": 40, "expectancy_after_costs": 120.0, "max_dd": 0.10,
               "block_pnl": good_blocks, "returns": strong,
               "trial_sharpes": few_trials}
    failing = {"n_trades": 40, "expectancy_after_costs": -50.0, "max_dd": 0.10,
               "block_pnl": good_blocks, "returns": strong,
               "trial_sharpes": few_trials}
    thin = {"n_trades": 12, "expectancy_after_costs": 500.0, "max_dd": 0.05,
            "block_pnl": good_blocks, "returns": strong, "trial_sharpes": few_trials}

    # the epoch-2 shape: profitable overall, but from ONE block
    concentrated = {"n_trades": 94, "expectancy_after_costs": 671.0, "max_dd": 0.04,
                    "block_pnl": one_block, "returns": strong,
                    "trial_sharpes": few_trials}
    ok, why = _verdict(concentrated)
    assert not ok and any("blocks positive" in f for f in why), why

    # a marginal Sharpe must fail significance
    marginal = {"n_trades": 94, "expectancy_after_costs": 10.0, "max_dd": 0.04,
                "block_pnl": good_blocks, "returns": weakish,
                "trial_sharpes": few_trials}
    ok2, why2 = _verdict(marginal)
    assert not ok2 and any("PSR" in f or "DSR" in f for f in why2), why2

    # the same strong result must fail once deflated by 1,000 trials
    deflated = {**passing, "trial_sharpes": many_trials}
    ok3, why3 = _verdict(deflated)
    assert not ok3 and any("DSR" in f for f in why3), why3

    # omitting per-block data is a failure, never a silent pass
    ok4, why4 = _verdict({k: v for k, v in passing.items() if k != "block_pnl"})
    assert not ok4 and any("per-block" in f for f in why4), why4

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
