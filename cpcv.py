#!/usr/bin/env python3
"""Combinatorial Purged Cross-Validation and the Probability of Backtest
Overfitting (PBO).

Everything else in this project judges a CANDIDATE. PBO judges the SEARCH: given
the whole candidate set, how often does picking the best performer in-sample
land you below median out-of-sample? If that happens more than ~half the time,
the selection procedure is fitting noise and no single result from it can be
trusted, however good its own numbers look.

Method (Bailey, Borwein, Lopez de Prado, Zhu -- "The Probability of Backtest
Overfitting"): split the timeline into S blocks; for every combination of S/2
blocks as train, find the spec with the best train performance, then measure
that spec's RELATIVE RANK among all specs on the complementary test blocks.

    omega = rank / (N + 1)          in (0,1), 1 = best
    lambda = ln(omega / (1 - omega))
    PBO = fraction of combinations with lambda < 0  (i.e. below median)

Cheap because it re-uses per-block P&L already computed per spec -- no
re-simulation per combination. The expensive part (signal generation) happens
once; only the block accounting is combinatorial.

Purging: a trade is attributed to the block containing its ENTRY, and any trade
whose exit falls in a different block is dropped. Without that a position opened
in train and closed in test leaks across the boundary.
"""
import itertools
import math
import statistics
import sys


def attribute(trades, blocks) -> dict:
    """-> {block_label: [trade, ...]}, purged of boundary-crossing trades."""
    day_block = {d: lbl for lbl, days in blocks.items() for d in days}
    out = {lbl: [] for lbl in blocks}
    for t in trades:
        b_in = day_block.get(t.entry_day)
        b_out = day_block.get(t.exit_day)
        if b_in is None or b_out is None or b_in != b_out:
            continue                       # purged: spans a boundary
        out[b_in].append(t)
    return out


def block_pnl(trades, blocks) -> dict:
    """-> {block_label: total net P&L}."""
    return {lbl: sum(t.net for t in ts) for lbl, ts in attribute(trades, blocks).items()}


SCORERS = {
    # How the train blocks are collapsed into ONE number to pick a winner.
    # This choice IS the selector, and PBO measures the selector -- so a high
    # PBO may indict the metric rather than the candidate pool.
    "sum":       lambda v: sum(v),
    "median":    lambda v: sorted(v)[len(v) // 2],
    "min":       lambda v: min(v),
    "n_positive": lambda v: sum(1 for x in v if x > 0),
    # magnitude scaled by consistency: rewards specs that are positive often,
    # not specs that are enormous once
    "consistency": lambda v: (sum(1 for x in v if x > 0) / len(v)) * sum(v),
}


def pbo(spec_block_pnl: dict, k=None, scorer="sum") -> dict:
    """spec_block_pnl: {spec_id: {block_label: pnl}} -> PBO statistics.

    `k` is the number of blocks held out as test per combination; defaults to
    half, which is the published choice.
    """
    specs = sorted(spec_block_pnl)
    if len(specs) < 2:
        return {"pbo": float("nan"), "n_specs": len(specs), "n_paths": 0}
    labels = sorted({b for v in spec_block_pnl.values() for b in v})
    k = k or max(1, len(labels) // 2)
    if len(labels) <= k:
        return {"pbo": float("nan"), "n_specs": len(specs), "n_paths": 0}

    lambdas, below = [], 0
    paths = 0
    for test in itertools.combinations(labels, k):
        train = [b for b in labels if b not in test]
        fn = SCORERS[scorer]
        tr_score = {s: fn([spec_block_pnl[s].get(b, 0.0) for b in train]) for s in specs}
        te_score = {s: fn([spec_block_pnl[s].get(b, 0.0) for b in test]) for s in specs}
        best = max(specs, key=lambda s: tr_score[s])

        # rank of the train-winner among all specs on the test blocks; 1 = worst
        order = sorted(specs, key=lambda s: te_score[s])
        rank = order.index(best) + 1
        omega = rank / (len(specs) + 1)
        omega = min(max(omega, 1e-9), 1 - 1e-9)
        lam = math.log(omega / (1 - omega))
        lambdas.append(lam)
        if lam < 0:
            below += 1
        paths += 1

    return {
        "pbo": below / paths,
        "n_specs": len(specs), "n_blocks": len(labels), "k_test": k,
        "n_paths": paths,
        "median_lambda": statistics.median(lambdas),
        "verdict": ("SEARCH IS OVERFITTING" if below / paths > 0.5
                    else "search selection carries signal"),
    }


def _selftest():
    blocks = [f"B{i}" for i in range(6)]

    # A spec that is genuinely best everywhere must give PBO ~ 0: whichever
    # blocks are held out, the train winner is still the test winner.
    real = {f"s{i}": {b: float(i) for b in blocks} for i in range(8)}
    r = pbo(real)
    assert r["pbo"] == 0.0, r
    assert r["verdict"].startswith("search selection"), r
    assert r["n_paths"] == 20, r          # C(6,3) = 20

    # Pure noise, constructed adversarially: each spec is excellent in exactly
    # one block and poor elsewhere. The train winner is then reliably NOT the
    # test winner, which is precisely what PBO is built to detect.
    noise = {}
    for i, b_good in enumerate(blocks):
        noise[f"n{i}"] = {b: (100.0 if b == b_good else -10.0) for b in blocks}
    r2 = pbo(noise)
    assert r2["pbo"] > 0.5, r2
    assert r2["verdict"] == "SEARCH IS OVERFITTING", r2

    # every scorer must be usable and produce a valid PBO
    for name in SCORERS:
        out = pbo(real, scorer=name)
        assert 0.0 <= out["pbo"] <= 1.0, (name, out)

    # degenerate inputs must not raise
    assert math.isnan(pbo({"only": {"B0": 1.0}})["pbo"])
    assert math.isnan(pbo({})["pbo"])

    # purging: a trade spanning two blocks is dropped, not attributed to either
    from datetime import date, timedelta
    import backtest
    d0 = date(2024, 1, 1)
    blk = {"A": [d0 + timedelta(days=i) for i in range(10)],
           "B": [d0 + timedelta(days=i) for i in range(10, 20)]}
    inside = backtest.Trade("X", d0, d0, d0 + timedelta(days=3), 100, 110, 10,
                            1.0, "target", 100, 5, 95, 1.0, 3)
    spanning = backtest.Trade("Y", d0, d0 + timedelta(days=8),
                              d0 + timedelta(days=12), 100, 110, 10,
                              1.0, "target", 100, 5, 95, 1.0, 4)
    att = attribute([inside, spanning], blk)
    assert [t.symbol for t in att["A"]] == ["X"], att
    assert att["B"] == [], att
    assert block_pnl([inside, spanning], blk)["A"] == 95
    print("cpcv selftest ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    else:
        print(__doc__.strip().split("\n\n")[0])
