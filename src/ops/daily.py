#!/usr/bin/env python3
"""Daily driver for the Rs 3,00,000 bucket.

Re-selects only when the bucket has room. Re-running the screen every session
and queueing the new top-5 would churn it daily and never let a thesis play out
-- the holding period IS the strategy.
"""

# First: finds src/paths.py, which puts every source dir on sys.path.
import sys as _sys, pathlib as _pl
_sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[1]))
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
        # price we can buy at" at 09:20 becomes a fill at 10:20; recorded
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

    # EVERY bucket queues, each against its OWN holdings and its own equity.
    # main is first and its path is unchanged: same capital, same allocate()
    # defaults, same room arithmetic, so the recorded baseline still reproduces.
    queued = {}
    for name, cfg in positions.BUCKETS.items():
        bs = s if name == positions.MAIN else positions.summary(conn, which=name)
        seats = cfg["seats"] or selection.MAX_POSITIONS
        room = seats - (bs["open"] + bs["pending"])
        if room <= 0:
            queued[name] = 0
            continue
        # The ranking rule is set for THIS bucket and restored immediately. A
        # leak would silently re-rank the other bucket, and the two would stop
        # being a comparison at all.
        _was = selection.RANKING
        try:
            selection.RANKING = cfg["ranking"]
            rows = selection.build(corpus, day, capital=bs["equity"])
            picks = (selection.allocate(rows, None, max_pos=seats)
                     if cfg["ranking"] == "pooled" else selection.allocate(rows))
        finally:
            selection.RANKING = _was
        # Pass the WHOLE allocation and let queue() apply the room, because it
        # is the function that knows which names are already live. Slicing here
        # first spent the room on duplicates -- see positions.queue.
        queued[name] = positions.queue(picks, day, conn, which=name, limit=room)
    assert selection.RANKING == "per_cluster", "ranking leaked out of the loop"

    print(f"{day}  equity Rs {s['equity']:,.0f}  realised Rs {s['realised']:+,.0f}")
    print(f"  filled {len(filled)}  closed {len(closed)}  "
          f"queued {sum(queued.values())} ({', '.join(f'{k} {v}' for k, v in queued.items())})")
    for name in positions.BUCKETS:
        b = s if name == positions.MAIN else positions.summary(conn, which=name)
        print(f"  {name}: open {b['open']}  pending {b['pending']}  "
              f"closed-total {b['closed']}  -- {positions.slice_of(name)}")
    allb = positions.summary(conn, which=None)
    known = sum(positions.summary(conn, which=n)["open"]
                for n in positions.BUCKETS)
    if allb["open"] != known:
        print(f"  incl. retired buckets: open {allb['open'] - known} more")

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

    # The tick is the only thing that changes the record, so this is the one
    # place it has to be re-exported. audit.py fails if the two disagree, so a
    # hand-edit that skips this does not pass quietly.
    try:
        positions.export_record(conn)
    except Exception as e:
        print(f"  record export failed: {type(e).__name__}: {e}")

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
