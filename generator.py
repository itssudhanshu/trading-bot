#!/usr/bin/env python3
"""Programmatic spec generator over the bounded vocabulary.

No LLM in this loop. Within a bounded search space a seeded sampler is
reproducible, free, and runs unattended -- an LLM per candidate would cost more
and explore no better. The LLM's role is the post-mortem: reading results and
writing lessons.md, which this reads back as constraints.

Screening order is deliberate and implements lessons.md L4:

    1. valid?              -- vocabulary check
    2. novel?              -- spec_hash dedupe
    3. TESTABLE?           -- enough instances to constitute evidence
    4. REACHABLE?          -- does the target ever get hit (L1)
    5. only now, P&L

Stages 3 and 4 are structural and cheap. Rejecting a spec on instance count
BEFORE its returns are computed is what stops the search from selecting noise:
a spec with n=8 and a great backtest is not a finding, and should never be
allowed to look like one.

Never touches the holdout. Asserted at load, not merely intended.
"""
import argparse
import json
import random
import statistics
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import backtest
import engine
import features
import judge
import spec as specmod

ROOT = Path(__file__).resolve().parent
CANDIDATES = ROOT / "data" / "candidates.jsonl"

MIN_INSTANCES = 100      # over the train span; ~30/fold, per lessons.md L4
# ...and an upper bound. L4 asks for specs "loose enough to be testable while
# still selective"; without this only the first half is enforced, and the search
# converges on specs firing on 14% of bars -- where a microscopic edge times a
# huge n outranks any real setup. A swing setup is rare by construction.
MAX_SIGNALS_PER_SYMBOL_YEAR = 6.0
FAMILIES = {
    # family -> (required predicates, pool of optional extras)
    "stage2_breakout": (["close_above_sma", "ema_slope_up", "breakout_prior_high"],
                        ["vol_expansion", "deliv_zscore_above", "turnover_above",
                         "breadth_above", "above_prior_close"]),
    "vcp":             (["range_contraction", "breakout_prior_high"],
                        ["atr_pct_below", "vol_expansion", "close_above_sma",
                         "deliv_zscore_above", "turnover_above"]),
    "ema_pullback":    (["close_above_sma", "pullback_to_ema"],
                        ["ema_cluster_tight", "ema_slope_up", "deliv_pct_above",
                         "turnover_above", "breadth_above"]),
}


def _snap(v, typ, lo, hi, rng):
    """Sample on a coarse grid. Discretising bounds the indicator memo AND makes
    spec_hash dedupe actually bite -- period=47 vs 48 is not a real hypothesis."""
    if typ is int:
        step = max(1, int((hi - lo) / 40))
        return int(min(hi, max(lo, round(rng.randint(int(lo), int(hi)) / step) * step)))
    return round(rng.uniform(lo, hi), 2)


def sample_spec(rng) -> dict:
    fam = rng.choice(sorted(FAMILIES))
    required, pool = FAMILIES[fam]
    names = list(required) + rng.sample(pool, rng.randint(1, min(3, len(pool))))

    conditions = []
    for name in names:
        schema = specmod.PREDICATES[name][1]
        cond = {"pred": name}
        for p, (typ, lo, hi) in schema.items():
            cond[p] = _snap(None, typ, lo, hi, rng)
        conditions.append(cond)

    stop = ({"rule": "swing_low", "lookback": rng.choice([5, 10, 15, 20]),
             "atr_mult": rng.choice([0.0, 0.25, 0.5, 1.0])}
            if rng.random() < 0.5 else
            {"rule": "atr", "mult": rng.choice([1.5, 2.0, 2.5, 3.0]), "period": 14})

    # r_multiple must be >= engine.MIN_RR or the gate rejects every signal.
    target = ({"rule": "r_multiple", "r": rng.choice([3.0, 3.5, 4.0, 5.0])}
              if rng.random() < 0.7 else
              {"rule": "prior_swing_high", "lookback": rng.choice([50, 100, 250])})

    return {
        "setup": fam, "version": "gen1",
        "conditions": conditions,
        "entry": {"rule": rng.choice(sorted(specmod.ENTRY_RULES)),
                  "buffer_pct": rng.choice([0.0, 0.1, 0.25])},
        "stop": stop, "target": target,
        "hold": {"max_bars": rng.choice([10, 20, 30, 45, 60])},
    }


def signals_for(spec, corpus, bd, ctx_cache, equity=1_000_000.0):
    """Like backtest.generate but reuses one Ctx per symbol across every spec in
    the run. Indicator cost is paid once for the whole search, not once per spec."""
    out = []
    for sym, s in corpus.items():
        c = ctx_cache.get(sym)
        if c is None:
            c = ctx_cache[sym] = specmod.Ctx(s, bd)
        for i in range(len(s)):
            sig = specmod.evaluate(spec, c, i)
            if sig is None:
                continue
            qty, why = engine.gate(sig, backtest._B(s, i), equity, 0.0)
            if not why:
                out.append((i, s, sig, qty))
    out.sort(key=lambda t: t[1].days[t[0]])
    return out


def screen(spec, corpus, bd, ctx_cache, symbol_years=None):
    """-> (stage, payload). Stops at the first gate the spec fails."""
    try:
        specmod.validate(spec)
    except specmod.SpecError as e:
        return "invalid", str(e)

    sigs = signals_for(spec, corpus, bd, ctx_cache)
    if len(sigs) < MIN_INSTANCES:
        # Rejected WITHOUT computing returns. This is the whole discipline.
        return "too_few_instances", {"n_signals": len(sigs)}
    if symbol_years:
        rate = len(sigs) / symbol_years
        if rate > MAX_SIGNALS_PER_SYMBOL_YEAR:
            return "too_frequent", {"n_signals": len(sigs), "per_symbol_year": round(rate, 1)}

    hold = spec["hold"]["max_bars"]
    mfe, hits = [], 0
    for i, s, sig, q in sigs:
        j = i + 1
        if j >= len(s):
            continue
        e = engine.entry_fill(sig.entry, backtest._B(s, j))
        if e is None:
            continue
        risk = sig.entry - sig.stop
        window = s.high[j:min(j + hold, len(s))]
        if not window:
            continue
        m = (max(window) - e) / risk
        mfe.append(m)
        if e + risk * ((sig.target - sig.entry) / risk) <= max(window):
            hits += 1
    if not mfe:
        return "no_fills", {"n_signals": len(sigs)}
    if hits == 0:
        return "unreachable_target", {"n_signals": len(sigs), "median_mfe": statistics.median(mfe)}

    res, trades = backtest.run(spec, corpus, bd)
    res["median_mfe"] = statistics.median(mfe)
    res["target_hit_rate"] = hits / len(mfe)
    return "evaluated", res


def search(n_specs, seed, corpus, bd, verbose=True):
    rng = random.Random(seed)
    days = {d for s in corpus.values() for d in s.days}
    span_years = (max(days) - min(days)).days / 365.25
    symbol_years = len(corpus) * span_years
    ctx_cache, seen, results = {}, set(), []
    stages = {}
    t0 = time.time()
    for k in range(n_specs):
        sp = sample_spec(rng)
        h = judge.spec_hash(sp)
        if h in seen:
            stages["duplicate"] = stages.get("duplicate", 0) + 1
            continue
        seen.add(h)
        stage, payload = screen(sp, corpus, bd, ctx_cache, symbol_years)
        stages[stage] = stages.get(stage, 0) + 1
        if stage == "evaluated":
            results.append({"spec_hash": h, "spec": sp, **payload})
        if verbose and (k + 1) % 10 == 0:
            print(f"  {k+1}/{n_specs}  {dict(sorted(stages.items()))}  "
                  f"{time.time()-t0:.0f}s", flush=True)
    return results, stages


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("-n", "--n-specs", type=int, default=60)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--symbols", type=int, default=0, help="cap universe for a fast pass")
    a = ap.parse_args()

    train_end = judge.HOLDOUT_START - timedelta(days=1)
    corpus = features.load_corpus(end=train_end)

    # The seal, verified rather than assumed.
    latest = max(d for s in corpus.values() for d in s.days)
    assert latest < judge.HOLDOUT_START, f"holdout leaked into train: {latest}"
    print(f"train corpus: {len(corpus)} symbols, through {latest} "
          f"(holdout sealed from {judge.HOLDOUT_START})")

    if a.symbols:
        corpus = dict(sorted(corpus.items())[:a.symbols])
        print(f"capped to {len(corpus)} symbols")

    bd = features.breadth(corpus)
    results, stages = search(a.n_specs, a.seed, corpus, bd)

    CANDIDATES.parent.mkdir(parents=True, exist_ok=True)
    with CANDIDATES.open("w") as f:
        for r in results:
            f.write(json.dumps(r, default=str) + "\n")

    print(f"\nscreening outcomes: {dict(sorted(stages.items()))}")
    yielded = stages.get("evaluated", 0)
    print(f"generator yield: {yielded}/{a.n_specs} = {yielded/a.n_specs*100:.1f}%")
    if not results:
        print("no spec survived screening")
        return
    # Ranked on PORTFOLIO expectancy: what the invariants would actually let you
    # take. Unconstrained expectancy flatters specs that exceed capacity.
    results.sort(key=lambda r: r["portfolio_expectancy"], reverse=True)
    print(f"\ntop candidates by portfolio expectancy (NOT evidence -- train only):")
    print(f"  {'hash':16} {'setup':16} {'inst':>6} {'taken':>6} {'cap':>5} "
          f"{'avgR':>6} {'tgt%':>5} {'portExp':>9}")
    for r in results[:10]:
        print(f"  {r['spec_hash']:16} {r['spec']['setup']:16} {r['n_trades']:>6} "
              f"{r['n_taken']:>6} {r['capacity_ratio']:>5.1f}x {r['avg_r']:>+6.2f} "
              f"{r['target_hit_rate']*100:>4.0f}% {r['portfolio_expectancy']:>+9,.0f}")


def _selftest():
    rng = random.Random(0)
    for _ in range(200):
        sp = sample_spec(rng)
        specmod.validate(sp)                      # every sample must be legal
        if sp["target"]["rule"] == "r_multiple":
            assert sp["target"]["r"] >= engine.MIN_RR, sp["target"]

    # sampling is reproducible from the seed
    assert judge.spec_hash(sample_spec(random.Random(7))) == \
           judge.spec_hash(sample_spec(random.Random(7)))

    # a spec below the instance floor must be rejected without P&L in the payload
    s = features.Series("T")
    d0 = date(2024, 1, 1)
    for k in range(300):
        px = 100 + k * 0.1
        s.days.append(d0 + timedelta(days=k))
        s.open.append(px); s.high.append(px); s.low.append(px); s.close.append(px)
        s.volume.append(100); s.turnover.append(1e8)
        s.deliv_pct.append(50.0); s.surveillance_known.append(True)
    stage, payload = screen(specmod.STAGE2_BREAKOUT, {"T": s}, {}, {})
    assert stage == "too_few_instances", stage
    assert "expectancy_after_costs" not in payload, "returns computed for a rejected spec"

    # a spec firing on nearly every bar is rejected as unselective, before P&L
    loose = {"setup": "x", "version": "t",
             "conditions": [{"pred": "above_prior_close"}],
             "entry": {"rule": "close", "buffer_pct": 0.0},
             "stop": {"rule": "atr", "mult": 2.0, "period": 14},
             "target": {"rule": "r_multiple", "r": 3.0},
             "hold": {"max_bars": 20}}
    stage2, payload2 = screen(loose, {"T": s}, {}, {}, symbol_years=1.0)
    assert stage2 in ("too_frequent", "too_few_instances"), stage2
    if stage2 == "too_frequent":
        assert "expectancy_after_costs" not in payload2
    print("generator selftest ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    else:
        main()
