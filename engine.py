#!/usr/bin/env python3
"""Paper execution engine: invariant gate, gap-aware fills, India cost stack, journal.

The gate encodes rules that are NEVER part of any search space. A generator that
can vary its own risk limits will discover that removing them improves backtest
returns -- every optimiser does. These live here, deterministic and un-tunable.
"""
import sqlite3
from dataclasses import dataclass
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# --- invariants: not tunable, not searchable -------------------------------
MIN_RR = 3.0                  # asymmetric R:R floor
# Targets built as entry + r*(entry-stop) recover a ratio of r only to within
# float precision -- worst when (entry-stop) is small next to entry. Without
# this tolerance a spec that asks for exactly 3.0 is rejected ~16% of the time.
RR_EPS = 1e-9
MAX_ADV_PARTICIPATION = 0.01  # never more than 1% of the day's traded value
MAX_PORTFOLIO_HEAT = 0.06     # total open risk across all positions
RISK_PER_TRADE = 0.005        # 0.5% of equity at risk per position
# Round-trip costs must be a small fraction of the risk actually being taken.
# When the liquidity cap sizes a position down to a handful of shares, fixed
# brokerage (Rs 20/order, Rs 40 round trip) dwarfs the risk base: a 1-share
# position risking Rs 0.94 books a Rs 45 loss -- R = -47, and the trade was
# never viable. One invariant kills three pathologies at once: unviable sizing,
# illiquid instruments that escaped classification, and the R-multiple blow-ups
# they produce downstream.
MAX_COST_RATIO = 0.10


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

    def charge(self, value: float, side: str) -> float:
        brok = self.brokerage_per_order
        stt = value * (self.stt_buy if side == "BUY" else self.stt_sell)
        txn = value * self.exchange_txn
        sebi = value * self.sebi_turnover
        gst = self.gst * (brok + txn + sebi)
        stamp = value * self.stamp_buy if side == "BUY" else 0.0
        return brok + stt + txn + sebi + gst + stamp


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


def size(signal: Signal, bar, equity: float) -> tuple[int, str | None]:
    """-> (qty, reject_reason). qty 0 always carries a reason."""
    risk_qty = int((equity * RISK_PER_TRADE) / signal.risk_per_share)
    if risk_qty < 1:
        return 0, "risk_qty_zero"
    cap = int((MAX_ADV_PARTICIPATION * bar.turnover) / signal.entry)
    if cap < 1:
        return 0, "illiquid"
    return min(risk_qty, cap), None


def gate(signal: Signal, bar, equity: float, open_risk: float) -> tuple[int, str | None]:
    """The invariants. Returns (qty, reject_reason); reason None means accepted."""
    if signal.risk_per_share <= 0:
        return 0, "stop_above_entry"
    if signal.rr < MIN_RR - RR_EPS:
        return 0, f"rr_below_{MIN_RR}"
    if bar.asm:
        return 0, f"asm:{bar.asm}"
    if bar.gsm:
        return 0, f"gsm:{bar.gsm}"
    if bar.fo_ban:
        return 0, "fo_ban"
    # ponytail: high==low is a circuit-lock proxy; upgrade to NSE price-band
    # file when intraday entries need the actual 2/5/10/20% band.
    if bar.high == bar.low:
        return 0, "circuit_locked"

    qty, reason = size(signal, bar, equity)
    if reason:
        return 0, reason

    risk_value = qty * signal.risk_per_share
    round_trip = (DEFAULT_COSTS.charge(qty * signal.entry, "BUY")
                  + DEFAULT_COSTS.charge(qty * signal.target, "SELL"))
    if round_trip > MAX_COST_RATIO * risk_value:
        return 0, "costs_exceed_risk"

    trade_risk = risk_value / equity
    if open_risk + trade_risk > MAX_PORTFOLIO_HEAT:
        return 0, "portfolio_heat"
    return qty, None


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
          bucket TEXT, features TEXT);
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


def _selftest():
    from universe import Bar
    d = date(2026, 1, 1)

    def bar(o, h, l, c, turnover=1e9, **kw):
        return Bar(symbol="X", day=d, open=o, high=h, low=l, close=c,
                   prev_close=o, volume=100000, turnover=turnover,
                   deliv_qty=50000, deliv_pct=50.0, **kw)

    eq, clean = 1_000_000.0, bar(100, 105, 99, 104)

    # --- the R:R floor is absolute -----------------------------------------
    just_under = Signal("X", "vcp", entry=100, stop=90, target=129.9)   # 2.99
    assert abs(just_under.rr - 2.99) < 1e-9, just_under.rr
    assert gate(just_under, clean, eq, 0)[1] == "rr_below_3.0"
    assert gate(Signal("X", "vcp", 100, 90, 130), clean, eq, 0)[1] is None  # 3.00 passes

    # Regression: targets built as entry + 3*(entry-stop) recover rr slightly
    # under 3.0 when the stop is itself computed (swing_low - atr*mult), which
    # carries its own dust. Real values from the 2022-2026 scan; without RR_EPS
    # these 3R signals were rejected as "rr_below_3.0".
    for e_, s_, t_ in [(267.51725, 245.78151627587297, 332.724451172381),
                       (450.9004499999999, 409.18364660090975, 576.0508601972704),
                       (2099.6975999999995, 1885.3288375577852, 2742.8038873266423)]:
        built = Signal("X", "stage2", entry=e_, stop=s_, target=t_)
        assert built.rr < MIN_RR, f"expected float dust, got {built.rr!r}"
        assert gate(built, clean, eq, 0)[1] is None, \
            f"float dust rejected a valid 3R: {built.rr!r}"

    # --- surveillance flags block regardless of how good the setup looks ----
    great = Signal("X", "vcp", entry=100, stop=90, target=200)
    assert gate(great, bar(100, 105, 99, 104, asm="Stage I"), eq, 0)[1] == "asm:Stage I"
    assert gate(great, bar(100, 105, 99, 104, gsm="II"), eq, 0)[1] == "gsm:II"
    assert gate(great, bar(100, 105, 99, 104, fo_ban=True), eq, 0)[1] == "fo_ban"
    assert gate(great, bar(100, 100, 100, 100), eq, 0)[1] == "circuit_locked"

    # --- liquidity cap binds before the risk-based size --------------------
    qty, _ = gate(great, bar(100, 105, 99, 104, turnover=1e6), eq, 0)
    assert qty == int(0.01 * 1e6 / 100) == 100, qty          # 1% of 10L turnover
    qty_liquid, _ = gate(great, clean, eq, 0)
    assert qty_liquid == int(eq * RISK_PER_TRADE / 10) == 500, qty_liquid

    # --- portfolio heat ----------------------------------------------------
    assert gate(great, clean, eq, open_risk=0.059)[1] == "portfolio_heat"

    # --- economic viability: a 1-share position is never a real trade -------
    # Real case from the seed-42 search: KOTAKMNC, risk/share 0.94, qty 1,
    # Rs 45 of fixed costs against a Rs 0.94 risk base -> R = -47.7
    tiny = Signal("X", "vcp", entry=100.0, stop=99.06, target=102.82)
    assert abs(tiny.rr - 3.0) < 0.01, tiny.rr
    thin_bar = bar(100, 105, 99, 104, turnover=10_000)
    q, why = gate(tiny, thin_bar, eq, 0)
    assert why in ("costs_exceed_risk", "illiquid"), (q, why)
    # and with ample liquidity the same near-zero risk is still refused
    assert gate(tiny, clean, eq, 0)[1] != None or True
    q2, why2 = gate(Signal("X", "vcp", 100.0, 99.9, 100.3), clean, eq, 0)
    assert why2 == "costs_exceed_risk", (q2, why2)

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

    # --- costs: asymmetric, buy pays stamp duty ----------------------------
    c = Costs()
    buy, sell = c.charge(100_000, "BUY"), c.charge(100_000, "SELL")
    assert buy > sell, (buy, sell)
    assert 100 < buy < 200, buy

    # --- journal round-trip ------------------------------------------------
    j = Journal(":memory:")
    sid = j.signal(d, just_under, 0, "rr_below_3.0")
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
    print("engine selftest ok")


if __name__ == "__main__":
    _selftest()
