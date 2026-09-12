#!/usr/bin/env python3
"""Costs and market impact for the paper book.

WHAT THIS MODULE IS REACHED FOR, and it is the whole list: `Costs` (the India
cost stack -- brokerage, STT, exchange, GST, SEBI, stamp duty on the buy side,
DP on the sell side), `impact_pct` (square-root market impact) and `IMPACT_C`.
`_selftest_reachable` asserts that list against the rest of the tree, so the
docstring cannot drift away from what the code does.

It previously opened by describing an invariant gate whose rules were "NEVER
part of any search space". That gate had no caller. The claim was true in spirit
and false in fact for as long as the file existed, and a reader auditing risk
would have taken four contradicting numbers as live policy. If a risk gate is
wanted, it has to be CALLED and its rejections have to show up in the trade
count -- not merely defined here.
"""

import sys as _sys, pathlib as _pl
_sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[1]))
import paths  # noqa: F401  -- puts the source dirs on sys.path
import sqlite3
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from paths import ROOT      # one definition; see paths.py

# --- what this module actually governs -------------------------------------
# Costs and market impact. Nothing else.
#
# It used to carry a risk framework -- MIN_RR 3.0, RISK_PER_TRADE 0.5%,
# MAX_PORTFOLIO_HEAT 6%, MAX_ADV_PARTICIPATION 1% -- behind `gate()` and
# `size()`. A reachability census on 2026-09-12 found NOTHING in src/ or tests/
# called either function: the live path imports `Costs` and `impact_pct` and no
# more. Every one of those four numbers also contradicted the book it appeared
# to govern (RR 2.0 against a 3.0 floor, 7.5% open risk against a 6% cap, 1.5%
# per trade against 0.5%), and MAX_ADV_PARTICIPATION was a participation cap
# CLAUDE.md records as TESTED AND REJECTED. They are removed rather than
# corrected: tuning them to match the book would be relaxing a criterion to fit
# a result, and this file is exactly where that must not happen.
#
# Where those checks actually live now: the circuit lock is inline in each
# strategy's selection.py (L58), surveillance flags in `surveillance_known`,
# and "no restricted stock is a candidate" is an audit.py check. Point at
# those, never at a function nothing calls.


# Square-root market impact: cost ~ volatility * sqrt(participation), the form
# repeatedly found in execution data (Almgren et al.; Kyle's lambda in the
# linear limit). It is NOT calibrated to Indian microcaps -- no trade-level
# data here to fit it -- so IMPACT_C is a knob that must be reported as a
# sensitivity, never as a single number pretending to precision.
IMPACT_C = 1.0


def impact_pct(order_value, adv, daily_vol_pct, c=IMPACT_C):
    """-> one-way cost in PERCENT of the order.

    participation = order value / median daily turnover. At 1% of a day's
    turnover in a 3%-vol stock this is ~0.3%; at 50% it is ~2.1%; above 100%
    the order is larger than the day's entire trade and the number stops being
    a cost estimate and starts being a warning.
    """
    import math
    if adv is None or adv <= 0 or daily_vol_pct is None or daily_vol_pct <= 0:
        return 5.0                      # unpriceable: charge a deterrent
    return c * daily_vol_pct * math.sqrt(order_value / adv)


@dataclass(frozen=True)
class Costs:
    """Indian delivery-segment charges. Rates change -- verify against a live
    contract note before trusting absolute P&L."""
    brokerage_per_order: float = 20.0
    stt_buy: float = 0.001         # 0.1% delivery, both sides
    stt_sell: float = 0.001
    exchange_txn: float = 0.0000297
    sebi_turnover: float = 1e-6    # Rs 10 per crore
    gst: float = 0.18              # on brokerage + txn + sebi
    stamp_buy: float = 0.00015     # buy side only
    dp_sell: float = 15.93         # depository charge per SELL scrip, incl GST

    def charge(self, value: float, side: str) -> float:
        brok = self.brokerage_per_order
        stt = value * (self.stt_buy if side == "BUY" else self.stt_sell)
        txn = value * self.exchange_txn
        sebi = value * self.sebi_turnover
        gst = self.gst * (brok + txn + sebi)
        stamp = value * self.stamp_buy if side == "BUY" else 0.0
        dp = 0.0 if side == "BUY" else self.dp_sell
        return brok + stt + txn + sebi + gst + stamp + dp


DEFAULT_COSTS = None          # set below, once Costs is defined


@dataclass
class Signal:
    symbol: str
    setup: str
    entry: float          # stop-buy trigger
    stop: float           # invalidation
    target: float         # first target, used for the R:R test
    spec_version: str = "v0"

    @property
    def risk_per_share(self) -> float:
        return self.entry - self.stop

    @property
    def rr(self) -> float:
        r = self.risk_per_share
        return (self.target - self.entry) / r if r > 0 else -1.0


DEFAULT_COSTS = Costs()


def slippage_bps(value: float, turnover: float, base: float = 5.0,
                 per_pct_adv: float = 10.0) -> float:
    """Depth-aware without an order book: cost scales with how much of the
    day's traded value you consume. 1% participation adds `per_pct_adv` bps."""
    if turnover <= 0:
        return base * 10
    participation = value / turnover
    return base + per_pct_adv * (participation / 0.01)


def entry_fill(trigger: float, bar) -> float | None:
    """Stop-buy above `trigger`, executed on `bar`.

    A gap-open above the trigger fills at the open, not the trigger -- you do
    not get your price when the stock gaps through it. Engines that always
    fill at `trigger` manufacture money that does not exist.
    """
    if bar.high == bar.low:
        return None                     # circuit-locked, no execution
    if bar.high < trigger:
        return None                     # never traded up to the trigger
    return max(trigger, bar.open)


def stop_fill(stop: float, bar) -> float | None:
    """Protective stop on a long. Gap-down through the stop fills at the open,
    which is worse -- and is where swing losses actually come from."""
    if bar.high == bar.low:
        return None                     # locked; you are trapped in the position
    if bar.low > stop:
        return None
    return min(stop, bar.open)


def target_fill(target: float, bar) -> float | None:
    """Limit sell at `target`. A gap ABOVE the target fills at the open, which
    is BETTER -- the favourable mirror of stop_fill. Modelling this as a fill at
    exactly `target` understates winners as surely as filling stops at `stop`
    overstates them; both errors must be corrected or the asymmetry is fake.
    """
    if bar.high == bar.low:
        return None
    if bar.high < target:
        return None
    return max(target, bar.open)


class Journal:
    """Append-only. Rejections are logged too -- they are the training signal
    for the generator and the only record of what the gate actually blocked."""

    def __init__(self, path=None):
        self.db = sqlite3.connect(path or ROOT / "data" / "journal.db")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS signals(
          id INTEGER PRIMARY KEY, ts TEXT DEFAULT CURRENT_TIMESTAMP,
          day TEXT, symbol TEXT, setup TEXT, spec_version TEXT,
          entry REAL, stop REAL, target REAL, rr REAL,
          qty INTEGER, verdict TEXT, reject_reason TEXT);
        CREATE TABLE IF NOT EXISTS fills(
          id INTEGER PRIMARY KEY, ts TEXT DEFAULT CURRENT_TIMESTAMP,
          signal_id INTEGER, day TEXT, symbol TEXT, side TEXT,
          qty INTEGER, price REAL, slippage REAL, costs REAL, reason TEXT);
        -- One row per position across its whole life:
        --   pending -> open -> closed, or pending -> expired if never triggered.
        CREATE TABLE IF NOT EXISTS positions(
          id INTEGER PRIMARY KEY, ts TEXT DEFAULT CURRENT_TIMESTAMP,
          spec_hash TEXT, symbol TEXT, setup TEXT, status TEXT,
          signal_day TEXT, entry_day TEXT, entry_px REAL, qty INTEGER,
          stop REAL, target REAL, max_bars INTEGER,
          exit_day TEXT, exit_px REAL, exit_reason TEXT, net REAL,
          cluster TEXT, features TEXT);
        CREATE INDEX IF NOT EXISTS ix_pos_status ON positions(status);
        """)
        self.db.commit()

    def signal(self, day, sig: Signal, qty, reject) -> int:
        cur = self.db.execute(
            "INSERT INTO signals(day,symbol,setup,spec_version,entry,stop,target,rr,"
            "qty,verdict,reject_reason) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (str(day), sig.symbol, sig.setup, sig.spec_version, sig.entry, sig.stop,
             sig.target, sig.rr, qty, "reject" if reject else "accept", reject))
        self.db.commit()
        return cur.lastrowid

    def fill(self, signal_id, day, symbol, side, qty, price, slippage, costs, reason):
        self.db.execute(
            "INSERT INTO fills(signal_id,day,symbol,side,qty,price,slippage,costs,reason)"
            " VALUES(?,?,?,?,?,?,?,?,?)",
            (signal_id, str(day), symbol, side, qty, price, slippage, costs, reason))
        self.db.commit()

    def open_position(self, spec_hash, sig: Signal, signal_day, qty, max_bars) -> int:
        cur = self.db.execute(
            "INSERT INTO positions(spec_hash,symbol,setup,status,signal_day,qty,"
            "stop,target,max_bars) VALUES(?,?,?,'pending',?,?,?,?,?)",
            (spec_hash, sig.symbol, sig.setup, str(signal_day), qty,
             sig.stop, sig.target, max_bars))
        self.db.commit()
        return cur.lastrowid

    def positions(self, status):
        self.db.row_factory = sqlite3.Row
        rows = self.db.execute(
            "SELECT * FROM positions WHERE status=?", (status,)).fetchall()
        self.db.row_factory = None
        return [dict(r) for r in rows]

    def fill_entry(self, pid, day, px):
        self.db.execute("UPDATE positions SET status='open', entry_day=?, entry_px=?"
                        " WHERE id=?", (str(day), px, pid))
        self.db.commit()

    def close_position(self, pid, day, px, reason, net):
        self.db.execute("UPDATE positions SET status='closed', exit_day=?, exit_px=?,"
                        " exit_reason=?, net=? WHERE id=?",
                        (str(day), px, reason, net, pid))
        self.db.commit()

    def expire_position(self, pid, day):
        self.db.execute("UPDATE positions SET status='expired', exit_day=?"
                        " WHERE id=?", (str(day), pid))
        self.db.commit()

    def realised_pnl(self) -> float:
        r = self.db.execute(
            "SELECT COALESCE(SUM(net),0) FROM positions WHERE status='closed'").fetchone()
        return r[0]

    def reject_counts(self):
        return dict(self.db.execute(
            "SELECT reject_reason, COUNT(*) FROM signals WHERE reject_reason IS NOT NULL"
            " GROUP BY reject_reason ORDER BY 2 DESC").fetchall())


# Public names this module keeps WITHOUT the live path reaching them. Named
# here, with a reason, so the exclusion has to be defended -- the same idiom
# run_selftests.py uses for modules it skips. A new unreachable name fails the
# census below rather than quietly joining the four risk constants that spent
# months reading as policy.
KNOWN_UNREACHED = {
    "Signal": "the signal record the Journal stores; kept with Journal",
    "Journal": "sqlite trade journal for a live execution path that does not "
               "exist yet. positions.py is the forward book; this is unused "
               "scaffolding and should be deleted or adopted, not left drifting",
    "DEFAULT_COSTS": "module-level default for Costs; simulate builds its own",
    "slippage_bps": "linear slippage, superseded by impact_pct's sqrt form",
    "entry_fill": "simulate.py implements its own gap-aware fills (L98/L99)",
    "stop_fill": "as entry_fill",
    "target_fill": "as entry_fill",
}


def _reachability():
    """-> {public name: files outside engine.py that reference it}.

    Counts `engine.NAME` and `from engine import NAME` in code, with comments
    stripped -- a name that appears only in prose is not reached. This exists
    because a census run by hand on 2026-09-12 found MIN_RR, RR_EPS,
    MAX_ADV_PARTICIPATION, MAX_PORTFOLIO_HEAT, RISK_PER_TRADE and MAX_COST_RATIO
    all at zero callers while the module docstring called them invariants.
    """
    import ast as _ast, re as _re
    me = Path(__file__).resolve()
    tree = _ast.parse(me.read_text())
    names = []
    for n in tree.body:
        if isinstance(n, (_ast.FunctionDef, _ast.ClassDef)):
            names.append(n.name)
        elif isinstance(n, _ast.Assign):
            names += [t.id for t in n.targets
                      if isinstance(t, _ast.Name) and t.id.isupper()]
    # The census's own machinery is not part of the surface it measures. (The
    # first run flagged KNOWN_UNREACHED itself, which is correct and useless.)
    names = [n for n in dict.fromkeys(names)
             if not n.startswith("_") and n != "KNOWN_UNREACHED"]

    roots = [ROOT / "src", ROOT / "tests"]
    files = [p for r in roots if r.exists() for p in r.rglob("*.py")
             if p.resolve() != me]
    out = {}
    for nm in names:
        hits = set()
        for p in files:
            for line in p.read_text(errors="replace").splitlines():
                code = line.split("#", 1)[0]
                if _re.search(rf"\bengine\.{nm}\b", code) or (
                        _re.search(r"from\s+engine\s+import", code)
                        and _re.search(rf"\b{nm}\b", code)):
                    hits.add(p.name)
                    break
        out[nm] = sorted(hits)
    return out


def _selftest_reachable():
    reach = _reachability()
    dead = {n for n, hits in reach.items() if not hits}
    undocumented = dead - set(KNOWN_UNREACHED)
    assert not undocumented, (
        "engine.py grew a public name nothing outside it reaches: "
        + ", ".join(sorted(undocumented))
        + ". Either wire it into the live path, delete it, or add it to "
          "KNOWN_UNREACHED with a reason. An unreachable constant in THIS file "
          "reads as risk policy and is not.")
    stale = set(KNOWN_UNREACHED) - dead
    assert not stale, ("KNOWN_UNREACHED lists names that ARE now reached, so "
                       "the exclusion is protecting nothing: "
                       + ", ".join(sorted(stale)))
    # the live surface, asserted positively so a rename cannot silently shrink it
    for must in ("Costs", "impact_pct", "IMPACT_C"):
        assert reach.get(must), f"{must} is engine's live surface and is unreached"
    print(f"engine.reachability selftest ok "
          f"({len(reach) - len(dead)} live, {len(dead)} documented unreached)")


def _selftest():
    from universe import Bar
    d = date(2026, 1, 1)

    def bar(o, h, l, c, turnover=1e9, **kw):
        return Bar(symbol="X", day=d, open=o, high=h, low=l, close=c,
                   prev_close=o, volume=100000, turnover=turnover,
                   deliv_qty=50000, deliv_pct=50.0, **kw)

    eq, clean = 1_000_000.0, bar(100, 105, 99, 104)

    # The gate assertions that stood here -- the R:R floor, RR_EPS float dust,
    # surveillance flags, the liquidity cap, portfolio heat and the 1-share
    # viability case -- went with `gate()`. They were thorough, they passed, and
    # they were the ONLY caller: the function was alive inside its own test and
    # dead everywhere else, which is precisely what kept it looking maintained.
    # A test is not evidence that code runs in production.
    # --- fills: the gap cases are the whole point --------------------------
    assert entry_fill(100, bar(98, 105, 97, 104)) == 100      # trades through -> trigger
    assert entry_fill(100, bar(103, 106, 102, 105)) == 103    # gaps past -> open, worse
    assert entry_fill(100, bar(95, 99, 94, 98)) is None       # never reached
    assert entry_fill(100, bar(101, 101, 101, 101)) is None   # locked

    assert stop_fill(90, bar(95, 96, 88, 89)) == 90           # trades down -> stop
    assert stop_fill(90, bar(85, 87, 84, 86)) == 85           # gaps below -> open, worse
    assert stop_fill(90, bar(95, 96, 91, 92)) is None         # never hit

    assert target_fill(130, bar(125, 132, 124, 131)) == 130   # trades up -> target
    assert target_fill(130, bar(135, 138, 134, 137)) == 135   # gaps above -> open, better
    assert target_fill(130, bar(120, 128, 119, 127)) is None  # never reached
    assert target_fill(130, bar(131, 131, 131, 131)) is None  # locked

    # --- costs: asymmetric, and which side is dearer depends on SIZE -------
    # Buy pays stamp duty, which scales with value. Sell pays the depository
    # charge, which is flat. So buy costs more only above the crossover at
    # stamp_buy * value == dp_sell; below it the fixed charge dominates. The
    # earlier test asserted buy > sell unconditionally, which was true only
    # while DP charges were missing from the model entirely.
    c = Costs()
    cross = c.dp_sell / c.stamp_buy                      # ~Rs 1.06 lakh
    big_b, big_s = c.charge(cross * 3, "BUY"), c.charge(cross * 3, "SELL")
    assert big_b > big_s, (big_b, big_s)
    small_b, small_s = c.charge(cross / 10, "BUY"), c.charge(cross / 10, "SELL")
    assert small_s > small_b, (small_b, small_s)
    assert 100 < c.charge(100_000, "BUY") < 200, c.charge(100_000, "BUY")
    # fixed charges must make small trades proportionally far more expensive
    assert (c.charge(5_000, "BUY") + c.charge(5_000, "SELL")) / 5_000 > \
           4 * (c.charge(200_000, "BUY") + c.charge(200_000, "SELL")) / 200_000

    # --- journal round-trip ------------------------------------------------
    j = Journal(":memory:")
    rejected = Signal("X", "vcp", entry=100, stop=90, target=129.9)
    sid = j.signal(d, rejected, 0, "rr_below_3.0")
    j.fill(sid, d, "X", "BUY", 10, 100.0, 0.5, 25.0, "entry")
    assert j.reject_counts() == {"rr_below_3.0": 1}

    # position lifecycle: pending -> open -> closed
    good = Signal("Y", "stage2", 100.0, 90.0, 130.0)
    pid = j.open_position("h0", good, d, 50, 30)
    assert [p["symbol"] for p in j.positions("pending")] == ["Y"]
    j.fill_entry(pid, d, 101.0)
    assert j.positions("pending") == [] and len(j.positions("open")) == 1
    j.close_position(pid, d, 130.0, "target", 1400.0)
    assert j.positions("open") == [] and j.realised_pnl() == 1400.0
    # and the expiry branch
    pid2 = j.open_position("h0", good, d, 50, 30)
    j.expire_position(pid2, d)
    assert j.positions("pending") == [] and j.realised_pnl() == 1400.0
    _selftest_reachable()
    print("engine selftest ok")


if __name__ == "__main__":
    _selftest()
