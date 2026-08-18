#!/usr/bin/env python3
"""Daily driver for the Rs 3,00,000 cluster book.

Re-selects only when the book has room. Re-running the screen every session and
queueing the new top-5 would churn the book daily and never let a 15-day thesis
play out -- the holding period IS the strategy.
"""
import sys
from datetime import date

import clusters
import features
import pbook
import portfolio


def fill_live_main():
    """Morning run: fill pending orders at today's actual open."""
    from datetime import date as _date
    corpus = features.load_corpus()
    conn = pbook.db()
    today = _date.today()
    filled, why = pbook.fill_live(today, conn)
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

    conn = pbook.db()
    # Correct any morning fill against the official open before stepping.
    # reconcile and step are book-agnostic: they walk positions by status, so
    # every book advances in one pass.
    for sym, was, now in pbook.reconcile(corpus, day, conn):
        print(f"  reconciled {sym}: live {was:,.2f} -> official {now:,.2f}")
    filled, closed = pbook.step(corpus, day, conn)
    s = pbook.summary(conn)

    # Rank ONCE, allocate per book. build() is the expensive call and every
    # book reads the same ranking -- the books differ only in how far down it
    # they reach, which is exactly what makes their positions disjoint.
    rows = portfolio.build(corpus, day, capital=s["equity"])
    queued = {}
    for name, cfg in pbook.PORTFOLIOS.items():
        bs = pbook.summary(conn, which=name)
        room = portfolio.MAX_POSITIONS - (bs["open"] + bs["pending"])
        if room <= 0:
            continue
        picks = portfolio.allocate(rows, offset=cfg["offset"])
        n = pbook.queue(picks[:room], day, conn, which=name)
        if n:
            queued[name] = n

    print(f"{day}  equity Rs {s['equity']:,.0f}  realised Rs {s['realised']:+,.0f}")
    print(f"  filled {len(filled)}  closed {len(closed)}  "
          f"queued {sum(queued.values())} {queued or ''}")
    print(f"  main: open {s['open']}  pending {s['pending']}  "
          f"closed-total {s['closed']}")
    allb = pbook.summary(conn, which=None)
    print(f"  all books: open {allb['open']}  pending {allb['pending']}  "
          f"closed-total {allb['closed']}  "
          f"({len(pbook.PORTFOLIOS)} books, ~{71 * len(pbook.PORTFOLIOS):.0f} trades/yr)")

    # Record the findings after every session that closed something, so the
    # per-stock and per-cluster picture accumulates instead of being recomputed
    # from scratch and forgotten.
    if closed:
        try:
            import analysis
            done = [{"sym": r["symbol"], "clu": r["cluster"],
                     "ret": (r["exit_px"] / r["entry_px"] - 1) * 100}
                    for r in pbook.summary(conn)["rows"]
                    if r["status"] == "closed" and r["entry_px"]]
            analysis.record(f"book through {day}", done,
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
        print("pbook_run selftest ok (logic covered by pbook.py)")
    elif "--fill-live" in sys.argv:
        sys.exit(fill_live_main() or 0)
    else:
        d = date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else None
        main(d)
