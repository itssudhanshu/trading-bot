#!/usr/bin/env python3
"""The learning loop: which entry conditions actually predicted outcomes.

Every trade is recorded WITH THE FEATURES PRESENT AT ENTRY -- score, bucket,
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
import json
import statistics
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LEDGER = ROOT / "data" / "trade_features.jsonl"
WEIGHTS = ROOT / "data" / "selection_weights.json"

MIN_TRADES = 200        # before any weight moves
MAX_STEP = 0.15         # fraction of the way toward a new estimate
FEATURES = ("rs", "deliv", "liq", "off_high", "near_high", "rsi")
DEFAULT_WEIGHTS = {f: 1.0 for f in ("rs", "deliv", "liq", "near_high")}


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


def propose(trades, current=None):
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
    pos = {f: max(s, 0.0) for f, s in spreads.items()}
    total = sum(pos.values()) or 1.0
    new = {}
    for f, w in cur.items():
        target = len(pos) * pos.get(f, 0.0) / total      # 1.0 = average weight
        new[f] = round(w + (target - w) * MAX_STEP, 4)
        notes.append(f"{f}: spread {spreads.get(f, 0):+.2f}% -> weight {w:.2f} -> {new[f]:.2f}")
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
