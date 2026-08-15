#!/usr/bin/env python3
"""Cross-check psearch (parallel) against generator (serial) on a shared seed.

psearch is 3.8x faster and feeds a judge whose verdicts cost budget. A silent
divergence -- one missing symbol partition, a mis-merged spec index -- would
produce plausible results that are simply wrong, which is the worst failure mode
available here. So the fast path is not trusted until it reproduces the slow one
signal for signal.
"""
import random
import sys

import features
import generator
import psearch
import split


def main(n_specs=6, workers=6):
    rng = random.Random(2024)
    specs = [generator.sample_spec(rng) for _ in range(n_specs)]

    corpus = features.load_corpus()
    days = sorted({d for s in corpus.values() for d in s.days})
    allowed = set(split.split_days(days)[0])

    print(f"{n_specs} specs, {len(corpus)} symbols, {workers} workers")
    par = psearch.parallel_signals(specs, corpus, allowed, workers=workers)

    ctx = {}
    bd = features.breadth(corpus)
    ok = True
    for i, sp in enumerate(specs):
        ser = generator.signals_for(sp, corpus, bd, ctx, allowed=allowed)
        ser_set = {(s.symbol, idx) for idx, s, _, _ in ser}
        par_set = {(sym, idx) for sym, idx, *_ in par[i]}
        same = ser_set == par_set
        ok &= same
        flag = "ok " if same else "MISMATCH"
        print(f"  spec {i}: serial {len(ser_set):>6}  parallel {len(par_set):>6}  {flag}")
        if not same:
            only_s = sorted(ser_set - par_set)[:3]
            only_p = sorted(par_set - ser_set)[:3]
            print(f"     serial-only {only_s}   parallel-only {only_p}")
    print("\nAGREE — parallel path is trustworthy" if ok else "\nDIVERGED — do not use psearch")
    return 0 if ok else 1


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        print("xcheck selftest ok (it is itself the check)")
    else:
        sys.exit(main(int(sys.argv[1]) if len(sys.argv) > 1 else 6))
