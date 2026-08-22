#!/usr/bin/env python3
"""Parse a raw snapshot into a point-in-time tradeable universe.

Reads only from data/raw/<date>/ -- never the live NSE endpoints -- so a
backtest sees exactly the surveillance state that existed on that date.
"""

import sys as _sys, pathlib as _pl
_sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[1]))
import paths  # noqa: F401  -- puts the source dirs on sys.path
import csv
import io
import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from paths import ROOT      # one definition; see paths.py
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


_NON_EQUITY = None

# Funds that stopped trading before any snapshot here held a company master.
# Nothing in data/raw/ can classify them -- see the module note on
# non_equity_symbols -- so they are derived once, with evidence, by
# src/ops/classify_non_equity.py and recorded. Read as data, never regenerated
# on the fly: it encodes a judgement about instruments that no longer exist.
HISTORY = ROOT / "data" / "non_equity_history.json"


def _seen_non_equity() -> set:
    """-> every symbol any snapshot ever showed trading EQ while its own
    master omitted it.

    BOTH sides come from the SAME day, which is what makes it point-in-time:
    a symbol is judged by the master that was current when it traded.

    It is a UNION over days, and that is the whole fix. Reading only the
    newest snapshot meant a symbol had to be trading TODAY to be eligible for
    the denylist at all, so every fund that had already delisted was invisible
    -- and re-entered the historical universe as though it were a company. The
    live bucket bought 22 gold, silver and index ETFs that way. Accumulating
    also stops it recurring: LICNETFSEN traded as a fund on 2026-08-14 and was
    gone by 2026-08-20, and only the union still knows what it was.

    Cost is one pass per snapshot that HOLDS a master, not per snapshot: 5 of
    1,699 here. It grows by one a day once daily.py is saving masters, so if
    this ever shows up in a profile the answer is to cache the per-day sets,
    not to go back to reading one snapshot.
    """
    out = set()
    if not RAW.exists():
        return out
    for d in sorted(p for p in RAW.iterdir() if p.is_dir()):
        master_f, bhav_f = d / "equity_master.csv", d / "bhavcopy_delivery.csv"
        if not (master_f.exists() and bhav_f.exists()):
            continue
        master = {r["SYMBOL"].strip()
                  for r in csv.DictReader(io.StringIO(master_f.read_text(errors="replace")))
                  if r.get("SYMBOL")}
        traded = {r["SYMBOL"].strip()
                  for r in csv.DictReader(io.StringIO(bhav_f.read_text(errors="replace")),
                                          skipinitialspace=True)
                  if r.get("SYMBOL") and r.get("SERIES", "").strip() in TRADEABLE_SERIES}
        out |= traded - master
    return out


def historical_non_equity() -> dict:
    """-> {symbol: evidence} from HISTORY, or {} if the file is absent."""
    if not HISTORY.exists():
        return {}
    return json.loads(HISTORY.read_text()).get("symbols", {})


def non_equity_symbols() -> set:
    """EQ-series symbols that are not operating companies -- ETFs, liquid funds,
    index trackers. NSE lists them in EQ but omits them from the company master.

    Two sources, because one snapshot cannot answer for every date:

      seen     every symbol that traded EQ on a day whose own master omitted
               it, unioned over all such days. Point-in-time and permanent: a
               fund seen once stays classified after it delists.
      history  funds that delisted BEFORE the first snapshot with a master.
               Only 7 snapshots here hold one and they span a single week, so
               their union says nothing about 2021. HISTORY is the evidenced
               answer for those; see src/ops/classify_non_equity.py.

    Still a denylist, never a company allowlist. A company delisted in 2023 is
    absent from today's master AND today's bhavcopy; requiring master
    membership would silently delete every delisted name from history --
    textbook survivorship bias. THAT is why the historical half has to be
    classified on evidence rather than derived by subtraction: `ever traded
    minus the master` is 828 symbols and most of them are dead companies.
    """
    global _NON_EQUITY
    if _NON_EQUITY is None:
        _NON_EQUITY = _seen_non_equity() | set(historical_non_equity())
    return _NON_EQUITY


def master_snapshot():
    """-> newest snapshot dir holding BOTH equity_master.csv and a bhavcopy, or
    None if no snapshot does.

    Exposed so a caller building a CORPUS can refuse to proceed without it.
    Absent the master, `non_equity_symbols` is empty and every ETF, liquid fund
    and index tracker enters the universe -- silently, because an empty denylist
    is indistinguishable from a denylist with nothing to say. `backfill.py`
    fetches bhavcopy only, so a rebuilt machine hits exactly that (see L36).
    Single-day `load()` keeps the permissive behaviour: it is used on fixtures
    that legitimately have no master.
    """
    if not RAW.exists():
        return None
    for d in sorted((p for p in RAW.iterdir() if p.is_dir()), reverse=True):
        if (d / "equity_master.csv").exists() and (d / "bhavcopy_delivery.csv").exists():
            return d
    return None


def load(day: date, exclude_non_equity=True) -> dict:
    """-> {symbol: Bar} for the given snapshot date."""
    deny = non_equity_symbols() if exclude_non_equity else set()
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
        if series not in TRADEABLE_SERIES or not sym or sym in deny:
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
    global RAW, HISTORY
    import tempfile
    original, original_history = RAW, HISTORY
    try:
        with tempfile.TemporaryDirectory() as td:
            RAW = Path(td)
            HISTORY = Path(td) / "no-such-history.json"
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

            # no equity_master.csv in this fixture -> denylist empty, nothing dropped
            global _NON_EQUITY
            _NON_EQUITY = None
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

            # with a company master present, EQ symbols missing from it are dropped
            (snap / "equity_master.csv").write_text(
                "SYMBOL,NAME OF COMPANY, SERIES\nGOOD,Good Ltd,EQ\n"
                "FLAGGED,Flagged Ltd,EQ\nBANNED,Banned Ltd,EQ\n")
            _NON_EQUITY = None
            assert non_equity_symbols() == set(), "d has no ETFs beyond the master"
            _NON_EQUITY = None
            (snap / "bhavcopy_delivery.csv").write_text(
                (snap / "bhavcopy_delivery.csv").read_text()
                + "SOMEETF, EQ, 01-Jan-2026, 10, 10, 11, 9, 10, 10, 10, 100, 1.0, 5, 50, 50.0\n")
            _NON_EQUITY = None
            assert "SOMEETF" in non_equity_symbols(), "EQ symbol absent from master is not a company"
            assert "SOMEETF" not in load(d), "non-company must be excluded from the universe"
            assert "SOMEETF" in load(d, exclude_non_equity=False), "opt-out must still return it"
            _NON_EQUITY = None

            # THE POINT-IN-TIME GAP. A fund that delisted before the newest
            # snapshot is absent from the newest bhavcopy, so `traded - master`
            # read off that snapshot alone can never see it -- and it re-enters
            # the historical universe as a company. Both files exist on d, so
            # the union over days is what remembers OLDETF.
            (snap / "bhavcopy_delivery.csv").write_text(
                (snap / "bhavcopy_delivery.csv").read_text()
                + "OLDETF, EQ, 01-Jan-2026, 20, 20, 21, 19, 20, 20, 20, 100, 1.0, 5, 50, 50.0\n"
                + "DEADCO, EQ, 01-Jan-2026, 30, 30, 31, 29, 30, 30, 30, 100, 1.0, 5, 50, 50.0\n")
            (snap / "equity_master.csv").write_text(
                (snap / "equity_master.csv").read_text() + "DEADCO,Dead Co Ltd,EQ\n")
            newer = RAW / "2026-01-05"          # OLDETF and DEADCO both gone
            newer.mkdir(parents=True)
            (newer / "bhavcopy_delivery.csv").write_text(
                "SYMBOL, SERIES, DATE1, PREV_CLOSE, OPEN_PRICE, HIGH_PRICE, LOW_PRICE, "
                "LAST_PRICE, CLOSE_PRICE, AVG_PRICE, TTL_TRD_QNTY, TURNOVER_LACS, "
                "NO_OF_TRADES, DELIV_QTY, DELIV_PER\n"
                "GOOD, EQ, 05-Jan-2026, 100, 101, 105, 99, 104, 104, 102, 1000, 10.5, 50, 600, 60.0\n")
            (newer / "equity_master.csv").write_text(
                "SYMBOL,NAME OF COMPANY, SERIES\nGOOD,Good Ltd,EQ\n")
            assert master_snapshot() == newer, "newest snapshot with both files"
            _NON_EQUITY = None
            deny = non_equity_symbols()
            assert "OLDETF" in deny, \
                "a fund seen on an earlier master day must stay classified"
            assert "SOMEETF" in deny, "the still-listed fund must not be lost"
            assert "DEADCO" not in deny, \
                "a DELISTED COMPANY must not be denied -- that is survivorship bias"
            assert "OLDETF" not in load(d), "the fund must leave the historical universe"
            assert "DEADCO" in load(d), "the dead company must stay in it"

            # The historical artifact carries funds that delisted before any
            # master existed, so nothing in data/raw/ can reach them.
            HISTORY = Path(td) / "history.json"
            HISTORY.write_text(json.dumps({"symbols": {"ANCIENTETF": {"tier": "A"}}}))
            _NON_EQUITY = None
            assert "ANCIENTETF" in non_equity_symbols(), "HISTORY must be applied"
            assert set(historical_non_equity()) == {"ANCIENTETF"}
            HISTORY = Path(td) / "gone.json"
            _NON_EQUITY = None
            assert historical_non_equity() == {}, "a missing artifact must not raise"
            assert "OLDETF" in non_equity_symbols(), \
                "the snapshot-derived half must stand without the artifact"
            _NON_EQUITY = None
    finally:
        RAW, HISTORY = original, original_history
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
