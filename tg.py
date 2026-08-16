#!/usr/bin/env python3
"""Telegram bridge: push updates, answer status queries.

Stdlib only, consistent with the rest of the project -- the Bot API is two HTTP
endpoints and does not justify a dependency.

SECURITY. A Telegram bot is reachable by anyone who learns its handle, so every
incoming message is checked against TELEGRAM_CHAT_ID and anything else is
ignored. Without that, a stranger could query this system's state at will. The
token is read from .env (gitignored) and never logged -- errors from the API are
truncated because they can echo the request URL.

Read-only by design: it reports state and never starts a search, never spends
holdout budget, never touches the paper book. Those stay deliberate acts on the
machine, not things triggerable from a phone.

    python3 tg.py --send "text"     # push one message
    python3 tg.py --status          # push the status board
    python3 tg.py --listen          # poll for commands (foreground)
"""
import json
import sys
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
API = "https://api.telegram.org/bot{token}/{method}"
OFFSET = ROOT / "data" / "tg_offset.json"


def env(name, default=""):
    p = ROOT / ".env"
    if not p.exists():
        return default
    for line in p.read_text().splitlines():
        line = line.strip()
        if line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        if k.strip() == name:
            return v.strip().strip('"').strip("'")
    return default


def _call(method, params, timeout=30):
    token = env("TELEGRAM_BOT_TOKEN")
    if not token:
        return {"ok": False, "error": "TELEGRAM_BOT_TOKEN not set in .env"}
    url = API.format(token=token, method=method)
    data = urllib.parse.urlencode(params).encode()
    try:
        with urllib.request.urlopen(url, data=data, timeout=timeout) as r:
            return json.loads(r.read())
    except Exception as e:
        # Never surface the URL: it contains the bot token.
        return {"ok": False, "error": f"{type(e).__name__}"}


def send_document(path, caption="", chat_id=None):
    """Upload a file. Multipart by hand -- stdlib has no multipart encoder and
    a dependency for one would be the only third-party import in the project."""
    import uuid
    token = env("TELEGRAM_BOT_TOKEN")
    if not token:
        return {"ok": False, "error": "TELEGRAM_BOT_TOKEN not set in .env"}
    p = __import__("pathlib").Path(path)
    if not p.exists():
        return {"ok": False, "error": "file not found"}
    b = uuid.uuid4().hex
    parts = []
    for k, v in (("chat_id", str(chat_id or env("TELEGRAM_CHAT_ID"))),
                 ("caption", caption[:1000])):
        parts.append(f"--{b}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n"
                     .encode())
    parts.append(f"--{b}\r\nContent-Disposition: form-data; name=\"document\"; "
                 f"filename=\"{p.name}\"\r\nContent-Type: text/markdown\r\n\r\n".encode())
    parts.append(p.read_bytes())
    parts.append(f"\r\n--{b}--\r\n".encode())
    body = b"".join(parts)
    req = urllib.request.Request(
        API.format(token=token, method="sendDocument"), data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={b}"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read())
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}"}


def send(text, chat_id=None):
    """Telegram caps messages at 4096 chars.

    '%%' is escaped-percent from format-string habit and renders literally
    here -- these are plain strings, never %-formatted. It has reached the
    user twice ("20%% STCG"), so it is now a hard error rather than a typo
    that only shows up on their phone.
    """
    if "%%" in text:
        raise ValueError("literal '%%' in message -- use a single % "
                         f"(near: ...{text[max(0, text.find('%%') - 40):text.find('%%') + 4]!r})")
    return _call("sendMessage", {
        "chat_id": chat_id or env("TELEGRAM_CHAT_ID"),
        "text": text[:4000],
        "parse_mode": "Markdown",
        "disable_web_page_preview": "true",
    })


# --- command handlers: all read-only -------------------------------------

def cmd_status(_=None):
    import agent
    att = agent.attention()
    ok, why = agent.health()
    jobs = agent._jobs_loaded()
    out = [f"*trading-bot* {datetime.now():%d %b %H:%M}", "",
           f"*agent:* {'🟢' if ok else '🔴'} {why}",
           f"*scheduled:* {', '.join(jobs) if jobs else 'NONE INSTALLED'}", ""]
    out.append("*needs attention*")
    out += [f"• {a}" for a in att] if att else ["• nothing"]
    out += ["", f"*due now:* {', '.join(agent.due()) or 'nothing'}",
            f"*busy:* {'yes' if agent._busy() else 'no'}"]
    st = agent._state()
    out += ["", "*last run*"]
    for k in ("last_snapshot", "last_runner", "last_research"):
        out.append(f"• {k.replace('last_', '')}: {st.get(k, 'never')}")
    return "\n".join(out)


def cmd_progress(_=None):
    import judge
    out = [f"*progress* {datetime.now():%d %b %H:%M}", ""]
    days = len(list((ROOT / "data" / "raw").glob("*/bhavcopy_delivery.csv")))
    surv = len(list((ROOT / "data" / "raw").glob("*/asm.json")))
    out += [f"• corpus: {days} days, {surv} with surveillance",
            f"• holdout budget: {judge._load()['spent']}/{judge.BUDGET} spent"]
    p = ROOT / "data" / "pipeline_state.json"
    if p.exists():
        runs = json.loads(p.read_text()).get("runs", [])
        out += ["", "*recent research cycles*"]
        for r in runs[-4:]:
            out.append(f"• seed `{r['seed']}` PBO {r.get('pbo', 0):.3f} "
                       f"promoted {r.get('promoted', 0)}")
    return "\n".join(out)


def cmd_paper(_=None):
    """The Rs 5L cluster book FIRST -- it is the live one.

    This used to read engine.Journal() only, which is the spec-search book and
    has never held a position. /paper therefore reported an empty book while
    the cluster book had five queued. Two books, one command, no label.
    """
    import pbook
    s = pbook.summary()
    out = [f"*paper book* {datetime.now():%d %b %H:%M}", "",
           f"*Rs 5L cluster book* — your 20/20/20 design",
           f"• equity: Rs {s['equity']:,.0f}  (realised Rs {s['realised']:+,.0f})",
           f"• {s['open']} open · {s['pending']} queued · {s['closed']} closed"]
    for r in s["rows"]:
        if r["status"] in ("open", "pending"):
            px = r["entry_px"] or 0
            out.append(f"  – {r['symbol']} ({r['cluster']}) {r['status']}"
                       + (f" @ {px:.2f}" if px else "")
                       + f"  stop {r['stop']:.2f} → tgt {r['target']:.2f}")
    if not s["closed"]:
        out.append("_no closed trades yet — forward evidence is calendar-bound_")
    out.append("")
    import engine
    j = engine.Journal()
    op, cl = j.positions("open"), j.positions("closed")
    out.append(f"*spec-search book* — the earlier track, kept separate")
    out.append(f"• {len(op)} open · {len(cl)} closed · "
               f"realised Rs {j.realised_pnl():+,.0f}")
    return "\n".join(out)


def cmd_learning(_=None):
    import learning, statistics
    from collections import defaultdict
    t = learning.load()
    out = [f"*learning* {datetime.now():%d %b %H:%M}", "",
           f"trades analysed: *{len(t)}*", ""]
    if not t:
        return "\n".join(out + ["_no trades recorded yet_"])
    out.append("*feature information* (return spread, top third vs bottom)")
    for f, v in sorted(learning.analyse(t).items(),
                       key=lambda kv: -abs(kv[1]["spread"])):
        arrow = "↑" if v["spread"] > 0 else "↓"
        out.append(f"• `{f}` {arrow} {v['spread']:+.2f}%")
    # Defensive: a status command must never crash on a missing field. Records
    # come from several sources (historical seed, forward trades, ad-hoc) and
    # not all carry every key -- a KeyError here takes out the whole report.
    by = defaultdict(list)
    for x in t:
        if x.get("ret") is not None:
            by[x.get("cluster") or "unknown"].append(x["ret"])
    out += ["", "*by cluster*"]
    for b, v in sorted(by.items(), key=lambda kv: -statistics.fmean(kv[1])):
        out.append(f"• {b}: {statistics.fmean(v):+.2f}%/trade, "
                   f"{sum(1 for r in v if r>0)/len(v)*100:.0f}% win, n={len(v)}")
    w = learning.load_weights()
    out += ["", f"*weights*: " + ", ".join(f"{k} {v:.2f}" for k, v in w.items())]
    return "\n".join(out)


def push_learning(headline, lines):
    """Push a learning/improvement event unprompted. Kept short: a wall of text
    on a phone gets skipped, and then the alerts stop being read."""
    return send(f"*{headline}*\n" + "\n".join(f"• {l}" for l in lines[:8]))


def cmd_sims(_=None):
    """Plain-language simulation report: what the numbers MEAN, not just what
    they are. A table of CAGRs on a phone is unreadable and, worse, invites
    picking the biggest one -- which walk-forward showed selects losers."""
    import simulate
    rows = simulate.load_results()
    if not rows:
        return "*simulations*\n\n_none stored yet_"
    batches = sorted({r["batch"] for r in rows})
    latest = sorted([r for r in rows if r["batch"] == batches[-1]],
                    key=lambda r: -r["cagr"])
    base = next((r for r in latest if r["variant"].startswith("baseline")), latest[0])

    out = [f"*SIMULATION RESULTS*  `{batches[-1]}`",
           "_Rs 5,00,000 book, 5.7 years of NSE history_", ""]

    growth = base["equity"]
    out += ["*Your book, as configured*",
            f"• Rs 5,00,000 → *Rs {growth:,.0f}*   ({base['total_pct']:+.0f}% over 5.7 yrs)",
            f"• that is *{base['cagr']:+.1f}% a year*",
            f"• worst fall along the way: *-{base['maxdd']:.0f}%*",
            f"• {base['n']} trades, *{base['win']}%* won", ""]

    ex = base.get("exits") or {}
    if ex:
        tot = sum(ex.values()) or 1
        out += ["*How trades ended*"]
        for k, label in (("target", "hit +20% target"), ("stop", "hit -10% stop"),
                         ("time", "closed after 15 days")):
            if k in ex:
                out.append(f"• {label}: {ex[k]} ({ex[k]*100//tot}%)")
        out.append("")

    mix = base.get("mix") or {}
    if mix:
        out += ["*Where the trades came from*",
                "• " + " · ".join(f"{k} {v}" for k, v in mix.items()), ""]

    out += ["*Other settings tested*"]
    for r in latest[:6]:
        if r["variant"] == base["variant"]:
            continue
        d = r["cagr"] - base["cagr"]
        out.append(f"• {r['variant']}: {r['cagr']:+.1f}%/yr "
                   f"({d:+.1f} vs yours), fall -{r['maxdd']:.0f}%")

    out += ["", "⚠️ *Do not pick the best number here.* Walk-forward showed the",
            "best in-sample setting ranked LAST out-of-sample. See /wf."]
    return "\n".join(out)


def cmd_clusters(_=None):
    """The actual stocks the simulations trade."""
    import clusters, features
    c = features.load_corpus()
    days = sorted({d for s in c.values() for d in s.days})
    as_of = days[-1]
    picks = clusters.pick(c, as_of, per_cluster=20)
    sizes = {"micro": "smallest 3rd by turnover", "small": "middle 3rd",
             "mid": "largest 3rd"}
    out = [f"*CLUSTER STOCKS*  as of {as_of}",
           "_screened on: 6-month strength, delivery %, liquidity,_",
           "_must be above its own 200-day average_", ""]
    for b in ("micro", "small", "mid"):
        lst = picks.get(b, [])
        out.append(f"*{b.upper()}* ({sizes[b]}) — top {min(10, len(lst))} of {len(lst)}")
        out.append("  " + ", ".join(f"`{s}`" for s, _ in lst[:10]))
        out.append("")
    out.append("_the book takes 2 micro + 2 small + 1 mid from these_")
    return "\n".join(out)


def cmd_wf(_=None):
    import simulate
    rows = simulate.load_wf()
    if not rows:
        return "*walk-forward*\n\n_none stored yet_"
    out = [f"*walk-forward* ({len(rows)} tests)", ""]
    for r in rows[-6:]:
        v = r.get("verdict") or ("anti" if r.get("anti_predicts") else "ok")
        flag = {"anti": "🔴 ANTI-PREDICTS", "weak": "🟡 too weak to act on",
                "ok": "🟢 generalises"}[v]
        out.append(f"{flag} `{r['param']}`")
        out.append(f"   chose {r['chosen']} in-sample → rank "
                   f"{r['oos_rank']}/{r['oos_of']} out-of-sample "
                   f"(best was {r['oos_best']})")
    out += ["", "_red = tuning picks LOSERS · amber = no better than chance_",
            "_only green would justify changing a parameter_"]
    return "\n".join(out)


def cmd_overview(_=None):
    """The one-screen answer to 'where are we, is this working?'"""
    import overview
    s = overview.state()
    g = overview.gates(s)
    verdict, why = overview.direction(s, g)
    b = s["book"]
    out = [f"*OVERVIEW* — {verdict}", ""]
    out.append(f"*Data* {s['days']} sessions, {s['span'][0]} → {s['span'][1]}")
    out.append(f"*Search* {s['n_sims']} sims · {s['n_wf']} walk-forward · "
               f"{s['n_learn_trades']:,} trades studied")
    out.append(f"*Holdout* {s['budget_spent']}/{s['budget_total']} spent — "
               f"{s['holdout_pass']} PASS, {s['holdout_fail']} FAIL")
    out.append(f"*Book* {b['pending']} queued, {b['open']} open, {b['closed']} closed, "
               f"realised {b['net']:+,.0f}")
    out.append("")
    out.append("*Gates*")
    mark = {"PASS": "✅", "FAIL": "❌", "PENDING": "⏳", "NONE": "—",
            "WEAK": "⚠️", "THIN": "⚠️", "MEASURING": "📈"}
    for name, vd, ev in g:
        out.append(f"{mark.get(vd, '·')} {name} — _{ev}_")
    out.append("")
    out.append("*Why that verdict*")
    for w in why:
        out.append(f"• {w}")
    out.append("")
    out.append("_The apparatus is trustworthy; the strategy is not yet shown to "
               "work. Only the first has been earned._")
    return "\n".join(out)


def cmd_strategies(_=None):
    """Backtest survivors kept for forward testing."""
    import simulate
    rows = simulate.load_strats()
    if not rows:
        return ("*strategies*\nNothing stored yet — no configuration has cleared "
                f"the bar (CAGR>{simulate.KEEP_CAGR}%, DD<{simulate.KEEP_DD}%, "
                f"n≥{simulate.KEEP_N}, win≥{simulate.KEEP_WIN}%).")
    out = [f"*stored strategies* ({len(rows)})", ""]
    for r in sorted(rows, key=lambda x: -x["cagr"])[:8]:
        p = r.get("params", {})
        out.append(f"*{r['variant']}* — `{r['status']}`")
        out.append(f"  CAGR {r['cagr']:+.2f}% · DD {r['maxdd']:.1f}% · "
                   f"n={r['n']} · win {r['win']}%")
        out.append(f"  stop {p.get('stop_pct')}% / target {p.get('target_pct')}% / "
                   f"hold {p.get('hold')}d")
        if p.get("inverted"):
            out.append(f"  inverted: {', '.join(p['inverted'])}")
        out.append("")
    out.append("_Candidates are backtest survivors, not validated strategies. "
               "They earn 'paper' status only from forward trades._")
    return "\n".join(out)


def cmd_bucket(_=None):
    """The cluster book: clusters, stocks, entry logic, and why."""
    import bucketbook
    p = bucketbook.generate()
    r = send_document(p, caption="Bucket Book — clusters, entry logic, and why "
                                "each stock was picked.")
    if r.get("ok"):
        return None            # the document IS the reply
    # Upload failed: fall back to the sections that answer "why".
    txt = p.read_text()
    cut = txt.find("## 3. Entry logic")
    return ("*cluster book* (upload failed, showing entry logic)\n\n"
            + txt[cut:cut + 3200])


def cmd_wallet(_=None):
    """Where the money is right now."""
    import features, pbook, portfolio
    s = pbook.summary()
    corpus = features.load_corpus()
    days = sorted({d for x in corpus.values() for d in x.days})
    last = days[-1]
    deployed = unreal = 0.0
    lines = []
    for r in s["rows"]:
        if r["status"] not in ("open", "pending"):
            continue
        sym = r["symbol"]
        ser = corpus.get(sym)
        i = ser.index_of(last) if ser else None
        px = ser.close[i] if i is not None else (r["entry_px"] or 0)
        val = (r["qty"] or 0) * px
        deployed += val
        if r["status"] == "open" and r["entry_px"]:
            u = val - r["qty"] * r["entry_px"]
            unreal += u
            lines.append(f"  {sym} ({r['cluster']}) ₹{val:,.0f}  {u:+,.0f}")
        else:
            lines.append(f"  {sym} ({r['cluster']}) ₹{val:,.0f}  _queued_")
    cash = portfolio.CAPITAL + s["realised"] - deployed
    total = cash + deployed + 0.0
    out = [f"*wallet* {datetime.now():%d %b %H:%M}", "",
           f"*Starting capital*  ₹{portfolio.CAPITAL:,}",
           f"*Cash*              ₹{cash:,.0f}",
           f"*Deployed*          ₹{deployed:,.0f}  "
           f"({deployed / portfolio.CAPITAL * 100:.1f}%)",
           f"*Total value*       ₹{total:,.0f}", "",
           f"*Realised P&L*      ₹{s['realised']:+,.0f}  ({s['closed']} closed)",
           f"*Unrealised P&L*    ₹{unreal:+,.0f}  ({s['open']} open)", ""]
    if lines:
        out.append("*Positions*")
        out += lines
    else:
        out.append("_No positions. Fully in cash._")
    out.append("")
    out.append(f"_Cap: {portfolio.DEPLOY_PCT:.0f}% deployable "
               f"(₹{portfolio.CAPITAL * portfolio.DEPLOY_PCT / 100:,.0f}), "
               f"₹{portfolio.CAPITAL * portfolio.DEPLOY_PCT / 100 / portfolio.MAX_POSITIONS:,.0f} "
               f"per name._")
    return "\n".join(out)


def cmd_trades(_=None):
    """Closed trades with their cluster and P&L."""
    import pbook
    from collections import defaultdict
    s = pbook.summary()
    done = [r for r in s["rows"] if r["status"] == "closed"]
    if not done:
        return ("*trades*\nNo closed trades yet. The book is forward-testing "
                "only — nothing here is backfilled from a backtest.")
    by = defaultdict(list)
    for r in done:
        by[r["cluster"]].append(r["net"] or 0.0)
    out = [f"*closed trades* ({len(done)})", ""]
    for r in sorted(done, key=lambda x: x["exit_day"] or "")[-12:]:
        pct = ((r["exit_px"] / r["entry_px"] - 1) * 100) if r["entry_px"] else 0
        out.append(f"{r['symbol']} ({r['cluster']}) {r['exit_reason']} "
                   f"₹{r['net']:+,.0f} ({pct:+.1f}%)")
    out += ["", "*By cluster*"]
    for b, v in sorted(by.items()):
        w = sum(1 for x in v if x > 0)
        out.append(f"  {b}: {len(v)} trades, ₹{sum(v):+,.0f}, {w}/{len(v)} won")
    out.append("")
    out.append(f"*Total realised* ₹{s['realised']:+,.0f}")
    return "\n".join(out)


def cmd_stocks(_=None):
    """Per-stock attribution for the live book."""
    import analysis, pbook
    s = pbook.summary()
    done = [{"sym": r["symbol"], "ret": (r["exit_px"] / r["entry_px"] - 1) * 100,
             "clu": r["cluster"]}
            for r in s["rows"] if r["status"] == "closed" and r["entry_px"]]
    if not done:
        return ("*per-stock*\nNo closed trades yet, so there is nothing to "
                "attribute. Per-stock figures are for review only in any case — "
                "one or two trades per name carry no predictive weight.")
    c = analysis.concentration(done)
    out = [f"*per-stock attribution* ({len(done)} trades, {c['n_symbols']} names)", ""]
    out.append(f"best single name: *{c['top1']:.1f}%* of all gains")
    out.append(f"best 3 names:     *{c['top3']:.1f}%*")
    out.append("")
    out.append("*By cluster*")
    for cl, v in sorted(analysis.per_cluster(done).items()):
        out.append(f"  {cl}: n={v['n']} total {v['total']:+.1f}% "
                   f"win {v['wins']/max(v['n'],1)*100:.0f}%")
    rows = analysis.per_stock(done)
    out += ["", "*Best*"] + [f"  {r['symbol']} {r['total']:+.1f}% (n={r['n']})"
                             for r in rows[:5]]
    out += ["", "*Worst*"] + [f"  {r['symbol']} {r['total']:+.1f}% (n={r['n']})"
                              for r in rows[-5:]]
    return "\n".join(out)


def cmd_help(_=None):
    return ("*commands*\n"
            "/overview – where are we? is this working?\n"
            "/wallet – cash, deployed, realised and unrealised P&L\n"
            "/bucket – the cluster book: clusters, entry logic, why\n"
            "/trades – closed trades with cluster and P&L\n"
            "/stocks – per-stock attribution and concentration\n"
            "/status – what needs attention, what is due\n"
            "/progress – corpus, budget, research cycles\n"
            "/paper – paper trading book\n"
            "/health – is the agent actually running?\n"
            "/learning – what the bot has learned from its trades\n"
            "/sims – simulation results, in plain language\n"
            "/clusters – the stocks being traded, by size band\n"
            "/wf – walk-forward tests: does tuning even work?\n"
            "/strategies – profitable configs kept for paper trading\n"
            "/digest – full digest\n\n"
            "_read-only: I never start a search or spend holdout budget from here_")


def cmd_digest(_=None):
    import agent
    agent.digest()
    return (agent.DIGEST.read_text() if agent.DIGEST.exists()
            else "no digest yet")


def cmd_health(_=None):
    import agent
    ok, why = agent.health()
    jobs = agent._jobs_loaded()
    lines = [f"*health* {datetime.now():%d %b %H:%M}", "",
             f"{'🟢' if ok else '🔴'} agent: {why}",
             f"{'🟢' if jobs else '🔴'} scheduled: {', '.join(jobs) or 'NONE'}"]
    import subprocess as _sp
    for name, pat in (("telegram listener", "tg.py --listen"),
                      ("search/validate", "generator.py|validate.py")):
        r = _sp.run(["pgrep", "-f", pat], capture_output=True, text=True)
        live = bool(r.stdout.strip())
        lines.append(f"{'🟢' if live else '⚪'} {name}: {'running' if live else 'idle'}")
    return "\n".join(lines)


COMMANDS = {"/status": cmd_status, "/progress": cmd_progress, "/health": cmd_health,
            "/learning": cmd_learning, "/sims": cmd_sims, "/wf": cmd_wf,
            "/clusters": cmd_clusters,
            "/paper": cmd_paper, "/help": cmd_help, "/start": cmd_help,
            "/digest": cmd_digest, "/overview": cmd_overview,
            "/strategies": cmd_strategies, "/wallet": cmd_wallet,
            "/bucket": cmd_bucket, "/trades": cmd_trades,
            "/stocks": cmd_stocks}


def _offset(new=None):
    if new is not None:
        OFFSET.parent.mkdir(parents=True, exist_ok=True)
        OFFSET.write_text(json.dumps({"offset": new}))
        return new
    return json.loads(OFFSET.read_text())["offset"] if OFFSET.exists() else 0


def poll_once(timeout=25):
    """-> number of messages handled. Ignores anything not from the owner."""
    owner = str(env("TELEGRAM_CHAT_ID"))
    r = _call("getUpdates", {"offset": _offset(), "timeout": timeout},
              timeout=timeout + 10)
    if not r.get("ok"):
        return 0
    handled = 0
    for upd in r.get("result", []):
        _offset(upd["update_id"] + 1)
        msg = upd.get("message") or {}
        chat = str((msg.get("chat") or {}).get("id", ""))
        if chat != owner:
            continue                      # not the owner: ignore silently
        text = (msg.get("text") or "").strip().split()[:1]
        fn = COMMANDS.get(text[0].lower()) if text else None
        send(fn(msg) if fn else cmd_help(), chat_id=chat)
        handled += 1
    return handled


def _selftest():
    import tempfile
    global OFFSET, ROOT
    o_off = OFFSET
    try:
        with tempfile.TemporaryDirectory() as td:
            OFFSET = Path(td) / "off.json"
            assert _offset() == 0
            _offset(42)
            assert _offset() == 42, "offset not persisted; messages would repeat"
    finally:
        OFFSET = o_off

    src = Path(__file__).read_text()
    # the owner check is the whole security model
    assert 'if chat != owner:' in src, "bot would answer strangers"
    # No handler may EXECUTE anything or spend budget. Asserted precisely: a
    # pgrep PATTERN mentioning "generator.py" is read-only and must not trip
    # this, while subprocess.run([sys.executable, ...]) or judge.consult must.
    handlers = src.split("COMMANDS =")[0].split("def cmd_status")[1]
    for forbidden in ("judge.consult", "--consult", "run([sys.executable",
                      "os.remove", ".unlink("):
        assert forbidden not in handlers, f"a command can trigger {forbidden}"
    assert "pgrep" in handlers, "health must observe processes, not start them"
    # errors must never echo the URL (it carries the token)
    assert 'f"{type(e).__name__}"' in src, "error text could leak the token"

    for fn in (cmd_help,):
        assert isinstance(fn(None), str) and fn(None)

    # every command must survive records missing optional fields
    import learning as _l
    _orig = _l.load
    try:
        _l.load = lambda *a, **k: [{"ret": 1.0}, {"rs": 0.5}, {}]
        out = cmd_learning(None)
        assert isinstance(out, str) and "learning" in out, out
    finally:
        _l.load = _orig
    print("tg selftest ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    elif "--send" in sys.argv:
        i = sys.argv.index("--send")
        print(json.dumps(send(sys.argv[i + 1]).get("ok")))
    elif "--status" in sys.argv:
        print(json.dumps(send(cmd_status()).get("ok")))
    elif "--listen" in sys.argv:
        # Watch EVERY project module, not just this file. tg.py imports agent,
        # judge and engine at request time and holds them in memory, so editing
        # agent.py left the bot serving stale logic while tg.py was untouched --
        # it kept reporting attention items that had already been fixed. Watching
        # only your own source catches your own edits and nothing else.
        _watched = {p: p.stat().st_mtime for p in Path(__file__).parent.glob("*.py")}
        print(f"listening (pid {__import__('os').getpid()}, "
              f"{len(COMMANDS)} commands)", flush=True)
        while True:
            try:
                poll_once()
                # Restart on our own source changing. A poller that keeps
                # serving stale code answers the wrong thing and looks healthy
                # while doing it.
                changed = [p.name for p, m in _watched.items()
                           if not p.exists() or p.stat().st_mtime != m]
                if changed:
                    print(f"changed on disk: {', '.join(changed)} -- restarting",
                          flush=True)
                    sys.exit(0)
            except KeyboardInterrupt:
                break
            except SystemExit:
                raise
            except Exception as e:
                print(f"poll error: {type(e).__name__}", flush=True)
    else:
        print(__doc__)
