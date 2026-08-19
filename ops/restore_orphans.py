#!/usr/bin/env python3
"""Recover the open positions orphaned when pbook.db became positions.db.

The rename left an EMPTY positions.db. Three open positions and one pending
order stayed behind in the old file, and because the new file had no memory of
them the daily run re-selected names the bucket was already holding:

  YUKEN       was still PENDING, so filling it on 2026-08-19 is the same trade
              the queue had always intended. Nothing to undo.
  HAPPYFORGE  was already OPEN since 2026-08-17 at 2,131.20, and was bought a
              SECOND time on 2026-08-19 at 2,280.00 -- 7% higher, for no reason
              other than the dedup check having lost its memory.
  GMMPFAUDLR  belonged to the retired deeper buckets, so nothing re-selected
  SAHYADRI    them at all and they went missing from the bucket entirely.

So this restores the three open positions into the one bucket, drops the
`third` / `fourth` labels, and retires the duplicate HAPPYFORGE as `void`.

WHY `void` AND NOT `closed`: an order that should never have been placed is not
a trade. Recorded as closed it would contribute a return to the only forward
evidence this project has (`STATE.md`: 0 closed paper trades, "the only stream a
search cannot contaminate"), and a P&L number that no decision produced is
worse than no number. `void` appears in none of the three order views and in
none of summary()'s counts, and it releases the symbol so the real position can
be restored. It is an EDIT, not a delete -- the row stays forever.

Prices are copied from pbook.db rather than typed here; a money path should not
contain a hand-transcribed entry price.

Idempotent: `ux_pos_live` makes a second run a no-op.

    python3 ops/restore_orphans.py --dry-run     # show what would change
    python3 ops/restore_orphans.py
"""

import sys
import pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import paths  # noqa: F401  -- puts the source dirs on sys.path

import sqlite3
from datetime import date

import positions

OLD = paths.DATA / "pbook.db"

# The retired deeper buckets bought ranks the score marks as worse (-0.90% per
# rank step). Their positions keep running to their own exits -- nothing is sold
# to tidy up a naming decision -- but they must stay separable from the score's
# own picks, or the first closed forward trades read as evidence for a selection
# that did not make them. The bucket LABEL goes; the fact does not.
COHORT = "rank-cohort"


def orphaned(old=OLD):
    """-> the open positions still sitting in the pre-rename database."""
    if not old.exists():
        return []
    c = sqlite3.connect(old)
    c.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in c.execute(
            "SELECT * FROM pos WHERE status='open' ORDER BY entry_day, symbol")]
    finally:
        c.close()


def restore(dry=False, old=OLD):
    rows = orphaned(old)
    if not rows:
        print(f"{old.name}: no open positions to recover")
        return 0
    c = positions.db()
    today = date.today()
    voided = added = 0
    for o in rows:
        live = c.execute(
            "SELECT id, status, entry_day, entry_px FROM pos"
            " WHERE symbol=? AND status IN ('pending','open')",
            (o["symbol"],)).fetchone()
        if live:
            lid, lstatus, lday, lpx = live
            # Earlier entry wins: it is the position that actually ran. Decided
            # from the dates rather than a hardcoded row id, so re-running this
            # after any further churn still retires the right one.
            if not lday or str(lday) <= str(o["entry_day"]):
                print(f"  {o['symbol']:12} already live and it is the earlier "
                      f"entry ({lday} @ {lpx if lpx else '-'}) — left alone")
                continue
            print(f"  {o['symbol']:12} VOID id={lid} {lday} @ {lpx:,.2f} "
                  f"(duplicate of {o['entry_day']} @ {o['entry_px']:,.2f})")
            if not dry:
                c.execute(
                    "UPDATE pos SET status='void', exit_day=?, exit_reason=?"
                    " WHERE id=?",
                    (str(today),
                     f"void: duplicate entry in a name already open since "
                     f"{o['entry_day']}; the pbook->positions rename lost the "
                     f"original, so dedup could not see it", lid))
                voided += 1
        origin = None if o["bucket"] == positions.MAIN else COHORT
        print(f"  {o['symbol']:12} RESTORE {o['entry_day']} @ "
              f"{o['entry_px']:,.2f} x{o['qty']}"
              f"{' [' + COHORT + ']' if origin else ''}")
        if not dry:
            c.execute(
                "INSERT INTO pos(symbol,cluster,status,queued_on,entry_day,"
                "entry_px,qty,stop,target,features,fill_source,bucket,origin)"
                " VALUES(?,?,'open',?,?,?,?,?,?,?,?,?,?)",
                (o["symbol"], o["cluster"], o["queued_on"], o["entry_day"],
                 o["entry_px"], o["qty"], o["stop"], o["target"],
                 o["features"], o["fill_source"], positions.MAIN, origin))
            added += 1
    if dry:
        print("\n-- dry run, nothing written")
        return 0
    c.commit()
    _check(c)
    c.commit()
    print(f"\nrestored {added}, voided {voided}")
    return added


def _check(c):
    """Assert the bucket is legal AFTER the write, and roll back if it is not.

    A restore that quietly breaks the mix or the deployment cap is worse than a
    failed one: every later result would be measured against a bucket that the
    selection rules could never have produced.
    """
    import selection
    from collections import Counter
    s = positions.summary(c)
    live = [r for r in s["rows"] if r["status"] in ("open", "pending")]
    mix = Counter(r["cluster"] for r in live)
    val = sum((r["entry_px"] or 0) * (r["qty"] or 0) for r in live)
    cap = selection.CAPITAL * selection.DEPLOY_PCT / 100
    dup = [r[0] for r in c.execute(
        "SELECT symbol FROM pos WHERE status IN ('pending','open')"
        " GROUP BY symbol HAVING count(*) > 1")]
    try:
        assert not dup, f"the same symbol is live twice: {dup}"
        assert len(live) <= selection.MAX_POSITIONS, \
            f"{len(live)} live, max {selection.MAX_POSITIONS}"
        for clu, k in selection.TAKE_PER_CLUSTER.items():
            assert mix[clu] <= k, f"{mix[clu]} {clu}, quota {k}"
        assert val <= cap + 1, f"deployed Rs {val:,.0f} over the Rs {cap:,.0f} cap"
    except AssertionError:
        c.rollback()
        raise
    print(f"\n  live {len(live)}/{selection.MAX_POSITIONS}  mix {dict(mix)}  "
          f"deployed Rs {val:,.0f} of Rs {cap:,.0f}")


def _selftest():
    """Run the whole recovery against a throwaway pair of databases."""
    import tempfile
    from pathlib import Path
    _odb = positions.DB
    with tempfile.TemporaryDirectory() as td:
        old = Path(td) / "old.db"
        o = sqlite3.connect(old)
        o.executescript("""CREATE TABLE pos(
          id INTEGER PRIMARY KEY, symbol TEXT, cluster TEXT, status TEXT,
          queued_on TEXT, entry_day TEXT, entry_px REAL, qty INTEGER,
          stop REAL, target REAL, exit_day TEXT, exit_px REAL,
          exit_reason TEXT, net REAL, features TEXT, fill_source TEXT,
          bucket TEXT);""")
        o.executemany(
            "INSERT INTO pos(symbol,cluster,status,queued_on,entry_day,entry_px,"
            "qty,stop,target,bucket) VALUES(?,?,'open',?,?,?,?,?,?,?)", [
                ("AAA", "small", "2026-08-14", "2026-08-17", 100.0, 20, 90.0, 120.0, "main"),
                ("BBB", "micro", "2026-08-17", "2026-08-18", 50.0, 40, 45.0, 60.0, "third"),
            ])
        o.commit(); o.close()

        positions.DB = Path(td) / "new.db"
        try:
            c = positions.db()
            # the state the rename produced: AAA bought again, later and higher
            c.execute("INSERT INTO pos(symbol,cluster,status,entry_day,entry_px,"
                      "qty,stop,target,bucket) VALUES('AAA','small','open',"
                      "'2026-08-19',110.0,18,99.0,132.0,'main')")
            c.commit(); c.close()

            assert restore(dry=True, old=old) == 0, "a dry run must not write"
            assert restore(old=old) == 2

            c = positions.db()
            got = {r[0]: (r[1], r[2], r[3]) for r in c.execute(
                "SELECT symbol||':'||status, entry_px, bucket, origin FROM pos")}
            # the duplicate is retired, not deleted, and is not a closed trade
            assert "AAA:void" in got and got["AAA:void"][0] == 110.0, got
            assert c.execute("SELECT count(*) FROM closed_orders").fetchone()[0] == 0, \
                "a voided duplicate must not count as a closed trade"
            # the original is back, at ITS price, and the label is gone
            assert got["AAA:open"] == (100.0, "main", None), got
            # the cohort pick is back under the one bucket, provenance kept
            assert got["BBB:open"] == (50.0, "main", COHORT), got
            assert {r[0] for r in c.execute("SELECT symbol FROM open_orders")} \
                == {"AAA", "BBB"}, "open_orders must show both restored names"
            s = positions.summary(c)
            assert s["open"] == 2 and s["closed"] == 0, s
            # running it twice must change nothing
            n_before = c.execute("SELECT count(*) FROM pos").fetchone()[0]
            c.close()
            restore(old=old)
            c = positions.db()
            assert c.execute("SELECT count(*) FROM pos").fetchone()[0] == n_before, \
                "a second run added rows; the restore is not idempotent"
            c.close()
        finally:
            positions.DB = _odb
    print("restore_orphans selftest ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    else:
        restore(dry="--dry-run" in sys.argv)
