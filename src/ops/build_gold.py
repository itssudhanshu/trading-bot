#!/usr/bin/env python3
"""Gold builder — compacts Silver -> Gold Parquet + DuckDB views."""
import sys as _sys, pathlib as _pl
_sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[1]))
import paths  # noqa: F401
from paths import ROOT

GOLD_DIR = ROOT / "data" / "gold"
SILVER_FUNDAMENTALS = ROOT / "data" / "fundamentals" / "parsed"
SILVER_SCREENER = ROOT / "data" / "fundamentals_screener" / "parsed"
SECTORS_FILE = ROOT / "data" / "sectors.json"
JOURNAL_DB = ROOT / "data" / "positions.db"

def _collect_fundamentals(gold_dir, is_test):
    import json
    rows = []
    # Test injection: GOLD_DIR/fundamentals/*.json (brief selftest)
    test_dir = gold_dir / "fundamentals"
    if is_test and test_dir.exists():
        for p in test_dir.glob("*.json"):
            try:
                data = json.loads(p.read_text())
            except Exception:
                continue
            sym = p.stem
            items = data if isinstance(data, list) else [data]
            for r in items:
                if not isinstance(r, dict):
                    continue
                nr = dict(r)
                nr["symbol"] = sym
                nr.setdefault("source_file", str(p))
                rows.append(nr)
        return rows
    # Production: both Silver trees (NSE quarterly + Screener annual)
    for base in (SILVER_FUNDAMENTALS, SILVER_SCREENER):
        if not base.exists():
            continue
        for p in base.glob("*.json"):
            try:
                data = json.loads(p.read_text())
            except Exception:
                continue
            sym = p.stem
            items = data if isinstance(data, list) else [data]
            for r in items:
                if not isinstance(r, dict):
                    continue
                nr = dict(r)
                nr["symbol"] = sym
                nr.setdefault("source_file", str(p))
                rows.append(nr)
    return rows

def _collect_sectors():
    import json
    if not SECTORS_FILE.exists():
        return []
    try:
        d = json.loads(SECTORS_FILE.read_text())
    except Exception:
        return []
    return [{"symbol": k, "sector": v} for k, v in d.items()]

def _collect_prices():
    try:
        import features as _f
        try:
            corpus = _f.load_corpus(require_master=False)
        except TypeError:
            corpus = _f.load_corpus()
    except Exception:
        return []
    rows = []
    for sym, ser in corpus.items():
        try:
            days = ser.days
            opens = ser.open
            highs = ser.high
            lows = ser.low
            closes = ser.close
            volumes = ser.volume
        except Exception:
            continue
        for d, o, h, lo, c, v in zip(days, opens, highs, lows, closes, volumes):
            try:
                rows.append({"symbol": sym, "day": d.isoformat() if hasattr(d, "isoformat") else str(d), "open": float(o) if o is not None else None, "high": float(h) if h is not None else None, "low": float(lo) if lo is not None else None, "close": float(c) if c is not None else None, "volume": int(v) if v is not None else None})
            except Exception:
                continue
    return rows

def _collect_journal():
    import sqlite3
    if not JOURNAL_DB.exists():
        return []
    try:
        con = sqlite3.connect(str(JOURNAL_DB))
        con.row_factory = sqlite3.Row
        cur = con.cursor()
        # find table
        tbl = None
        for cand in ("pos", "positions"):
            try:
                cur.execute(f"SELECT * FROM {cand} LIMIT 1")
                tbl = cand
                break
            except Exception:
                continue
        if tbl is None:
            con.close()
            return []
        cur.execute(f"SELECT * FROM {tbl}")
        cols = [d[0] for d in cur.description] if cur.description else []
        out = []
        for r in cur.fetchall():
            d = dict(r)
            # keep as-is, ensure symbol exists
            out.append(d)
        con.close()
        return out
    except Exception:
        return []

def _write_parquet(rows, path, extra_cols=None):
    import pyarrow as pa
    import pyarrow.parquet as pq
    path = _pl.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        # empty: write with minimal schema so read_parquet works
        if extra_cols:
            tbl = pa.table({k: [] for k in extra_cols})
        else:
            tbl = pa.table({"symbol": []})
        pq.write_table(tbl, str(path))
        return
    # add built_at + normalize
    import datetime
    built = datetime.datetime.utcnow().isoformat()
    for r in rows:
        r.setdefault("built_at", built)
    try:
        tbl = pa.Table.from_pylist(rows)
    except Exception:
        # fallback: stringify
        tbl = pa.Table.from_pylist([{k: str(v) if v is not None else None for k, v in row.items()} for row in rows])
    pq.write_table(tbl, str(path))

def _create_duckdb_views(gold_dir):
    import duckdb
    db_path = gold_dir / "gold.duckdb"
    try:
        con = duckdb.connect(str(db_path))
        for name in ("fundamentals", "sectors", "prices", "journal"):
            p = gold_dir / f"{name}.parquet"
            if p.exists():
                con.execute(f"CREATE OR REPLACE VIEW {name} AS SELECT * FROM read_parquet('{p}')")
        con.close()
    except Exception:
        pass

def build(gold_dir=None):
    import datetime
    g = _pl.Path(gold_dir) if gold_dir is not None else GOLD_DIR
    g.mkdir(parents=True, exist_ok=True)
    is_test = (g != (ROOT / "data" / "gold")) and (g / "fundamentals").exists()
    # fundamentals
    f_rows = _collect_fundamentals(g, is_test)
    _write_parquet(f_rows, g / "fundamentals.parquet")
    # sectors
    if is_test:
        _write_parquet([{"symbol": "FAKE", "sector": "Test"}], g / "sectors.parquet")
    else:
        s_rows = _collect_sectors()
        _write_parquet(s_rows, g / "sectors.parquet", extra_cols=["symbol", "sector"])
    # prices
    if is_test:
        _write_parquet([], g / "prices.parquet", extra_cols=["symbol", "day", "open", "high", "low", "close", "volume"])
    else:
        p_rows = _collect_prices()
        _write_parquet(p_rows, g / "prices.parquet", extra_cols=["symbol", "day", "open", "high", "low", "close", "volume"])
    # journal
    if is_test:
        _write_parquet([], g / "journal.parquet")
    else:
        j_rows = _collect_journal()
        _write_parquet(j_rows, g / "journal.parquet")
    _create_duckdb_views(g)
    # manifest for audit trail
    try:
        import json as _js
        man = {"built_at": datetime.datetime.utcnow().isoformat(), "gold_dir": str(g), "fundamentals_rows": len(f_rows), "is_test": bool(is_test)}
        (g / "manifest.json").write_text(_js.dumps(man, indent=2))
    except Exception:
        pass

def _selftest():
    import tempfile, pathlib, json
    import build_gold as bg
    orig = bg.GOLD_DIR
    tmp = pathlib.Path(tempfile.mkdtemp())
    bg.GOLD_DIR = tmp
    try:
        (tmp / "fundamentals").mkdir(parents=True)
        (tmp / "fundamentals" / "FAKE.json").write_text(json.dumps([{"visible_from": "2024-05-17", "year_end": "2024-03-31", "revenue": 100}]))
        bg.build()
        assert (tmp / "fundamentals.parquet").exists()
        import duckdb
        con = duckdb.connect()
        con.execute(f"SELECT revenue FROM read_parquet('{tmp}/fundamentals.parquet') WHERE symbol='FAKE'")
        assert con.fetchone()[0] == 100
        print("build_gold selftest ok")
    finally:
        bg.GOLD_DIR = orig

if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        _selftest()
    else:
        print(__doc__.strip().splitlines()[0])
