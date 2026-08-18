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

# First: puts core/, book/, research/ and ops/ on sys.path.
import paths  # noqa: F401
import json
import sys
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

from paths import ROOT      # one definition; see paths.py
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
    except urllib.error.HTTPError as e:
        # Telegram explains WHY in the body ("can't parse entities..."), and a
        # bare exception name hid that: a message rejected for unbalanced
        # Markdown looked identical to a network failure. The body is safe to
        # surface; the URL is not, because it carries the bot token.
        try:
            return {"ok": False, "error": f"{type(e).__name__}",
                    "description": json.loads(e.read()).get("description", "")[:200]}
        except Exception:
            return {"ok": False, "error": f"{type(e).__name__}"}
    except Exception as e:
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
    # Unbalanced * or _ outside code fences is a Markdown parse error, and
    # Telegram rejects the whole message. A bare identifier like size_clusters
    # or run_task opens an italic that never closes. This has failed three
    # times; catch it here rather than on the user's phone.
    import re as _re
    _parts = text.split("```")
    _outside = "".join(_parts[i] for i in range(0, len(_parts), 2))
    _outside = _re.sub(r"`[^`]*`", "", _outside)      # inline code is literal too
    _outside = _re.sub(r"\\.", "", _outside)           # backslash-escaped markers are literal
    for _ch in ("*", "_"):
        if _outside.count(_ch) % 2:
            _i = _outside.rfind(_ch)
            raise ValueError(
                f"unbalanced {_ch!r} outside code fences -- Telegram will reject "
                f"the message. Wrap identifiers in backticks. "
                f"near: ...{_outside[max(0, _i - 50):_i + 10]!r}")
    return _call("sendMessage", {
        "chat_id": chat_id or env("TELEGRAM_CHAT_ID"),
        "text": text[:4000],
        "parse_mode": "Markdown",
        "disable_web_page_preview": "true",
    })


# --- command handlers: all read-only -------------------------------------

SIZE = {"micro": "smallest", "small": "small"}


def _rs(x):
    """Rupees, always the same way."""
    return f"Rs {x:,.0f}"


def _title(name, sub=""):
    return f"*{name}*" + (f" — {sub}" if sub else "")


def _lag_note():
    """-> one line explaining the end-of-day lag, or '' when there is none.

    The book fills from the bhavcopy, which NSE cuts after the close. So on a
    weekday morning an order that has already entered the real market is still
    'waiting' here. Saying so is the difference between a system that looks
    broken and one that is merely a day behind on purpose.
    """
    from datetime import datetime as _dt
    import features
    now = _dt.now()
    try:
        corpus = features.load_corpus()
        last = max(d for s in corpus.values() for d in s.days)
    except Exception:
        return ""
    if last >= now.date():
        return ""
    if now.weekday() >= 5:
        return ""
    if now.hour < 18:
        return (f"_Prices only go to {last}. Today's close is published after "
                f"18:00 IST, so anything that entered the market this morning "
                f"is recorded tonight, at the price it actually got._")
    return (f"_Prices only go to {last}. Tonight's data has not been collected "
            f"yet — the agent runs after 18:00._")


def _px_now(corpus, sym, day):
    s = corpus.get(sym)
    i = s.index_of(day) if s else None
    return s.close[i] if i is not None else None


# ============================================================ MONEY
def cmd_wallet(_=None):
    """Cash, holdings, profit."""
    import analysis, features, pbook, portfolio
    s = pbook.summary()
    corpus = features.load_corpus()
    days = sorted({d for x in corpus.values() for d in x.days})
    import quotes
    q = quotes.live([r["symbol"] for r in s["rows"]
                     if r["status"] in ("open", "pending")])
    invested = unreal = spent = 0.0
    for r in s["rows"]:
        if r["status"] not in ("open", "pending"):
            continue
        px = ((q.get(r["symbol"]) or {}).get("ltp")
              or _px_now(corpus, r["symbol"], days[-1]) or r["entry_px"] or 0)
        invested += (r["qty"] or 0) * px
        # Cash left the account at the FILL price, not today's price. Deducting
        # the current value instead made unrealised profit cancel itself out,
        # so the book showed +1,405 on paper and a total value of exactly the
        # starting capital.
        spent += (r["qty"] or 0) * (r["entry_px"] or px)
        if r["status"] == "open" and r["entry_px"]:
            unreal += r["qty"] * (px - r["entry_px"])
    cash = portfolio.CAPITAL + s["realised"] - spent
    out = [_title("WALLET"), "",
           f"*Total value*  {_rs(cash + invested)}",
           f"*Cash*         {_rs(cash)}",
           f"*Invested*     {_rs(invested)}  "
           f"({invested / portfolio.CAPITAL * 100:.0f}% of capital)", "",
           f"*Profit realised*    Rs {s['realised']:+,.0f}   ({s['closed']} closed)",
           f"*Profit on paper*    Rs {unreal:+,.0f}   ({s['open']} running)", "",
           f"*Started with*  {_rs(portfolio.CAPITAL)}",
           f"*Change*        Rs {cash + invested - portfolio.CAPITAL:+,.0f}  "
           f"({(cash + invested) / portfolio.CAPITAL * 100 - 100:+.2f}%)"]
    base = analysis.load_occupancy()
    held = s["open"] + s["pending"]
    if base:
        out += ["", f"_Holding {held} of {portfolio.MAX_POSITIONS}. Typical is "
                    f"{base['mean']:.2f}; {base['dist'].get(held, 0):.0f}% of "
                    f"sessions hold exactly this many._"]
    return "\n".join(out)


# ============================================================ THE PIPELINE
def cmd_clusters(_=None):
    """The ranking, deep enough to show which portfolio buys which stock."""
    import clusters, features, pbook, portfolio
    corpus = features.load_corpus()
    days = sorted({d for s in corpus.values() for d in s.days})
    as_of = days[-1]
    # ONE source of ranking. This used clusters.pick() while /bucket used
    # portfolio.build(), and the two disagreed on the same day: build() also
    # drops surveillance-flagged names and anything too expensive to size, so
    # /clusters was advertising names that could never be bought and showing a
    # different top 5 than the bucket taken from it.
    rows = portfolio.build(corpus, as_of)
    trig = {r["symbol"] for r in rows if r.get("triggered")}
    # Which PORTFOLIO buys each stock. The deeper ones reach past the top 5,
    # so a name absent from a short list used to look unranked when it had
    # simply been taken further down.
    owner = {}
    for name, cfg in pbook.PORTFOLIOS.items():
        for r in portfolio.allocate(rows, offset=cfg["offset"]):
            owner[r["symbol"]] = name
    depth = max(portfolio.TAKE_PER_CLUSTER.values()) * len(pbook.PORTFOLIOS)
    out = [_title("RANKING", f"as of {as_of}"),
           f"_The {clusters.TRADEABLE_PCT * 100:.0f}% of NSE shares that trade "
           f"least each day, split into two size groups. All "
           f"{len(pbook.PORTFOLIOS)} portfolios pick from this one list._", ""]
    for c in clusters.CLUSTERS:
        take = portfolio.TAKE_PER_CLUSTER.get(c, 0)
        inc = [r for r in rows if r["cluster"] == c]
        spans = "  ".join(
            f"{n}:{cfg['offset'] * take + 1}-{cfg['offset'] * take + take}"
            for n, cfg in pbook.PORTFOLIOS.items())
        plain = {"micro": "SMALLEST COMPANIES", "small": "SMALL COMPANIES"}
        out.append(f"*{plain.get(c, c.upper())}*  — {len(inc)} worth buying, "
                   f"{take} per portfolio")
        out.append(f"  _{spans}_")
        for n, r in enumerate(inc[:depth], 1):
            sym, who = r["symbol"], owner.get(r["symbol"])
            mark = ("🟢" if who == pbook.MAIN else "📊" if who
                    else "🔸" if sym in trig else "▫️")
            tag = f"  →  _{pbook.label(who)}_" if who else ""
            out.append(f"  rank {n}. {mark} {sym}  score {r['score']:.0f}{tag}")
        if len(inc) > depth:
            out.append(f"  _...{len(inc) - depth} more, ranked too low for "
                       f"any portfolio to buy_")
        out.append("")
    out.append("_🟢 bought by the main portfolio · 📊 bought by a deeper one · "
               "🔸 price broke higher but ranked too low · ▫️ ranked only_")
    return "\n".join(out)


def cmd_bucket(_=None):
    """The 5 stocks chosen to trade, and what happened to each."""
    import clusters, features, portfolio, pbook
    corpus = features.load_corpus()
    days = sorted({d for s in corpus.values() for d in s.days})
    as_of = days[-1]
    rows = portfolio.build(corpus, as_of)
    book = {r["symbol"]: r["status"] for r in pbook.summary()["rows"]}
    mix = portfolio.TAKE_PER_CLUSTER
    out = [_title("TODAY'S PICKS",
                  " + ".join(f"{v} {SIZE.get(k, k)}" for k, v in mix.items())),
           f"_The {sum(mix.values())} best-ranked. Each is bought only once its "
           f"price breaks above its recent high; until then that money stays in "
           f"cash._", ""]
    n = 0
    for c, k in mix.items():
        for r in [x for x in rows if x["cluster"] == c][:k]:
            n += 1
            st = book.get(r["symbol"])
            state = ("🟢 running" if st == "open" else
                     "🟡 buying tomorrow" if st == "pending" else
                     "⚪ waiting for the price to break higher")
            out.append(f"*{n}. {r['symbol']}* ({c})  {state}")
            out.append(f"    score {r['score']:.0f} · {r['why']}")
    if not n:
        out.append("_No candidates today._")
    import pbook
    live = sum(1 for v in book.values() if v in ("open", "pending"))
    out += ["", f"_{live} of {sum(mix.values())} bought so far. The rest are "
                f"waiting for their price to break higher._"]
    # This is the main portfolio. The others buy further down the same list,
    # which is why a stock being bought can be absent from here -- the
    # question that prompted the tag.
    others = {n: [r["symbol"] for r in
                  portfolio.allocate(rows, offset=c["offset"])]
              for n, c in pbook.PORTFOLIOS.items() if n != pbook.MAIN}
    if any(others.values()):
        out.append("")
        out.append("*The other portfolios are buying:*")
        for n, syms in others.items():
            out.append(f"  {pbook.label(n)}: "
                       f"{', '.join(syms) if syms else '_nothing_'}")
        out.append("_Same rules, further down the same ranking. /clusters "
                   "shows where each one sits._")
    return "\n".join(out)


def cmd_next_orders(_=None):
    """Stocks queued and waiting for the market to open."""
    import features, pbook
    # ALL books. Defaulting to main would have shown "nothing waiting" while
    # three research books held queued orders -- a report that is confidently
    # wrong is worse than no report.
    s = pbook.summary(which=None)
    pend = [r for r in s["rows"] if r["status"] == "pending"]
    if not pend:
        return (_title("NEXT ORDERS") + "\nNothing waiting. No stock in the "
                "bucket has broken out, so the money stays in cash.")
    corpus = features.load_corpus()
    days = sorted({d for x in corpus.values() for d in x.days})
    out = [_title("NEXT ORDERS", f"{len(pend)} waiting"),
           "_These are bought at tomorrow morning's opening price._"]
    note = _lag_note()
    if note:
        out.append(note)
    out.append("")
    total = risk = 0.0
    for r in pend:
        px = _px_now(corpus, r["symbol"], days[-1]) or 0
        val = (r["qty"] or 0) * px
        # The stop percentage is the BOOK's, not a constant. `tight` runs 5%
        # and printing "-10%" against a 5% stop would misreport the risk on the
        # one book whose whole purpose is measuring that number.
        sp = pbook.portfolio_cfg(r["portfolio"])["stop_pct"]
        total += val
        risk += val * sp / 100
        tag = ("" if r["portfolio"] == pbook.MAIN
               else f"  ·  _{pbook.label(r['portfolio'])}_")
        out.append(f"*{r['symbol']}* ({SIZE.get(r['cluster'], r['cluster'])}){tag}")
        out.append(f"   buy {r['qty']} at about {px:,.2f}   = {_rs(val)}")
        out.append(f"   sell if it falls to {r['stop']:,.2f}  (−{sp:g}%)")
        out.append(f"   sell if it rises to {r['target']:,.2f}  "
                   f"(+{pbook.TARGET_PCT:g}%)")
        out.append(f"   most it can lose {_rs(val * sp / 100)}")
        out.append("")
    out.append(f"*Total being spent*  {_rs(total)}")
    out.append(f"*Most it can lose*   {_rs(risk)}")
    books = sorted({r["portfolio"] for r in pend})
    if books != [pbook.MAIN]:
        out.append(f"_Across {len(books)} books: {', '.join(books)}. "
                   f"Notional — they are alternative portfolios, not one pot._")
    return "\n".join(out)


def cmd_open_orders(_=None):
    """Trades that are live in the market right now."""
    import features, pbook, portfolio
    s = pbook.summary(which=None)
    live = [r for r in s["rows"] if r["status"] == "open"]
    if not live:
        pend = s["pending"]
        out = [_title("OPEN ORDERS"), "Nothing recorded as live yet."]
        if pend:
            out += ["", f"{pend} order(s) queued — see /next\\_orders."]
        note = _lag_note()
        if note:
            out += ["", note]
        return "\n".join(out)
    import quotes
    corpus = features.load_corpus()
    days = sorted({d for x in corpus.values() for d in x.days})
    q = quotes.live([r["symbol"] for r in live])
    src = ("live" if q else f"last close {days[-1]}")
    out = [_title("OPEN ORDERS", f"{len(live)} live"),
           f"_Prices: {src}._", ""]
    tot_val = tot_pl = 0.0
    for r in live:
        lq = q.get(r["symbol"]) or {}
        px = lq.get("ltp") or _px_now(corpus, r["symbol"], days[-1]) or r["entry_px"]
        val = r["qty"] * px
        pl = r["qty"] * (px - r["entry_px"])
        pct = (px / r["entry_px"] - 1) * 100
        tot_val += val
        tot_pl += pl
        held = (days[-1] - __import__("datetime").date.fromisoformat(
            str(r["entry_day"]))).days if r["entry_day"] else 0
        icon = "🟢" if pl > 0 else ("🔴" if pl < 0 else "⚪")
        to_stop = (px / r["stop"] - 1) * 100 if r["stop"] else 0
        to_tgt = (r["target"] / px - 1) * 100 if px else 0
        tag = ("" if r["portfolio"] == pbook.MAIN
               else f"  ·  _{pbook.label(r['portfolio'])}_")
        out.append(f"{icon} *{r['symbol']}* "
                   f"({SIZE.get(r['cluster'], r['cluster'])}){tag}  {pct:+.1f}%")
        out.append(f"   in at {r['entry_px']:,.2f} → now {px:,.2f}")
        out.append(f"   worth {_rs(val)}   profit Rs {pl:+,.0f}")
        out.append(f"   sells if it falls to {r['stop']:,.2f} "
                   f"({to_stop:+.1f}% away)")
        out.append(f"   sells if it rises to {r['target']:,.2f} "
                   f"({to_tgt:+.1f}% away)")
        out.append(f"   held {held} of {portfolio.HOLD_DAYS} days, then sold "
                   f"either way")
        out.append("")
    out.append(f"*Total worth*  {_rs(tot_val)}")
    out.append(f"*Total profit* Rs {tot_pl:+,.0f}  "
               f"({tot_pl / (tot_val - tot_pl) * 100 if tot_val != tot_pl else 0:+.1f}%)")
    return "\n".join(out)


def cmd_closed_orders(_=None):
    """Finished trades and what they made or lost."""
    import analysis, pbook
    from collections import defaultdict
    s = pbook.summary(which=None)
    done = [r for r in s["rows"] if r["status"] == "closed" and r["entry_px"]]
    if not done:
        return (_title("CLOSED ORDERS") + "\nNothing has been sold yet. Only "
                "real trades made going forward show up here — never anything "
                "replayed from past data.")
    # Statistics come from the POOLED books only. `tight` holds the same names
    # as main, so its trades are not independent -- including them would count
    # the same price path twice and overstate the evidence, which is exactly
    # the error the book design exists to avoid.
    pooled = {n for n, c in pbook.PORTFOLIOS.items() if c["pool"]}
    ev = [r for r in done if r["portfolio"] in pooled]
    rets = [{"ret": (r["exit_px"] / r["entry_px"] - 1) * 100,
             "sym": r["symbol"], "clu": r["cluster"]} for r in ev]
    out = [_title("CLOSED ORDERS", f"{len(done)} finished"), ""]
    for r in sorted(done, key=lambda x: x["exit_day"] or "")[-10:]:
        pct = (r["exit_px"] / r["entry_px"] - 1) * 100
        icon = "✅" if (r["net"] or 0) > 0 else "❌"
        tag = ("" if r["portfolio"] == pbook.MAIN
               else f"  ·  _{pbook.label(r['portfolio'])}_")
        out.append(f"{icon} *{r['symbol']}* ({r['cluster']}){tag}  {pct:+.1f}%  "
                   f"Rs {r['net']:+,.0f}")
        out.append(f"    {r['entry_px']:,.2f} → {r['exit_px']:,.2f} · "
                   f"{r['exit_reason']} · {r['exit_day']}")
    won = sum(1 for r in done if (r["net"] or 0) > 0)
    main_net = sum(r["net"] or 0 for r in done if r["portfolio"] == pbook.MAIN)
    out += ["", f"*Won* {won}   *Lost* {len(done) - won}   "
                f"*Hit rate* {won / len(done) * 100:.0f}%",
            f"*Total (the record portfolio)* Rs {main_net:+,.0f}"]
    if len(done) != len(ev) or any(r["portfolio"] != pbook.MAIN for r in done):
        out.append(f"_All books Rs {s['realised']:+,.0f} across "
                   f"{len({r['portfolio'] for r in done})} books — notional._")
    out += ["", "*By cluster*"]
    by = defaultdict(list)
    for r in done:
        by[r["cluster"]].append(r["net"] or 0.0)
    for c, v in sorted(by.items()):
        out.append(f"  {c}: {len(v)} trades, Rs {sum(v):+,.0f}, "
                   f"{sum(1 for x in v if x > 0)} won")
    if rets:
        conc = analysis.concentration(rets)
        out += ["", f"_Best single name is {conc['top1']:.0f}% of all gains._",
                f"_Evidence pools {len(rets)} trades from "
                f"{len({r['portfolio'] for r in ev})} disjoint books._",
                "_" + analysis.verdict(rets) + "_"]
    return "\n".join(out)


# ============================================================ EVIDENCE
def cmd_findings(_=None):
    """What has been recorded, newest first."""
    import analysis
    rows = analysis.load_findings()
    if not rows:
        return _title("FINDINGS") + "\nNothing recorded yet. Findings are written when trades close."
    out = [_title("FINDINGS", f"{len(rows)} recorded"), ""]
    for r in rows[-3:][::-1]:
        cfg = r.get("config", {})
        mix = "/".join(str(v) for v in cfg.get("mix", {}).values())
        # SAY WHICH IT IS. These were rendered identically to forward results:
        # "+2.96% per trade — measurable" reads as evidence, and every row here
        # so far is a BACKTEST. This project's whole position is that a
        # simulation cannot establish the approach works, and the one screen
        # reporting results did not distinguish them.
        sim = r.get("source") == "simulation"
        out.append(f"{'🧪' if sim else '📈'} *{r['label']}*  _{r['at'][:10]}_")
        out.append(f"  {'TESTED ON PAST DATA — proves nothing' if sim else 'real trades, made forward'}"
                   f" · {r['n']} trades")
        st = r.get("stats") or {}
        if st.get("se"):
            tag = "measurable" if st.get("significant") else "inside the noise"
            give = (st["hi"] - st["lo"]) / 2
            out.append(f"  {st['mean']:+.2f}% average per trade, give or "
                       f"take {give:.2f}% — {tag}")
        for cl, v in sorted(r.get("by_cluster", {}).items()):
            out.append(f"  {cl}: {v['n']} trades {v['total']:+.1f}%")
        out.append("")
    n_sim = sum(1 for r in rows if r.get("source") == "simulation")
    out.append(f"_🧪 {n_sim} of {len(rows)} of these were run on past data. "
               f"Replaying history can always be made to look good, so none "
               f"of it counts. Only trades made going forward do, and there "
               f"are none finished yet._")
    return "\n".join(out)


# ============================================================ SYSTEM
def cmd_health(_=None):
    """Is everything actually running?"""
    import json
    import subprocess
    from datetime import datetime as _dt
    out = [_title("HEALTH"), ""]
    hb = ROOT / "data" / "agent_heartbeat.json"
    if hb.exists():
        at = _dt.fromisoformat(json.loads(hb.read_text())["at"])
        mins = (_dt.now() - at).total_seconds() / 60
        out.append(("✅" if mins < 90 else "❌") +
                   f" agent — last ran {mins:.0f} min ago")
    else:
        out.append("❌ agent — never ran")
    try:
        r = subprocess.run(["pgrep", "-f", "tg.py --listen"],
                           capture_output=True, timeout=5)
        out.append(("✅" if r.stdout.strip() else "❌") + " telegram listener")
    except Exception:
        out.append("· telegram listener — cannot tell")
    raw = ROOT / "data" / "raw"
    if raw.exists():
        bh = sorted(p.name for p in raw.iterdir()
                    if (p / "bhavcopy_delivery.csv").exists())
        out.append(f"✅ data — {len(bh)} trading days, newest {bh[-1] if bh else '?'}")
    try:
        import agent
        due = agent.due()
        att = agent.attention()
        out += ["", f"*Due now*  {', '.join(due) if due else 'nothing'}",
                f"*Attention*  " + ("; ".join(att) if att else "nothing")]
    except Exception as e:
        out.append(f"· agent state unavailable ({type(e).__name__})")
    return "\n".join(out)


def cmd_portfolios(_=None):
    """Every portfolio side by side.

    They exist to multiply finished trades WITHOUT creating a choice: same
    rules, different depth in the same ranking, no shared stocks. None of them
    runs different settings -- see rules.md and pbook.PORTFOLIOS.
    """
    import features
    import pbook
    out = [_title("PORTFOLIOS", f"{len(pbook.PORTFOLIOS)} running"),
           "_Same rules, different depth in the same ranking. More portfolios "
           "means more finished trades, and finished trades are the only thing "
           "that can settle whether this works._", ""]
    conn = pbook.db()
    tot_closed = 0
    for name, cfg in pbook.PORTFOLIOS.items():
        s = pbook.summary(conn, which=name)
        tot_closed += s["closed"]
        mark = "⭐" if name == pbook.MAIN else ("🔬" if not cfg["pool"] else "📊")
        out.append(f"{mark} *{pbook.label(name)}*  _{pbook.role_of(name)}_")
        out.append(f"    {s['open']} held · {s['pending']} buying tomorrow · "
                   f"{s['closed']} finished · banked {_rs(s['realised'])}")
    pooled = [n for n, c in pbook.PORTFOLIOS.items() if c["pool"]]
    out += ["", f"*Counted together*  {len(pooled)} portfolios, no shared stocks "
                f"({', '.join(pooled)})",
            f"*Finished trades, all portfolios*  {tot_closed}"]
    if tot_closed < 105:
        out.append(f"_{105 - tot_closed} more needed before these numbers mean "
                   f"anything. Below that, luck and skill look the same._")

    # The tighter-stop question, as a counterfactual on real positions.
    try:
        sh = pbook.shadow_stop(features.load_corpus(), conn, pct=5.0)
        if sh:
            hit = sum(1 for x in sh if x["shadow_hit"])
            out += ["", f"*If we sold at a 5% loss instead of 10%*  {hit} of "
                        f"{len(sh)} would already be out "
                        f"({hit / len(sh) * 100:.0f}%)",
                    f"_The simulation says 62%. This checks whether the "
                    f"simulation tells the truth — it is not an argument for "
                    f"selling earlier._"]
    except Exception as e:
        out.append(f"_that check is unavailable ({type(e).__name__})_")

    out.append("_⭐ the one we judge results by · 📊 counted alongside it_")
    return "\n".join(out)


def cmd_review(_=None):
    """The daily read: what the book holds, what it would buy next, and whether
    anything has EARNED a change.

    Suggestions are printed, never applied. This project's own record (L47) is
    that parameter tuning on this book anti-predicts out of sample, so a daily
    job that quietly retunes the strategy would be the single most damaging
    thing to automate here. It proposes; a person decides and re-simulates.
    """
    import analysis
    import learning
    import pbook
    s = pbook.summary()
    allb = pbook.summary(which=None)
    closed = [r for r in s["rows"] if r["status"] == "closed" and r["entry_px"]]
    out = [_title("DAILY REVIEW", str(datetime.now().date())), "",
           f"*{pbook.label(pbook.MAIN).capitalize()}*  {s['open']} held · {s['pending']} buying "
           f"tomorrow · {s['closed']} finished · worth {_rs(s['equity'])}"]
    # Counting only main here said "0 queued" while /next_orders said "2
    # waiting". Both were right about their own scope and the pair was
    # incoherent to read.
    if allb["open"] + allb["pending"] != s["open"] + s["pending"]:
        out.append(f"*All {len(pbook.PORTFOLIOS)} portfolios*  {allb['open']} held · "
                   f"{allb['pending']} buying tomorrow · {allb['closed']} finished")

    trades = [{"ret": (r["exit_px"] / r["entry_px"] - 1) * 100} for r in closed]
    out += ["", "*What the results so far can prove*",
            "_" + analysis.verdict(trades) + "_"]

    out += ["", "*Anything worth changing?*"]
    try:
        led = learning.load()
        cur = learning.load_weights()
        new, notes = learning.propose(led, current=dict(cur))
        moved = {k: (cur.get(k, 1.0), v) for k, v in new.items()
                 if abs(v - cur.get(k, v)) > 1e-6}
        # A uniform rescale changes nothing -- the score is a weighted average,
        # so one common factor cancels. propose() says so in its notes and this
        # must repeat it, not print four numbers that move the book nowhere.
        # Reporting an update that changes nothing is a failure this project
        # has already shipped once (learning.py, the decayed-weights loop).
        noop = [n for n in notes if n.startswith("NO-OP")]
        if noop:
            out.append(f"  none — {noop[0].split('--')[-1].strip()}")
        elif moved:
            for k, (a, b) in sorted(moved.items()):
                out.append(f"  {k}: {a:.2f} → {b:.2f}  _(proposed, NOT applied)_")
            out.append("_Re-run the simulation before adopting any of these._")
        else:
            out.append(f"  no — {len(led)} finished trades on record, "
                       f"{learning.MIN_TRADES} needed before anything moves")
    except Exception as e:
        out.append(f"  unavailable ({type(e).__name__})")

    log = ROOT / "data" / "audit.log"
    if log.exists():
        tail = [l.strip() for l in log.read_text().splitlines() if "passed," in l]
        if tail:
            ok = ", 0 failed" in tail[-1]
            # Say WHEN. This quoted a hand-run log with no date and was still
            # reporting 21 checks after the suite reached 30 -- stale presented
            # as current, which is the failure this repo keeps warning about.
            # Calendar days, not elapsed hours. An audit run at 11:23
            # yesterday is 14 hours old at 01:30 and was being labelled
            # "today", which is the same stale-as-current error one layer down.
            ran = datetime.fromtimestamp(log.stat().st_mtime).date()
            days_old = (datetime.now().date() - ran).days
            when = ("today" if days_old == 0 else "yesterday" if days_old == 1
                    else f"{days_old} days old — STALE")
            out += ["", f"*Self-audit*  {'✅' if ok else '❌'} {tail[-1]}  _({when})_"]

    # A compact line per pick, not the whole of /bucket. Pasting that in made
    # the review four fifths duplicate, and two screens that must be kept in
    # step is one screen too many.
    try:
        import features
        import portfolio
        corpus = features.load_corpus()
        as_of = max(d for x in corpus.values() for d in x.days)
        picks = portfolio.allocate(portfolio.build(corpus, as_of))
        held = {r["symbol"] for r in pbook.summary(which=None)["rows"]
                if r["status"] in ("open", "pending")}
        out += ["", f"*Bucket* ({as_of})"]
        for r in picks:
            out.append(f"  {'🟢' if r['symbol'] in held else '⚪'} "
                       f"{r['symbol']} ({r['cluster']}) score {r['score']:.0f}")
        if not picks:
            out.append("  _nothing triggered_")
        out.append("_/bucket for why · /clusters for the full ranking · "
                   "/portfolios for all four_")
    except Exception as e:
        out.append(f"_bucket unavailable ({type(e).__name__})_")
    return "\n".join(out)


def notify(title, lines):
    """Push an event the user should see without asking. Never raises.

    The command rewrite dropped the old push_learning() and left two callers in
    pbook_run.py pointing at nothing, so the very first forward fill reported
    "telegram push failed: AttributeError" and no message went out. The audit
    now checks that these callers resolve.
    """
    body = "*" + str(title) + "*\n" + "\n".join(f"• {l}" for l in lines)
    try:
        return send(body)
    except Exception as e:
        return {"ok": False, "error": type(e).__name__}


def cmd_help(_=None):
    return ("*COMMANDS*\n\n"
            "*Money*\n"
            "/wallet — cash, holdings, profit\n\n"
            "*The pipeline*, in order\n"
            "/clusters — the ranking, and who buys what\n"
            "/bucket — today's 5 picks, and why each one\n"
            "/next\\_orders — waiting to enter, with entry, stop, target, value\n"
            "/open\\_orders — trades live in the market now\n"
            "/closed\\_orders — finished trades and their profit or loss\n\n"
            "*Evidence*\n"
            "/findings — what has been recorded\n"
            "/review — the daily read: portfolio, evidence, suggestions\n"
            "/portfolios — all five sets of picks side by side\n\n"
            "*System*\n"
            "/health — is everything running\n\n"
            "_Hyphens work too: /next-orders, /open-orders, /closed-orders._\n"
            "_Read-only. I never place a trade or change a setting from here._")


# Telegram only autocompletes underscores, but the hyphen spellings are
# what was asked for and arrive intact as plain text, so both are bound.
COMMANDS = {"/wallet": cmd_wallet, "/clusters": cmd_clusters,
            "/bucket": cmd_bucket,
            "/next_orders": cmd_next_orders, "/next-orders": cmd_next_orders,
            "/open_orders": cmd_open_orders, "/open-orders": cmd_open_orders,
            # /portfolio was the earlier name for the live trades; kept so it
            # does not silently stop working.
            "/portfolio": cmd_open_orders,
            "/closed_orders": cmd_closed_orders,
            "/closed-orders": cmd_closed_orders,
            "/findings": cmd_findings, "/review": cmd_review,
            "/portfolios": cmd_portfolios, "/books": cmd_portfolios,
            "/health": cmd_health,
            "/help": cmd_help, "/start": cmd_help}

# Spellings that work but are deliberately not advertised: the hyphen forms
# (Telegram only autocompletes underscores) and older names kept alive so they
# do not silently break. Everything else must appear in /help.
ALIASES = {"/start", "/help", "/portfolio", "/books",
           "/next-orders", "/open-orders", "/closed-orders"}



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
        raw = (msg.get("text") or "").strip()
        tok = raw.split()[0].lower() if raw.split() else ""
        # Telegram appends @botname when a command is chosen from the
        # autocomplete menu ("/next_orders@swingalpha_bot"). Without stripping
        # it the lookup misses and every menu-selected command silently fell
        # through to /help.
        cmd = tok.split("@")[0]
        fn = COMMANDS.get(cmd)
        print(f"recv {raw[:40]!r} -> {cmd} -> "
              f"{fn.__name__ if fn else 'help (no match)'}", flush=True)
        out = fn(msg) if fn else cmd_help()
        if out is not None:              # a handler may reply with a document
            send(out, chat_id=chat)
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
    handlers = src.split("COMMANDS =")[0].split("def cmd_wallet")[1]
    for forbidden in ("judge.consult", "--consult", "run([sys.executable",
                      "os.remove", ".unlink("):
        assert forbidden not in handlers, f"a command can trigger {forbidden}"
    assert "pgrep" in handlers, "health must observe processes, not start them"
    # errors must never echo the URL (it carries the token)
    assert 'f"{type(e).__name__}"' in src, "error text could leak the token"

    for fn in (cmd_help,):
        assert isinstance(fn(None), str) and fn(None)
    # every advertised command must exist, and every command must be advertised
    import re as _re
    listed = {m for m in _re.findall(r"/[a-z][a-z_-]*", cmd_help(None).replace("\\", ""))
              if "-" not in m}
    have = set(COMMANDS) - ALIASES
    for a in ALIASES:
        assert a in COMMANDS, f"{a} is listed as an alias but is not bound"
    assert listed == have, (sorted(listed ^ have),
                            "help and COMMANDS disagree")

    # Every command must survive an EMPTY book. The bot is at its most useful
    # before the first trade, which is exactly when every record is missing.
    import pbook as _pb
    _orig = _pb.summary
    try:
        _pb.summary = lambda *a, **k: {"pending": 0, "open": 0, "closed": 0,
                                       "realised": 0.0, "equity": 0.0, "rows": []}
        for name in ("/wallet", "/portfolio", "/open_orders",
                     "/closed_orders", "/findings"):
            out = COMMANDS[name](None)
            assert isinstance(out, str) and out, name
    finally:
        _pb.summary = _orig
    print("tg selftest ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    elif "--send" in sys.argv:
        i = sys.argv.index("--send")
        print(json.dumps(send(sys.argv[i + 1]).get("ok")))
    elif "--review" in sys.argv:
        print(json.dumps(send(cmd_review()).get("ok")))
    elif "--listen" in sys.argv:
        # Watch EVERY project module, not just this file. tg.py imports agent,
        # judge and engine at request time and holds them in memory, so editing
        # agent.py left the bot serving stale logic while tg.py was untouched --
        # it kept reporting attention items that had already been fixed. Watching
        # only your own source catches your own edits and nothing else.
        _watched = {p: p.stat().st_mtime
                    for p in Path(__file__).parent.glob("**/*.py")
                    if "__pycache__" not in p.parts}
        # Build the corpus BEFORE serving. It costs ~19s and is cached for the
        # life of the process, so paying it here means the operator's first
        # message is answered in under two seconds instead of waiting for it.
        try:
            import features as _f
            _t0 = __import__("time").time()
            _n = len(_f.load_corpus())
            print(f"corpus warm: {_n} symbols in "
                  f"{__import__('time').time() - _t0:.1f}s", flush=True)
        except Exception as _e:
            print(f"corpus warm failed ({type(_e).__name__}); "
                  f"first command will be slow", flush=True)
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
