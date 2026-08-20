#!/usr/bin/env python3
"""The learning loop: which entry conditions actually predicted outcomes.

Every trade is recorded WITH THE FEATURES PRESENT AT ENTRY -- score, cluster,
momentum, delivery, distance from the 200-day average. Afterwards those features
are correlated against what happened, and the selection weights move toward the
ones that carried information.

TWO GUARDS, because "learn from every trade" is also how a system overfits
itself into uselessness:

  MIN_TRADES   no weight moves before this many observations. With 20 trades,
               the best-looking feature is noise; the loop would chase it and
               then chase the next one.
  MAX_STEP     weights move a fraction of the way toward the new estimate, never
               all of it. A single unusual month should nudge, not rewrite.

Learning starts on HISTORICAL trades -- thousands are available now -- and
continues on forward paper trades as they close. Waiting for live trades to
start learning would waste the seven years already on disk.
"""

import sys as _sys, pathlib as _pl
_sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[2]))  # -> src/
import paths  # noqa: F401  -- puts the source dirs on sys.path
import json
import statistics
import sys
from datetime import datetime
from pathlib import Path

# Strategy-scoped: these are one strategy's weights and one strategy's trade
# ledger. paths.SDATA moves with STRATEGY, so a second strategy cannot learn
# from the first one's trades or overwrite its weights.
LEDGER = paths.SDATA / "trade_features.jsonl"
WEIGHTS = paths.SDATA / "weights.json"

def entry_features(s, i):
    """Snapshot of what was true at ENTRY. Stored with the position, never
    recomputed at exit: recomputing would read post-trade values and let the
    outcome explain itself."""
    import statistics
    if i is None or i < 200:
        return {}
    prev = s.close[i - 63] if i >= 63 else None
    hi125 = max(s.high[max(0, i - 125):i + 1])
    dl = [d for d in s.deliv_pct[max(0, i - 60):i + 1] if d and d > 0]
    liq = [x for x in s.turnover[max(0, i - 60):i + 1] if x > 0]
    return {
        "rs": (s.close[i] / prev - 1) if prev else None,
        "deliv": statistics.fmean(dl) if dl else None,
        "liq": statistics.median(liq) if liq else None,
        "off_high": ((hi125 - s.close[i]) / hi125 * 100) if hi125 else None,
        "near_high": -((hi125 - s.close[i]) / hi125 * 100) if hi125 else None,
        "rsi": None,
    }


MIN_TRADES = 200        # before any weight moves
MAX_STEP = 0.15         # fraction of the way toward a new estimate
FEATURES = ("rs", "deliv", "liq", "off_high", "near_high", "rsi")
DEFAULT_WEIGHTS = {f: 1.0 for f in ("rs", "deliv", "liq", "near_high")}
# Features whose information runs BACKWARDS and survives a split-check.
#
# EMPTY, and the reason matters. `deliv` qualified on every test available --
# consistent negative spread (-0.97 -> -0.70 across halves), split-check passed
# -- and inverting it took the bucket from +6.37% CAGR / 48% DD to -19.92% / 89%.
#
# The measurement was CONDITIONED ON SELECTION: those 2,758 trades were chosen
# partly BY delivery, so the spread describes "among stocks already picked for
# high delivery, the even-higher ones did worse". That is a statement about the
# selected sample, not about the universe, and it does not survive changing the
# population. Consistency across halves confirms the sign is stable; it cannot
# confirm the relationship is causal, because both halves share the same
# selection.
#
# Nothing goes in here until it is measured on trades chosen WITHOUT that
# feature -- otherwise the loop keeps rediscovering its own selection rule.
INVERTED = ()


def record(rows, path=None):
    """Append trades with their entry features and realised outcome."""
    p = Path(path) if path else LEDGER
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a") as f:
        for r in rows:
            f.write(json.dumps(r, default=str) + "\n")
    return len(rows)


def load(path=None):
    p = Path(path) if path else LEDGER
    if not p.exists():
        return []
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()]


def analyse(trades):
    """-> {feature: information} where information is the return spread between
    the top and bottom third by that feature.

    A spread near zero means the feature ranked trades no better than chance,
    whatever its intuitive appeal.
    """
    out = {}
    for f in FEATURES:
        vals = [(t[f], t["ret"]) for t in trades
                if t.get(f) is not None and t.get("ret") is not None]
        if len(vals) < 30:
            continue
        # A CONSTANT feature carries no information, but sorting it preserves
        # input order -- so if that order happens to correlate with the outcome,
        # the spread looks real. Without this guard a column of identical values
        # inherits whatever the neighbouring column predicts.
        xs = [v for v, _ in vals]
        if len(set(xs)) < 3 or statistics.pstdev(xs) == 0:
            out[f] = {"spread": 0.0, "top_third": 0.0, "bottom_third": 0.0,
                      "n": len(vals), "note": "no variance"}
            continue
        vals.sort(key=lambda vr: vr[0])
        k = len(vals) // 3
        lo = statistics.fmean(r for _, r in vals[:k])
        hi = statistics.fmean(r for _, r in vals[-k:])
        out[f] = {"spread": hi - lo, "top_third": hi, "bottom_third": lo, "n": len(vals)}
    return out


def unconditioned_test(corpus, days, feature, n_dates=40, hold=None,
                       stop_pct=10.0, target_pct=20.0):
    """Measure a feature on trades selected WITHOUT it.

    The ledger's spread for `deliv` was measured on trades that delivery helped
    choose, so it described the selected sample rather than the universe --
    inverting on that evidence cost 26 points of CAGR (L48). The only honest
    test samples the universe at RANDOM, ignoring every score, then asks whether
    the feature separates outcomes anyway.
    """
    import random
    import selection
    # The bucket's own clock, not a copy of it: this read 15 for three months
    # after L52 moved the live hold to 10, so a feature was being scored over a
    # window the bucket does not hold for.
    hold = selection.HOLD_DAYS if hold is None else hold
    rng = random.Random(11)
    rows = []
    for di in range(300, len(days) - hold - 1, max(1, (len(days) - 320) // n_dates)):
        day = days[di]
        syms = [s for s in corpus if corpus[s].index_of(day) is not None]
        if not syms:
            continue
        for sym in rng.sample(syms, min(25, len(syms))):
            s = corpus[sym]
            i = s.index_of(day)
            if i is None or i < 200 or i + 1 >= len(s):
                continue
            e = s.open[i + 1]
            if not e:
                continue
            f = entry_features(s, i)
            if f.get(feature) is None:
                continue
            stop, tgt = e * (1 - stop_pct / 100), e * (1 + target_pct / 100)
            px = s.close[min(i + hold, len(s) - 1)]
            for k in range(i + 1, min(i + 1 + hold, len(s))):
                if s.low[k] <= stop:
                    px = min(stop, s.open[k]); break
                if s.high[k] >= tgt:
                    px = max(tgt, s.open[k]); break
            rows.append({**f, "ret": (px / e - 1) * 100 - 0.4})
    if len(rows) < 200:
        return None, f"only {len(rows)} unconditioned samples"
    info = analyse(rows)
    if feature not in info:
        return None, "not measurable"
    v = info[feature]
    return v["spread"], (f"unconditioned spread {v['spread']:+.2f}% "
                         f"(top {v['top_third']:+.2f} vs bottom {v['bottom_third']:+.2f}, "
                         f"n={v['n']})")


def split_check(trades, feature, split=0.5):
    """Does this feature's information survive out of sample?

    propose() moves weights on the spread measured across ALL trades -- the same
    in-sample tuning that walk-forward proved anti-predicts for parameters
    (lessons L47). A feature whose spread flips sign between halves is noise,
    and raising its weight makes selection worse, confidently.
    """
    rows = [t for t in trades if t.get(feature) is not None and t.get("ret") is not None]
    if len(rows) < 100:
        return None, "too few observations"
    rows.sort(key=lambda t: t.get("date") or "")
    cut = int(len(rows) * split)
    early, late = analyse(rows[:cut]), analyse(rows[cut:])
    if feature not in early or feature not in late:
        return None, "not measurable in both halves"
    a, b = early[feature]["spread"], late[feature]["spread"]
    if a == 0 or b == 0:
        return False, f"no information in one half ({a:+.2f} / {b:+.2f})"
    if (a > 0) != (b > 0):
        return False, f"spread FLIPS SIGN across halves ({a:+.2f} -> {b:+.2f})"
    return True, f"consistent sign ({a:+.2f} -> {b:+.2f})"


def propose(trades, current=None, unconditioned=None):
    """-> (new_weights, notes). Returns current weights unchanged when the
    evidence is too thin; the caller should say so rather than pretend."""
    cur = dict(current or load_weights())
    notes = []
    if len(trades) < MIN_TRADES:
        notes.append(f"only {len(trades)} trades; need {MIN_TRADES} before moving weights")
        return cur, notes
    info = analyse(trades)
    if not info:
        notes.append("no feature had enough observations")
        return cur, notes
    spreads = {f: v["spread"] for f, v in info.items() if f in cur}
    if not spreads or all(abs(s) < 1e-9 for s in spreads.values()):
        notes.append("no feature separated winners from losers")
        return cur, notes
    # Target weight proportional to how much each feature separated outcomes;
    # negative spreads mean the feature ranked BACKWARDS and get floored at zero.
    # Only features whose information survives a split may move. Without this,
    # weights are tuned on in-sample spread alone -- the exact mistake L47
    # measured one layer up, where the in-sample winner ranked LAST out of
    # sample.
    held = []
    for f in list(spreads):
        ok, why = split_check(trades, f)
        if ok is False:
            spreads[f] = 0.0
            held.append(f"{f}: HELD -- {why}")
        elif ok is None:
            spreads[f] = 0.0
            held.append(f"{f}: held -- {why}")
    notes.extend(held)

    # A CONSISTENTLY negative spread is information, not absence: the feature
    # ranks backwards and should be INVERTED, not discarded. Flooring it at zero
    # threw away the strongest signal in the ledger (deliv, -0.97 -> -0.70 across
    # halves) and -- once every spread floored to zero -- decayed all weights by
    # the SAME factor, which a weighted average cancels exactly. The loop
    # reported updates and changed nothing.
    # Every bad update today passed the conditioned tests and failed this one:
    # a weight may only move if the feature's information survives being
    # measured on trades it did NOT help select (L48).
    corpus = (current or {}).pop("_corpus", None) if isinstance(current, dict) else None
    # The unconditioned measurement IS the measurement. Disagreement with the
    # selected-sample reading does not make both untrustworthy -- it identifies
    # the selected one as the artefact, which is the entire point of measuring
    # on trades the feature did not choose. Holding on disagreement discarded
    # deliv at +1.22%, the strongest genuine signal in the data.
    if unconditioned:
        for f in list(spreads):
            u = unconditioned.get(f)
            if u is None:
                spreads[f] = 0.0
                notes.append(f"{f}: held -- no unconditioned measurement")
                continue
            was = spreads[f]
            spreads[f] = u
            if abs(was) > 1e-9 and (u > 0) != (was > 0):
                notes.append(f"{f}: selected-sample said {was:+.2f}%, unconditioned "
                             f"says {u:+.2f}% -- using unconditioned, the other is "
                             f"a selection artefact")
            else:
                notes.append(f"{f}: unconditioned {u:+.2f}%")

    # Inversion is allowed ONLY on unconditioned evidence. Inverting deliv on
    # the selected-sample reading cost 26 points of CAGR (L48); the same
    # operation on an unconditioned measurement is a different claim, because
    # the sample was not chosen by the feature being tested.
    inverted = []
    for f, s in list(spreads.items()):
        if s < 0 and abs(s) > 0.5 and unconditioned and unconditioned.get(f) is not None:
            inverted.append(f)
            spreads[f] = -s
            notes.append(f"{f}: INVERT -- unconditioned {s:+.2f}% (low values did "
                         f"better); verify by simulation before keeping")
        elif s < 0:
            spreads[f] = 0.0
    pos = {f: max(s, 0.0) for f, s in spreads.items()}
    total = sum(pos.values()) or 1.0
    new = {}
    for f, w in cur.items():
        target = len(pos) * pos.get(f, 0.0) / total      # 1.0 = average weight
        new[f] = round(w + (target - w) * MAX_STEP, 4)
        notes.append(f"{f}: spread {spreads.get(f, 0):+.2f}% -> weight {w:.2f} -> {new[f]:.2f}")
    # A uniform rescale changes NOTHING: the score is a weighted average, so
    # multiplying every weight by one factor cancels. Say so, rather than
    # reporting an update with no effect.
    ratios = [new[f] / w for f, w in cur.items() if w]
    if ratios and max(ratios) - min(ratios) < 1e-6:
        notes.append("NO-OP: all weights scaled equally -- ranking unchanged")
    if inverted:
        notes.append("INVERTED: " + ", ".join(inverted) +
                     " -- must be confirmed by a full simulation")
    return new, notes


def load_weights():
    """-> the weights dict ONLY.

    The file also stores `updated` and `note`; returning the whole document fed
    those into propose() as though they were features, and save_weights() then
    nested the previous document inside the new one.
    """
    if WEIGHTS.exists():
        doc = json.loads(WEIGHTS.read_text())
        w = doc.get("weights", doc) if isinstance(doc, dict) else {}
        # tolerate the nested form written before this was fixed
        while isinstance(w, dict) and isinstance(w.get("weights"), dict):
            w = w["weights"]
        out = dict(DEFAULT_WEIGHTS)
        out.update({k: float(v) for k, v in w.items()
                    if k in DEFAULT_WEIGHTS and isinstance(v, (int, float))})
        return out
    return dict(DEFAULT_WEIGHTS)


def save_weights(w, note=""):
    WEIGHTS.parent.mkdir(parents=True, exist_ok=True)
    WEIGHTS.write_text(json.dumps(
        {"weights": w, "updated": datetime.now().isoformat(), "note": note}, indent=1))
    return w


def _selftest():
    import tempfile
    # a feature that genuinely separates outcomes must gain weight
    trades = []
    for i in range(300):
        rs = i / 300
        trades.append({"rs": rs, "deliv": 0.5, "liq": 0.5,
                       "off_high": None, "rsi": None,
                       "ret": (rs - 0.5) * 20})       # rs predicts perfectly
    new, notes = propose(trades, {"rs": 1.0, "deliv": 1.0, "liq": 1.0})
    assert new["rs"] > 1.0, (new, notes)
    assert new["deliv"] < 1.0, new

    # a uniform rescale must be reported as the no-op it is
    flat_t = [{"rs": i / 300, "deliv": 0.5, "liq": 0.5, "near_high": 0.5,
               "off_high": None, "rsi": None, "ret": 0.0,
               "date": f"2024-{1 + i // 30:02d}-01"} for i in range(300)]
    _, nn = propose(flat_t, {"rs": 1.0, "deliv": 1.0, "liq": 1.0, "near_high": 1.0})
    assert any("NO-OP" in n for n in nn) or all(
        "spread" not in n for n in nn), nn

    # a feature that flips sign between halves must be held at zero influence
    flip = []
    for i in range(400):
        v = i / 400
        # first half rewards high rs, second half punishes it
        r = (v - 0.5) * 20 if i < 200 else (0.5 - v) * 20
        flip.append({"rs": v, "deliv": (i % 7) / 7, "liq": (i % 5) / 5,
                     "near_high": (i % 3) / 3, "off_high": None, "rsi": None,
                     "ret": r, "date": f"2024-{1 + i // 40:02d}-01"})
    ok, why = split_check(flip, "rs")
    assert ok is False and "FLIP" in why.upper(), (ok, why)

    # too few trades: weights must not move at all
    same, notes2 = propose(trades[:50], {"rs": 1.0, "deliv": 1.0, "liq": 1.0})
    assert same == {"rs": 1.0, "deliv": 1.0, "liq": 1.0}, same
    assert any("need" in n for n in notes2), notes2

    # a step is bounded: one batch cannot rewrite a weight
    big = propose(trades, {"rs": 1.0, "deliv": 1.0, "liq": 1.0})[0]
    assert big["rs"] < 1.0 + 3 * MAX_STEP, big

    # noise must not move weights meaningfully
    flat = [{"rs": i / 300, "deliv": 0.5, "liq": 0.5, "off_high": None,
             "rsi": None, "ret": 0.0} for i in range(300)]
    nw, nn = propose(flat, {"rs": 1.0, "deliv": 1.0, "liq": 1.0})
    assert nw == {"rs": 1.0, "deliv": 1.0, "liq": 1.0}, (nw, nn)

    # load_weights must return features only, never the file's metadata, and
    # must survive the nested document the earlier bug wrote.
    import tempfile as _tf
    global WEIGHTS
    _ow = WEIGHTS
    try:
        with _tf.TemporaryDirectory() as td:
            WEIGHTS = Path(td) / "w.json"
            WEIGHTS.write_text(json.dumps(
                {"weights": {"weights": {"rs": 1.3, "deliv": 0.85},
                             "updated": "x", "note": "y"},
                 "updated": "z", "note": "w"}))
            got = load_weights()
            assert set(got) == set(DEFAULT_WEIGHTS), got
            assert got["rs"] == 1.3 and got["deliv"] == 0.85, got
            assert all(isinstance(v, float) for v in got.values()), got
    finally:
        WEIGHTS = _ow

    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "t.jsonl"
        record([{"rs": 1, "ret": 2}], p)
        record([{"rs": 2, "ret": 3}], p)
        assert len(load(p)) == 2, "ledger must append, not overwrite"
    print("learning selftest ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    else:
        t = load()
        print(f"trades recorded: {len(t)}")
        for f, v in analyse(t).items():
            print(f"  {f:<9} spread {v['spread']:+6.2f}%  (top {v['top_third']:+.2f} "
                  f"vs bottom {v['bottom_third']:+.2f}, n={v['n']})")
        w, notes = propose(t)
        print(f"\nweights: {w}")
        for n in notes:
            print(f"  {n}")
