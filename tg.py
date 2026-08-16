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

def _rs(x):
    """Rupees, always the same way."""
    return f"Rs {x:,.0f}"


def _title(name, sub=""):
    return f"*{name}*" + (f" — {sub}" if sub else "")


# ============================================================ THE BOOK
def cmd_book(_=None):
    """Positions, cash, and profit. The single answer to 'where is my money'."""
    import analysis, features, pbook, portfolio
    s = pbook.summary()
    corpus = features.load_corpus()
    days = sorted({d for x in corpus.values() for d in x.days})
    deployed = unreal = 0.0
    lines = []
    for r in s["rows"]:
        if r["status"] not in ("open", "pending"):
            continue
        ser = corpus.get(r["symbol"])
        i = ser.index_of(days[-1]) if ser else None
        px = ser.close[i] if i is not None else (r["entry_px"] or 0)
        val = (r["qty"] or 0) * px
        deployed += val
        if r["status"] == "open" and r["entry_px"]:
            u = val - r["qty"] * r["entry_px"]
            unreal += u
            lines.append(f"  {r['symbol']} ({r['cluster']}) {_rs(val)}  {u:+,.0f}")
        else:
            lines.append(f"  {r['symbol']} ({r['cluster']}) {_rs(val)}  _queued_")
    cash = portfolio.CAPITAL + s["realised"] - deployed
    out = [_title("BOOK"), "",
           f"*Value*     {_rs(cash + deployed)}",
           f"*Cash*      {_rs(cash)}",
           f"*Invested*  {_rs(deployed)}  ({deployed / portfolio.CAPITAL * 100:.0f}% of capital)",
           "",
           f"*Realised*    Rs {s['realised']:+,.0f}  ({s['closed']} closed)",
           f"*Unrealised*  Rs {unreal:+,.0f}  ({s['open']} open)", ""]
    out += (["*Holdings*"] + lines) if lines else ["_No positions. All cash._"]

    held = s["open"] + s["pending"]
    base = analysis.load_occupancy()
    if base:
        pct = base["dist"].get(held, 0.0)
        out += ["", f"_Holding {held} of {portfolio.MAX_POSITIONS}. "
                    f"Normal — {pct:.0f}% of sessions hold exactly this many, "
                    f"typical is {base['mean']:.2f}._"]
    return "\n".join(out)


def cmd_trades(_=None):
    """Closed trades, with the error bar that says whether they mean anything."""
    import analysis, pbook
    from collections import defaultdict
    s = pbook.summary()
    done = [r for r in s["rows"] if r["status"] == "closed" and r["entry_px"]]
    if not done:
        return (_title("TRADES") + "\nNothing closed yet. Only forward trades "
                "count here — nothing is copied in from a backtest.")
    rets = [{"ret": (r["exit_px"] / r["entry_px"] - 1) * 100} for r in done]
    by = defaultdict(list)
    for r in done:
        by[r["cluster"]].append(r["net"] or 0.0)
    out = [_title("TRADES", f"{len(done)} closed"), ""]
    for r in sorted(done, key=lambda x: x["exit_day"] or "")[-10:]:
        pct = (r["exit_px"] / r["entry_px"] - 1) * 100
        out.append(f"{r['symbol']} ({r['cluster']}) {r['exit_reason']} "
                   f"Rs {r['net']:+,.0f}  {pct:+.1f}%")
    out += ["", "*By cluster*"]
    for c, v in sorted(by.items()):
        w = sum(1 for x in v if x > 0)
        out.append(f"  {c}: {len(v)} trades, Rs {sum(v):+,.0f}, {w} won")
    out += ["", f"*Realised* Rs {s['realised']:+,.0f}",
            "", "_" + analysis.verdict(rets) + "_"]
    return "\n".join(out)


def cmd_stocks(_=None):
    """Which names made or lost the money, and how concentrated that is."""
    import analysis, pbook
    s = pbook.summary()
    done = [{"sym": r["symbol"], "clu": r["cluster"],
             "ret": (r["exit_px"] / r["entry_px"] - 1) * 100}
            for r in s["rows"] if r["status"] == "closed" and r["entry_px"]]
    if not done:
        return (_title("STOCKS") + "\nNothing closed yet.\n\n_Per-stock numbers "
                "are for reading, never for choosing: one or two trades per name "
                "cannot predict anything._")
    c = analysis.concentration(done)
    out = [_title("STOCKS", f"{len(done)} trades, {c['n_symbols']} names"), "",
           "_" + analysis.verdict(done) + "_", "",
           f"Best single name is *{c['top1']:.0f}%* of all gains, best three "
           f"*{c['top3']:.0f}%*.", "", "*By cluster*"]
    for cl, v in sorted(analysis.per_cluster(done).items()):
        out.append(f"  {cl}: {v['n']} trades, {v['total']:+.1f}%, "
                   f"{v['wins']} won")
    rows = analysis.per_stock(done)
    out += ["", "*Best*"] + [f"  {r['symbol']} {r['total']:+.1f}% ({r['n']})"
                             for r in rows[:5]]
    out += ["", "*Worst*"] + [f"  {r['symbol']} {r['total']:+.1f}% ({r['n']})"
                              for r in rows[-5:]]
    return "\n".join(out)


# ============================================================ THE APPROACH
def cmd_bucket(_=None):
    """The full written record: clusters, scoring, entry logic, and why."""
    import bucketbook
    p = bucketbook.generate()
    r = send_document(p, caption="Bucket Book — the clusters, the scoring, the "
                                "entry rule, and why each stock was picked.")
    if r.get("ok"):
        return None
    txt = p.read_text()
    cut = txt.find("## 3. Entry logic")
    return _title("BUCKET BOOK", "upload failed, showing entry logic") + "\n\n" + txt[cut:cut + 3200]


def cmd_clusters(_=None):
    """The stocks currently eligible, by cluster."""
    import clusters, features, portfolio
    corpus = features.load_corpus()
    days = sorted({d for s in corpus.values() for d in s.days})
    as_of = days[-1]
    picks = clusters.pick(corpus, as_of)
    rows = portfolio.build(corpus, as_of)
    chosen = {r["symbol"] for r in portfolio.allocate(rows)}
    trig = {r["symbol"] for r in rows if r.get("triggered")}
    out = [_title("CLUSTERS", f"as of {as_of}"), ""]
    for c in clusters.CLUSTERS:
        take = portfolio.TAKE_PER_CLUSTER.get(c, 0)
        out.append(f"*{c}* — top {take} are taken")
        for sym, sc in picks.get(c, [])[:6]:
            mark = "🟢" if sym in chosen else ("•" if sym in trig else "·")
            out.append(f"  {mark} {sym} {sc:.0f}")
        out.append("")
    out.append("_🟢 in the bucket · • triggered · · ranked only_")
    return "\n".join(out)


def cmd_overview(_=None):
    """Where the whole thing stands, and whether it is working."""
    import overview
    st = overview.state()
    verdict, why = overview.direction(st)
    b = st["book"]
    mix = " / ".join(f"{v} {k}" for k, v in st["mix"].items())
    out = [_title("OVERVIEW", verdict), "",
           f"*Bucket*  {mix} = {sum(st['mix'].values())} stocks",
           f"*Entry*   {st['trigger']} → next open",
           f"*Exit*    −{st['stop']:.0f}% / +{st['target']:.0f}% / {st['hold']} days",
           f"*Money*   {_rs(st['capital'])}, up to {st['deploy']:.0f}% invested",
           f"*Book*    {b['pending']} queued, {b['open']} open, {b['closed']} closed",
           "", "*Checks*"]
    mark = {"PASS": "✅", "FAIL": "❌", "PENDING": "⏳", "GAPS": "⚠️", "MEASURING": "📈"}
    for name, vd, ev in overview.gates(st):
        out.append(f"{mark.get(vd, '·')} {name}")
        out.append(f"   _{ev}_")
    out += ["", "*Why*"] + [f"• {w}" for w in why]
    return "\n".join(out)


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
        out.append(f"*{r['label']}*  _{r['at'][:10]}_")
        out.append(f"  mix {mix} · {r['n']} trades")
        st = r.get("stats") or {}
        if st.get("se"):
            tag = "measurable" if st.get("significant") else "inside the noise"
            out.append(f"  {st['mean']:+.2f}% per trade "
                       f"[{st['lo']:+.2f}, {st['hi']:+.2f}] — {tag}")
        for cl, v in sorted(r.get("by_cluster", {}).items()):
            out.append(f"  {cl}: {v['n']} trades {v['total']:+.1f}%")
        out.append("")
    return "\n".join(out)


# ============================================================ THE SYSTEM
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
        latest = max(p.name for p in raw.iterdir() if p.is_dir())
        bh = sorted(p.name for p in raw.iterdir()
                    if (p / "bhavcopy_delivery.csv").exists())
        out.append(f"✅ data — {len(bh)} trading days, newest {bh[-1] if bh else '?'}")
        out.append(f"   (snapshot folder newest: {latest})")

    try:
        import agent
        due = agent.due()
        att = agent.attention()
        out += ["", f"*Due now*  {', '.join(due) if due else 'nothing'}"]
        out += ["*Attention*  " + ("; ".join(att) if att else "nothing")]
    except Exception as e:
        out.append(f"· agent state unavailable ({type(e).__name__})")
    return "\n".join(out)


def cmd_help(_=None):
    return ("*COMMANDS*\n\n"
            "*Your money*\n"
            "/book — cash, holdings, profit\n"
            "/trades — closed trades and what they prove\n"
            "/stocks — which names won and lost\n\n"
            "*The approach*\n"
            "/bucket — the full written record, as a file\n"
            "/clusters — eligible stocks right now\n"
            "/overview — where we stand, and is it working\n"
            "/findings — what has been recorded\n\n"
            "*The system*\n"
            "/health — is everything running\n\n"
            "_Read-only. I never place a trade, start a job, or change a "
            "setting from here._")


COMMANDS = {"/book": cmd_book, "/trades": cmd_trades, "/stocks": cmd_stocks,
            "/bucket": cmd_bucket, "/clusters": cmd_clusters,
            "/overview": cmd_overview, "/findings": cmd_findings,
            "/health": cmd_health, "/help": cmd_help, "/start": cmd_help}



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
    handlers = src.split("COMMANDS =")[0].split("def cmd_book")[1]
    for forbidden in ("judge.consult", "--consult", "run([sys.executable",
                      "os.remove", ".unlink("):
        assert forbidden not in handlers, f"a command can trigger {forbidden}"
    assert "pgrep" in handlers, "health must observe processes, not start them"
    # errors must never echo the URL (it carries the token)
    assert 'f"{type(e).__name__}"' in src, "error text could leak the token"

    for fn in (cmd_help,):
        assert isinstance(fn(None), str) and fn(None)
    # every advertised command must exist, and every command must be advertised
    listed = {w.strip(" \n") for w in cmd_help(None).split() if w.startswith("/")}
    have = set(COMMANDS) - {"/start", "/help"}   # meta, not listed in itself
    assert listed == have, (sorted(listed ^ have),
                            "help and COMMANDS disagree")

    # Every command must survive an EMPTY book. The bot is at its most useful
    # before the first trade, which is exactly when every record is missing.
    import pbook as _pb
    _orig = _pb.summary
    try:
        _pb.summary = lambda *a, **k: {"pending": 0, "open": 0, "closed": 0,
                                       "realised": 0.0, "equity": 0.0, "rows": []}
        for name in ("/book", "/trades", "/stocks", "/findings"):
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
    elif "--overview" in sys.argv:
        print(json.dumps(send(cmd_overview()).get("ok")))
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
