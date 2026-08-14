#!/usr/bin/env python3
"""Parse a raw snapshot into a point-in-time tradeable universe.

Reads only from data/raw/<date>/ -- never the live NSE endpoints -- so a
backtest sees exactly the surveillance state that existed on that date.
"""
import csv
import io
import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RAW = ROOT / "data" / "raw"

# EQ only. BE/BZ are trade-to-trade: delivery-compulsory, no intraday exit,
# and typically already distressed. Not swing-tradeable.
TRADEABLE_SERIES = {"EQ"}


@dataclass
class Bar:
    symbol: str
    day: date
    open: float
    high: float
    low: float
    close: float
    prev_close: float
    volume: int
    turnover: float          # rupees
    deliv_qty: int
    deliv_pct: float         # NaN-ish sources use -1
    asm: str = ""            # "" | stage label
    gsm: str = ""
    fo_ban: bool = False
    # False on backfilled dates: NSE publishes surveillance lists for today only,
    # so history has no ASM/GSM/ban state. Absence is not evidence of absence --
    # callers must not read restricted=False here as "was tradeable".
    surveillance_known: bool = True

    @property
    def restricted(self) -> bool:
        """Any *known* surveillance flag => not swing-tradeable."""
        return bool(self.asm) or bool(self.gsm) or self.fo_ban


def _f(x, default=-1.0):
    try:
        return float(str(x).strip())
    except (ValueError, AttributeError):
        return default


def _read(day: date, name: str, ext: str):
    p = RAW / day.isoformat() / f"{name}.{ext}"
    return p.read_text(errors="replace") if p.exists() else None


def _asm_symbols(txt) -> dict:
    """ASM payload nests by horizon: {'longterm': {'data': [...]}, 'shortterm': ...}."""
    if not txt:
        return {}
    out = {}
    for block in json.loads(txt).values():
        if isinstance(block, dict):
            for row in block.get("data", []):
                if row.get("symbol"):
                    out[row["symbol"]] = row.get("asmSurvIndicator") or "ASM"
    return out


def _gsm_symbols(txt) -> dict:
    if not txt:
        return {}
    return {r["symbol"]: r.get("gsmStage") or "GSM"
            for r in json.loads(txt) if r.get("symbol")}


def _ban_symbols(txt) -> set:
    """fo_secban.csv is 'n,SYMBOL' rows under a header naming the NEXT trade date."""
    if not txt:
        return set()
    out = set()
    for line in txt.splitlines()[1:]:
        parts = line.split(",")
        if len(parts) >= 2 and parts[1].strip():
            out.add(parts[1].strip())
    return out


def load(day: date) -> dict:
    """-> {symbol: Bar} for the given snapshot date."""
    bhav = _read(day, "bhavcopy_delivery", "csv")
    if not bhav:
        return {}

    asm, gsm, ban = (_asm_symbols(_read(day, "asm", "json")),
                     _gsm_symbols(_read(day, "gsm", "json")),
                     _ban_symbols(_read(day, "fo_secban", "csv")))
    sv_known = (RAW / day.isoformat() / "asm.json").exists()

    bars = {}
    for row in csv.DictReader(io.StringIO(bhav), skipinitialspace=True):
        row = {k.strip(): (v.strip() if isinstance(v, str) else v)
               for k, v in row.items() if k}
        sym, series = row.get("SYMBOL", ""), row.get("SERIES", "")
        if series not in TRADEABLE_SERIES or not sym:
            continue
        bars[sym] = Bar(
            symbol=sym, day=day,
            open=_f(row["OPEN_PRICE"]), high=_f(row["HIGH_PRICE"]),
            low=_f(row["LOW_PRICE"]), close=_f(row["CLOSE_PRICE"]),
            prev_close=_f(row["PREV_CLOSE"]),
            volume=int(_f(row["TTL_TRD_QNTY"], 0)),
            turnover=_f(row["TURNOVER_LACS"], 0) * 100_000,   # lacs -> rupees
            deliv_qty=int(_f(row.get("DELIV_QTY"), 0)),
            deliv_pct=_f(row.get("DELIV_PER")),
            asm=asm.get(sym, ""), gsm=gsm.get(sym, ""), fo_ban=sym in ban,
            surveillance_known=sv_known,
        )
    return bars


def _selftest():
    global RAW
    import tempfile
    original = RAW
    try:
        with tempfile.TemporaryDirectory() as td:
            RAW = Path(td)
            d = date(2026, 1, 1)
            snap = RAW / d.isoformat()
            snap.mkdir(parents=True)
            (snap / "bhavcopy_delivery.csv").write_text(
                "SYMBOL, SERIES, DATE1, PREV_CLOSE, OPEN_PRICE, HIGH_PRICE, LOW_PRICE, "
                "LAST_PRICE, CLOSE_PRICE, AVG_PRICE, TTL_TRD_QNTY, TURNOVER_LACS, "
                "NO_OF_TRADES, DELIV_QTY, DELIV_PER\n"
                "GOOD, EQ, 01-Jan-2026, 100, 101, 105, 99, 104, 104, 102, 1000, 10.5, 50, 600, 60.0\n"
                "FLAGGED, EQ, 01-Jan-2026, 50, 50, 51, 49, 50, 50, 50, 500, 2.5, 10, 100, 20.0\n"
                "BANNED, EQ, 01-Jan-2026, 70, 70, 71, 69, 70, 70, 70, 800, 5.6, 20, 200, 25.0\n"
                "T2TONLY, BE, 01-Jan-2026, 10, 10, 11, 9, 10, 10, 10, 100, 0.1, 5, 50, 50.0\n")
            (snap / "asm.json").write_text(json.dumps(
                {"longterm": {"data": [{"symbol": "FLAGGED", "asmSurvIndicator": "Stage I"}]}}))
            (snap / "gsm.json").write_text("[]")
            (snap / "fo_secban.csv").write_text(
                "Securities in Ban For Trade Date 02-JAN-2026:\n1,BANNED\n")

            u = load(d)
            assert "T2TONLY" not in u, "BE series must be excluded"
            assert set(u) == {"GOOD", "FLAGGED", "BANNED"}, sorted(u)
            assert not u["GOOD"].restricted
            assert u["FLAGGED"].asm == "Stage I" and u["FLAGGED"].restricted
            assert u["BANNED"].fo_ban and u["BANNED"].restricted
            assert u["GOOD"].turnover == 10.5 * 100_000, "lacs->rupees conversion"
            assert u["GOOD"].deliv_pct == 60.0
            assert u["GOOD"].surveillance_known

            # backfilled date: bhavcopy only, no surveillance sidecars
            d2 = date(2026, 1, 2)
            snap2 = RAW / d2.isoformat()
            snap2.mkdir(parents=True)
            (snap2 / "bhavcopy_delivery.csv").write_text(
                "SYMBOL, SERIES, DATE1, PREV_CLOSE, OPEN_PRICE, HIGH_PRICE, LOW_PRICE, "
                "LAST_PRICE, CLOSE_PRICE, AVG_PRICE, TTL_TRD_QNTY, TURNOVER_LACS, "
                "NO_OF_TRADES, DELIV_QTY, DELIV_PER\n"
                "OLD, EQ, 02-Jan-2026, 100, 101, 105, 99, 104, 104, 102, 1000, 10.5, 50, 600, 60.0\n")
            old = load(d2)["OLD"]
            assert not old.surveillance_known, "backfilled bars must flag unknown surveillance"
            assert not old.restricted, "no known flags, but that is not the same as clean"
    finally:
        RAW = original
    print("universe selftest ok")


if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        _selftest()
    else:
        d = date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else date.today()
        u = load(d)
        flagged = [b for b in u.values() if b.restricted]
        print(f"{d}: {len(u)} EQ symbols, {len(flagged)} restricted")
        print(f"  ASM={sum(1 for b in u.values() if b.asm)} "
              f"GSM={sum(1 for b in u.values() if b.gsm)} "
              f"FO_ban={sum(1 for b in u.values() if b.fo_ban)}")
