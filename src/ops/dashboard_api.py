"""dashboard_api.py — FastAPI over Gold Parquet + company_data.

Endpoints:
  GET /company/{symbol}?as_of=YYYY-MM-DD   → fundamentals snapshot
  GET /watchlist                          → watchlist summary
  GET /sector/heatmap                    → sector P&L heatmap
  GET /journal                           → last N journal entries
  GET /etf_trend                         → ETF trend paper state (positions/queue)
  GET /snapshot                          → full dashboard snapshot (composed from Gold + strategy data)
"""

import sys as _sys
import pathlib as _pl
_sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[1]))
import paths  # noqa: F401

from fastapi import FastAPI

app = FastAPI()


@app.get("/company/{symbol}")
def company(symbol: str, as_of: str = None):
    from datetime import date
    import company_data
    d = date.fromisoformat(as_of) if as_of else None
    return company_data.get(symbol, as_of=d)


@app.get("/watchlist")
def watchlist():
    import company_data
    return {"symbols": company_data.get_universe()}


@app.get("/sector/heatmap")
def sector_heatmap():
    import company_data
    return company_data.get_sector_heatmap()


@app.get("/journal")
def journal(limit: int = 50):
    import company_data
    return company_data.get_journal(limit=limit)


@app.get("/etf_trend")
def etf_trend():
    import json
    from paths import ROOT
    p = ROOT / "data" / "etf_trend" / "paper_state.json"
    if p.exists():
        try:
            return json.loads(p.read_text())
        except Exception:
            return {"positions": [], "queue": [], "last_day": None}
    return {"positions": [], "queue": [], "last_day": None}


@app.get("/snapshot")
def snapshot(as_of: str = None):
    """Return full dashboard snapshot composed from Gold + strategy data."""
    # Reuse the dashboard_export logic to compose the snapshot
    import dashboard_export as de
    snap = de.export()
    if as_of:
        snap["as_of"] = as_of
    return snap


def _selftest():
    from fastapi.testclient import TestClient
    import dashboard_api as api
    client = TestClient(api.app)
    r = client.get("/company/RELIANCE?as_of=2024-05-17")
    assert r.status_code == 200
    assert "revenue" in r.json()["fundamentals"]
    # watchlist / heatmap / journal must not 500 (reviewer critical)
    w = client.get("/watchlist")
    assert w.status_code == 200 and "symbols" in w.json() and len(w.json()["symbols"]) >= 1000
    h = client.get("/sector/heatmap")
    assert h.status_code == 200 and "sectors" in h.json() and h.json()["total"] >= 1000
    j = client.get("/journal?limit=2")
    assert j.status_code == 200 and isinstance(j.json(), list)
    e = client.get("/etf_trend")
    assert e.status_code == 200 and "positions" in e.json()
    # Test snapshot endpoint
    r2 = client.get("/snapshot")
    assert r2.status_code == 200
    assert "approach" in r2.json()
    assert "trades" in r2.json()
    print("dashboard_api selftest ok")


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--selftest":
        _selftest()