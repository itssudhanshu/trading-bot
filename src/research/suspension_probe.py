#!/usr/bin/env python3
"""Did the book fill trades on bars where no counterparty existed?

PRE-REGISTERED BEFORE RUNNING (batch 20260824-suspensionprobe1). Last of the
L58/L70/L71 artifact family: the circuit-lock guard taught this project that
fills need a counterparty, but the fix covered only SELECTION-time locks (the
signal bar). Two exposures were never measured:

  exits     a held position whose EXIT bar is LOWER-locked (high==low and
            close below the previous close: all sellers, no buyers) could not
            have sold at any price that day. The engine books min(stop, open)
            as if it did. Upper-locked exit bars are the mirror image -- selling
            INTO an upper lock is easy -- so they must NOT count.
  entries   a FILL bar that is UPPER-locked has no sellers; buying there is
            phantom. Selection marks the SIGNAL bar untriggered but cannot see
            tomorrow. Lower-locked fill bars are fine -- a buy order fills
            instantly into a sell-only market -- so they must NOT count.

AMENDMENT (same batch, second run). First run's sanity gate compared the
arm's FULL-PRECISION CAGR against the two-decimal reference constant with a
1e-6 tolerance and cried DRIFT while printing values identical to the
reference (+2.18% / 194). The gate now compares at the reference's own
precision. Nothing about the arms or the registered policy moved; the bogus
row in suspension_probe.jsonl stays (append-only) and is superseded by this
one.

DEFINITIONS (registered before results).
  locked      high == low (engine.py's own proxy for a band lock; the true
              2/5/10/20% band needs NSE's price-band file, which is absent).
  direction   close >= previous close -> upper, else lower. A lock with no
              previous bar blocks both directions.
  resumption  first bar after a >7-calendar-day hole in the symbol's series.
              Trading exists on resumption, so these stay TRADABLE in the
              guarded arm; they are counted because a stop filled across a
              month of suspension is still a different risk than one filled
              in continuous trade.

ARMS.
  LIVE       simulate.run(**remeasure.LIVE), tradable=None. Sanity gate: must
             equal split_audit's LIVE arm (+2.18% CAGR, 194 trades, same data,
             same day); a mismatch voids every delta below.
  GUARDED    identical run with tradable = direction-aware policy above:
              exit refused on lower-locked bars, entry refused on
              upper-locked bars, everything else tradable. Deferred positions
              simply try again next session, which is what a real book does.

DECISION FRAMING. L58-family data correction: a fill without a counterparty is
wrong at any t-statistic. Error bars reported as context only. As always,
recording the finding and deliberately re-baselining remain separate steps.

MECHANICAL NOTE. The `tradable` hook in simulate.run is additive and default-
inert by construction (None short-circuits before any behaviour change); the
selftest proves inertness AND proves the hook actually gates when active.

    python3 src/research/suspension_probe.py            # the measurement
    python3 src/research/suspension_probe.py --selftest # mechanics on fixtures
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

BATCH = "20260824-suspensionprobe1"

MAX_GAP_DAYS = 7          # same hole width the split audit calls a gap

LIVE = dict(stop_pct=selection.STOP_PCT, target_pct=selection.TARGET_PCT,
            hold=selection.HOLD_DAYS, max_pos=selection.MAX_POSITIONS,
            take_per_cluster=dict(selection.TAKE_PER_CLUSTER),
            trigger=selection.TRIGGER)

REF_CAGR, REF_N = 2.18, 194   # split_audit LIVE arm, batch 20260824-splitaudit1


def lock_direction(s, i):
    """-> "upper" | "lower" | None. None means not locked."""
    if s.high[i] != s.low[i]:
        return None
    if i < 1:
        return "upper"      # direction unknowable: block the dangerous side
    return "upper" if s.close[i] >= s.close[i - 1] else "lower"


def is_resumption(s, i):
    return i >= 1 and (s.days[i] - s.days[i - 1]).days > MAX_GAP_DAYS


def guarded_tradable(s, i, purpose):
    """The registered policy: refuse only the side with no counterparty."""
    d = lock_direction(s, i)
    if d is None:
        return True
    return d != ("lower" if purpose == "exit" else "upper")


def classify_bar(s, i):
    """-> label for reporting: normal / upper-lock / lower-lock / resumption."""
    d = lock_direction(s, i)
    if d == "lower":
        return "lower-lock"
    if d == "upper":
        return "upper-lock"
    if is_resumption(s, i):
        return "resumption"
    return "normal"


def run_arms(corpus, days):
    import entry as breakout_entry
    out = {}
    for name, kw in (("LIVE", {}), ("GUARDED", dict(tradable=guarded_tradable))):
        breakout_entry._CACHE.clear()
        out[name] = simulate.run(corpus, days, **{**LIVE, **kw})
    return out


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
    out = run_arms(corpus, days)

    live = out["LIVE"]
    counts = {"exit": {}, "entry": {}}
    flagged = []
    for t in live["trades"]:
        s = corpus[t["sym"]]
        hi = s.index_of(t["day"])
        if hi is None:
            continue
        lab = classify_bar(s, hi)
        counts["exit"][lab] = counts["exit"].get(lab, 0) + 1
        if lab != "normal":
            flagged.append((t["sym"], t["day"], "exit", lab, t["why"],
                            t["ret"]))
        fi = hi - (t["held"] or 0)
        if fi >= 0:
            elab = classify_bar(s, fi)
            counts["entry"][elab] = counts["entry"].get(elab, 0) + 1
            if elab != "normal":
                flagged.append((t["sym"], s.days[fi], "entry", elab,
                                "fill", t["ret"]))

    gu = out["GUARDED"]
    el = _edge([t["ret"] for t in live["trades"]])
    eg = _edge([t["ret"] for t in gu["trades"]])
    se = (el[1] ** 2 + eg[1] ** 2) ** 0.5

    print(f"SUSPENSION PROBE  batch {BATCH}\n")
    print("LIVE ledger bar classification:")
    print(f"  {'':<12}{'normal':>9}{'upper-lock':>12}{'lower-lock':>12}"
          f"{'resumption':>12}")
    for side in ("exit", "entry"):
        c = counts[side]
        print(f"  {side:<12}{c.get('normal', 0):>9}{c.get('upper-lock', 0):>12}"
              f"{c.get('lower-lock', 0):>12}{c.get('resumption', 0):>12}")
    if flagged:
        print("\nflagged fills/exits:")
        for sym, day, side, lab, why, ret in sorted(flagged, key=lambda x: x[5]):
            print(f"  {sym:<14} {day} {side:<6} {lab:<11} {why:<7} "
                  f"ret {ret:+7.2f}%")

    print(f"\n{'arm':<10}{'CAGR':>9}{'maxDD':>8}{'n':>6}"
          f"{'per trade':>20}")
    for name, r, e in (("LIVE", live, el), ("GUARDED", gu, eg)):
        print(f"{name:<10}{r['cagr']:>+8.2f}%{r['maxdd']:>7.1f}%"
              f"{len(r['trades']):>6}"
              f"{e[0]:>+10.2f} +/- {e[1]:.2f}")
    print(f"\nEDGE guarded-live: {eg[0]-el[0]:+.2f}% +/- {se:.2f}%  "
          f"t = {(eg[0]-el[0])/se if se else float('nan'):+.2f}")

    ok = (round(live["cagr"], 2) == REF_CAGR
          and len(live["trades"]) == REF_N)
    print(f"sanity: LIVE vs split_audit reference ({REF_CAGR}%, n={REF_N}): "
          f"{'OK' if ok else 'DRIFTED -- deltas above are void'}")

    log = paths.DATA / "research"
    log.mkdir(exist_ok=True)
    row = {"batch": BATCH,
           "live": {"cagr": round(live["cagr"], 2), "dd": round(live["maxdd"], 1),
                    "n": len(live["trades"])},
           "guarded": {"cagr": round(gu["cagr"], 2), "dd": round(gu["maxdd"], 1),
                       "n": len(gu["trades"])},
           "bars": counts,
           "sanity_ok": bool(ok)}
    (log / "suspension_probe.jsonl").open("a").write(json.dumps(row) + "\n")
    print(f"appended summary to {log / 'suspension_probe.jsonl'}")
    return 0


def _mk_series(symbol, days, closes, highs=None, lows=None):
    s = features.Series(symbol)
    for k, d in enumerate(days):
        px = closes[k]
        h = highs[k] if highs else px * 1.001
        lo = lows[k] if lows else px * 0.999
        s.days.append(d)
        s.open.append(px)
        s.high.append(h)
        s.low.append(lo)
        s.close.append(px)
        s.volume.append(1000)
        s.turnover.append(1e6)
        s.deliv_pct.append(50.0)
        s.surveillance_known.append(True)
        s.restricted.append(False)
    return s


def _selftest():
    """Mechanics: direction inference, the two policies, resumption, and proof
    that the shared hook is inert when None and gating when active."""
    from datetime import date, timedelta
    d0 = date(2024, 1, 1)
    days = [d0 + timedelta(days=k) for k in range(60)]

    closes = [100.0] * 60
    up_lock = _mk_series("UP", days, closes,
                         highs=[101.0] * 60, lows=[101.0] * 60)
    # high==low==101 over prev close 100 -> upper lock everywhere
    assert lock_direction(up_lock, 30) == "upper"
    assert guarded_tradable(up_lock, 30, "entry") is False
    assert guarded_tradable(up_lock, 30, "exit") is True

    # A lower lock needs the CLOSE to be falling: high==low over a decline.
    dn_closes = [100.0 - k * 0.1 for k in range(60)]
    dn_lock = _mk_series("DN", days, dn_closes,
                         highs=[c for c in dn_closes],
                         lows=[c for c in dn_closes])
    assert lock_direction(dn_lock, 30) == "lower"
    assert guarded_tradable(dn_lock, 30, "exit") is False
    assert guarded_tradable(dn_lock, 30, "entry") is True

    normal = _mk_series("NO", days, closes)
    assert lock_direction(normal, 30) is None
    assert guarded_tradable(normal, 30, "exit") is True

    # resumption: three-week hole then trading again -> tradable but labelled
    hole = _mk_series("HOLE", [d for k, d in enumerate(days) if not 20 <= k < 40],
                      [100.0] * 40)
    assert is_resumption(hole, 20) and (hole.days[20] - hole.days[19]).days > 7
    assert guarded_tradable(hole, 20, "exit") is True

    # Hook inertness + gating, on the shared engine path. A tiny corpus under
    # the ACTIVE strategy's selection (which needs >=200 bars per name);
    # blocking every bar must yield zero trades and flat equity; passing None
    # must equal omitting the argument.
    from datetime import timedelta as _td
    d0 = date(2024, 1, 1)
    long_days = [d0 + _td(days=k) for k in range(420)]
    corpus = {}
    for j in range(12):
        s = features.Series(f"F{j:02d}")
        for k, d in enumerate(long_days):
            px = 100.0 + j * 0.01 * k
            s.days.append(d)
            s.open.append(px)
            s.high.append(px * 1.002)
            s.low.append(px * 0.998)
            s.close.append(px)
            s.volume.append(1000)
            s.turnover.append(1e6 * (j + 1))
            s.deliv_pct.append(50.0)
            s.surveillance_known.append(True)
            s.restricted.append(False)
        corpus[s.symbol] = s

    base = simulate.run(corpus, long_days, start_idx=300, hold=5,
                        trigger="none", impact_c=0.0)
    inert = simulate.run(corpus, long_days, start_idx=300, hold=5,
                         trigger="none", impact_c=0.0, tradable=None)
    assert base["equity"] == inert["equity"], "tradable=None must be byte-inert"

    blocked = simulate.run(corpus, long_days, start_idx=300, hold=5,
                           trigger="none", impact_c=0.0,
                           tradable=lambda s, i, p: False)
    assert blocked["trades"] == [], "a bar nobody can trade on must not fill"
    assert blocked["equity"] == base["capital"]
    print("suspension_probe selftest ok")


if __name__ == "__main__":
    sys.exit(main(sys.argv))
