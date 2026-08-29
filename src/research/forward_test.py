#!/usr/bin/env python3
"""What the forward run must show, written down before it has anything to show.

Two buckets now run side by side inside breakout -- `main` (rank inside each band,
fill a 3/2 quota) and `pooled` (rank everything, take the best five). This file
fixes, in advance, what would count as the backtest being RIGHT and what would
count as it being WRONG, so that in three months the numbers are read against a
standard rather than argued about.

    python3 src/research/forward_test.py          # where we are against the bounds

THE HONEST HEADLINE FIRST, because it changes what this is for.

**The forward run cannot prove the strategy is profitable, and not for years.**
Per-trade standard deviation is ~15% and the backtested edge is ~+2.15%, so
separating the edge from zero at |t| > 2 needs about 195 trades. At the
backtested rate of 2.9 trades a month that is **5.7 YEARS**. No amount of care
in the setup shortens it; it is arithmetic on the variance of the thing being
measured.

**And main vs pooled will essentially never separate.** They differ by +0.04%
per trade (t = +0.03, L65). Detecting a gap that size needs a sample larger than
this book will generate in a human lifetime. Running both is still right --
neither is measurably worse, they hold visibly different books, and the cost is
zero -- but nobody should wait on a winner.

SO WHAT IS THIS FOR. The fast checks. Every one of them is STRUCTURAL: does the
live book behave the way the backtest said it would, in ways that show up in
weeks rather than years? A backtest that has already been wrong once by half
(L58, the circuit-lock guard) is not owed the benefit of the doubt, and the
structural checks are where a second error of that kind would surface first.

  READABLE IN WEEKS          how many names it holds, how long it holds them,
                             whether orders actually fill
  READABLE IN MONTHS         how often it trades
  READABLE IN YEARS          whether it makes money
  NOT READABLE, EVER         which of the two buckets is better

THE EXPECTATIONS, frozen from batch 20260820-forward at the live settings
(hold 10, stop 10, target 20, breakout, c=1.0, 5 seats), over 5.67 years:

    main    n=195   34.4 trades/yr (2.86/mo)   +2.15% +/- 1.08 per trade
            win 47%   mean hold 6.9d   occupancy 3.10   maxDD 31.0%
    pooled  n=207   36.5 trades/yr (3.04/mo)   +2.19% +/- 1.05 per trade
            win 46%   mean hold 6.8d   occupancy 2.11   maxDD 30.0%

Mean hold is 6.9 days and not 10: the 10-day limit is a backstop and most trades
exit earlier on the stop or the target. A forward book averaging 10 would mean
the stop and target were not being hit, which is a different strategy.

THE BOUNDS. Each fires only once its minimum sample exists; before that it
reports "too early" and NOT "pass", because a check that cannot fail is not a
check.

  trade rate      >= 6 months.  FAIL if the observed count falls outside a 95%
                  Poisson interval around expected_rate * months. Six and not
                  three: at three months the band is 2.8..14.3 around an
                  expectation of 8.6, so a book trading at half rate passes.
                  The check only acquires the power to see a halving at ~5.4
                  months, and a check that cannot fail is not a check.
  occupancy       >= 4 weeks.   FAIL if the mean held count is more than 1.0
                  away from expectation.
  hold length     >= 10 closed. FAIL if the mean is outside 4-10 sessions.
  fills           always.       FAIL if a pending order is older than 3
                  sessions -- that is the fill path broken, not a market.
  per-trade edge  >= 195 closed. Before that: NO VERDICT. Reported with its
                  error bar so the number can be watched, never judged.

WHAT A FAILURE MEANS. Not "stop trading". It means the backtest and reality
disagree about something structural, and the disagreement must be explained
before any backtested number is quoted again. That is the whole value: the
backtest's numbers are currently unfalsifiable, and this makes them falsifiable.

ONE ASYMMETRY, RECORDED RATHER THAN CORRECTED. `main` carries four positions
opened 2026-08-17..19, before this comparison begins; `pooled` starts empty.
Only trades ENTERED on or after FORWARD_FROM count for either bucket, so those
four are excluded from the numbers -- but they still occupy main's seats until
they exit, so main will queue fewer new names for its first ~10 sessions. It
washes out; it is not corrected, because retiring live positions to tidy a
comparison would be exactly the kind of interference this book does not do.
"""

import sys as _sys, pathlib as _pl
_sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[1]))
import paths  # noqa: F401  -- puts the source dirs on sys.path
import datetime as _dt
import sqlite3
import statistics

import positions

BATCH = "20260820-forward"
FORWARD_FROM = "2026-08-21"      # the first session both buckets queue into

# Frozen expectations. NOT read live: the point is to compare reality against
# what was believed BEFORE it arrived, so re-deriving these from a fresh
# backtest would defeat the file. Re-freeze deliberately, with a new batch.
# A field set to None was NEVER REGISTERED for that book, and its check reports
# "not registered" instead of running. That is not the same as a check that
# cannot fail: it says out loud that there is no frozen number to test against,
# where inventing one would let the book pass a bar nobody set.
#
# etf_trend is the case. Its pre-registration (trend_fund_test, batch
# 20260824-trendfund2, L70) froze CAGR, drawdown, n and the per-trade mean and
# error -- and nothing else. Re-running the study today to fill in win, hold,
# occupancy and the trade rate would produce numbers from a corpus five sessions
# longer than the one the rules were frozen against, which is re-baselining an
# expectation to fit the book it is supposed to judge.
EXPECTED = {
    "main":   dict(rate_mo=2.86, per_trade=2.15, se=1.08, sd=15.0,
                   win=47.0, hold=6.9, occ=3.10, maxdd=31.0, n=195),
    "pooled": dict(rate_mo=3.04, per_trade=2.19, se=1.05, sd=15.1,
                   win=46.0, hold=6.8, occ=2.11, maxdd=30.0, n=207),
    # FAILED its promotion bar (edge t = +1.19, L70) and runs forward anyway as
    # an evidence generator. Its exit is an SMA100 break, so `hold` is not a
    # rule it obeys and there is no day count to compare against.
    "etf_trend": dict(rate_mo=None, per_trade=1.04, se=1.08, sd=None,
                      win=None, hold=None, occ=None, maxdd=17.8, n=147),
    # main's ranks with at most one name per broad sector, holding cash rather
    # than reaching deeper. Frozen from H18's hold-cash arm (batch
    # 20260829-sectorcap, L89): +2.99% CAGR, 31.6% DD, n=180, win 46%,
    # occupancy 2.92, +1.32% +/- 1.20 per trade.
    #
    # Two fields are DERIVED and say so rather than being measured: `rate_mo`
    # scales main's 2.86 by 180/195, the two arms' trade counts over the same
    # span, and `hold` is main's 6.9 because the cap changes WHICH names are
    # bought and not how long they are held -- the exit rules are byte-identical.
    # `sd` was not printed by that run and is left unregistered.
    "capped": dict(rate_mo=2.64, per_trade=1.32, se=1.20, sd=None,
                   win=46.0, hold=6.9, occ=2.92, maxdd=31.6, n=180,
                   # It started EIGHT DAYS after the other two, so its expected
                   # trade count must be measured from its own first session.
                   # Against the shared FORWARD_FROM it would be charged for a
                   # week it did not trade -- small now, and exactly the kind of
                   # quiet bias that makes a gate fire on the wrong book.
                   since="2026-08-29"),
}
MIN_N_EDGE = 195          # trades before the per-trade edge may be judged
# SIX months, not three, and the arithmetic is why. A 95% Poisson band around
# 2.86/mo is 2.8..14.3 at three months, so a book trading at HALF its expected
# rate sits comfortably inside and passes. The band only excludes half-rate once
# expected/2 < expected - 1.96*sqrt(expected), i.e. above ~15 trades, which is
# 5.4 months. Reporting "pass" at three months would have been a check that
# could not fail -- the exact defect this file exists to avoid. Before six
# months only a near-total stall shows up, and the fills check catches that.
MIN_MONTHS_RATE = 6
MIN_CLOSED_HOLD = 10
STALE_PENDING_SESSIONS = 3


def _poisson_ok(observed, expected):
    """-> True if `observed` sits inside a 95% interval around `expected`.

    Normal approximation on sqrt(expected), floored so a tiny expectation does
    not produce a zero-width interval that fails on arithmetic rather than on
    evidence.
    """
    half = 1.96 * max(expected, 1.0) ** 0.5
    return expected - half <= observed <= expected + half


def forward_rows(conn, bucket, since=None):
    """-> the rows of one bucket ENTERED on or after its own start date."""
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM pos WHERE bucket=? AND status IN "
        "('pending','open','closed')", (bucket,))]
    conn.row_factory = None
    floor = str(since or FORWARD_FROM)
    keep = []
    for r in rows:
        stamp = r.get("entry_day") or r.get("queued_on")
        if stamp and str(stamp) >= floor:
            keep.append(r)
    return keep


def check(conn=None, today=None):
    """-> {bucket: [(name, verdict, detail)]}. verdict in pass/FAIL/too early."""
    c = conn or positions.db()
    today = today or _dt.date.today()
    out = {}
    for bucket, exp in EXPECTED.items():
        # Each book is measured from ITS OWN first session. FORWARD_FROM is the
        # default because main and pooled began together; a book that started
        # later says so and is not charged for the weeks before it existed.
        start = _dt.date.fromisoformat(exp.get("since") or FORWARD_FROM)
        days_live = max((today - start).days, 0)
        months = days_live / 30.44
        rows = forward_rows(c, bucket, since=start)
        closed = [r for r in rows if r["status"] == "closed" and r["entry_px"]]
        pending = [r for r in rows if r["status"] == "pending"]
        res = []

        # -- fills: always checkable, and the fastest thing that can break
        stale = [r for r in pending
                 if r["queued_on"] and (today - _dt.date.fromisoformat(
                     str(r["queued_on"]))).days > STALE_PENDING_SESSIONS + 2]
        res.append(("fills", "FAIL" if stale else "pass",
                    f"{len(stale)} order(s) pending over "
                    f"{STALE_PENDING_SESSIONS} sessions"
                    if stale else f"{len(pending)} pending, none stale"))

        # -- trade rate
        if exp["rate_mo"] is None:
            res.append(("trade rate", "not registered",
                        f"{len(rows)} entered; no frozen rate for this book"))
            expect_n = None
        elif True:
            expect_n = exp["rate_mo"] * months
        if expect_n is None:
            pass
        elif months < MIN_MONTHS_RATE:
            res.append(("trade rate", "too early",
                        f"{len(rows)} entered in {months:.1f} months; "
                        f"needs {MIN_MONTHS_RATE}"))
        else:
            ok = _poisson_ok(len(rows), expect_n)
            res.append(("trade rate", "pass" if ok else "FAIL",
                        f"{len(rows)} entered against {expect_n:.1f} expected "
                        f"in {months:.1f} months"))

        # -- occupancy
        live_n = len([r for r in rows if r["status"] == "open"])
        if exp["occ"] is None:
            res.append(("occupancy", "not registered",
                        f"{live_n} held; no frozen occupancy for this book"))
        elif days_live < 28:
            res.append(("occupancy", "too early",
                        f"{live_n} held; needs 4 weeks"))
        else:
            ok = abs(live_n - exp["occ"]) <= 1.0
            res.append(("occupancy", "pass" if ok else "FAIL",
                        f"{live_n} held against {exp['occ']:.2f} expected"))

        # -- hold length
        holds = [positions.bars_held(None, r["entry_day"], r["exit_day"])
                 for r in closed if r["exit_day"]]
        holds = [h for h in holds if h]
        if exp["hold"] is None:
            res.append(("hold length", "not registered",
                        "this book exits on a trend break, not a day count"))
        elif len(closed) < MIN_CLOSED_HOLD:
            res.append(("hold length", "too early",
                        f"{len(closed)} closed; needs {MIN_CLOSED_HOLD}"))
        else:
            m = statistics.fmean(holds) if holds else 0
            res.append(("hold length", "pass" if 4 <= m <= 10 else "FAIL",
                        f"mean {m:.1f} sessions against {exp['hold']:.1f}"))

        # -- the edge. Reported always, judged only at MIN_N_EDGE.
        rets = [(r["exit_px"] / r["entry_px"] - 1) * 100
                for r in closed if r["exit_px"] and r["entry_px"]]
        if len(rets) >= 2:
            m = statistics.fmean(rets)
            se = statistics.stdev(rets) / len(rets) ** 0.5
            detail = (f"{m:+.2f}% +/- {se:.2f} on n={len(rets)} against "
                      f"{exp['per_trade']:+.2f}% expected")
        else:
            detail = f"n={len(rets)}"
        res.append(("per-trade edge",
                    "too early" if len(rets) < MIN_N_EDGE else "judge now",
                    detail + (f" -- no verdict before n={MIN_N_EDGE}"
                              if len(rets) < MIN_N_EDGE else "")))
        out[bucket] = res
    return out


def report(res):
    print(f"batch {BATCH} | forward from {FORWARD_FROM}")
    print("The backtest cannot be confirmed profitable here for ~5.7 years, and "
          "main vs pooled\nnever separates. These are the STRUCTURAL checks.\n")
    bad = 0
    for bucket, rows in res.items():
        exp = EXPECTED[bucket]
        # A field this book never froze prints as "--", not as a number and
        # not as a crash. etf_trend registered CAGR, drawdown, n and the
        # per-trade edge and nothing else (L70).
        def _n(v, fmt):
            return format(v, fmt) if v is not None else "--"
        print(f"{bucket}  (expects {_n(exp['rate_mo'], '.2f')} trades/mo, "
              f"{_n(exp['per_trade'], '+.2f')}% per trade, "
              f"{_n(exp['occ'], '.2f')} held)")
        for name, verdict, detail in rows:
            # A dict lookup, not .get(): an unknown verdict must raise here
            # rather than print blank and be read as "fine". "n/a" is the
            # gate this book froze no number for, and it is deliberately not
            # spelled like a pass.
            mark = {"pass": "ok  ", "FAIL": "FAIL", "too early": "--  ",
                    "not registered": "n/a ", "judge now": "JUDGE"}[verdict]
            print(f"   {mark} {name:<16} {detail}")
            bad += verdict == "FAIL"
        print()
    print("FAIL means the backtest and reality disagree about something "
          "structural.\nIt does not mean stop; it means explain it before "
          "quoting a backtested number again."
          if not bad else
          f"{bad} structural check(s) FAILING -- explain before quoting any "
          f"backtested number.")
    return bad


def _selftest():
    d = _dt.date
    # The Poisson gate accepts the expectation and rejects a GROSS miss. It
    # does NOT reject a halving at small counts, which is why MIN_MONTHS_RATE
    # is 6 -- asserting the real behaviour here rather than the behaviour that
    # would be convenient.
    assert _poisson_ok(9, 8.6), "the expectation itself must pass"
    assert _poisson_ok(3, 8.6), "3 of 8.6 IS inside 95% Poisson; do not pretend"
    assert not _poisson_ok(0, 8.6), "a total stall must fail"
    assert not _poisson_ok(30, 8.6), "triple rate must fail"
    # at six months the band must exclude a halving, which is the whole reason
    # for the delay
    six = 2.86 * 6
    assert not _poisson_ok(six / 2, six), "the 6-month gate cannot see a halving"
    # And the floor behaves: expecting 1, observing 0 is NOT surprising, so it
    # must pass. Asserting otherwise would have made the gate fire on a quiet
    # fortnight, which is how a monitor gets ignored and the real alarm missed.
    assert _poisson_ok(0, 1.0), "expecting 1 and seeing 0 is not evidence"
    assert _poisson_ok(1, 1.0)
    # every bucket that runs forward must have a frozen expectation, or it
    # would be measured against nothing and silently always pass
    for name in positions.BUCKETS:
        assert name in EXPECTED, f"{name} runs forward with no expectation"
    # a check that cannot fail is not a check: with no data every dated gate
    # must read "too early", never "pass"
    import tempfile
    _odb = positions.DB
    with tempfile.TemporaryDirectory() as td:
        positions.DB = f"{td}/f.db"
        try:
            c = positions.db()
            res = check(c, today=d.fromisoformat(FORWARD_FROM))
            for bucket, rows in res.items():
                got = {n: v for n, v, _ in rows}
                # The property is that a gate NEVER reads "pass" on no data.
                # "not registered" is not a pass -- it says out loud that this
                # book froze no number for that gate (etf_trend registered CAGR,
                # drawdown, n and the per-trade edge and nothing else, L70), so
                # it cannot be mistaken for a check that ran and approved.
                for gate in ("trade rate", "occupancy", "hold length"):
                    assert got[gate] in ("too early", "not registered"), (gate, got)
                    assert got[gate] != "pass", (gate, got)
                # ...and a gate the book DID register must still say "too
                # early", or the None path is swallowing real expectations.
                if EXPECTED[bucket]["occ"] is not None:
                    assert got["occupancy"] == "too early", got
                assert got["per-trade edge"] == "too early", got
                assert got["fills"] == "pass", got   # no orders is not a stall
            # report() must survive a book with unregistered fields. It
            # formatted rate_mo and occ unconditionally and raised TypeError on
            # the first bucket that left them None -- invisible here until the
            # selftest exercised the printer as well as the checker.
            report(res)
            # a stale pending order must FAIL the fill check
            c.execute("INSERT INTO pos(symbol,cluster,status,queued_on,qty,bucket)"
                      " VALUES('AAA','micro','pending',?,10,'main')",
                      (FORWARD_FROM,))
            c.commit()
            late = d.fromisoformat(FORWARD_FROM) + _dt.timedelta(days=30)
            got = {n: v for n, v, _ in check(c, today=late)["main"]}
            assert got["fills"] == "FAIL", got
            # ...and a fresh one must not
            got2 = {n: v for n, v, _ in check(c, today=d.fromisoformat(FORWARD_FROM))["main"]}
            assert got2["fills"] == "pass", got2
            c.close()
        finally:
            positions.DB = _odb
    # rows entered before the start must be excluded from the forward count
    assert FORWARD_FROM > "2026-08-19", "the four pre-existing mains would count"
    print("forward_test selftest ok")


if __name__ == "__main__":
    if "--selftest" in _sys.argv:
        _selftest()
    else:
        _sys.exit(1 if report(check()) else 0)
