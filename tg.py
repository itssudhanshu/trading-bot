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


def send(text, chat_id=None):
    """Telegram caps messages at 4096 chars."""
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
    import engine
    j = engine.Journal()
    op, cl = j.positions("open"), j.positions("closed")
    out = [f"*paper book* {datetime.now():%d %b %H:%M}", "",
           f"• open: {len(op)}", f"• closed: {len(cl)}",
           f"• realised: Rs {j.realised_pnl():+,.0f}"]
    for p in op[:8]:
        out.append(f"  – {p['symbol']} @ {p['entry_px'] or 0:.2f} "
                   f"stop {p['stop']:.2f}")
    if not op and not cl:
        out.append("\n_no trades yet -- forward evidence is calendar-bound_")
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
    by = defaultdict(list)
    for x in t:
        by[x["bucket"]].append(x["ret"])
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


def cmd_help(_=None):
    return ("*commands*\n"
            "/status – what needs attention, what is due\n"
            "/progress – corpus, budget, research cycles\n"
            "/paper – paper trading book\n"
            "/health – is the agent actually running?\n"
            "/learning – what the bot has learned from its trades\n"
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
            "/learning": cmd_learning,
            "/paper": cmd_paper, "/help": cmd_help, "/start": cmd_help,
            "/digest": cmd_digest}


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
