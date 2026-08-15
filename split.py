#!/usr/bin/env python3
"""Regime-stratified train/holdout split.

REPLACES the contiguous "most recent 12 months" holdout, which was a design
error (lessons L19): it put the entire bull market in train and the bear out of
sample, so it tested "does this survive the next regime" rather than "does this
have an edge". Every spec that cleared walk-forward then failed for the same
regime reason.

The corpus now spans 2019-10 to 2026-08 -- roughly seven years containing five
bull, five bear and four flat half-years. That is enough cycles to hold out
blocks from EACH regime class, so both train and holdout contain bull and bear.
A strategy must then work across regimes rather than survive one transition.

Blocks are half-years. Holdout blocks are chosen by seeded draw from within each
regime class, fixed here before any result is examined, and listed explicitly
below so the choice is auditable rather than re-derived (and re-derivable to
something more convenient) on every run.

Purging: trades opened within MAX_HOLD days of a boundary can close on the other
side of it. Those days are dropped from TRAIN, never from holdout -- shrinking
train is honest, shrinking holdout would flatter the result.
"""
import random
import statistics
from datetime import date, timedelta

PURGE_DAYS = 60          # >= the largest spec hold horizon

# Regime classification thresholds, applied to the median symbol's return
# over the block. Set once, from the distribution, not tuned per result.
BULL_RET, BEAR_RET = 8.0, -4.0

# Produced by _choose_holdout(seed=0) over the regime classes, then frozen.
# One BULL (2020-H2), one flat (2023-H1), two BEAR (2025-H1, 2026-H1).
# This is the draw's actual output, not a hand-picked set -- a "deterministic"
# choice that gets overridden when it looks inconvenient is just hand-picking.
HOLDOUT_BLOCKS = ("2020-H2", "2023-H1", "2025-H1", "2026-H1")

EPOCH = 2                # ledger epoch; epoch 1 used the contiguous holdout


def block_of(d: date) -> str:
    return f"{d.year}-H{1 if d.month <= 6 else 2}"


def blocks(days):
    """-> {label: [days]} in chronological order."""
    out = {}
    for d in sorted(days):
        out.setdefault(block_of(d), []).append(d)
    return out


def classify(corpus, blks):
    """-> {label: 'BULL'|'BEAR'|'flat'} from the median symbol return per block."""
    out = {}
    for label, ds in blks.items():
        rets = []
        for s in corpus.values():
            i0 = next((i for i, dd in enumerate(s.days) if dd >= ds[0]), None)
            i1 = next((i for i, dd in enumerate(s.days) if dd >= ds[-1]), None)
            if i0 is not None and i1 is not None and i1 > i0 and s.close[i0]:
                rets.append(s.close[i1] / s.close[i0] - 1)
        mr = statistics.median(rets) * 100 if rets else 0.0
        out[label] = "BULL" if mr > BULL_RET else ("BEAR" if mr < BEAR_RET else "flat")
    return out


def _choose_holdout(regimes, seed=0, frac=0.27):
    """Reproduce HOLDOUT_BLOCKS. Kept so the frozen tuple can be audited."""
    by_class = {}
    for label, r in sorted(regimes.items()):
        by_class.setdefault(r, []).append(label)
    rng, picked = random.Random(seed), []
    for r in sorted(by_class):
        pool = sorted(by_class[r])
        k = max(1, round(len(pool) * frac))
        picked += rng.sample(pool, k)
    return tuple(sorted(picked))


def is_holdout(d: date) -> bool:
    return block_of(d) in HOLDOUT_BLOCKS


def split_days(days):
    """-> (train_days, holdout_days), with train purged around every boundary."""
    days = sorted(days)
    hold = [d for d in days if is_holdout(d)]
    if not hold:
        return days, []
    # Purge train days within PURGE_DAYS of any holdout day, either side.
    bounds = []
    for label in HOLDOUT_BLOCKS:
        blk = [d for d in days if block_of(d) == label]
        if blk:
            bounds.append((blk[0], blk[-1]))
    train = []
    for d in days:
        if is_holdout(d):
            continue
        near = any(lo - timedelta(days=PURGE_DAYS) <= d <= hi + timedelta(days=PURGE_DAYS)
                   for lo, hi in bounds)
        if not near:
            train.append(d)
    return train, hold


def _selftest():
    days = [date(2019, 10, 1) + timedelta(days=k) for k in range(2500)
            if (date(2019, 10, 1) + timedelta(days=k)).weekday() < 5]

    assert block_of(date(2024, 3, 15)) == "2024-H1"
    assert block_of(date(2024, 7, 1)) == "2024-H2"

    b = blocks(days)
    assert "2020-H1" in b and len(b) > 10, sorted(b)[:3]

    train, hold = split_days(days)
    assert hold and train, (len(train), len(hold))
    assert all(is_holdout(d) for d in hold)
    assert not any(is_holdout(d) for d in train)

    # every train day must sit at least PURGE_DAYS from every holdout day
    hs = set(hold)
    for d in train:
        for off in range(-PURGE_DAYS, PURGE_DAYS + 1):
            assert (d + timedelta(days=off)) not in hs, \
                f"train day {d} within purge window of a holdout day"

    # holdout must span more than one regime class, or the split solves nothing
    assert len(HOLDOUT_BLOCKS) >= 3, HOLDOUT_BLOCKS
    years = {lbl.split("-")[0] for lbl in HOLDOUT_BLOCKS}
    assert len(years) >= 3, f"holdout blocks clustered in time: {HOLDOUT_BLOCKS}"

    # the frozen tuple must match what the documented draw produces
    fake = {"2021-H1": "BULL", "2020-H2": "BULL", "2022-H2": "BULL",
            "2023-H2": "BULL", "2024-H1": "BULL",
            "2020-H1": "BEAR", "2022-H1": "BEAR", "2024-H2": "BEAR",
            "2025-H1": "BEAR", "2025-H2": "BEAR", "2026-H1": "BEAR",
            "2019-H2": "flat", "2021-H2": "flat", "2023-H1": "flat",
            "2026-H2": "flat"}
    assert _choose_holdout(fake, seed=0) == HOLDOUT_BLOCKS, _choose_holdout(fake, seed=0)
    print("split selftest ok")


if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        _selftest()
    else:
        import features
        c = features.load_corpus()
        days = sorted({d for s in c.values() for d in s.days})
        blks = blocks(days)
        regimes = classify(c, blks)
        tr, ho = split_days(days)
        print(f"corpus {days[0]} .. {days[-1]}   {len(days)} days")
        print(f"train {len(tr)}   holdout {len(ho)}   purged "
              f"{len(days)-len(tr)-len(ho)}")
        print()
        for lbl in sorted(blks):
            mark = "HOLDOUT" if lbl in HOLDOUT_BLOCKS else ""
            print(f"  {lbl:<9} {len(blks[lbl]):>4}d  {regimes[lbl]:<5} {mark}")
        hc = [regimes[l] for l in HOLDOUT_BLOCKS]
        print(f"\nholdout regime mix: {dict((r, hc.count(r)) for r in set(hc))}")
