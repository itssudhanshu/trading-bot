#!/usr/bin/env python3
"""The trend book's paper bucket: its own ledger, its own state, its own rules.

ISOLATION. This file never imports breakout's selection, positions or order
book -- the shared order machinery is hardwired to the two equity books'
semantics (3/2 quota, pooled ranking, TAKE_PER_CLUSTER), and a third book
squeezed into it would couple three ledgers that must stay separable
(CLAUDE.md: a mixed ledger cannot be un-mixed). Everything this book writes
lives under data/etf_trend/.

WHAT IT DOES, one idempotent step per invocation (`--update`):
  1. read the fund corpus; `today` = newest session on disk;
  2. already processed? -> say so, change nothing;
  3. FILL yesterday's queue at today's OPEN (next-open discipline). A fund
     that did not trade today stays queued; a band-locked fill bar defers --
     an upper lock has no sellers to buy from (L58);
  4. manage open positions on today's bar:
       - stop: low <= stop -> filled at min(stop, open);
       - trend break: close < SMA(EXIT_SMA) -> sold at today's close;
       - no target, no time limit: one idea, held while the trend lives;
  5. re-rank as of today and REPLACE the queue with the best free seats --
     stale signals die at the next decision point rather than lingering.
Costs are real charges plus the engine's square-root impact model, because a
paper book that logs gross fills is a lie with extra steps.

STATUS. Registered rules, failed promotion bar (see selection.py): this book
is a FORWARD EVIDENCE GENERATOR. Nothing here promotes anything anywhere; the
equity books' baselines are unreachable from this code path.

    python3 src/strategies/trend/paper.py --update   # one idempotent step
    python3 src/strategies/trend/paper.py --status   # the book, for humans
    python3 src/strategies/trend/paper.py --selftest # mechanics on fixtures
"""

import sys as _sys, pathlib as _pl
_sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[2]))  # -> src/
import paths  # noqa: F401

import json
import pathlib
import statistics
import sys

import clusters
import engine
import selection
from paths import SDATA

STATE = SDATA / "paper_state.json"
LEDGER = SDATA / "paper_trades.jsonl"

COSTS = engine.Costs()


def _liq(s, i, win=60):
    t = [x for x in s.turnover[max(0, i - win):i + 1] if x > 0]
    rets = [s.close[k] / s.close[k - 1] - 1.0
            for k in range(max(1, i - 20), i + 1) if s.close[k - 1]]
    if not t or len(rets) < 5:
        return None, None
    return statistics.median(t), statistics.pstdev(rets) * 100


def _sym(q):
    """-> the symbol of a queue entry. Old state stored bare strings; newer
    state stores the full row so --status can show the reasoning."""
    return q if isinstance(q, str) else q["symbol"]


def load_state():
    if STATE.exists():
        return json.loads(STATE.read_text())
    return {"last_day": None, "queue": [], "positions": []}


def save_state(st):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, indent=1) + "\n")
    tmp.replace(STATE)


def log_trade(row):
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with LEDGER.open("a") as f:
        f.write(json.dumps(row) + "\n")


def _exit_net(s, i, px, p):
    """-> net rupees for selling `p['qty']` at px, charges and impact paid."""
    adv, vol = _liq(s, i)
    imp = engine.impact_pct(p["qty"] * px, adv, vol,
                            engine.IMPACT_C) if engine.IMPACT_C else 0.0
    px *= (1 - imp / 100)
    buy_val, sell_val = p["entry_px"] * p["qty"], px * p["qty"]
    cost = COSTS.charge(buy_val, "BUY") + COSTS.charge(sell_val, "SELL")
    return (sell_val - buy_val) - cost, cost / buy_val * 100, imp


def update(loader=None):
    """One idempotent day-step. Returns (status_string, n_events)."""
    corpus, days = (loader or clusters.fund_corpus)()
    if not days:
        return "no data on disk", 0
    today = days[-1]
    iso = today.isoformat()
    st = load_state()
    events = 0

    if st["last_day"] == iso:
        return f"up to date at {iso}", 0
    first_run = st["last_day"] is None

    # --- fills --------------------------------------------------------------
    still_queued = []
    for q in st.get("queue", []):
        sym = _sym(q)
        s = corpus.get(sym)
        i = s.index_of(today) if s else None
        if i is None:
            still_queued.append(q)            # did not trade today: order lives
            continue
        if s.high[i] == s.low[i]:
            still_queued.append(q)            # locked fill bar: no sellers/buyers
            continue
        e = s.open[i]
        qty, _risk = selection.position_size(selection.CAPITAL, e)
        if qty < 1:
            continue
        adv, vol = _liq(s, i)
        imp = engine.impact_pct(qty * e, adv, vol,
                                engine.IMPACT_C) if engine.IMPACT_C else 0.0
        e_eff = e * (1 + imp / 100)
        st["positions"].append({
            "symbol": sym,
            "cluster": clusters.asset_group(sym),
            "queued_on": st["last_day"],
            "entry_day": iso,
            "entry_px": round(e_eff, 4),
            "qty": qty,
            "stop": round(e_eff * (1 - selection.STOP_PCT / 100), 4),
        })
        log_trade({"day": iso, "event": "fill", "symbol": sym,
                   "px": round(e_eff, 4), "qty": qty,
                   "impact_pct": round(imp, 3)})
        events += 1
    st["queue"] = still_queued

    # --- manage open positions ---------------------------------------------
    kept = []
    for p in st.get("positions", []):
        s = corpus.get(p["symbol"])
        i = s.index_of(today) if s else None
        if s is None or i is None:
            kept.append(p)                    # suspended today: try tomorrow
            continue
        px = why = None
        if s.low[i] <= p["stop"]:
            px, why = min(p["stop"], s.open[i]), "stop"
        elif s.close[i] < clusters.sma(s.close, i, clusters.EXIT_SMA):
            px, why = s.close[i], "trend"
        if px is None:
            kept.append(p)
            continue
        net, cost_pct, imp_out = _exit_net(s, i, px, p)
        log_trade({"day": iso, "event": "exit", "symbol": p["symbol"],
                   "why": why, "px": round(px, 4), "qty": p["qty"],
                   "entry_px": p["entry_px"], "entry_day": p["entry_day"],
                   "net": round(net, 2),
                   "ret_pct": round(net / (p["entry_px"] * p["qty"]) * 100, 2),
                   "cost_pct": round(cost_pct, 3),
                   "impact_exit_pct": round(imp_out, 3)})
        events += 1
    st["positions"] = kept

    # --- re-rank and replace the queue --------------------------------------
    # Unfilled leftovers die here: if a signal still ranks today it re-queues,
    # otherwise it was yesterday's idea and the book does not chase it.
    # Queue entries carry their row so --status can SHOW the reasoning, and
    # the wider gated ranking is stored too, because a person asking "what
    # are the tops?" deserves the list behind the five seats.
    rows = selection.build(corpus, today)
    held = {p["symbol"] for p in st["positions"]}
    seats = selection.MAX_POSITIONS - len(held)
    fresh = []
    for r in selection.allocate(rows):
        if len(fresh) >= max(seats, 0):
            break
        if r["symbol"] not in held:
            fresh.append({"symbol": r["symbol"], "cluster": r["cluster"],
                          "score": r["score"], "ref": r["ref_close"],
                          "why": r["why"]})
    st["queue"] = fresh
    st["ranking"] = [{"symbol": r["symbol"], "cluster": r["cluster"],
                      "score": r["score"]}
                     for r in rows[:12]]
    st["last_day"] = iso
    save_state(st)

    tag = "initialised (queue built, fills begin next session)" if first_run \
        else f"processed {iso}"
    return (f"{tag}: {len(fresh)} queued, {len(st['positions'])} open, "
            f"{events} events"), events


def status():
    st = load_state()
    out = [f"trend book, last processed {st['last_day']}"]
    if st["positions"]:
        out.append(f"open ({len(st['positions'])}):")
        for p in st["positions"]:
            out.append(f"  {p['symbol']:<14} {p['cluster']:<7} "
                       f"in {p['entry_day']} @ {p['entry_px']}  "
                       f"stop {p['stop']}")
    else:
        out.append("open: none")
    out.append("")
    q = st.get("queue") or []
    held_syms = {p["symbol"] for p in st["positions"]}
    if q:
        out.append(f"queued for the next open ({len(q)}), best first:")
        for item in q:
            if isinstance(item, str):
                out.append(f"  {item}")
                continue
            out.append(f"  {item['symbol']} — {item.get('cluster', '')} "
                       f"{item.get('score', 0):+.1f}% · "
                       f"{item.get('why', '')}")
        out.append("")
    else:
        out.append("queued for the next open (0): -")
    # The wider gate-passing list earns its space only by showing what the
    # queue does NOT: the next names in line. Re-listing the five queued
    # names again was the same screen twice (the operator's screenshots).
    ranking = [r for r in (st.get("ranking") or [])
               if r["symbol"] not in held_syms
               and r["symbol"] not in {_sym(x) for x in q}]
    if ranking:
        out.append("")
        out.append("next in line if a seat frees:")
        for r in ranking:
            out.append(f"  {r['symbol']} — {r.get('cluster', '')} "
                       f"{r.get('score', 0):+.1f}%")
        out.append("")
    if LEDGER.exists():
        rows = [json.loads(l) for l in LEDGER.read_text().splitlines() if l]
        ex = [r for r in rows if r["event"] == "exit"]
        wins = [r for r in ex if r["net"] > 0]
        net = sum(r["net"] for r in ex)
        out.append(f"closed trades: {len(ex)}  win {len(wins)}  "
                   f"net Rs{net:,.0f}  (fills: {sum(1 for r in rows if r['event'] == 'fill')})")
    else:
        out.append("no closed trades yet")
    # Same rhythm rule as the bot's _spaced(): per-entry blanks must never
    # stack with the blank a section header already carries.
    clean = []
    for ln in out:
        if ln == "" and (not clean or clean[-1] == ""):
            continue
        clean.append(ln)
    while clean and clean[-1] == "":
        clean.pop()
    return "\n".join(clean)


def _selftest():
    """Two-day walk on fixtures: queue -> fill -> stop-out, then idempotence."""
    import tempfile
    from datetime import date, timedelta
    d0 = date(2024, 1, 1)
    days = [d0 + timedelta(days=k) for k in range(clusters.HISTORY_MIN + 40)]
    n = len(days)

    def mk(sym, closes):
        import features
        s = features.Series(sym)
        for k, d in enumerate(days):
            px = closes[k]
            s.days.append(d)
            s.open.append(px)
            s.high.append(px * 1.001)
            s.low.append(px * 0.999)
            s.close.append(px)
            s.volume.append(1000)
            s.turnover.append(1e8)
            s.deliv_pct.append(50.0)
            s.surveillance_known.append(True)
            s.restricted.append(False)
        return s

    rising = [100.0 * (1 + 0.002 * k) for k in range(n)]
    crash = rising[:]
    crash[n - 1] = rising[n - 2] * 0.85          # through any plausible stop
    corpus = {"UPBEES": mk("UPBEES", rising),
              "CRASHBEE": mk("CRASHBEE", crash)}

    global STATE, LEDGER
    import tempfile as _tf
    old_state, old_ledger = STATE, LEDGER
    tmp = _tf.mkdtemp()
    STATE = pathlib.Path(tmp) / "state.json"
    LEDGER = pathlib.Path(tmp) / "ledger.jsonl"
    try:
        seen = [days[:clusters.HISTORY_MIN + 10],
                days[:clusters.HISTORY_MIN + 11],
                days[:clusters.HISTORY_MIN + 12]]

        def loader():
            if seen:
                return corpus, sorted(set(seen.pop(0)))
            # repeats of the last day must be a no-op (idempotence)
            return corpus, sorted(set(days[:clusters.HISTORY_MIN + 12]))

        msg, ev = update(loader)
        assert "initialised" in msg and ev == 0, msg
        st = load_state()
        assert st["queue"], "an uptrending fund must be queued on day one"

        msg, ev = update(loader)
        assert ev >= 1, msg
        st = load_state()
        assert any(p["symbol"] in ("UPBEES", "CRASHBEE")
                   for p in st["positions"]), st

        msg, ev = update(loader)
        again, ev2 = update(loader)
        assert "up to date" in again and ev2 == 0, \
            "re-running a processed day must change nothing"

        # CRASHBEE, if it was the one filled, must have stopped out on day 3
        ex = [json.loads(l) for l in LEDGER.read_text().splitlines()]
        stops = [r for r in ex if r.get("why") == "stop"]
        assert all(r["ret_pct"] <= 0.5 for r in stops), stops
    finally:
        STATE, LEDGER = old_state, old_ledger
    print("trend.paper selftest ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    elif "--status" in sys.argv:
        print(status())
    elif "--update" in sys.argv:
        msg, _ = update()
        print(msg)
    else:
        print(__doc__)
