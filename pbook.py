#!/usr/bin/env python3
"""The Rs 5,00,000 cluster book -- executed and tracked SEPARATELY.

Kept apart from the generated-spec book on purpose. Merging them would make it
impossible to say which approach worked, and they carry different risk rules:
0.5% risk per trade in one and 2% in the other cannot share a heat budget
without one silently constraining the other.

Rules (operator's design, one parameter changed on evidence):
  entry     next session's OPEN after selection
  stop      10% below entry, fixed        (3% measured -0.6%/trade; see L-notes)
  target    20% above entry
  time exit 15 trading days
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

CAPITAL = 300_000    # must match portfolio.CAPITAL
STOP_PCT, TARGET_PCT, HOLD_DAYS = 10.0, 20.0, 15
# Same charge model as the simulation. A paper book that costs differently
# from the backtest cannot validate it -- any divergence would be unreadable.
COSTS = __import__("engine").Costs()


def db():
    c = sqlite3.connect(DB)
    c.executescript("""
    CREATE TABLE IF NOT EXISTS pos(
      id INTEGER PRIMARY KEY, symbol TEXT, cluster TEXT, status TEXT,
      queued_on TEXT, entry_day TEXT, entry_px REAL, qty INTEGER,
      stop REAL, target REAL, exit_day TEXT, exit_px REAL,
      exit_reason TEXT, net REAL, features TEXT);
    CREATE INDEX IF NOT EXISTS ix_pos_status ON pos(status);
    """)
    c.commit()
    return c


def queue(rows, day, conn=None):
    """Queue today's book for entry at the NEXT session's open."""
    c = conn or db()
    held = {r[0] for r in c.execute(
        "SELECT symbol FROM pos WHERE status IN ('pending','open')")}
    n = 0
    for r in rows:
        if r["symbol"] in held:
            continue
        c.execute("INSERT INTO pos(symbol,cluster,status,queued_on,qty,stop,target)"
                  " VALUES(?,?,'pending',?,?,?,?)",
                  (r["symbol"], r["cluster"], str(day), r["qty"], r["stop"], r["target"]))
        n += 1
    c.commit()
    return n


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
        c.execute("UPDATE pos SET status='open', entry_day=?, entry_px=?, stop=?,"
                  " target=?, features=? WHERE id=?",
                  (str(day), px, px * (1 - STOP_PCT / 100), px * (1 + TARGET_PCT / 100),
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
                                  "source": "portfolio"}])
        except Exception:
            pass
    c.commit()
    c.row_factory = None
    return filled, closed


def summary(conn=None):
    c = conn or db()
    c.row_factory = sqlite3.Row
    rows = [dict(r) for r in c.execute("SELECT * FROM pos").fetchall()]
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

    with tempfile.TemporaryDirectory() as td:
        c = sqlite3.connect(Path(td) / "t.db")
        c.executescript(open(__file__).read().split('"""')[2]
                        .split("CREATE TABLE")[0] if False else """
        CREATE TABLE pos(id INTEGER PRIMARY KEY, symbol TEXT, cluster TEXT, status TEXT,
          queued_on TEXT, entry_day TEXT, entry_px REAL, qty INTEGER, stop REAL,
          target REAL, exit_day TEXT, exit_px REAL, exit_reason TEXT, net REAL,
          features TEXT);""")
        corpus = {"T": s}
        assert queue([{"symbol": "T", "cluster": "small", "qty": 100,
                       "stop": 90.0, "target": 120.0}], days[200], c) == 1
        # queueing the same symbol twice must not double it
        assert queue([{"symbol": "T", "cluster": "small", "qty": 100,
                       "stop": 90.0, "target": 120.0}], days[200], c) == 0

        filled, closed = step(corpus, days[201], c)
        assert filled and not closed, (filled, closed)
        row = c.execute("SELECT entry_px, stop, target FROM pos").fetchone()
        assert abs(row[0] - 100.0) < 1e-9
        # stop/target rebuilt from the FILL, not the queued reference
        assert abs(row[1] - 90.0) < 1e-9 and abs(row[2] - 120.0) < 1e-9, row

        f2, c2 = step(corpus, days[211], c)
        assert c2 and c2[0][1] == "target", c2
        assert c2[0][2] > 0, "target exit must be profitable"
        s2 = summary(c)
        assert s2["closed"] == 1 and s2["realised"] > 0, s2
    print("pbook selftest ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    else:
        print(json.dumps({k: v for k, v in summary().items() if k != "rows"}, indent=1))
