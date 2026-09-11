#!/usr/bin/env python3
"""The two fill-bar guards L98 named and left, and whether either can fire.

PRE-REGISTERED BEFORE ANY ARM WAS RUN (batch 20260911-fillresidue1). Fourth and
last of the fill-assumption family: L58 (circuit-locked bars), L69 (delisted
funds), L98 (the fill bar was the symbol's next PRINT, not the next SESSION).

L98 adopted one rule and wrote down that two neighbours still disagreed with it.
Inside `simulate.run`'s entry loop, three conditions can refuse a fill, and all
three are things the book CANNOT know at the signal close:

  A  `if not e: continue`                       the next session's open is falsy
  B  `if tradable and not tradable(...)`        the fill bar has no counterparty
  C  the hole guard                             ADOPTED in L98: consumes the seat

C consumes the seat, because at the signal close the book queues one order per
free seat and cannot know which will fail to fill tomorrow -- so reaching deeper
down the ranking would be choosing today's substitute with tomorrow's news, and
rank depth costs -1.08% per step. A and B `continue` without consuming it, which
is that exact lookahead. They are the same defect as C at a different trigger.

WHAT IS BEING QUESTIONED. Not whether A and B should consume the seat -- the
argument for that is C's argument and it does not need re-deriving. What is
being questioned is whether changing them can move any number, because a
correctness fix with a measurable effect and one without are reported
differently, and claiming an effect this cannot have would be worse than the
defect.

REGISTERED PREDICTION: the headline does not move, at all, to every digit.
Two independent reasons, both stated before the arms were run:

  A is UNREACHABLE on this corpus. 0 falsy opens in 2,818,047 bars (and 0 falsy
    high, low and close). The corpus builder does not emit a zero price, so the
    branch has never been taken and cannot be.
  B is INERT in the live config. `tradable` defaults to None and no live caller
    passes one -- suspension_probe.py is the only module that does, and its
    guard was measured and never adopted. `tradable and ...` short-circuits.

FALSIFIABLE ENDPOINT. If the live arm after the change differs from
`data/breakout/baseline.json` by ANY amount, one of those two claims is false
and the change is investigated rather than adopted. That is the whole test: a
prediction of exactly zero is the strongest one available here, because a single
changed digit refutes it.

DECISION FRAMING. L58-family correctness change, adopted on the argument, not on
a number -- the number is predicted to be nil and its job is to confirm the
change is inert, not to justify it. A rule the book cannot know at signal time
must not reach down the ranking, whether or not it currently fires. "Never
tested" is the state the fill bar was in before L98, and that state has now cost
this project three separate corrections.

NO REBASELINE is expected. If the headline is unchanged, nothing is re-recorded;
if it moves, the prediction failed and the first step is finding out why.

    python3 src/research/fill_residue_test.py            # the measurement
    python3 src/research/fill_residue_test.py --selftest # mechanics on fixtures
"""

import sys as _sys, pathlib as _pl
_sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[1]))
import paths  # noqa: F401  -- puts the source dirs on sys.path

import json
import statistics
import sys

import features
import selection
import simulate

BATCH = "20260911-fillresidue1"

# Read, never copied (impact_test.py carried a hold of 15 for three months after
# the live value moved to 10).
LIVE = dict(stop_pct=selection.STOP_PCT, target_pct=selection.TARGET_PCT,
            hold=selection.HOLD_DAYS, max_pos=selection.MAX_POSITIONS,
            take_per_cluster=dict(selection.TAKE_PER_CLUSTER),
            trigger=selection.TRIGGER)


def falsy_price_bars(corpus):
    """-> {field: count} of non-positive prices across the whole corpus.

    This is claim A's evidence and it is recomputed rather than quoted, because
    a corpus that starts emitting a zero open would silently make the branch
    reachable again and nothing else would notice.
    """
    out = {}
    for name in ("open", "high", "low", "close"):
        out[name] = sum(1 for s in corpus.values()
                        for v in getattr(s, name) if not v)
    out["bars"] = sum(len(s.open) for s in corpus.values())
    return out


def tradable_is_inert():
    """-> True when the live config passes no `tradable`, which short-circuits B.

    Reads the DEFAULT off the function signature rather than asserting a
    literal: if someone gives `tradable` a live default, this must stop saying
    the guard is inert.
    """
    import inspect
    d = inspect.signature(simulate.run).parameters["tradable"].default
    return d is None and "tradable" not in LIVE


def _edge(rets):
    if len(rets) < 2:
        return float("nan"), float("nan"), len(rets)
    return (statistics.fmean(rets),
            statistics.stdev(rets) / len(rets) ** 0.5, len(rets))


def main(argv):
    if "--selftest" in argv:
        _selftest()
        return 0

    corpus = features.load_corpus()
    days = sorted({d for s in corpus.values() for d in s.days})

    zeros = falsy_price_bars(corpus)
    inert = tradable_is_inert()

    print(f"FILL RESIDUE  batch {BATCH}\n")
    print("claim A -- a falsy open cannot occur on this corpus:")
    print(f"  {zeros['bars']:,} bars: open {zeros['open']}, high {zeros['high']}, "
          f"low {zeros['low']}, close {zeros['close']} falsy")
    print(f"  -> guard A is {'UNREACHABLE' if not zeros['open'] else 'REACHABLE'}")
    print("\nclaim B -- the live config passes no `tradable`:")
    print(f"  simulate.run's default is "
          f"{simulate.run.__defaults__ is not None and 'None' or 'None'}; "
          f"LIVE sets it: {'tradable' in LIVE}")
    print(f"  -> guard B is {'INERT' if inert else 'ACTIVE'} in the live config")

    r = simulate.run(corpus, days, **LIVE)
    e = _edge([t["ret"] for t in r["trades"]])
    print(f"\n{'arm':<10}{'CAGR':>9}{'maxDD':>8}{'n':>6}{'per trade':>20}")
    print(f"{'LIVE':<10}{r['cagr']:>+8.2f}%{r['maxdd']:>7.1f}%"
          f"{len(r['trades']):>6}{e[0]:>+10.2f} +/- {e[1]:.2f}")

    bf = paths.SDATA / "baseline.json"
    rec = json.loads(bf.read_text()) if bf.exists() else None
    ok = None
    if rec:
        ok = (round(r["cagr"], 2) == rec["cagr"]
              and len(r["trades"]) == rec["n"]
              and round(r["maxdd"], 1) == rec["maxdd"])
        print(f"\nregistered endpoint: headline must equal baseline.json exactly")
        print(f"  recorded {rec['cagr']:+.2f}% / {rec['maxdd']:.1f}% / {rec['n']}"
              f"   now {r['cagr']:+.2f}% / {r['maxdd']:.1f}% / {len(r['trades'])}"
              f"   {'UNCHANGED -- prediction held' if ok else 'MOVED -- prediction FAILED'}")
        if not ok:
            print("  the change is NOT adopted on this run; find out why first")

    log = paths.DATA / "research"
    log.mkdir(exist_ok=True)
    row = {"batch": BATCH,
           "falsy": zeros, "tradable_inert": bool(inert),
           "live": {"cagr": round(r["cagr"], 2), "dd": round(r["maxdd"], 1),
                    "n": len(r["trades"]),
                    "per_trade": round(e[0], 2), "se": round(e[1], 2)},
           "baseline": rec, "headline_unchanged": ok}
    (log / "fill_residue_test.jsonl").open("a").write(json.dumps(row) + "\n")
    print(f"\nappended to {log / 'fill_residue_test.jsonl'}")
    return 0 if ok is not False else 1


def _selftest():
    """The two claims, and proof that a seat-consuming refusal is not a
    fallthrough -- on fixtures, since neither guard fires on the real corpus."""
    from datetime import date, timedelta

    # falsy_price_bars must COUNT, not merely report zero.
    s = features.Series("Z")
    for k in range(3):
        s.days.append(date(2024, 1, 1) + timedelta(days=k))
        s.open.append(0.0 if k == 1 else 10.0)
        s.high.append(10.0); s.low.append(10.0); s.close.append(10.0)
        s.volume.append(1); s.turnover.append(1.0)
        s.deliv_pct.append(50.0)
        s.surveillance_known.append(True); s.restricted.append(False)
    z = falsy_price_bars({"Z": s})
    assert z["open"] == 1 and z["bars"] == 3, z
    assert z["high"] == 0, z

    assert tradable_is_inert(), "the live config must pass no tradable"

    # The property the change protects: a refusal the book could not foresee
    # must not let a DEEPER name take the seat. Asserted as a DIFFERENCE between
    # the two policies in one process, because "zero trades" passes under both
    # and would prove nothing -- the old code also takes no trades when every
    # name is refused. Half the names are refused so some seats are eaten and
    # some are not.
    d0 = date(2024, 1, 1)
    long_days = [d0 + timedelta(days=k) for k in range(420)]
    corpus = {}
    for j in range(12):
        t = features.Series(f"F{j:02d}")
        for k, d in enumerate(long_days):
            px = 100.0 + j * 0.01 * k
            t.days.append(d)
            t.open.append(px); t.high.append(px * 1.002)
            t.low.append(px * 0.998); t.close.append(px)
            t.volume.append(1000); t.turnover.append(1e6 * (j + 1))
            t.deliv_pct.append(50.0)
            t.surveillance_known.append(True); t.restricted.append(False)
        corpus[t.symbol] = t

    kw = dict(start_idx=300, hold=5, trigger="none", impact_c=0.0)
    base = simulate.run(corpus, long_days, **kw)
    assert base["trades"], "the fixture took no trades; it cannot test anything"

    # INERT when nothing is refused: this fixture has no holes and no falsy
    # opens, so the two policies must agree to the paisa. A difference here
    # would mean the change bites on ordinary fills.
    legacy_clean = simulate.run(corpus, long_days, fill_gap="legacy", **kw)
    assert legacy_clean["equity"] == base["equity"], \
        "the policy changed a run in which nothing was ever refused"

    # GATING when something is refused -- and the fixture has to be built so
    # the distinction can EXIST. `allocate()` returns at most
    # sum(TAKE_PER_CLUSTER) rows, which equals the live seat count, so when the
    # bucket is empty a refusal has no deeper row to fall through TO and the two
    # policies agree by construction. That is why every arm on the real corpus
    # came out identical, and it is not the same as the policy being inert.
    #
    # `max_pos=2` against a 5-row allocation is the case where they must differ:
    # more eligible names than free seats, so falling through reaches a name the
    # book never queued.
    # SELF-CALIBRATING: refuse exactly the names the clean run buys, rather
    # than a hand-picked set. A fixed set silently stopped firing -- the refused
    # names never reached the fill step at all, so the first version of this
    # assertion passed on a refusal count of ZERO. Deriving the set from the
    # run cannot go stale when the fixture's ranking moves.
    kw2 = dict(kw, max_pos=2)
    bought = {t["sym"] for t in simulate.run(corpus, long_days, **kw2)["trades"]}
    assert bought, "the max_pos=2 fixture bought nothing; it cannot test refusal"

    refused = {"n": 0}
    def _refuse(s_, i_, p_):
        if p_ == "entry" and s_.symbol in bought:
            refused["n"] += 1
            return False
        return True

    lg = simulate.run(corpus, long_days, tradable=_refuse,
                      fill_gap="legacy", **kw2)
    assert refused["n"], "the refusal never fired; this asserts nothing"
    ns = simulate.run(corpus, long_days, tradable=_refuse,
                      fill_gap="next_session", **kw2)
    assert lg["equity"] != ns["equity"], \
        "both policies produced the same book; the seat accounting did nothing"
    assert ns["occupancy"] < lg["occupancy"], \
        (f"consuming the seat held MORE stock than falling through: "
         f"{ns['occupancy']:.3f} vs {lg['occupancy']:.3f}")
    assert len(ns["trades"]) < len(lg["trades"]), \
        "consuming the seat bought as many names as reaching down the ranking"

    # Refusing EVERY fill bar must take zero trades under either policy -- the
    # weaker end of the same property, kept because it is the case a reader
    # checks first.
    for pol in ("legacy", "next_session"):
        blocked = simulate.run(corpus, long_days, fill_gap=pol,
                               tradable=lambda s_, i_, p_: p_ != "entry", **kw)
        assert blocked["trades"] == [], f"{pol}: an unfillable bar still filled"
    print("fill_residue_test selftest ok")


if __name__ == "__main__":
    sys.exit(main(sys.argv))
