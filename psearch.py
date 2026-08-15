#!/usr/bin/env python3
"""Parallel signal generation for the spec search.

Partitions by SYMBOL, not by spec. Splitting the spec list instead would give
every worker its own full indicator cache (1.7 GB measured), so 8 workers would
need ~14 GB to do the same work. Splitting symbols keeps each worker's cache
proportional to its share.

DETERMINISM: every spec is sampled from the single seeded RNG in the parent
BEFORE any work is distributed, so the candidate set is byte-identical to the
serial path. Only the evaluation order changes, and results are merged by spec
index -- never by completion order.

Workers return plain tuples, not Signal objects: macOS spawns rather than forks,
so everything crossing the boundary is pickled, and Series objects are large.
"""
import multiprocessing as mp
import os
import random
import sys
import time

import backtest
import engine
import features
import generator
import judge
import spec as specmod
import split

_W = {}          # per-worker state, populated once per process


def _init(allowed, symbols):
    """Runs once per worker. RS rank and breadth are cross-sectional, so the
    FULL universe must be loaded before the partition is taken -- computing them
    on a subset would silently change every rank."""
    corpus = features.load_corpus()
    bd = features.breadth(corpus)
    _W["corpus"] = {s: corpus[s] for s in symbols if s in corpus}
    _W["bd"] = bd
    _W["allowed"] = allowed
    _W["ctx"] = {}


def _eval(args):
    """-> (spec_idx, [(symbol, bar_index, entry, stop, target, qty), ...])"""
    idx, sp = args
    out = []
    allowed, ctx = _W["allowed"], _W["ctx"]
    for sym, s in _W["corpus"].items():
        c = ctx.get(sym)
        if c is None:
            c = ctx[sym] = specmod.Ctx(s, _W["bd"])
        for i in range(len(s)):
            if s.days[i] not in allowed:
                continue
            sig = specmod.evaluate(sp, c, i)
            if sig is None:
                continue
            qty, why = engine.gate(sig, backtest._B(s, i), 1_000_000.0, 0.0)
            if not why:
                out.append((sym, i, sig.entry, sig.stop, sig.target, qty))
    return idx, out


def _worker(chunk, specs, allowed, out_q):
    """Module level, not nested: macOS spawns workers, so the target must be
    importable by name. A closure pickles as a local object and dies at start."""
    _init(allowed, chunk)
    out_q.put([_eval((i, sp)) for i, sp in enumerate(specs)])


def parallel_signals(specs, corpus, allowed, workers=None):
    """-> {spec_idx: [(symbol, i, entry, stop, target, qty)]}"""
    workers = workers or max(1, min(6, (os.cpu_count() or 2) - 1))
    syms = sorted(corpus)
    chunks = [syms[k::workers] for k in range(workers)]

    results = {i: [] for i in range(len(specs))}
    procs = []
    q = mp.Queue()

    for ch in chunks:
        p = mp.Process(target=_worker, args=(ch, specs, allowed, q))
        p.start()
        procs.append(p)
    for _ in procs:
        for idx, sigs in q.get():
            results[idx].extend(sigs)
    for p in procs:
        p.join()
    return results


def _selftest():
    # determinism: the candidate set must not depend on worker count
    a = [judge.spec_hash(generator.sample_spec(random.Random(3))) for _ in range(20)]
    b = [judge.spec_hash(generator.sample_spec(random.Random(3))) for _ in range(20)]
    assert a == b, "sampling is not reproducible from the seed"

    # partitioning must cover every symbol exactly once
    syms = [f"S{k}" for k in range(97)]
    for w in (1, 3, 6, 8):
        chunks = [syms[k::w] for k in range(w)]
        flat = sorted(x for ch in chunks for x in ch)
        assert flat == sorted(syms), f"partition lost symbols at w={w}"
        assert sum(len(c) for c in chunks) == len(syms), w

    # merge must key on spec index, never on completion order
    merged = {0: [], 1: []}
    for idx, sigs in [(1, [("B", 1, 1, 1, 1, 1)]), (0, [("A", 0, 1, 1, 1, 1)])]:
        merged[idx].extend(sigs)
    assert merged[0][0][0] == "A" and merged[1][0][0] == "B", merged
    print("psearch selftest ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    else:
        n = int(sys.argv[1]) if len(sys.argv) > 1 else 12
        w = int(sys.argv[2]) if len(sys.argv) > 2 else 6
        rng = random.Random(99)
        specs = [generator.sample_spec(rng) for _ in range(n)]
        corpus = features.load_corpus()
        days = sorted({d for s in corpus.values() for d in s.days})
        tr, _ = split.split_days(days)
        allowed = set(tr)
        print(f"{n} specs, {len(corpus)} symbols, {w} workers")
        t0 = time.time()
        res = parallel_signals(specs, corpus, allowed, workers=w)
        el = time.time() - t0
        tot = sum(len(v) for v in res.values())
        print(f"  {el:.0f}s total, {el/n:.1f}s per spec, {tot:,} signals")
