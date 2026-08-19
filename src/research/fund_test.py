#!/usr/bin/env python3
"""Do fundamentals carry information the price features do not?

Measured the ONLY honest way: on trades sampled at RANDOM from the universe,
never on trades a fundamental score helped choose. Measuring a feature on its
own selections is what made `deliv` look backwards and cost 26 CAGR points.

For each feature the universe is split at its median on the entry date, and the
question is whether the top half outperforms the bottom half over the holding
period. A spread near zero means the feature is decoration.
"""

import sys as _sys, pathlib as _pl
_sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[1]))
import paths  # noqa: F401  -- puts the source dirs on sys.path
import random
import statistics

import features as F
import fundamentals

HOLD, STOP, TARGET = 15, 10.0, 20.0
FEATS = ("rev_growth", "profit_growth", "margin", "margin_change")


def sample(corpus, days, n_dates=60, per_date=40, seed=11):
    """-> [{feature values, ret}] over randomly chosen symbol-dates."""
    rng = random.Random(seed)
    out = []
    step = max(1, (len(days) - 320) // n_dates)
    for di in range(300, len(days) - HOLD - 1, step):
        day = days[di]
        day_iso = day.isoformat()
        syms = [s for s in corpus if corpus[s].index_of(day) is not None]
        for sym in rng.sample(syms, min(per_date, len(syms))):
            s = corpus[sym]
            i = s.index_of(day)
            if i is None or i < 200 or i + 1 >= len(s):
                continue
            e = s.open[i + 1]
            if not e:
                continue
            f = fundamentals.features_asof(getattr(s, "fund", []) or [], day_iso)
            if not f:
                continue
            stop, tgt = e * (1 - STOP / 100), e * (1 + TARGET / 100)
            px = s.close[min(i + HOLD, len(s) - 1)]
            for k in range(i + 1, min(i + 1 + HOLD, len(s))):
                if s.low[k] <= stop:
                    px = min(stop, s.open[k]); break
                if s.high[k] >= tgt:
                    px = max(tgt, s.open[k]); break
            f["ret"] = (px / e - 1) * 100
            out.append(f)
    return out


def spread(rows, feat):
    vals = [r[feat] for r in rows if feat in r]
    if len(vals) < 100:
        return None, 0
    med = statistics.median(vals)
    hi = [r["ret"] for r in rows if r.get(feat) is not None and r[feat] > med]
    lo = [r["ret"] for r in rows if r.get(feat) is not None and r[feat] <= med]
    if len(hi) < 50 or len(lo) < 50:
        return None, 0
    return statistics.fmean(hi) - statistics.fmean(lo), len(hi) + len(lo)


def main():
    corpus = F.load_corpus()
    days = sorted({d for s in corpus.values() for d in s.days})
    rows = sample(corpus, days)
    print(f"{len(rows)} randomly-sampled trades with fundamentals visible\n")
    print(f"  {'feature':<16}{'spread':>10}{'n':>8}   reading")
    res = {}
    for f in FEATS:
        sp, n = spread(rows, f)
        res[f] = sp
        if sp is None:
            print(f"  {f:<16}{'--':>10}{n:>8}   too few observations")
            continue
        verdict = ("high half does better" if sp > 0.5 else
                   "low half does better" if sp < -0.5 else "NO SIGNAL")
        print(f"  {f:<16}{sp:>+9.2f}%{n:>8}   {verdict}")
    print()
    print("  For scale, the price features measured the same way:")
    print("    deliv +1.22%   liq -1.09%   off_high +0.30%   rs -0.03%")
    best = max((v for v in res.values() if v is not None), key=abs, default=None)
    if best is None or abs(best) < 0.5:
        print("\n  VERDICT: no fundamental feature separates outcomes. Adding any")
        print("  of them to the score would add noise, not information.")
    else:
        nm = [k for k, v in res.items() if v == best][0]
        print(f"\n  VERDICT: {nm} is the strongest at {best:+.2f}%. Worth a")
        print("  simulation before it earns a weight.")
    return res


if __name__ == "__main__":
    main()
