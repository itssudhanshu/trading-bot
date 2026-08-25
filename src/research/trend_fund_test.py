#!/usr/bin/env python3
"""Can a trend-following book trade NSE ETFs/funds profitably -- and is the
ABSOLUTE TREND CONDITION the reason, or just the 2025 metals rally?

PRE-REGISTERED BEFORE RUNNING (experiment skill): hypothesis, universe,
rule shapes, control, endpoints and promotion bar are fixed below. Nothing
here may be re-tuned after seeing results; a miss is recorded as a miss.

WHY NOW. The operator watched metals ETFs double while equities fell
(2024-11-28..2026-08-24: gold+silver basket +127.6%, equities -4.6%, split-
adjusted equal-weight baskets). Over the FULL span equities beat every fund
group (+29.4%/yr vs +24.9% metals), so "ETFs are good" is one regime, not a
fact. This test asks whether an absolute trend rule earns money ACROSS regimes
-- including by sitting out downtrends -- or only rode silver.

HYPOTHESIS (the thing being measured). Among liquid NSE funds, entering names
that are above their 200-session average AND up over their trailing ~6 months,
exiting on a trend break (close below the 100-session average) or a -10% stop,
earns positive per-trade returns after real charges and impact -- AND beats
the same rotation WITHOUT the absolute-trend condition.

CONTROL (named before the run, per skill). The control is the SAME book minus
the trend idea: cross-sectional momentum rotation with no entry gate -- always
holding the strongest recent movers among liquid funds, stopped only at -10%.

AMENDMENT (20260824-trendfund2, recorded BEFORE the re-run). Batch
20260824-trendfund1 implemented that control WITHOUT any rotation: no gate and
no trend exit meant no sell path at all, so the control bought five seats in
week one and held them for 6.8 years -- occupancy 4.99, ONE closed trade. That
is not the registered design ("always holding THE STRONGEST recent movers"
implies rotating out of names that stop being strongest), so the control now
exits a position the day it leaves the top-5 ranking ("rotate"). No bar moved;
the fix brings the code up to the registration instead of the reverse.

UNIVERSE (fixed).
  every symbol in universe.non_equity_symbols() (still-trading funds come in
  through the snapshot union, delisted ones through data/non_equity_history)
  with OHLCV built from the raw bhavcopies;
  eligible on a date iff: >= 200 prior sessions, no |daily move| > 40% inside
  its last 200 sessions (raw closes carry unadjusted unit splits; the largest
  REAL fund day observed is -23%), median daily turnover over its trailing 250
  sessions ranks among the TOP 40 funds that date. The count 40 was chosen
  before running as "the liquid core", not fitted.

RULES (shapes fixed, values conventional -- none searched).
  signal daily, fill next open (shared engine path);
  gate      close > SMA200 and ret(close, 125) > 0
  rank      ret(close, 125), best first
  seats     5, equal sizing, Rs 3,00,000 capital, 75% deployment cap,
            risk-based qty then Rs-per-name cap exactly as breakout sizes;
  exits     -10% hard stop; trend break: close < SMA100, sold at that close;
            no profit target (a target would be a second, untuned rule);
  refresh   every 5 sessions, like the sibling books;
  impact    engine.IMPACT_C read live (=1.0), both sides;
  tax       the engine's STCG convention (20% on net FY gains) applied as an
            approximation; fund taxation actually differs by asset class and
            this test does not pretend otherwise.

ENDPOINTS AND PROMOTION BAR (set before the run; criteria may be tightened
later, never loosened).
  primary   per-trade mean(treatment) - mean(control) +/- std err, Welch t.
  Phase 2 (a src/strategies/trend/ paper book) may be designed ONLY if ALL of:
    a) full-period |t| >= 2 AND treatment per-trade mean > 0;
    b) the edge is DIRECTIONALLY positive pooled across the three PRE-2024-11
       blocks -- a win that exists only in the metals window is the regime,
       not a rule, and gets reported as such;
    c) treatment worst single block better than -3% per trade.
  Anything less: record the null in docs/lessons.md, do not build the book.

REPORTING. Per regime block (L61's four cuts) and per asset group
(metals / index-sector / bond-liquid), n next to every figure, mean +/- std err
and t for every comparison. Never one blended number.

RUNS UNDER THE ACTIVE STRATEGY'S PATH BUT WRITES NOTHING: simulate.store()
would append to data/<active>/simulations.jsonl, so this file reports to
stdout only.

    python3 src/research/trend_fund_test.py            # the measurement
    python3 src/research/trend_fund_test.py --selftest # mechanics on fixtures
"""

import sys as _sys, pathlib as _pl
_sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[1]))
import paths  # noqa: F401  -- puts the source dirs on sys.path

import csv
import io
import json
import statistics
import sys
from datetime import date

import engine
import features
import universe
from paths import RAW

BATCH = "20260824-trendfund2"

# --- the pre-registration, as code ------------------------------------------
CAPITAL = 300_000
MAX_POSITIONS = 5
DEPLOY_PCT = 75.0        # share of capital ever deployed, as the siblings
RISK_PCT = 2.0           # of capital at risk per position, as the siblings
STOP_PCT = 10.0
REFRESH = 5
UNIVERSE_TOP = 40        # liquid core: top funds by as-of median turnover
LIQ_WINDOW = 250         # sessions behind the turnover median
HISTORY_MIN = 200        # sessions of history before any eligibility
TREND_SMA = 200          # entry gate: close above this average
MOM_WINDOW = 125         # entry gate & rank: ~6-month return
EXIT_SMA = 100           # trend-break exit: close below this average
SPLIT_JUMP = 40.0        # % one-day move that means corporate action
SPLIT_LOOKBACK = 200     # sessions a split keeps a fund ineligible
NEVER_TARGET = 1e9       # disables the profit target entirely
NEVER_HOLD = 10**6       # disables the flat time exit entirely

BLOCKS = (("2019-10-01", "2021-06-18"), ("2021-06-21", "2023-03-03"),
          ("2023-03-06", "2024-11-27"), ("2024-11-28", "2999-12-31"))


def asset_group(sym):
    if "GOLD" in sym:
        return "metals"
    if "SILVER" in sym or "SILVE" in sym:
        return "metals"
    if any(t in sym for t in ("GILT", "GSEC", "SDL", "LIQUID", "EBBETF", "LIQ")):
        return "bond"
    return "index"


def fund_corpus():
    """-> (corpus, days) for every classified non-equity symbol, OHLCV.

    Mirrors universe.load()'s column handling (turnover in lacs -> rupees),
    but KEEPS the funds instead of dropping them. Surveillance sidecars are
    parsed for completeness; a fund flagged restricted is skipped in build().
    """
    deny = universe.non_equity_symbols()
    raw = {s: features.Series(s) for s in deny}
    for p in sorted(RAW.glob("*/bhavcopy_delivery.csv")):
        day_s = p.parent.name
        day = date.fromisoformat(day_s)
        sv_known = (RAW / day_s / "asm.json").exists()
        for r in csv.DictReader(io.StringIO(p.read_text(errors="replace")),
                                skipinitialspace=True):
            sym = (r.get("SYMBOL") or "").strip()
            if sym not in raw:
                continue
            if (r.get("SERIES") or "").strip() not in universe.TRADEABLE_SERIES:
                continue
            try:
                o, h = float(r["OPEN_PRICE"]), float(r["HIGH_PRICE"])
                lo, c = float(r["LOW_PRICE"]), float(r["CLOSE_PRICE"])
                vol = int(float(r.get("TTL_TRD_QNTY") or 0))
                to = float(r.get("TURNOVER_LACS") or 0) * 100_000
            except (KeyError, ValueError):
                continue
            if not (o > 0 and h > 0 and lo > 0 and c > 0):
                continue
            s = raw[sym]
            s.days.append(day)
            s.open.append(o)
            s.high.append(h)
            s.low.append(lo)
            s.close.append(c)
            s.volume.append(vol)
            s.turnover.append(to)
            try:
                s.deliv_pct.append(float((r.get("DELIV_PER") or "").strip()))
            except ValueError:
                s.deliv_pct.append(None)
            s.surveillance_known.append(sv_known)
            s.restricted.append(False)
    corpus = {s: v for s, v in raw.items() if len(v) >= HISTORY_MIN + 1}
    days = sorted({d for s in corpus.values() for d in s.days})
    return corpus, days


def _sma(vals, i, n):
    if i + 1 < n:
        return None
    return sum(vals[i - n + 1:i + 1]) / n


def _ret(vals, i, n):
    j = i - n
    if j < 0 or not vals[j]:
        return None
    return vals[i] / vals[j] - 1.0


def _split_recent(s, i):
    """True if a corporate-action-sized jump sits inside the lookback."""
    for k in range(max(1, i - SPLIT_LOOKBACK), i + 1):
        prev = s.close[k - 1]
        if prev and abs(s.close[k] / prev - 1.0) * 100 > SPLIT_JUMP:
            return True
    return False


def _eligible(corpus, as_of):
    """-> [(sym, median_turnover)] passing history, split and liquidity gates."""
    med = []
    for sym, s in corpus.items():
        i = s.index_of(as_of)
        if i is None or i < HISTORY_MIN:
            continue
        if _split_recent(s, i):
            continue
        window = [x for x in s.turnover[max(0, i - LIQ_WINDOW + 1):i + 1] if x > 0]
        if len(window) < LIQ_WINDOW // 2:
            continue
        med.append((statistics.median(window), sym))
    med.sort(reverse=True)
    return med[:UNIVERSE_TOP]


class FundSelection:
    """The `selection` seam simulate.run() plugs into. `gated` switches the
    ABSOLUTE TREND condition off for the control arm -- the two arms differ in
    this one flag and nothing else."""

    gated = True
    CAPITAL = CAPITAL

    @classmethod
    def build(cls, corpus, as_of, capital=CAPITAL, trigger=None):
        rows = []
        for liq, sym in _eligible(corpus, as_of):
            s = corpus[sym]
            i = s.index_of(as_of)
            if i is None or i + 1 >= len(s):
                continue
            if (i < len(s.restricted) and s.restricted[i]
                    and i < len(s.surveillance_known) and s.surveillance_known[i]):
                continue
            if s.high[i] == s.low[i]:
                continue    # band-locked bar: no next-open fill exists (L58)
            mom = _ret(s.close, i, MOM_WINDOW)
            if mom is None:
                continue
            if cls.gated:
                sma = _sma(s.close, i, TREND_SMA)
                if sma is None or s.close[i] <= sma or mom <= 0:
                    continue
            rows.append({"symbol": sym, "cluster": asset_group(sym),
                         "score": round(mom * 100, 2),
                         "detail": {"liq": round(liq)}})
        rows.sort(key=lambda r: -r["score"])
        return rows

    @staticmethod
    def allocate(rows, take_per_cluster=None, offset=0, max_pos=MAX_POSITIONS):
        del take_per_cluster, offset
        return rows[:max_pos]

    @staticmethod
    def decorrelate(rows, corpus, as_of, max_corr):
        del corpus, as_of, max_corr
        return rows

    @staticmethod
    def size_mult(scheme, rank, vol_pct, med_vol_pct):
        del rank, vol_pct, med_vol_pct
        return 1.0 if scheme in (None, "", "equal") else 1.0

    @classmethod
    def position_size(cls, capital, entry, stop_pct=STOP_PCT, mult=1.0,
                      max_pos=MAX_POSITIONS):
        risk_rupees = capital * RISK_PCT / 100
        risk_per_share = entry * stop_pct / 100
        if risk_per_share <= 0:
            return 0, 0.0
        qty = int(risk_rupees / risk_per_share)
        cap_value = capital * DEPLOY_PCT / 100 / max_pos * mult
        qty = min(qty, int(cap_value / entry))
        return qty, qty * risk_per_share


def trend_exit(s, i, pos, held, hold):
    """Shared-engine hook: sell at today's close once it loses its SMA100."""
    del pos, held, hold
    sma = _sma(s.close, i, EXIT_SMA)
    if sma is not None and s.close[i] < sma:
        return "trend"
    return False


def _welch(a, b):
    """-> (diff, stderr, t) of per-trade means, a minus b."""
    if len(a) < 2 or len(b) < 2:
        return None
    ma, mb = statistics.fmean(a), statistics.fmean(b)
    va = statistics.pvariance(a) / len(a)
    vb = statistics.pvariance(b) / len(b)
    se = (va + vb) ** 0.5
    return ma - mb, se, ((ma - mb) / se if se > 0 else float("nan"))


def _arm_stats(trades):
    if not trades:
        return {"n": 0}
    rets = [t["ret"] for t in trades]
    return {
        "n": len(rets),
        "mean": statistics.fmean(rets),
        "se": (statistics.pvariance(rets) / len(rets)) ** 0.5,
        "win": sum(1 for r in rets if r > 0) / len(rets) * 100,
    }


def make_rotate(corpus):
    """The control's exit: a position leaves when it is no longer among the
    strongest MAX_POSITIONS names by the same ranking its entries used.

    trendfund1's control had no sell path at all and held five seats for 6.8
    years; this is what "always holding THE STRONGEST movers" always meant."""
    cache = {}

    def top_syms(day):
        if day not in cache:
            rows = FundSelection.build(corpus, day)
            cache[day] = [r["symbol"] for r in rows[:MAX_POSITIONS]]
        return cache[day]

    def rotate(s, i, pos, held, hold):
        del held, hold
        if pos["sym"] not in top_syms(s.days[i]):
            return "rotate"
        return False

    return rotate


def run_arms(corpus, days):
    import simulate
    saved = simulate.selection
    rotate = make_rotate(corpus)
    out = {}
    try:
        for name, gated, te in (("treatment", True, trend_exit),
                                ("control", False, rotate)):
            FundSelection.gated = gated
            simulate.selection = FundSelection
            out[name] = simulate.run(
                corpus, days, stop_pct=STOP_PCT, target_pct=NEVER_TARGET,
                hold=NEVER_HOLD, max_pos=MAX_POSITIONS, refresh=REFRESH,
                capital=CAPITAL, trigger="none", impact_c=engine.IMPACT_C,
                time_exit=te)
    finally:
        simulate.selection = saved
        FundSelection.gated = True
    return out


def report(out):
    tr, ct = out["treatment"], out["control"]
    for name, r in (("TREATMENT trend-gated", tr), ("CONTROL momentum-only", ct)):
        st = _arm_stats(r["trades"])
        if not st["n"]:
            print(f"{name:<24} no trades")
            continue
        print(f"\n{name:<24} CAGR {r['cagr']:+7.2f}%  DD {r['maxdd']:5.1f}%  "
              f"n={st['n']:>4}  win {st['win']:3.0f}%  "
              f"+{st['mean']:.2f}% +/- {st['se']:.2f}% per trade  "
              f"occ {r['occupancy']:.2f}/{MAX_POSITIONS}")
    d = _welch([t["ret"] for t in tr["trades"]],
               [t["ret"] for t in ct["trades"]])
    if d:
        print(f"\nEDGE treatment-control: {d[0]:+.2f}% +/- {d[1]:.2f}%  "
              f"t = {d[2]:+.2f}")

    print("\nPer regime block (exit-day blocks, L61 cuts):")
    hdr = f"  {'block':<26}{'tr n':>6}{'tr %':>8}{'ct n':>6}{'ct %':>8}{'edge':>8}{'t':>7}"
    print(hdr)
    block_edges = []
    for lo, hi in BLOCKS:
        tag = f"{lo}..{hi}"
        bt = [t for t in tr["trades"] if lo <= str(t["day"]) <= hi]
        bc = [t for t in ct["trades"] if lo <= str(t["day"]) <= hi]
        st_t, st_c = _arm_stats(bt), _arm_stats(bc)
        w = _welch([t["ret"] for t in bt], [t["ret"] for t in bc])
        if w:
            block_edges.append(w)
        fmt = lambda st: (f"{st['n']:>6}{st['mean']:>8.2f}" if st["n"]
                          else f"{'--':>6}{'--':>8}")
        print(f"  {tag:<26}{fmt(st_t)}{fmt(st_c)}"
              + (f"{w[0]:>+8.2f}{w[2]:>+7.2f}" if w else f"{'--':>8}{'--':>7}"))

    print("\nPer asset group (treatment trades):")
    groups = {}
    for t in tr["trades"]:
        groups.setdefault(asset_group(t["sym"]), []).append(t["ret"])
    for g, rets in sorted(groups.items()):
        print(f"  {g:<8} n={len(rets):>4}  "
              f"+{statistics.fmean(rets):.2f}% +/- "
              f"{(statistics.pvariance(rets)/len(rets))**0.5:.2f}%")

    # The pre-registered promotion bar, evaluated mechanically.
    full = _welch([t["ret"] for t in tr["trades"]],
                  [t["ret"] for t in ct["trades"]]) if tr["trades"] and ct["trades"] else None
    early = [w for w, (lo, hi) in zip(block_edges, BLOCKS) if hi <= "2024-11-27"]
    worst_block = min((w[0] for w in block_edges), default=float("nan"))
    checks = {
        "a) full-period |t|>=2 and treatment mean>0":
            bool(full) and abs(full[2]) >= 2
            and statistics.fmean([t["ret"] for t in tr["trades"]]) > 0,
        "b) direction positive in blocks 1-3 pooled":
            bool(early) and statistics.fmean([w[0] for w in early]) > 0,
        "c) worst block better than -3%/trade":
            worst_block > -3.0,
    }
    print("\nPROMOTION BAR (pre-registered):")
    for k, ok in checks.items():
        print(f"  [{'x' if ok else ' '}] {k}")
    verdict = all(checks.values())
    print(f"\nVERDICT: {'PROCEED to phase 2 design' if verdict else 'NULL -- do not build'}")
    return verdict


def main(argv):
    if "--selftest" in argv:
        _selftest()
        return 0
    corpus, days = fund_corpus()
    print(f"TREND-FUND TEST  batch {BATCH}\n"
          f"funds with enough history: {len(corpus)}   "
          f"days {days[0]}..{days[-1]}  ({len(days)} sessions)\n")
    out = run_arms(corpus, days)
    verdict = report(out)

    # A result nobody recorded gets re-decided differently. One line, the
    # headline numbers only; the reasoning lives in docs/lessons.md afterwards.
    log = paths.DATA / "research"
    log.mkdir(exist_ok=True)
    row = {"batch": BATCH, "proceed": verdict}
    for name, r in out.items():
        st = _arm_stats(r["trades"])
        row[name] = {"cagr": round(r["cagr"], 2), "dd": round(r["maxdd"], 1),
                     "n": st["n"],
                     "per_trade": (round(st["mean"], 2), round(st["se"], 2))
                     if st["n"] else None}
    (log / "trend_fund_test.jsonl").open("a").write(json.dumps(row) + "\n")
    print(f"\nappended summary to {log / 'trend_fund_test.jsonl'}")
    return 0


def _selftest():
    global MAX_POSITIONS
    """Mechanics on synthetic fixtures, not market claims.

    Four funds, one path: UP trends above every average, DOWN trends below
    every average, SPLIT carries a -90% corporate-action jump, DEAD barely
    moves. Assert the properties the pre-registration depends on:
    eligibility gates, the gate's selectivity between arms, and the trend exit.
    """
    from datetime import timedelta
    d0 = date(2024, 1, 1)
    days = [d0 + timedelta(days=k) for k in range(HISTORY_MIN + 80)]
    n = len(days)

    def mk(sym, px_fn):
        s = features.Series(sym)
        for k, d in enumerate(days):
            px = px_fn(k)
            s.days.append(d)
            s.open.append(px)
            s.high.append(px * 1.001)
            s.low.append(px * 0.999)
            s.close.append(px)
            s.volume.append(1000)
            s.turnover.append(1e7)
            s.deliv_pct.append(50.0)
            s.surveillance_known.append(True)
            s.restricted.append(False)
        return s

    up = mk("UPBEES", lambda k: 100.0 * (1.0 + 0.002 * k))
    dn = mk("DNBEES", lambda k: 200.0 * (1.0 - 0.002 * k))

    def spl(k):
        px = 100.0 + 0.001 * k
        if k == HISTORY_MIN + 10:
            px *= 0.10                      # a unit split, unadjusted
        return px

    sp = mk("SPLITETF", spl)
    dd = mk("LIQUID1", lambda k: 100.0)
    corpus = {s.symbol: s for s in (up, dn, sp, dd)}

    last = days[-2]    # a signal needs a NEXT open to fill into
    eli = dict((sym, liq) for liq, sym in _eligible(corpus, last))
    assert "UPBEES" in eli and "DNBEES" in eli and "LIQUID1" in eli, eli
    assert "SPLITETF" not in eli, "a fund with a fresh split must sit out"

    gated = FundSelection.build(corpus, last)
    names = [r["symbol"] for r in gated]
    assert names == ["UPBEES"], names
    FundSelection.gated = False
    ctrl = [r["symbol"] for r in FundSelection.build(corpus, last)]
    FundSelection.gated = True
    assert "DNBEES" in ctrl, "without the gate the control must hold the downtrend"

    alloc = FundSelection.allocate(gated, max_pos=5)
    assert [r["symbol"] for r in alloc] == ["UPBEES"]
    qty, risk = FundSelection.position_size(CAPITAL, 100.0)
    assert qty == min(int(CAPITAL * RISK_PCT / 100 / 10.0),
                      int(CAPITAL * DEPLOY_PCT / 100 / MAX_POSITIONS / 100.0)), qty
    assert risk == qty * 10.0

    # Trend exit: below its own SMA100 the hook must name the exit.
    crash = mk("CRASHBEE", lambda k: 150.0 - (k - HISTORY_MIN) * 0.5
               if k > HISTORY_MIN else 100.0 + 0.05 * k)
    ci = len(crash) - 1
    assert trend_exit(crash, ci, {}, 0, NEVER_HOLD) == "trend"
    assert trend_exit(up, len(up) - 1, {}, 0, NEVER_HOLD) is False

    # The control must ROTATE: with one seat, the weaker mover is out.
    saved_seats = MAX_POSITIONS
    MAX_POSITIONS = 1
    try:
        rot = make_rotate(corpus)
        i = up.index_of(days[-3])
        assert rot(up, i, {"sym": "UPBEES"}, 0, NEVER_HOLD) is False
        assert rot(dn, i, {"sym": "DNBEES"}, 0, NEVER_HOLD) == "rotate", \
            "control held a name that left the top seats"
    finally:
        MAX_POSITIONS = saved_seats
    print("trend_fund_test selftest ok")


if __name__ == "__main__":
    sys.exit(main(sys.argv))
