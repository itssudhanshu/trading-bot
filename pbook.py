#!/usr/bin/env python3
"""The Rs 3,00,000 cluster book -- executed and tracked SEPARATELY.

Kept apart from the generated-spec book on purpose. Merging them would make it
impossible to say which approach worked, and they carry different risk rules:
0.5% risk per trade in one and 2% in the other cannot share a heat budget
without one silently constraining the other.

Rules (operator's design, one parameter changed on evidence):
  entry     next session's OPEN after selection
  stop      10% below entry, fixed        (3% measured -0.6%/trade; see L-notes)
  target    20% above entry
  time exit 10 trading days
  trail     none -- every trailing variant tested lowered expectancy

Every closed trade feeds learning.py tagged `source: portfolio`, so this book's
results stay distinguishable from the historical seed and the spec book.
"""
import json
import sqlite3
import sys
from datetime import date
from pathlib import Path

import features

ROOT = Path(__file__).resolve().parent
DB = ROOT / "data" / "pbook.db"

# The exit rules are READ from portfolio, never restated. A second copy of
# these constants would let the live book and the simulation that validates it
# drift apart silently -- and the whole point of matching cost models and exit
# rules is that a divergence between the two is readable.
import portfolio

CAPITAL = portfolio.CAPITAL
STOP_PCT, TARGET_PCT, HOLD_DAYS = (portfolio.STOP_PCT, portfolio.TARGET_PCT,
                                   portfolio.HOLD_DAYS)
# Same charge model as the simulation. A paper book that costs differently
# from the backtest cannot validate it -- any divergence would be unreadable.
COSTS = __import__("engine").Costs()


# Parallel paper books. Trade count is the binding constraint on this project
# -- one book produces ~71 trades a year, and 105 are needed before a 3%/trade
# edge is resolvable at all. More books is the only lever that moves that.
#
# THE CONSTRAINT THAT MAKES THIS HONEST: books 1-3 run the SAME rules at
# different RANK DEPTHS, so their positions are disjoint by construction and
# their trades pool as near-independent samples. They multiply evidence for the
# question that matters -- does the score rank? -- without creating a choice.
#
# What they are NOT is a parameter search. Comparing two parameter settings on
# RETURN needs 238 trades per arm (3.4 years) for the largest gap ever measured
# here, 40 years for the ladder, and 162,554 for the hold. Running variants
# forward and adopting the leader would contaminate the one evidence stream a
# search cannot reach (L47, and PBO 0.929 in L41). It is forbidden here, not
# discouraged.
#
# `tight` is the single exception and its endpoint is deliberately different.
# It holds the SAME names as main with a 5% stop, so the comparison is PAIRED
# on identical price paths, and what it measures is the stop-hit RATE -- a
# proportion, resolvable in ~62 trades, not a mean needing 238. Its job is to
# falsify the simulator's fill and gap model, which predicts 62% of positions
# stop out at 5% against 37% at 10%. If forward reality disagrees, the model is
# wrong and every backtest built on it moves. It may never be promoted on P&L.
BOOKS = {
    "main":  dict(offset=0, stop_pct=None, pool=True,
                  note="the record; STATE.md, overview.py and audit key off it"),
    # NOT "rank1/2/3". A book called `rank2` displayed beside a name sitting at
    # rank 5 put two meanings of "rank" in one line saying different numbers.
    # A cohort is a slice of the ranking; a rank is a position in it. This
    # project has a standing rule about exactly this kind of collision.
    "cohort1": dict(offset=1, stop_pct=None, pool=True, note=""),
    "cohort2": dict(offset=2, stop_pct=None, pool=True, note=""),
    "cohort3": dict(offset=3, stop_pct=None, pool=True, note=""),
}
# There is deliberately NO variant book. A `tight` book running a 5% stop was
# built and removed: to be a paired test it must enter the same name at the
# same price on the same day as main, and a separately-queued book cannot --
# it queues when IT has room, so it entered a position main had already held
# for a session and was 3.2% into. That is chasing, not pairing, and it also
# put a duplicate order in /next_orders for a name already live.
#
# `shadow_stop` answers the same question exactly and without a second order:
# a tighter stop either was or was not touched between the real entry and the
# real exit, and main's own bars settle it. Exact beats approximately-paired.
MAIN = "main"


def slice_of(name):
    """-> 'ranks 7-9 micro, 5-6 small' for a book, DERIVED not restated.

    Written out by hand this went stale the moment the mix changed -- the same
    way a comment describing a 2/2/1 bucket survived a minute past the design
    that made it true.
    """
    import portfolio
    off = (BOOKS.get(name) or BOOKS[MAIN])["offset"]
    return ", ".join(f"ranks {off * k + 1}-{off * k + k} {c}"
                     for c, k in portfolio.TAKE_PER_CLUSTER.items())


def role_of(name):
    """-> the slice, plus any standing note about the book."""
    n = (BOOKS.get(name) or BOOKS[MAIN]).get("note")
    return slice_of(name) + (f" — {n}" if n else "")


def book_cfg(name):
    """-> the book's config, with None meaning 'the live rule'."""
    b = dict(BOOKS.get(name) or BOOKS[MAIN])
    b["stop_pct"] = STOP_PCT if b["stop_pct"] is None else b["stop_pct"]
    return b


def db():
    c = sqlite3.connect(DB)
    c.executescript("""
    CREATE TABLE IF NOT EXISTS pos(
      id INTEGER PRIMARY KEY, symbol TEXT, cluster TEXT, status TEXT,
      queued_on TEXT, entry_day TEXT, entry_px REAL, qty INTEGER,
      stop REAL, target REAL, exit_day TEXT, exit_px REAL,
      exit_reason TEXT, net REAL, features TEXT, fill_source TEXT);
    CREATE INDEX IF NOT EXISTS ix_pos_status ON pos(status);
    """)
    # Existing rows predate the parallel books and are the record: they become
    # 'main'. Done as a migration rather than a fresh table so the one live
    # position keeps its id and history.
    if "book" not in {r[1] for r in c.execute("PRAGMA table_info(pos)")}:
        c.execute(f"ALTER TABLE pos ADD COLUMN book TEXT DEFAULT '{MAIN}'")
        c.execute(f"UPDATE pos SET book='{MAIN}' WHERE book IS NULL")
    c.commit()
    return c


def queue(rows, day, conn=None, book=MAIN):
    """Queue a book's picks for entry at the NEXT session's open.

    Dedup is PER BOOK, not global. `tight` is meant to hold the same names as
    `main` -- that pairing is what makes its stop-hit comparison run on
    identical price paths -- so a global "already held" check would silently
    empty it and the test would look like it ran.
    """
    c = conn or db()
    held = {r[0] for r in c.execute(
        "SELECT symbol FROM pos WHERE status IN ('pending','open') AND book=?",
        (book,))}
    cfg = book_cfg(book)
    n = 0
    for r in rows:
        if r["symbol"] in held:
            continue
        # A book with its own stop re-derives stop and target from the
        # reference close rather than inheriting main's levels.
        stop, target = r["stop"], r["target"]
        if cfg["stop_pct"] != STOP_PCT:
            ref = r["ref_close"]
            stop = round(ref * (1 - cfg["stop_pct"] / 100), 2)
        c.execute("INSERT INTO pos(symbol,cluster,status,queued_on,qty,stop,target,book)"
                  " VALUES(?,?,'pending',?,?,?,?,?)",
                  (r["symbol"], r["cluster"], str(day), r["qty"], stop, target, book))
        n += 1
    c.commit()
    return n


def fill_live(day, conn=None):
    """Fill pending orders at TODAY'S opening price, fetched live.

    The evening run fills from the bhavcopy and is the record of truth. This
    runs in the morning so the book reflects reality within minutes of the open
    instead of nine hours later. Both fill at the same price -- the day's open
    -- so the evening run reconciles rather than re-fills.

    -> (filled, skipped_reason). Fills nothing if quotes are unavailable.
    """
    import quotes
    c = conn or db()
    c.row_factory = sqlite3.Row
    pend = [dict(r) for r in
            c.execute("SELECT * FROM pos WHERE status='pending'").fetchall()]
    c.row_factory = None
    if not pend:
        return [], "nothing pending"
    # Same guard as the evening path: a signal built from a close cannot be
    # filled at that same session's open.
    due = [p for p in pend if not p["queued_on"] or str(day) > str(p["queued_on"])]
    if not due:
        return [], "orders were queued today; they enter at the NEXT open"
    q = quotes.live([p["symbol"] for p in due])
    if not q:
        return [], "no live quote source"
    # A fill sets the entry price, the stop and the target for the life of the
    # trade. It may only come from a source whose fields are documented, never
    # from one whose meaning was inferred from where it sat on a web page.
    if not quotes.authoritative():
        return [], (f"quote source '{getattr(quotes.live, 'source', '?')}' is "
                    f"display-only; fills need an authoritative source")
    filled = []
    for p in due:
        px = (q.get(p["symbol"]) or {}).get("open")
        if not px:
            continue
        sp = book_cfg(p["book"])["stop_pct"]
        c.execute("UPDATE pos SET status='open', entry_day=?, entry_px=?, stop=?,"
                  " target=?, fill_source='live' WHERE id=?",
                  (str(day), px, px * (1 - sp / 100),
                   px * (1 + TARGET_PCT / 100), p["id"]))
        filled.append((p["symbol"], px))
    c.commit()
    return filled, ""


def reconcile(corpus, day, conn=None):
    """Check live fills against the official open once the bhavcopy lands.

    A live quote is a convenience; the bhavcopy is the record. If they differ
    the book is corrected and the difference reported, because a fill price
    that quietly drifts is a P&L error that compounds.
    """
    c = conn or db()
    c.row_factory = sqlite3.Row
    rows = [dict(r) for r in c.execute(
        "SELECT * FROM pos WHERE fill_source='live' AND entry_day=?",
        (str(day),)).fetchall()]
    c.row_factory = None
    out = []
    for p in rows:
        s = corpus.get(p["symbol"])
        i = s.index_of(day) if s else None
        if i is None:
            continue
        official = s.open[i]
        if official and abs(official - p["entry_px"]) > 0.005:
            sp = book_cfg(p["book"])["stop_pct"]
            c.execute("UPDATE pos SET entry_px=?, stop=?, target=?,"
                      " fill_source='reconciled' WHERE id=?",
                      (official, official * (1 - sp / 100),
                       official * (1 + TARGET_PCT / 100), p["id"]))
            out.append((p["symbol"], p["entry_px"], official))
        else:
            c.execute("UPDATE pos SET fill_source='confirmed' WHERE id=?", (p["id"],))
    c.commit()
    return out


def step(corpus, day, conn=None):
    """One session: fill pending at the open, then check exits. -> (filled, closed)."""
    import learning
    c = conn or db()
    c.row_factory = sqlite3.Row
    filled, closed = [], []

    for p in c.execute("SELECT * FROM pos WHERE status='pending'").fetchall():
        s = corpus.get(p["symbol"])
        i = s.index_of(day) if s else None
        if i is None:
            continue
        # A signal built from a day's CLOSE cannot be filled at that same day's
        # OPEN. Without this, a step re-run on the queue date (which happens
        # whenever the next bhavcopy is late and days[-1] has not advanced)
        # buys in the past at a price the signal already knew.
        if p["queued_on"] and str(day) <= str(p["queued_on"]):
            continue
        px = s.open[i]
        if not px:
            continue
        # Stop and target are recomputed from the ACTUAL fill, not from the
        # reference close used when queueing -- an overnight gap moves both.
        sp = book_cfg(p["book"])["stop_pct"]
        c.execute("UPDATE pos SET status='open', entry_day=?, entry_px=?, stop=?,"
                  " target=?, features=? WHERE id=?",
                  (str(day), px, px * (1 - sp / 100), px * (1 + TARGET_PCT / 100),
                   json.dumps(learning.entry_features(s, i)), p["id"]))
        filled.append((p["symbol"], px))

    for p in c.execute("SELECT * FROM pos WHERE status='open'").fetchall():
        s = corpus.get(p["symbol"])
        i = s.index_of(day) if s else None
        if i is None or str(day) <= (p["entry_day"] or ""):
            continue
        held = len([d for d in s.days if p["entry_day"] < str(d) <= str(day)])
        px, why = None, None
        if s.low[i] <= p["stop"]:
            px, why = min(p["stop"], s.open[i]), "stop"      # gap-through fills worse
        elif s.high[i] >= p["target"]:
            px, why = max(p["target"], s.open[i]), "target"  # gap-through fills better
        elif held >= HOLD_DAYS:
            px, why = s.close[i], "time"
        if px is None:
            continue
        buy_val, sell_val = p["entry_px"] * p["qty"], px * p["qty"]
        cost = COSTS.charge(buy_val, "BUY") + COSTS.charge(sell_val, "SELL")
        net = (sell_val - buy_val) - cost
        c.execute("UPDATE pos SET status='closed', exit_day=?, exit_px=?,"
                  " exit_reason=?, net=? WHERE id=?", (str(day), px, why, net, p["id"]))
        closed.append((p["symbol"], why, net))
        try:
            f = json.loads(p["features"] or "{}")
            if f:
                learning.record([{**f, "ret": (px / p["entry_px"] - 1) * 100,
                                  "net": net, "exit": why, "symbol": p["symbol"],
                                  "cluster": p["cluster"], "date": str(day),
                                  "book": p["book"], "source": "portfolio"}])
        except Exception:
            pass
    c.commit()
    c.row_factory = None
    return filled, closed


def shadow_stop(corpus, conn=None, pct=5.0, book=MAIN):
    """Would a `pct` stop have been hit on this book's OWN positions?

    The counterfactual is exact rather than approximate: same entry price, same
    bars, so there is nothing to pair up and nothing that can drift. It answers
    a PROPORTION -- how often a tighter stop fires -- which resolves in ~62
    trades, where comparing the two stops on RETURN would need 238 per arm.

    The simulator predicts 62% of positions stop out at 5% against 37% at 10%.
    If forward reality disagrees, the fill and gap model is wrong and every
    backtest resting on it moves. This can never say which stop is BETTER: once
    a tighter stop fires the paths diverge, and that divergence is not modelled
    here on purpose.
    """
    if pct <= 0:
        # A non-positive percentage puts the level at or above the entry, where
        # every bar "hits" it and the answer is meaninglessly True. Refuse it
        # rather than return a number that looks like a measurement.
        raise ValueError(f"shadow stop pct must be > 0, got {pct}")
    out = []
    for r in summary(conn, book=book)["rows"]:
        if not r["entry_px"] or not r["entry_day"]:
            continue
        s = corpus.get(r["symbol"])
        if s is None:
            continue
        lvl = r["entry_px"] * (1 - pct / 100)
        real = r["stop"]
        hit = day = None
        for i, d in enumerate(s.days):
            if str(d) <= str(r["entry_day"]):
                continue
            if r["exit_day"] and str(d) > str(r["exit_day"]):
                break
            if s.low[i] <= lvl:
                hit, day = True, d
                break
        out.append({"symbol": r["symbol"], "book": r["book"],
                    "entry": r["entry_px"], "level": lvl, "real_stop": real,
                    "shadow_hit": bool(hit), "shadow_day": day,
                    "real_exit": r["exit_reason"], "status": r["status"]})
    return out


def summary(conn=None, book=MAIN):
    """-> the book's state. `book=None` aggregates every book.

    Defaults to `main` because that is the record: overview.py, STATE.md and
    the audit all describe one book, and silently folding four research books
    into those numbers would overstate the forward evidence fourfold.
    """
    c = conn or db()
    c.row_factory = sqlite3.Row
    q = "SELECT * FROM pos" + ("" if book is None else " WHERE book=?")
    rows = [dict(r) for r in c.execute(q, () if book is None else (book,)).fetchall()]
    c.row_factory = None
    closed = [r for r in rows if r["status"] == "closed"]
    realised = sum(r["net"] or 0 for r in closed)
    return {"pending": sum(1 for r in rows if r["status"] == "pending"),
            "open": sum(1 for r in rows if r["status"] == "open"),
            "closed": len(closed), "realised": realised,
            "equity": CAPITAL + realised, "rows": rows}


def _selftest():
    import tempfile, learning
    _orig_ledger = learning.LEDGER
    learning.LEDGER = __import__("pathlib").Path(tempfile.gettempdir()) / "pbook_selftest_ledger.jsonl"
    try:
        return __selftest_body()
    finally:
        learning.LEDGER = _orig_ledger


def __selftest_body():
    import tempfile
    from datetime import timedelta
    d0 = date(2024, 1, 1)
    days = [d0 + timedelta(days=k) for k in range(260)]
    s = features.Series("T", list(days))
    for k in range(260):
        px = 100.0
        s.close.append(px); s.high.append(px); s.low.append(px); s.open.append(px)
        s.volume.append(1000); s.turnover.append(1e9)
        s.deliv_pct.append(50.0); s.surveillance_known.append(True)
    # make it hit target on day 210
    for k in range(210, 260):
        s.high[k] = 130.0; s.close[k] = 128.0

    global DB
    _odb = DB
    with tempfile.TemporaryDirectory() as td:
        # Build the table through db(), not by restating the schema here. The
        # hand-written copy silently went stale the moment a column was added,
        # and a selftest that tests a different table than production is worse
        # than no selftest.
        DB = Path(td) / "t.db"
        try:
            c = db()
            corpus = {"T": s}
            row_in = {"symbol": "T", "cluster": "small", "qty": 100,
                      "stop": 90.0, "target": 120.0, "ref_close": 100.0}
            assert queue([row_in], days[200], c) == 1
            # queueing the same symbol twice must not double it
            assert queue([row_in], days[200], c) == 0
            # ...but a DIFFERENT book must still take it. Dedup is per book,
            # so a rank cohort is never blocked by what another book holds.
            assert queue([row_in], days[200], c, book="cohort1") == 1

            filled, closed = step(corpus, days[201], c)
            assert len(filled) == 2 and not closed, (filled, closed)
            got = dict(c.execute(
                "SELECT book, stop FROM pos WHERE status='open'").fetchall())
            assert abs(got["main"] - 90.0) < 1e-9, got     # 10% below the fill
            assert abs(got["cohort1"] - 90.0) < 1e-9, got

            # The shadow stop replaces the variant book: exact, same entry,
            # same bars. A 5% stop sits at 95 and this path never trades below
            # 100 before the target, so it must NOT report a hit.
            sh = {x["symbol"]: x for x in shadow_stop(corpus, c, pct=5.0)}
            assert sh["T"]["shadow_hit"] is False, sh
            assert abs(sh["T"]["level"] - 95.0) < 1e-9, sh
            # a level at or above the entry is not a stop; it must be refused
            # rather than answered, since every bar would trivially "hit" it
            for bad in (0.0, -5.0):
                try:
                    shadow_stop(corpus, c, pct=bad)
                    raise AssertionError(f"pct={bad} was accepted")
                except ValueError:
                    pass

            f2, c2 = step(corpus, days[211], c)
            assert c2 and all(x[1] == "target" for x in c2), c2
            assert all(x[2] > 0 for x in c2), "target exit must be profitable"
            s2 = summary(c)                       # defaults to main only
            assert s2["closed"] == 1 and s2["realised"] > 0, s2
            assert summary(c, book=None)["closed"] == 2, "book=None must pool"
            assert summary(c, book="cohort1")["closed"] == 1
        finally:
            DB = _odb
    print("pbook selftest ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    else:
        print(json.dumps({k: v for k, v in summary().items() if k != "rows"}, indent=1))
