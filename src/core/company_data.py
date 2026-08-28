#!/usr/bin/env python3
"""Logical view: one call per company, point-in-time. Gold read-only; Bronze write-once."""
import sys as _sys, pathlib as _pl
_sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[1]))
import paths  # noqa: F401
import json, sqlite3
from datetime import date, timedelta
from pathlib import Path
from paths import ROOT
FRESHNESS_DAYS = 550
def _timeline(symbol: str) -> list:
    try:
        import fundamentals
        return fundamentals.timeline(symbol)
    except Exception:
        return []
def _screener_timeline(symbol: str) -> list:
    try:
        import screener_fundamentals as sf
        return sf.timeline_annual_screener(symbol)
    except Exception:
        return []
def _latest_visible(rows: list, as_of):
    if not rows:
        return None
    if as_of is None:
        try:
            return sorted(rows, key=lambda r: r.get("visible_from", ""))[-1]
        except Exception:
            return rows[-1]
    if isinstance(as_of, str):
        as_of_date = date.fromisoformat(as_of); as_of_s = as_of
    elif hasattr(as_of, "isoformat"):
        as_of_date = as_of; as_of_s = as_of.isoformat()
    else:
        return None
    visible = [r for r in rows if r.get("visible_from") and r["visible_from"] <= as_of_s]
    if not visible:
        return None
    cutoff = (as_of_date - timedelta(days=FRESHNESS_DAYS)).isoformat()
    fresh = []
    for r in visible:
        ye = r.get("year_end")
        if ye is None or ye >= cutoff:
            fresh.append(r)
    if not fresh:
        return None
    fresh.sort(key=lambda r: r.get("visible_from", ""))
    return fresh[-1]
def _sector(symbol: str):
    try:
        p = ROOT / "data" / "sectors.json"
        if not p.exists():
            return None
        return json.loads(p.read_text()).get(symbol)
    except Exception:
        return None
def _announcements(symbol: str, as_of):
    rows = []
    try:
        import announcements as _ann
        rows.extend(_ann.timeline(symbol) or [])
    except Exception:
        pass
    try:
        import bse_announcements as _bse
        rows.extend(_bse.timeline(symbol) or [])
    except Exception:
        pass
    if not rows:
        return []
    if as_of is not None:
        as_of_s = as_of.isoformat() if hasattr(as_of, "isoformat") else str(as_of)
        rows = [r for r in rows if r.get("visible_from") and r["visible_from"] <= as_of_s]
    rows.sort(key=lambda r: (r.get("visible_from", ""), r.get("an_dt", "")))
    return rows
def _prices(symbol: str, as_of):
    try:
        import features as _f
        try:
            corpus = _f.load_corpus(require_master=False)
        except TypeError:
            corpus = _f.load_corpus()
        ser = corpus.get(symbol)
        if ser is None:
            return None
        if as_of is None:
            return {"days": [d.isoformat() for d in ser.days], "open": list(ser.open), "high": list(ser.high), "low": list(ser.low), "close": list(ser.close), "volume": list(ser.volume)}
        as_of_date = as_of if isinstance(as_of, date) else date.fromisoformat(str(as_of))
        out = {"days": [], "open": [], "high": [], "low": [], "close": [], "volume": []}
        for d, o, h, lo, c, v in zip(ser.days, ser.open, ser.high, ser.low, ser.close, ser.volume):
            if d <= as_of_date:
                out["days"].append(d.isoformat()); out["open"].append(o); out["high"].append(h); out["low"].append(lo); out["close"].append(c); out["volume"].append(v)
        return out
    except Exception:
        return None
def _journal(symbol: str, as_of):
    try:
        dbp = ROOT / "data" / "positions.db"
        if not dbp.exists():
            return []
        con = sqlite3.connect(str(dbp)); con.row_factory = sqlite3.Row
        rows = None
        for tbl in ("pos", "positions"):
            try:
                rows = con.execute(f"SELECT * FROM {tbl} WHERE symbol = ?", (symbol,)).fetchall(); break
            except Exception:
                continue
        con.close()
        if not rows:
            return []
        out = [dict(r) for r in rows]
        if as_of is not None:
            as_of_s = as_of.isoformat() if hasattr(as_of, "isoformat") else str(as_of)
            filtered = [r for r in out if (r.get("entry_day") or r.get("queued_on") or None) is None or (r.get("entry_day") or r.get("queued_on")) <= as_of_s]
            has_day = any("entry_day" in r or "queued_on" in r for r in out)
            out = filtered if has_day else out
        return out
    except Exception:
        return []
def get(symbol, as_of=None):
    sym = symbol.strip().upper() if isinstance(symbol, str) else symbol
    return {"prices": _prices(sym, as_of), "fundamentals": _latest_visible(_timeline(sym), as_of), "screener": _latest_visible(_screener_timeline(sym), as_of), "sector": _sector(sym), "announcements": _announcements(sym, as_of), "journal": _journal(sym, as_of)}


def get_universe():
    """Tradeable universe — keys of sectors.json (1,276 micro/small) else fundamentals parsed."""
    try:
        p = ROOT / "data" / "sectors.json"
        if p.exists():
            d = json.loads(p.read_text())
            if isinstance(d, dict) and d:
                return sorted(d.keys())
    except Exception:
        pass
    try:
        import fundamentals as _f
        # fallback: scan parsed directory names
        parsed = ROOT / "data" / "fundamentals" / "parsed"
        if parsed.exists():
            return sorted(pp.stem for pp in parsed.glob("*.json"))
    except Exception:
        pass
    try:
        import features as _feat
        try:
            corp = _feat.load_corpus(require_master=False)
        except TypeError:
            corp = _feat.load_corpus()
        return sorted(corp.keys())
    except Exception:
        return []


def get_sector_heatmap():
    """Sector → count (and symbols) for the dashboard heatmap."""
    try:
        p = ROOT / "data" / "sectors.json"
        if not p.exists():
            return {"sectors": {}, "total": 0}
        d = json.loads(p.read_text())
        from collections import Counter
        cnt = Counter(d.values())
        return {"sectors": dict(cnt), "total": len(d), "by_sector": {k: sorted([s for s, v in d.items() if v == k]) for k in cnt}}
    except Exception:
        return {"sectors": {}, "total": 0}


def get_journal(limit=50):
    """Global journal — last `limit` rows from positions.db pos/positions, ordered by entry_day desc."""
    try:
        dbp = ROOT / "data" / "positions.db"
        if not dbp.exists():
            return []
        con = sqlite3.connect(str(dbp))
        con.row_factory = sqlite3.Row
        rows = None
        tbl = None
        for cand in ("pos", "positions"):
            try:
                con.execute(f"SELECT 1 FROM {cand} LIMIT 1")
                tbl = cand
                break
            except Exception:
                continue
        if tbl is None:
            con.close()
            return []
        # order by entry_day/queued_on desc, fallback to id desc
        try:
            rows = con.execute(f"SELECT * FROM {tbl} ORDER BY COALESCE(entry_day, queued_on, '') DESC, id DESC LIMIT ?", (int(limit),)).fetchall()
        except Exception:
            rows = con.execute(f"SELECT * FROM {tbl} LIMIT ?", (int(limit),)).fetchall()
        con.close()
        return [dict(r) for r in rows]
    except Exception:
        return []
def _selftest():
    from datetime import date
    import company_data as cd
    orig_tl, orig_sl = cd._timeline, cd._screener_timeline
    try:
        cd._timeline = lambda sym: [{"visible_from": "2024-05-17", "year_end": "2024-03-31", "revenue": 100}]
        cd._screener_timeline = lambda sym: [{"visible_from": "2024-05-30", "year_end": "2024-03-31", "ocf": 80}]
        r = cd.get("FAKE", as_of=date(2024, 5, 20))
        assert r["screener"] is None, "future screener leaked"
        assert r["fundamentals"]["revenue"] == 100
        r2 = cd.get("FAKE", as_of=date(2024, 6, 1))
        assert r2["screener"]["ocf"] == 80
        cd._timeline = lambda sym: [{"visible_from": "2022-04-15", "year_end": "2022-03-31", "revenue": 999}]
        cd._screener_timeline = lambda sym: [{"visible_from": "2022-04-20", "year_end": "2022-03-31", "ocf": 50}]
        r_stale = cd.get("FAKE", as_of=date(2024, 6, 1))
        assert r_stale["fundamentals"] is None and r_stale["screener"] is None, f"stale leaked {r_stale}"
        cd._timeline = lambda sym: [{"visible_from": "2023-12-10", "year_end": "2023-03-31", "revenue": 77}]
        r_fresh = cd.get("FAKE", as_of=date(2024, 6, 1))
        assert r_fresh["fundamentals"] and r_fresh["fundamentals"]["revenue"] == 77
        cd._timeline = lambda sym: []; cd._screener_timeline = lambda sym: []
        r_missing = cd.get("FAKE", as_of=date(2024, 6, 1))
        assert r_missing["fundamentals"] is None and r_missing["screener"] is None
        cd._timeline = lambda sym: [{"visible_from": "2024-05-17", "year_end": "2024-03-31", "revenue": 100}]
        cd._screener_timeline = lambda sym: [{"visible_from": "2024-05-30", "year_end": "2024-03-31", "ocf": 80}]
        r_latest = cd.get("FAKE", as_of=None)
        assert r_latest["fundamentals"]["revenue"] == 100 and r_latest["screener"]["ocf"] == 80
        r_shape = cd.get("FAKE", as_of=date(2024, 6, 1))
        assert set(r_shape) == {"prices", "fundamentals", "screener", "sector", "announcements", "journal"}
        # new helpers
        uni = cd.get_universe()
        assert isinstance(uni, list) and len(uni) >= 1000, f"universe too small {len(uni)}"
        heat = cd.get_sector_heatmap()
        assert isinstance(heat, dict) and "sectors" in heat and heat["total"] >= 1000, f"heatmap failed {heat}"
        jour = cd.get_journal(limit=5)
        assert isinstance(jour, list), f"journal failed {jour}"
    finally:
        cd._timeline, cd._screener_timeline = orig_tl, orig_sl
    print("company_data selftest ok")
if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        _selftest()
    else:
        print(__doc__.strip().splitlines()[0])
