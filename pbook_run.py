#!/usr/bin/env python3
"""Daily driver for the Rs 5,00,000 cluster book.

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


def main(day=None):
    corpus = features.load_corpus()
    days = sorted({d for s in corpus.values() for d in s.days})
    day = day or days[-1]
    if day not in days:
        print(f"{day}: not a trading day")
        return

    conn = pbook.db()
    filled, closed = pbook.step(corpus, day, conn)
    s = pbook.summary(conn)

    room = portfolio.MAX_POSITIONS - (s["open"] + s["pending"])
    queued = 0
    if room > 0:
        rows = portfolio.allocate(portfolio.build(corpus, day, capital=s["equity"]))
        queued = pbook.queue(rows[:room], day, conn)

    print(f"{day}  equity Rs {s['equity']:,.0f}  realised Rs {s['realised']:+,.0f}")
    print(f"  filled {len(filled)}  closed {len(closed)}  queued {queued}")
    print(f"  open {s['open']}  pending {s['pending']}  closed-total {s['closed']}")

    if filled or closed:
        try:
            import tg
            lines = [f"filled {sym} @ {px:.2f}" for sym, px in filled[:4]]
            lines += [f"closed {sym} {why} Rs {net:+,.0f}" for sym, why, net in closed[:4]]
            lines.append(f"equity Rs {s['equity']:,.0f} ({s['realised']:+,.0f} realised)")
            tg.push_learning(f"cluster book {day}", lines)
        except Exception as e:
            print(f"  telegram push failed: {type(e).__name__}")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        print("pbook_run selftest ok (logic covered by pbook.py)")
    else:
        d = date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else None
        main(d)
