#!/usr/bin/env python3
"""Which input is scarce: the breakout, or the five seats?

HYPOTHESIS. The bucket looks like it is gated by the breakout -- most days it
buys one name, or none. If breakouts were genuinely rare among gated candidates
that would be the whole story and there would be nothing to decide. But the
trigger is applied only to the five names the RANKING already nominated, so a
name can be breaking out at rank 7 and never be looked at.

ENDPOINT. Per session, over the last N: how many of the ~40 gated candidates
broke out, how many of the nominated five broke out, and at what RANK the
un-nominated breakouts sat. If breakouts per day comfortably exceed five, the
seats are the scarce input and "substitute the next-ranked breakout" is a real
question rather than a hypothetical.

WHAT THIS DOES NOT DO. It does not test that substitution. It PRICES it, and
the price is the reason not to bother. rank_test's cohort k is ranks 2k..2k+1,
so the median skipped breakout at rank 11 is cohort 5 -- the deepest cohort it
measured, and the one that earns +6.63% +/- 1.79% per trade LESS than the top
(t = 3.71, n = 1015, batch 20260819-postlock). The trigger itself is worth
+1.99% per trade at t = +1.29. So substituting the next-ranked breakout spends a
RESOLVED -6.6% to buy an UNRESOLVED +2.0%. That is the wrong side of the only
effect in this project that clears its error bar. Measure it if it is ever
built; do not expect it to survive.
"""

import sys as _sys, pathlib as _pl
_sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[1]))
import paths  # noqa: F401  -- puts the source dirs on sys.path
import statistics
import features
import selection

SESSIONS = 40


def measure(corpus, days):
    """-> per-session (date, gated, broke_out, nominated_and_broke_out, ranks).

    `ranks` is the within-cluster rank of every breakout the bucket did NOT
    nominate -- the depth a substitution rule would have to reach to buy it.
    """
    out = []
    for as_of in days:
        rows = selection.build(corpus, as_of)
        if not rows:
            continue
        # rank within a cluster, which is what TAKE_PER_CLUSTER cuts against.
        # rows arrives score-sorted, so a per-cluster counter IS the rank.
        seen, rank = {}, {}
        for r in rows:
            c = r["cluster"]
            seen[c] = seen.get(c, 0) + 1
            rank[id(r)] = seen[c]
        take = selection.TAKE_PER_CLUSTER
        got = selection.allocate(rows)
        chosen = {id(r) for r in got}
        missed = [rank[id(r)] for r in rows
                  if r["triggered"] and id(r) not in chosen
                  and rank[id(r)] > take.get(r["cluster"], 0)]
        out.append((as_of, len(rows), sum(r["triggered"] for r in rows),
                    len(got), missed))
    return out


def report(per):
    n = len(per)
    gated = sum(p[1] for p in per) / n
    trig = sum(p[2] for p in per) / n
    bought = sum(p[3] for p in per) / n
    depths = [d for p in per for d in p[4]]
    print(f"{n} sessions, trigger={selection.TRIGGER}, "
          f"take={selection.TAKE_PER_CLUSTER}")
    print(f"  gated candidates        {gated:5.1f} per session")
    print(f"  ...that broke out       {trig:5.1f} per session "
          f"({100 * trig / gated:.0f}%)")
    print(f"  ...that were nominated  {bought:5.1f} per session (cap is 5)")
    print(f"  breakouts left unbought {trig - bought:5.1f} per session, "
          f"median rank {statistics.median(depths) if depths else 0:.0f}, "
          f"deepest {max(depths) if depths else 0}")
    print()
    print("date         gated  broke_out  bought")
    for d, g, t, a, _ in per[-12:]:
        print(f"{d}   {g:4d}   {t:6d}   {a:5d}")
    return trig, bought


def _selftest():
    corpus = features.load_corpus()
    days = sorted({d for s in corpus.values() for d in s.days})[-5:]
    per = measure(corpus, days)
    assert per, "no session produced candidates"
    trig, bought = report(per)
    # The property, not the number: if breakouts ever became scarcer than the
    # seats, the trigger would BE the constraint and this file's premise -- and
    # every argument built on it about substituting deeper names -- is void.
    assert trig > bought, (
        f"{trig:.1f} breakouts vs {bought:.1f} nominated: the trigger is now "
        f"the binding constraint, not the five seats")
    assert bought <= 5, "allocate() returned more names than the bucket holds"
    print("occupancy_test selftest ok")


if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        _selftest()
    else:
        corpus = features.load_corpus()
        days = sorted({d for s in corpus.values() for d in s.days})[-SESSIONS:]
        report(measure(corpus, days))
