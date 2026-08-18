#!/usr/bin/env python3
"""Daily driver for the Rs 3,00,000 bucket.

Re-selects only when the bucket has room. Re-running the screen every session
and queueing the new top-5 would churn it daily and never let a thesis play out
-- the holding period IS the strategy.
"""

# First: puts core/, bucket/, research/ and ops/ on sys.path.
import paths  # noqa: F401
import sys
from datetime import date

import clusters
import features
import positions
import selection


def fill_live_main():
    """Morning run: fill pending orders at today's actual open."""
    from datetime import date as _date
    corpus = features.load_corpus()
    conn = positions.db()
    today = _date.today()
    filled, why = positions.fill_live(today, conn)
    if not filled:
        print(f"{today}: nothing filled — {why}")
        # Exit non-zero when the fill was POSTPONED rather than completed, so
        # the agent does not tick it off and skip the rest of the day. "No
        # authoritative quote yet" at 09:20 becomes a fill at 10:20; recorded
        # as success it becomes no fill at all.
        return 0 if why == "nothing pending" else 1
    for sym, px in filled:
        print(f"{today}: FILLED {sym} at {px:,.2f} (live)")
    
    try:
        import tg
        tg.notify(f"Filled at the open — {today}",
                  [f"{s} at {p:,.2f}" for s, p in filled])
    except Exception as e:
        print(f"  telegram push failed: {type(e).__name__}")


def main(day=None):
    corpus = features.load_corpus()
    days = sorted({d for s in corpus.values() for d in s.days})
    day = day or days[-1]
    if day not in days:
        print(f"{day}: not a trading day")
        return

    conn = positions.db()
    # Correct any morning fill against the official open before stepping.
    # reconcile and step are bucket-agnostic: they walk positions by status, so
    # every bucket advances in one pass.
    for sym, was, now in positions.reconcile(corpus, day, conn):
        print(f"  reconciled {sym}: live {was:,.2f} -> official {now:,.2f}")
    filled, closed = positions.step(corpus, day, conn)
    s = positions.summary(conn)

    rows = selection.build(corpus, day, capital=s["equity"])
    room = selection.MAX_POSITIONS - (s["open"] + s["pending"])
    queued = positions.queue(selection.allocate(rows)[:room], day, conn) if room > 0 else 0

    print(f"{day}  equity Rs {s['equity']:,.0f}  realised Rs {s['realised']:+,.0f}")
    print(f"  filled {len(filled)}  closed {len(closed)}  queued {queued}")
    print(f"  bucket: open {s['open']}  pending {s['pending']}  "
          f"closed-total {s['closed']}")
    allb = positions.summary(conn, which=None)
    if allb["open"] != s["open"] or allb["closed"] != s["closed"]:
        print(f"  incl. retired buckets: open {allb['open']}  "
              f"closed-total {allb['closed']}")

    # Record the findings after every session that closed something, so the
    # per-stock and per-cluster picture accumulates instead of being recomputed
    # from scratch and forgotten.
    if closed:
        try:
            import analysis
            done = [{"sym": r["symbol"], "clu": r["cluster"],
                     "ret": (r["exit_px"] / r["entry_px"] - 1) * 100}
                    for r in positions.summary(conn)["rows"]
                    if r["status"] == "closed" and r["entry_px"]]
            analysis.record(f"bucket through {day}", done,
                            extra={"realised": s["realised"], "day": str(day)})
        except Exception as e:
            print(f"  findings record failed: {type(e).__name__}")

    if filled or closed:
        try:
            import tg
            lines = [f"filled {sym} @ {px:.2f}" for sym, px in filled[:4]]
            lines += [f"closed {sym} {why} Rs {net:+,.0f}" for sym, why, net in closed[:4]]
            lines.append(f"equity Rs {s['equity']:,.0f} ({s['realised']:+,.0f} realised)")
            tg.notify(f"Book update — {day}", lines)
        except Exception as e:
            print(f"  telegram push failed: {type(e).__name__}")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        print("pbook_run selftest ok (logic covered by positions.py)")
    elif "--fill-live" in sys.argv:
        sys.exit(fill_live_main() or 0)
    else:
        d = date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else None
        main(d)
