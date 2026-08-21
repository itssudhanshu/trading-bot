#!/usr/bin/env python3
"""Telegram bridge: push updates, answer status queries.

Stdlib only, consistent with the rest of the project -- the Bot API is two HTTP
endpoints and does not justify a dependency.

SECURITY. A Telegram bot is reachable by anyone who learns its handle, so every
incoming message is checked against the TELEGRAM_CHAT_ID allowlist and anything
else is ignored. Without that, a stranger could query this system's state at
will. The list is comma-separated; the FIRST id is the owner and the only one
proactive messages are pushed to, so adding a reader lets them ask without
subscribing them to every fill. An empty list matches nobody, never everybody.
The
token is read from .env (gitignored) and never logged -- errors from the API are
truncated because they can echo the request URL.

Read-only by design: it reports state and never starts a search, never spends
holdout budget, never touches the paper bucket. Those stay deliberate acts on the
machine, not things triggerable from a phone.

    python3 tg.py --send "text"     # push one message
    python3 tg.py --status          # push the status board
    python3 tg.py --listen          # poll for commands (foreground)
"""

# First: finds src/paths.py, which puts every source dir on sys.path.
import sys as _sys, pathlib as _pl
_sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[1]))
import paths  # noqa: F401
import json
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

from paths import ROOT      # one definition; see paths.py
API = "https://api.telegram.org/bot{token}/{method}"
OFFSET = ROOT / "data" / "tg_offset.json"
LISTENER_BEAT = ROOT / "data" / "listener_heartbeat.json"


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


def readers():
    """-> the chat ids allowed to query this bot. FIRST is the owner.

    TELEGRAM_CHAT_ID holds one id or a comma-separated list. Only the owner is
    pushed to; the rest may ask and receive an answer, which is the whole
    difference between "can read the data" and "gets woken up by it".

    FAIL-CLOSED, and that is the property worth protecting: an unset or blank
    TELEGRAM_CHAT_ID yields [], and poll_once then matches nobody. An empty
    allowlist must never read as "allow anyone" -- the bot is reachable by
    whoever learns its handle.
    """
    return [c.strip() for c in str(env("TELEGRAM_CHAT_ID")).split(",") if c.strip()]


def owner():
    """-> the one id proactive messages go to, or "" if none is configured.

    Every outbound default reads THIS, not the raw env var. Three places used
    the raw value, so the moment it held "111,222" each would have posted to a
    chat id of "111,222" and every push would have failed at once.
    """
    ids = readers()
    return ids[0] if ids else ""


def _call_raw(method, params, timeout=30):
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


def _call(method, params, timeout=30):
    """-> _call_raw's result, and LOGS it when the call failed.

    _call_raw already captured Telegram's explanation. EVERY caller threw it
    away: poll_once discards send()'s return value and returns 0 when getUpdates
    fails, and agent.py ignores both of its pushes. So a revoked token, a reply
    Telegram rejected, and a dropped connection all produced the same thing --
    "recv '/open_orders'" followed by silence, a log that reads as a healthy
    poll, and no answer on the phone. Six minutes of that is indistinguishable
    from a wedged handler, which is exactly the state this was debugged in.

    Logged HERE because it is the one point every outbound call passes through,
    so a new caller cannot reintroduce the blind spot by forgetting to check.

    Consecutive identical failures print once. An outage repeats every 25s
    forever, and a log that says the same line 3,000 times has buried whatever
    came before it.
    """
    r = _call_raw(method, params, timeout)
    if not r.get("ok"):
        m = f"{method} failed: {r.get('error', '')} {r.get('description', '')}".rstrip()
        if m != getattr(_call, "_last", None):
            print(m, flush=True)
            _call._last = m
    return r


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
    for k, v in (("chat_id", str(chat_id or owner())),
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


def check_markup(text):
    """Raise if Telegram would reject `text`. Called by send(); no network.

    Split out of send() so it can be run against a command's output WITHOUT
    posting it. While it lived inside send() the only way to discover that a
    screen had an unbalanced marker was to send it, which means the user's phone
    was the test environment -- and every one of the failures below reached it.
    audit.py now renders every command through this.
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
    # THIS LINE USED TO STRIP r"\\." AS AN ESCAPE, AND THAT IS MarkdownV2. send()
    # posts parse_mode=Markdown, the legacy one, which has no backslash escape at
    # all: `\_` prints a literal backslash AND still opens an italic. So the
    # validator blessed exactly what Telegram rejected -- /bucket died with
    # "can't find end of the entity starting at byte offset 1847" while audit
    # check 34 rendered it and passed. Reject the escape outright rather than
    # counting around it, because it is never what the author meant: it shows up
    # on the phone as `open\_orders`, which is also how the operator found it.
    _bad = _re.search(r"\\[*_`\[]", _outside)
    if _bad:
        _i = _bad.start()
        raise ValueError(
            f"backslash escape {_outside[_i:_i + 2]!r} in a parse_mode=Markdown "
            f"message: legacy Markdown has no escapes, so this prints the "
            f"backslash AND opens an entity. Use plain words or a hyphen alias "
            f"(/open-orders), or wrap it in backticks. "
            f"near: ...{_outside[max(0, _i - 50):_i + 20]!r}")
    for _ch in ("*", "_"):
        if _outside.count(_ch) % 2:
            _i = _outside.rfind(_ch)
            raise ValueError(
                f"unbalanced {_ch!r} outside code fences -- Telegram will reject "
                f"the message. Wrap identifiers in backticks. "
                f"near: ...{_outside[max(0, _i - 50):_i + 10]!r}")
    return text


def send(text, chat_id=None):
    """Telegram caps messages at 4096 chars."""
    check_markup(text)
    return _call("sendMessage", {
        "chat_id": chat_id or owner(),
        "text": text[:4000],
        "parse_mode": "Markdown",
        "disable_web_page_preview": "true",
    })


# --- command handlers: all read-only -------------------------------------

# The operator's own words. "smallest companies" and "small companies" were
# a plain-English paraphrase that read as a comparison between the two
# clusters rather than as the names of two size bands, and micro cap and
# small cap are what these are called everywhere else, including by the
# person reading the screen (rules.md R1: a term already in use is not
# re-invented).
SIZE = {"micro": "micro cap", "small": "small cap"}



def _rs(x):
    """Rupees, always the same way."""
    return f"Rs {x:,.0f}"


def _title(name, sub=""):
    return f"*{name}*" + (f" — {sub}" if sub else "")


def _fields(*pairs):
    """-> ["name - value", ...]. THE definition of the order layout.

    All three order commands render through this, so a field cannot be spelled
    one way in /open-orders and another in /closed-orders. A None value is
    dropped rather than printed as "None"; a field the record does not hold is
    not a field worth a line.
    """
    return [f"{k} - {v}" for k, v in pairs if v is not None]


def _review(r, pct, held):
    """-> one plain line: how the trade did, and why it ended.

    Written from the STORED exit reason, never re-derived from the prices, so
    the sentence cannot disagree with the rule that actually sold the stock.
    """
    import positions
    sp = positions.bucket_cfg(r["bucket"])["stop_pct"]
    d = f"{held} day" + ("" if held == 1 else "s")
    return {
        "target": f"Worked. {pct:+.1f}% in {d} — reached the "
                  f"+{positions.TARGET_PCT:g}% target and sold itself.",
        "stop": f"Did not work. {pct:+.1f}% in {d} — reached the −{sp:g}% stop "
                f"and was sold before the loss could grow.",
        "time": f"Went nowhere. {pct:+.1f}% in {d} — neither price was reached, "
                f"so the {positions.HOLD_DAYS}-day limit sold it at the close.",
    }.get(r["exit_reason"], f"{pct:+.1f}% in {d} — sold: {r['exit_reason']}")


def _away(px, level):
    """-> the % the price must MOVE to reach level. Negative means it must fall.

    ONE definition, because the stop and the target had two. The target divided
    by the price; the stop divided by ITSELF, which printed a stop below the
    price as a POSITIVE distance. Both lines then said "+x% away" in the same
    layout while meaning opposite directions -- YUKEN read "stop 810.00 (+8.9%
    away)", which a person reads as "the price must rise 8.9% to be stopped
    out". It must FALL 8.2%. The wrong base also overstated the cushion, by
    2.7 points on HAPPYFORGE (+17.9% printed against a real -15.2%).
    """
    return (level / px - 1) * 100 if px and level else 0


def _groups(rows):
    """-> [(display_name, key, rows)] grouped by bucket, live buckets first.

    GROUPED, never interleaved. The two buckets hold the same names constantly
    -- they pick from one universe by two rules -- so a flat list shows the
    same stock twice with nothing to say why, which reads as a double position
    rather than as two records. Retired buckets sort last and keep their own
    key as a name, so their rows stay identifiable instead of quietly joining
    the live evidence.
    """
    import positions
    order = list(positions.BUCKETS)
    seen = {}
    for r in rows:
        seen.setdefault(r["bucket"], []).append(r)
    out = [(positions.label(k), k, seen.get(k, [])) for k in order]
    out += [(positions.label(k), k, v) for k, v in sorted(seen.items())
            if k not in order]
    return out


def _merged(rows):
    """-> [(row, [bucket labels])], identical positions collapsed to one line.

    The two buckets buy the same names constantly, and when they buy on the
    same day at the same price the two records are the same trade twice over --
    printing both is noise a reader has to diff by eye. They are merged ONLY
    when every number a reader would compare is identical.

    They are NOT always identical, which is why this merges on the numbers and
    not on the symbol. Size is drawn from each bucket's OWN equity, so the
    moment either banks a trade the quantities part company -- Rs 312,000
    against Rs 300,000 buys 52 shares against 50 at the same price. A name can
    also enter one bucket while the other is full, arriving later at a
    different price with its own ten-day clock. Merging those would print one
    entry price for two different trades, which is a lie rather than a summary.
    """
    import positions
    order = {k: i for i, k in enumerate(positions.BUCKETS)}
    groups = {}
    for r in rows:
        key = (r["symbol"], r["entry_day"], r["queued_on"], r["entry_px"],
               r["qty"], r["stop"], r["target"], r["exit_day"], r["exit_px"])
        groups.setdefault(key, []).append(r)
    out = []
    for _, rs in groups.items():
        rs.sort(key=lambda r: order.get(r["bucket"], 99))
        out.append((rs[0], [positions.label(r["bucket"]).title() for r in rs]))
    out.sort(key=lambda t: (order.get(t[0]["bucket"], 99), t[0]["symbol"]))
    return out


def _tag(labels):
    """-> ' — Bucket, Pool'. Always shown while more than one bucket runs: a
    label that appears only sometimes makes the reader ask what its absence
    means."""
    import positions
    return f" — {', '.join(labels)}" if len(positions.BUCKETS) > 1 else ""


def _twin_note(rows):
    """-> a line for a symbol printed on MORE THAN ONE line, only when it is.

    Not for a name held by both buckets: _merged() already collapses those to a
    single row tagged "Bucket, Pool", which says it better than a footnote can.
    This is for the case that genuinely confuses -- the same symbol on two
    lines at two entry prices, because the buckets bought it on different days
    or at different sizes. Without a word that reads as one position recorded
    twice by mistake.

    A note that shows unconditionally is read once and then never again, so it
    stays absent on the days it would be noise.
    """
    import positions
    from collections import Counter
    seen = Counter(r["symbol"] for r, _ in _merged(rows))
    dup = sorted(s for s, n in seen.items() if n > 1)
    if not dup:
        return ""
    return (f"_{', '.join(dup)} is listed twice: the bucket and the pool "
            f"bought it at different prices or sizes. Two separate records of "
            f"{_rs(positions.CAPITAL)} each, not one position bought twice._")


def _lag_note():
    """-> one line explaining the end-of-day lag, or '' when there is none.

    The bucket fills from the bhavcopy, which NSE cuts after the close. So on a
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
def _wallet_of(rows, realised, q, corpus, days):
    """-> (cash, invested, unrealised) for ONE bucket's rows."""
    import selection
    invested = unreal = spent = 0.0
    for r in rows:
        if r["status"] not in ("open", "pending"):
            continue
        px = ((q.get(r["symbol"]) or {}).get("ltp")
              or _px_now(corpus, r["symbol"], days[-1]) or r["entry_px"] or 0)
        invested += (r["qty"] or 0) * px
        # Cash left the account at the FILL price, not today's price. Deducting
        # the current value instead made unrealised profit cancel itself out,
        # so the bucket showed +1,405 on paper and a total value of exactly the
        # starting capital.
        spent += (r["qty"] or 0) * (r["entry_px"] or px)
        if r["status"] == "open" and r["entry_px"]:
            unreal += r["qty"] * (px - r["entry_px"])
    return selection.CAPITAL + realised - spent, invested, unreal


def cmd_wallet(_=None):
    """Cash, stocks held, profit -- for each bucket separately."""
    import analysis, features, positions, selection
    everything = positions.summary(which=None)
    corpus = features.load_corpus()
    days = sorted({d for x in corpus.values() for d in x.days})
    import live_source
    q = live_source.live([r["symbol"] for r in everything["rows"]
                          if r["status"] in ("open", "pending")])
    out = [_title("WALLET"), ""]
    base = analysis.load_occupancy()
    for name, key, rows in _groups(everything["rows"]):
        if not rows and key not in positions.BUCKETS:
            continue
        realised = sum(r["net"] or 0 for r in rows if r["status"] == "closed")
        closed = sum(1 for r in rows if r["status"] == "closed")
        open_n = sum(1 for r in rows if r["status"] == "open")
        pend_n = sum(1 for r in rows if r["status"] == "pending")
        cash, invested, unreal = _wallet_of(rows, realised, q, corpus, days)
        total = cash + invested
        # NEVER added together. Each bucket is its own Rs 3,00,000 record
        # running the same signals; summing them would report six lakh of
        # capital that does not exist and a profit nobody made.
        out += [f"*{name.upper()}* — {positions.slice_of(key)}",
                f"  Value      {_rs(total)}   "
                f"(Rs {total - selection.CAPITAL:+,.0f}, "
                f"{total / selection.CAPITAL * 100 - 100:+.2f}%)",
                f"  Cash       {_rs(cash)}",
                f"  Invested   {_rs(invested)}  "
                f"({invested / selection.CAPITAL * 100:.0f}% of capital)",
                f"  Banked     Rs {realised:+,.0f}   ({closed} finished)",
                f"  On paper   Rs {unreal:+,.0f}   ({open_n} running"
                + (f", {pend_n} buying at the next open)" if pend_n else ")")]
        if base and key == positions.MAIN:
            held = open_n + pend_n
            out.append(f"  _Holding {held} of {selection.MAX_POSITIONS}. "
                       f"Typical is {base['mean']:.2f}; "
                       f"{base['dist'].get(held, 0):.0f}% of sessions hold "
                       f"exactly this many._")
        out.append("")
    note = _twin_note(everything["rows"])
    out += [f"_Each runs {_rs(selection.CAPITAL)} of its own on the same "
            f"signals; they differ only in how the five places are handed "
            f"out. Never added together._"]
    if note:
        out.append(note)
    return "\n".join(out)


def _chosen(rows, mix):
    """-> the rows the bucket takes: the top k of each cluster, in that order.

    ONE definition, because /bucket and /clusters both need it and computing it
    separately is how they came to disagree. /clusters asked
    selection.allocate(), which applies the breakout TRIGGER, so on any evening
    where four of the five picks had not broken out yet it marked them "ranked
    only" while /bucket listed them as picks 2-5 of the same ranking. Rank
    first, trigger second (CLAUDE.md); the trigger decides WHEN a chosen stock
    is bought, not whether it was chosen.
    """
    return [r for c, k in mix.items()
            for r in [x for x in rows if x["cluster"] == c][:k]]


# ============================================================ THE PIPELINE
def cmd_clusters(_=None):
    """The ranking, deep enough to show which stocks the bucket buys."""
    import clusters, features, positions, selection
    corpus = features.load_corpus()
    days = sorted({d for s in corpus.values() for d in s.days})
    as_of = days[-1]
    # ONE source of ranking. This used clusters.pick() while /bucket used
    # selection.build(), and the two disagreed on the same day: build() also
    # drops surveillance-flagged names and anything too expensive to size, so
    # /clusters was advertising names that could never be bought and showing a
    # different top 5 than the picks taken from it.
    rows = selection.build(corpus, as_of)
    trig = {r["symbol"] for r in rows if r.get("triggered")}
    chosen = {r["symbol"] for r in _chosen(rows, selection.TAKE_PER_CLUSTER)}
    # Picked today and actually HELD are different things, and one mark for both
    # made the legend a lie: a name chosen this evening shows 🟢 "in the bucket"
    # before a single rupee has moved, and stays 🟢 the next day when the price
    # never broke out and it was never bought (rules.md R1 -- a word already in
    # use is not reused for something else).
    _rows = [r for r in positions.summary(which=None)["rows"]
             if r["status"] in ("open", "pending")]
    live = {r["symbol"] for r in _rows}
    # symbol -> the books holding it, in registry order, so a name held by both
    # reads "— Bucket, Pool" exactly as it does on the order screens.
    holders = {}
    for _r in sorted(_rows, key=lambda x: list(positions.BUCKETS).index(x["bucket"])
                     if x["bucket"] in positions.BUCKETS else 99):
        holders.setdefault(_r["symbol"], []).append(
            positions.label(_r["bucket"]).title())
    depth = max(selection.TAKE_PER_CLUSTER.values()) * 3
    out = [_title("RANKING", f"as of {as_of}"),
           f"_Of the NSE shares that trade least each day, the "
           f"{clusters.TRADEABLE_PCT * 100:.0f}% that this strategy will touch, "
           f"split into two size groups and scored. The bucket buys from the top "
           f"of each list._", ""]
    for c in clusters.CLUSTERS:
        take = selection.TAKE_PER_CLUSTER.get(c, 0)
        inc = [r for r in rows if r["cluster"] == c]
        plain = {c: v.upper() for c, v in SIZE.items()}
        out.append(f"*{plain.get(c, c.upper())}*  — {len(inc)} worth buying, "
                   f"the bucket takes the top {take}")
        for n, r in enumerate(inc[:depth], 1):
            sym = r["symbol"]
            mark = ("🟢" if sym in live else "🔵" if sym in chosen else
                    "🔸" if sym in trig else "▫️")
            # WHICH book holds it. `live` pools every bucket, so 🟢 under a
            # legend reading "the bucket owns it now" said the bucket held
            # KENNAMET when the POOL did -- a false statement about the live
            # book on the screen that lists the whole ranking.
            out.append(f"  rank {n}. {mark} {sym}  score {r['score']:.0f}"
                       f"{_tag(holders.get(sym, [])) if sym in live else ''}")
        if len(inc) > depth:
            out.append(f"  _...{len(inc) - depth} more, ranked too low to buy_")
        out.append("")
    out.append("_🟢 held now, by the book named · 🔵 chosen, waiting for its price to "
               "break higher · 🔸 price broke higher but ranked too low to buy · "
               "▫️ ranked only_")
    return "\n".join(out)


# Short forms for the ranking screens. The long labels stay canonical in
# selection.FEATURE_LABELS and are still what /help and the prose use; these are
# the same words with the filler removed, because eight of them on one line is
# the difference between a line a person scans and one they skip. Still plain
# words -- rules.md R2 is not relaxed, only tightened.
SHORT = {"rs": "6-mo gain", "deliv": "shares kept",
         "liq": "easy to trade", "near_high": "near high"}


def _rank_lines(picks, held, ref=None):
    """-> the numbered ranking, two lines a stock.

    One line names the stock and what it is doing; one carries the four numbers
    strongest-first. It used to be six lines a stock -- status spelled out as a
    sentence, size group repeated from the header, "score out of 100" -- which
    is thirty lines for five names on a phone.

    `ref` names WHAT THE SCORE IS A PERCENTILE OF, and it is printed beside the
    number rather than left to a footer. The score is relative by construction:
    /bucket ranks a stock inside its own size band (637 micro caps), /pool ranks
    it against all 1,275 eligible shares, so YUKEN reads 85 on one screen and 78
    on the other. Same stock, same features, different denominator. Two meanings
    of "score" printed as bare numbers on two screens is the collision rules.md
    R1 forbids -- the `rank2`-beside-`rank 5` failure -- and it was reported by
    the operator within a day of the pool screen existing. None = the stock's
    own band.
    """
    out = []
    for n, r in enumerate(picks, 1):
        st = held.get(r["symbol"])
        state = ("🟢 held" if st == "open" else
                 "🟡 buying at the next open" if st == "pending" else
                 "⚪ waiting for breakout")
        band = SIZE.get(r["cluster"], r["cluster"])
        # On /bucket the size band IS the reference set, so it is named once as
        # the reference rather than twice on one line. On /pool they differ --
        # the band is what the stock is, the reference is what it was scored
        # against -- and both are needed.
        head = (f"score {r['score']:.0f} vs {band}" if ref is None
                else f"{band} · score {r['score']:.0f} vs {ref}")
        out.append(f"*{n}. {r['symbol']}* — {head} · {state}")
        # Strongest first, so the line answers why THIS name and not the one
        # below it. A run-on sentence naming the strong features cannot: it
        # never says which was strongest.
        nums = [f"{SHORT[f]} {v:.0f}"
                for f, v in sorted((r.get("ranks") or {}).items(),
                                   key=lambda kv: -kv[1]) if f in SHORT]
        if nums:
            out.append("    " + " · ".join(nums))
        out.append("")
    return out


def cmd_bucket(_=None):
    """The stocks the bucket chose this session, and why."""
    import clusters, features, learning, selection, positions
    FEATURE = selection.FEATURE_LABELS      # one definition, in the strategy
    WEIGHTS = learning.load_weights()
    corpus = features.load_corpus()
    days = sorted({d for s in corpus.values() for d in s.days})
    as_of = days[-1]
    rows = selection.build(corpus, as_of)
    # Live rows only. A symbol can now hold more than one row -- a retired
    # 'void' entry alongside the real position -- and keying by symbol without
    # this filter let whichever row came last in the table decide whether the
    # stock showed as running.
    # main only: this screen IS the bucket's ranking, and marking a name green
    # because the POOL happens to hold it would say the bucket owns something
    # it does not. The pool's own holdings are named separately below.
    held = {r["symbol"]: r["status"] for r in positions.summary()["rows"]
            if r["status"] in ("open", "pending")}
    pool_held = {r["symbol"] for r in
                 positions.summary(which=positions.POOLED)["rows"]
                 if r["status"] in ("open", "pending")}
    mix = selection.TAKE_PER_CLUSTER
    out = [_title("THE BUCKET",
                  " + ".join(f"{v} {SIZE.get(k, k)}" for k, v in mix.items())),
           f"_Top {sum(mix.values())} by score, {sum(mix.values())} best in "
           f"each size band. Bought when the price breaks above its recent "
           f"high._", ""]
    picks = _chosen(rows, mix)
    shown = {r["symbol"] for r in picks}
    out += _rank_lines(picks, held)
    if not picks:
        out.append("_No candidates today._")
    # Stocks bought on an EARLIER evening, still running, and no longer in
    # tonight's top five -- the ranking moves nightly, the holding period does
    # not. Three of the four open positions were in exactly this state and this
    # screen, titled THE BUCKET, did not mention them. Worse, the footer counted
    # all four live positions and printed the total against these five names:
    # "4 of 5 bought so far" when one of the five was held. Count what was
    # printed; name what was not.
    earlier = sorted(s for s in held if s not in shown)
    if earlier:
        out += [f"*Also held, bought earlier*  {', '.join(earlier)}",
                "_Off tonight's top 5, still running to their own stop, target "
                "or 10-day limit. /open-orders for detail._", ""]
    # The POOL, named separately and never mixed into the ranking above. This
    # screen is the BUCKET's list; a pool holding shown green among these rows
    # would say the bucket owns a name it does not.
    if pool_held:
        out += [f"*The pool holds*  {', '.join(sorted(pool_held))}",
                "_A separate book, ranked without size bands. /pool for its "
                "own list._", ""]
    live = sum(1 for s in shown if s in held)
    out += [_score_note(WEIGHTS, "its own size band"),
            f"_{live} of {sum(mix.values())} bought, "
            f"{sum(mix.values()) - live} waiting._"]
    return "\n".join(out)


def _score_note(weights, against):
    """-> the one line explaining what the numbers are. Shared, so the bucket
    and the pool cannot end up describing the score differently."""
    return (f"_Each number is a place out of 100 against {against} — 100 is "
            f"best. Score is their average, shares kept counted "
            f"{weights.get('deliv', 1):g}x. Nothing is bought below its 200-day "
            f"average price._")


def cmd_pool(_=None):
    """The pool's five: ranked without size bands."""
    import features, learning, selection, positions
    WEIGHTS = learning.load_weights()
    corpus = features.load_corpus()
    days = sorted({d for s in corpus.values() for d in s.days})
    as_of = days[-1]
    seats = positions.BUCKETS[positions.POOLED]["seats"] or selection.MAX_POSITIONS
    # Set the pool's rule for this call and put it back. A leak would re-rank
    # the BUCKET's own screens for the rest of the process.
    _was = selection.RANKING
    try:
        selection.RANKING = positions.BUCKETS[positions.POOLED]["ranking"]
        rows = selection.build(corpus, as_of)
        # rows[:seats], NOT allocate(). allocate() applies the breakout trigger,
        # so on an evening where four of the five have not broken out yet this
        # screen would list one name and imply the pool chose one. Rank first,
        # trigger second (CLAUDE.md): the trigger decides WHEN a chosen stock is
        # bought, not whether it was chosen. _chosen() carries the same note --
        # /clusters and /bucket disagreed for exactly this reason once.
        picks = rows[:seats]
    finally:
        selection.RANKING = _was
    assert selection.RANKING == _was
    held = {r["symbol"]: r["status"]
            for r in positions.summary(which=positions.POOLED)["rows"]
            if r["status"] in ("open", "pending")}
    from collections import Counter
    split = Counter(r["cluster"] for r in picks)
    out = [_title("THE POOL",
                  " + ".join(f"{split[k]} {SIZE.get(k, k)}"
                             for k in ("micro", "small") if split[k])
                  or "nothing tonight"),
           f"_Top {seats} by score across every eligible share, no size quota. "
           f"The split lands wherever the ranking puts it._", ""]
    out += _rank_lines(picks, held, ref="all shares")
    shown = {r["symbol"] for r in picks}
    earlier = sorted(s for s in held if s not in shown)
    if earlier:
        out += [f"*Also held, bought earlier*  {', '.join(earlier)}",
                "_Off tonight's top 5, still running to their own stop, target "
                "or 10-day limit._", ""]
    live = sum(1 for s in shown if s in held)
    out += [_score_note(WEIGHTS, "every eligible share"),
            f"_{live} of {seats} bought, {seats - live} waiting._",
            "_Runs beside the bucket on its own Rs 3,00,000. /bucket for the "
            "quota book._"]
    return "\n".join(out)


def cmd_pending_orders(_=None):
    """Stocks queued and waiting for the market to open."""
    import features, positions
    # ALL buckets. Defaulting to main would have shown "nothing waiting" while
    # three research buckets held queued orders -- a report that is confidently
    # wrong is worse than no report.
    s = positions.summary(which=None)
    pend = [r for r in s["rows"] if r["status"] == "pending"]
    if not pend:
        return (_title("PENDING ORDERS") + "\nNothing waiting. No stock in the "
                "picks has broken out, so the money stays in cash.")
    corpus = features.load_corpus()
    days = sorted({d for x in corpus.values() for d in x.days})
    out = [_title("PENDING ORDERS", f"{len(pend)} waiting"),
           "_These are bought at tomorrow morning's opening price._"]
    note = _lag_note()
    if note:
        out.append(note)
    out.append("")
    total = risk = 0.0
    for r, labels in _merged(pend):
        px = _px_now(corpus, r["symbol"], days[-1]) or 0
        val = (r["qty"] or 0) * px
        # The stop percentage is the BOOK's, not a constant. `tight` runs 5%
        # and printing "-10%" against a 5% stop would misreport the risk on the
        # one bucket whose whole purpose is measuring that number.
        sp = positions.bucket_cfg()["stop_pct"]
        total += val
        risk += val * sp / 100
        out.append(f"*{r['symbol']}* "
                   f"({SIZE.get(r['cluster'], r['cluster'])}){_tag(labels)}")
        out += _fields(
            # `filled` is n/a and `entry` is an ESTIMATE off the last close --
            # the fill happens at tomorrow's open, which nobody knows yet.
            # Printing the estimate as a plain entry price would put a number
            # the bucket never paid into the record a person reads.
            ("filled", "n/a"),
            ("entry", f"about {px:,.2f} at the next open"),
            ("qty", f"{r['qty']}   = {_rs(val)}"),
            ("stop", f"{r['stop']:,.2f}   (−{sp:g}%, most it can lose "
                     f"{_rs(val * sp / 100)})"),
            ("target", f"{r['target']:,.2f}   (+{positions.TARGET_PCT:g}%)"))
        out.append("")
    out.append(f"*Total being spent*  {_rs(total)}")
    out.append(f"*Most it can lose*   {_rs(risk)}")
    # The pool is LIVE, not retired. This said "retired buckets still running
    # to their own exits" and would have introduced the pool's first order as a
    # relic the moment it queued -- a recorded reason outliving the thing it
    # described, which is the defect L60 tracks.
    note = _twin_note(pend)
    if note:
        out.append(note)
    retired = sorted({r["bucket"] for r in pend} - set(positions.BUCKETS))
    if retired:
        out.append(f"_Includes {', '.join(retired)} — retired buckets still "
                   f"running to their own exits._")
    return "\n".join(out)


def cmd_open_orders(_=None):
    """Trades that are live in the market right now."""
    import features, positions, selection
    s = positions.summary(which=None)
    live = [r for r in s["rows"] if r["status"] == "open"]
    if not live:
        pend = s["pending"]
        out = [_title("OPEN ORDERS"), "Nothing recorded as live yet."]
        if pend:
            out += ["", f"{pend} order(s) queued — see /pending-orders."]
        note = _lag_note()
        if note:
            out += ["", note]
        return "\n".join(out)
    import live_source
    corpus = features.load_corpus()
    days = sorted({d for x in corpus.values() for d in x.days})
    q = live_source.live([r["symbol"] for r in live])
    src = ("live" if q else f"last close {days[-1]}")
    out = [_title("OPEN ORDERS", f"{len(live)} live"),
           f"_Prices: {src}._", ""]
    tot_val = tot_pl = 0.0
    for r, labels in _merged(live):
        lq = q.get(r["symbol"]) or {}
        px = lq.get("ltp") or _px_now(corpus, r["symbol"], days[-1]) or r["entry_px"]
        val = r["qty"] * px
        pl = r["qty"] * (px - r["entry_px"])
        pct = (px / r["entry_px"] - 1) * 100
        tot_val += val
        tot_pl += pl
        held = positions.bars_held(corpus.get(r["symbol"]), r["entry_day"],
                                   days[-1])
        icon = "🟢" if pl > 0 else ("🔴" if pl < 0 else "⚪")
        to_stop, to_tgt = _away(px, r["stop"]), _away(px, r["target"])
        out.append(f"{icon} *{r['symbol']}* "
                   f"({SIZE.get(r['cluster'], r['cluster'])}){_tag(labels)}")
        out += _fields(
            ("filled", r["entry_day"]),
            ("entry", f"{r['entry_px']:,.2f} → now {px:,.2f}"),
            ("qty", f"{r['qty']}   = {_rs(val)} now"),
            ("stop", f"{r['stop']:,.2f}   ({to_stop:+.1f}% away)"),
            ("target", f"{r['target']:,.2f}   ({to_tgt:+.1f}% away)"),
            ("pnl", f"Rs {pl:+,.0f}   ({pct:+.1f}%)"),
            # bars_held, not a date subtraction: a calendar gap counts weekends
            # and would print a number the 10-day exit rule does not use.
            ("day(s)", f"{held} of {selection.HOLD_DAYS}, then sold either way"))
        out.append("")
    note = _twin_note(live)
    if note:
        out.append(note)
    out.append(f"*Total worth*  {_rs(tot_val)}")
    out.append(f"*Total profit* Rs {tot_pl:+,.0f}  "
               f"({tot_pl / (tot_val - tot_pl) * 100 if tot_val != tot_pl else 0:+.1f}%)")
    return "\n".join(out)


def cmd_closed_orders(_=None):
    """Finished trades and what they made or lost."""
    import analysis, features, positions
    from collections import defaultdict
    s = positions.summary(which=None)
    done = [r for r in s["rows"] if r["status"] == "closed" and r["entry_px"]]
    if not done:
        return (_title("CLOSED ORDERS") + "\nNothing has been sold yet. Only "
                "real trades made going forward show up here — never anything "
                "replayed from past data.")
    # EVERY statistic below is the BUCKET's alone. The pool holds the same
    # names constantly -- it picks from one universe by a second rule -- so its
    # trades are not independent draws; counting both would put the same price
    # path in twice and overstate the evidence. The pool's trades are SHOWN,
    # because they are real records, and never counted.
    #
    # The old comment here said "the POOLED buckets", meaning pooled-together.
    # That word now names a bucket, so it had to go: two meanings of one word
    # on one screen is the collision rules.md R1 forbids.
    ev = [r for r in done if r["bucket"] == positions.MAIN]
    rets = [{"ret": (r["exit_px"] / r["entry_px"] - 1) * 100,
             "sym": r["symbol"], "clu": r["cluster"]} for r in ev]
    out = [_title("CLOSED ORDERS", f"{len(done)} finished"), ""]
    corpus = features.load_corpus()
    for r, labels in sorted(_merged(done),
                            key=lambda t: t[0]["exit_day"] or "")[-10:]:
        pct = (r["exit_px"] / r["entry_px"] - 1) * 100
        icon = "✅" if (r["net"] or 0) > 0 else "❌"
        held = positions.bars_held(corpus.get(r["symbol"]), r["entry_day"],
                                   r["exit_day"])
        out.append(f"{icon} *{r['symbol']}* "
                   f"({SIZE.get(r['cluster'], r['cluster'])}){_tag(labels)}")
        out += _fields(
            ("filled", r["entry_day"]),
            ("exit", f"{r['exit_day']} at {r['exit_px']:,.2f}"),
            ("entry", f"{r['entry_px']:,.2f}"),
            ("qty", r["qty"]),
            ("stop", f"{r['stop']:,.2f}" if r["stop"] else None),
            ("target", f"{r['target']:,.2f}" if r["target"] else None),
            ("pnl", f"Rs {r['net'] or 0:+,.0f} after costs   ({pct:+.1f}% "
                    f"on the price)"),
            ("day(s)", held),
            ("review", _review(r, pct, held)))
        out.append("")
    # Counted over `ev`, not `done`: won, lost, hit rate and total must all
    # describe the SAME set, and it must be the bucket's. Mixing the count over
    # every bucket with a total from one was the shape already here.
    won = sum(1 for r in ev if (r["net"] or 0) > 0)
    main_net = sum(r["net"] or 0 for r in ev)
    out += [f"*Won* {won}   *Lost* {len(ev) - won}   "
            f"*Hit rate* {won / len(ev) * 100:.0f}%" if ev else "*No finished "
            "trades in the bucket yet*",
            f"*Total* Rs {main_net:+,.0f}"]
    others = sorted({r["bucket"] for r in done} - {positions.MAIN})
    if others:
        out.append(f"_Counts the bucket only. "
                   f"{', '.join(positions.label(b).title() for b in others)} "
                   f"trades are listed above but not counted: they hold the "
                   f"same names, so adding them would put one price path in "
                   f"twice._")
    out += ["", "*By cluster*"]
    by = defaultdict(list)
    for r in ev:
        by[r["cluster"]].append(r["net"] or 0.0)
    for c, v in sorted(by.items()):
        out.append(f"  {c}: {len(v)} trades, Rs {sum(v):+,.0f}, "
                   f"{sum(1 for x in v if x > 0)} won")
    if rets:
        conc = analysis.concentration(rets)
        out += ["", f"_Best single name is {conc['top1']:.0f}% of all gains._",
                f"_{len(rets)} finished trades._",
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
        # Rows older than the guard counted buys that could not have happened.
        # Left in place -- the file is append-only and history is not edited --
        # but a reader comparing "231 trades, +2.96% each" against today's 195
        # and +2.15% deserves to know they are not the same measurement.
        if r["at"][:10] < analysis.GUARD_DATE:
            out.append("  ⚠️ counted buys at prices the market could not have "
                       "given — see the note at the end")
        st = r.get("stats") or {}
        if st.get("se"):
            tag = "measurable" if st.get("significant") else "inside the noise"
            give = (st["hi"] - st["lo"]) / 2
            out.append(f"  {st['mean']:+.2f}% average per trade, give or "
                       f"take {give:.2f}% — {tag}")
        for cl, v in sorted(r.get("by_cluster", {}).items()):
            # AVERAGE per trade, not the sum of every trade's percentage.
            # "137 trades +462.3%" reads as a 462% return to anyone who is not
            # a trader, which is the one thing rules.md R2 forbids. The average
            # was already stored in the same row.
            out.append(f"  {SIZE.get(cl, cl)}: {v['n']} trades, "
                       f"{v.get('avg', 0):+.2f}% each")
        out.append("")
    n_sim = sum(1 for r in rows if r.get("source") == "simulation")
    old = sum(1 for r in rows if r["at"][:10] < analysis.GUARD_DATE)
    out.append(f"_🧪 {n_sim} of these {len(rows)} were run on past data. "
               f"Replaying history can always be made to look good, so none "
               f"of it counts. Only trades made going forward do, and there "
               f"are none finished yet._")
    if old:
        out.append(f"_⚠️ {old} were recorded before {analysis.GUARD_DATE}, when the "
                   f"test still bought shares on days the price was frozen at its "
                   f"limit and nobody was selling. Those buys were impossible, and "
                   f"they were the best ones — removing them cut the tested return "
                   f"roughly in half. Older numbers here read better than the "
                   f"strategy is._")
    return "\n".join(out)


# ============================================================ SYSTEM
def cmd_health(_=None):
    """Is everything actually running?

    EVERY tick and cross below is measured: a heartbeat that was written, a file
    that exists, an expiry decoded out of the token itself. The live-price
    fallback chain is the one exception and is marked "·" rather than ticked,
    because probing four sources every time someone types /health is what
    rate-limited Yahoo (L57). A line that cannot fail must not wear a tick -- the old
    "✅ data" was hardcoded and would have said ✅ with a week of sessions
    missing, and the listener line asked pgrep and said ❌ about the process
    answering the question.
    """
    import json
    from datetime import datetime as _dt
    out = [_title("SwingAlpha Bot Health"), ""]

    def _age(path):
        """-> minutes since the stamp in `path`, or None if there is not one."""
        if not path.exists():
            return None
        try:
            return (_dt.now() - _dt.fromisoformat(
                json.loads(path.read_text())["at"])).total_seconds() / 60
        except Exception:
            return None

    m = _age(ROOT / "data" / "agent_heartbeat.json")
    # A fresh heartbeat proves the agent RAN, not that anything will run it
    # again: `agent.py --once` by hand stamps the same file. Ticking on the stamp
    # alone put "✅ Scheduler agent — last ran 4 min ago" in the same message as
    # "no launchd job registered -- nothing runs on a schedule". Both facts were
    # true; the tick belonged to the one a person has to act on.
    try:
        import agent as _a
        jobs = _a._jobs_loaded()          # [] = none, None = could not ask
    except Exception:
        jobs = None
    if m is None:
        out.append("❌ Scheduler agent — never ran")
    elif m >= 90:
        out.append(f"❌ Scheduler agent — last ran {m:.0f} min ago")
    elif jobs == []:
        out.append(f"❌ Scheduler agent — ran {m:.0f} min ago, but no scheduled "
                   f"job is registered, so nothing will run it again")
    elif jobs is None:
        # "·" and not a tick, the same as the live-price chain: this cannot be
        # checked without launchctl, and a claim nobody could verify is worse
        # than an admission. Off a Mac, every run lands here.
        out.append(f"· Scheduler agent — ran {m:.0f} min ago; whether it is "
                   f"scheduled to run again cannot be checked from here")
    else:
        out.append(f"✅ Scheduler agent — last ran {m:.0f} min ago")

    # Its own stamp, not pgrep -- see _beat.
    m = _age(LISTENER_BEAT)
    if m is None:
        out.append("❌ Telegram listener — never polled")
    elif m < 3:
        pid = json.loads(LISTENER_BEAT.read_text()).get("pid")
        out.append("✅ Telegram listener — polling"
                   + (f" (pid {pid})" if pid else ""))
    else:
        out.append(f"❌ Telegram listener — stopped polling {m:.0f} min ago")

    raw = ROOT / "data" / "raw"
    days = (sorted(p.name for p in raw.iterdir()
                   if (p / "bhavcopy_delivery.csv").exists())
            if raw.exists() else [])
    if not days:
        out.append("❌ Market data — nothing downloaded")
    else:
        # Freshness is NOT "is there a newest file" -- there always is. It is
        # whether a weekday that should have one is missing, which needs the
        # holiday list and the 18:00 publication time. agent already does both.
        try:
            import agent as _a
            behind = _a._gaps_outstanding()
        except Exception:
            behind = None
        at = _dt.fromtimestamp((raw / days[-1] / "bhavcopy_delivery.csv")
                               .stat().st_mtime)
        out.append(("·" if behind is None else "❌" if behind else "✅") +
                   f" Market data — newest {days[-1]}, fetched {at:%d %b %H:%M}"
                   + ("  — A SESSION IS MISSING" if behind else ""))
        # Deliberately a separate line from freshness. These answer different
        # questions and one number was doing both: this is how much history the
        # ranking can see, not whether today arrived.
        out.append(f"✅ Data history — {len(days)} sessions, "
                   f"{days[0]} to {days[-1]}")

    try:
        import live_source
        tok = live_source.env_value("UPSTOX_ACCESS_TOKEN")
        h = live_source.token_hours_left(tok) if tok else None
        if not tok:
            out.append("· Upstox — no token set; fills fall through the chain")
        elif h is None:
            out.append("❌ Upstox — token is not a JWT and will be refused")
        elif h <= 0:
            out.append(f"❌ Upstox — token expired {-h:.1f} h ago; log in again "
                       f"for live fills")
        else:
            out.append(f"✅ Upstox — token good for another {h:.1f} h")
        # NOT "quote order". "quote" is the word the live_source rename
        # removed, and "order" in this bot means an instruction to buy -- two
        # meanings of one word on a screen that also lists pending orders is
        # exactly the collision rules.md R1 names.
        out.append("· Live prices — tries " +
                   ", then ".join(f.__name__.lstrip("_") for f in live_source.CHAIN) +
                   " (not checked just now)")
    except Exception as e:
        out.append(f"· Upstox — cannot tell ({type(e).__name__})")

    try:
        import positions
        s = positions.summary()
        # COUNTS, not money. summary()["equity"] is CAPITAL + realised: it
        # never marks the open positions to market, so with nothing closed it
        # returns the starting capital exactly. Printing that as "worth" reads
        # to anyone as today's value of the bucket, which it is not.
        # /wallet is the one place money is quoted, off live prices.
        # One line PER BUCKET. Reporting only main would have shown a green
        # tick while the pool sat dead, which is the shape of every /health
        # defect this file has already fixed: a check that cannot see the
        # thing it claims to cover.
        for _name, _key, _rows in _groups(positions.summary(which=None)["rows"]):
            if _key not in positions.BUCKETS:
                continue
            _o = sum(1 for r in _rows if r["status"] == "open")
            _p = sum(1 for r in _rows if r["status"] == "pending")
            _c = sum(1 for r in _rows if r["status"] == "closed")
            out.append(f"✅ {_name.title()} — {_o} held, {_p} buying at the "
                       f"next open, {_c} finished")
    except Exception as e:
        out.append(f"· Buckets — cannot tell ({type(e).__name__})")
    try:
        import agent
        due = agent.due()
        att = agent.attention()
        # agent.PLAIN, not the raw job names: "pbook" means nothing to a
        # person and R3 says fix the word rather than gloss it. Empty is the
        # good state for both, so both say so in a word rather than leaving a
        # bare "nothing" the reader has to interpret.
        # One per LINE, not comma-joined. "step the bucket -- stops, targets,
        # day count" contains its own commas, so a comma-joined list of six ran
        # together into one sentence with no way to see where a job ended.
        out += ["", "*Jobs waiting to run*"]
        out += ([f"· {agent.PLAIN.get(d, d)}" for d in due] if due
                else ["none — everything scheduled has run"])
        out += ["", "*Problems*"]
        out += ([f"· {a}" for a in att] if att else ["none"])
    except Exception as e:
        out.append(f"· agent state unavailable ({type(e).__name__})")
    return "\n".join(out)



def cmd_review(_=None):
    """The daily read: what the bucket holds, what it would buy next, and whether
    anything has EARNED a change.

    Suggestions are printed, never applied. This project's own record (L47) is
    that parameter tuning on this bucket anti-predicts out of sample, so a daily
    job that quietly retunes the strategy would be the single most damaging
    thing to automate here. It proposes; a person decides and re-simulates.
    """
    import analysis
    import learning
    import positions
    s = positions.summary()
    allb = positions.summary(which=None)
    closed = [r for r in s["rows"] if r["status"] == "closed" and r["entry_px"]]
    # COUNTS, not money. summary()["equity"] is CAPITAL + realised and never
    # marks the open positions to market, so with nothing closed it returns the
    # starting capital exactly -- this line printed "worth Rs 300,000" on the
    # same day /wallet correctly said Rs 303,205. /health already carried a
    # comment warning about this and pointed HERE as a place that got it right.
    # It did not. Money is quoted in one place, off live prices: /wallet.
    out = [_title("DAILY REVIEW", str(datetime.now().date())), "",
           f"*Bucket*  {s['open']} held · {s['pending']} buying tomorrow · "
           f"{s['closed']} finished",
           "_/wallet for what it is worth today._"]
    # Two positions were opened by the deeper buckets that have since been
    # removed. They run to their own exits and are counted here so the totals
    # match /open_orders, which would otherwise disagree.
    if allb["open"] + allb["pending"] != s["open"] + s["pending"]:
        out.append(f"*Including retired buckets*  {allb['open']} held · "
                   f"{allb['pending']} buying tomorrow · {allb['closed']} finished")

    trades = [{"ret": (r["exit_px"] / r["entry_px"] - 1) * 100} for r in closed]
    out += ["", "*What the results so far can prove*",
            "_" + analysis.verdict(trades) + "_"]

    out += ["", "*Anything worth changing?*"]
    try:
        # main ONLY. The pool's trades are recorded and reported, but they
        # must never move the weights: they would feed back into the bucket's
        # own picks and the two would stop being independent records.
        led = learning.for_weights(learning.load())
        cur = learning.load_weights()
        new, notes = learning.propose(led, current=dict(cur))
        moved = {k: (cur.get(k, 1.0), v) for k, v in new.items()
                 if abs(v - cur.get(k, v)) > 1e-6}
        # A uniform rescale changes nothing -- the score is a weighted average,
        # so one common factor cancels. propose() says so in its notes and this
        # must repeat it, not print four numbers that move the bucket nowhere.
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
        import selection
        corpus = features.load_corpus()
        as_of = max(d for x in corpus.values() for d in x.days)
        picks = selection.allocate(selection.build(corpus, as_of))
        held = {r["symbol"] for r in positions.summary(which=None)["rows"]
                if r["status"] in ("open", "pending")}
        out += ["", f"*Chosen tonight* ({as_of})"]
        for r in picks:
            out.append(f"  {'🟢' if r['symbol'] in held else '⚪'} "
                       f"{r['symbol']} — {SIZE.get(r['cluster'], r['cluster'])}, "
                       f"score {r['score']:.0f}")
        if not picks:
            out.append("  _nothing triggered_")
        out.append("_/bucket for why · /clusters for the full ranking._")
    except Exception as e:
        out.append(f"_picks unavailable ({type(e).__name__})_")
    return "\n".join(out)


def notify(title, lines):
    """Push an event the user should see without asking. Never raises.

    The command rewrite dropped the old push_learning() and left two callers in
    daily.py pointing at nothing, so the very first forward fill reported
    "telegram push failed: AttributeError" and no message went out. The audit
    now checks that these callers resolve.
    """
    body = "*" + str(title) + "*\n" + "\n".join(f"• {l}" for l in lines)
    try:
        return send(body)
    except Exception as e:
        return {"ok": False, "error": type(e).__name__}


def cmd_help(_=None):
    """The nine commands, grouped by the question each one answers.

    Grouped by QUESTION, not by module. The old list was ordered by where the
    code lives -- "the pipeline, in order" -- which is the one ordering nobody
    reading this has in their head. It also lost the blank line before *System*,
    so /health rendered glued to the line above it.
    """
    return ("*WHAT YOU CAN ASK*\n\n"
            "*How much money is there?*\n"
            "/wallet — cash, what is invested, profit so far\n\n"
            "*What am I in, and what is next?*\n"
            "/open-orders — bought and running in the market now\n"
            "/pending-orders — chosen, waiting to be bought tomorrow morning\n"
            "/closed-orders — finished, and what each one made or lost\n\n"
            "*Why these stocks?*\n"
            "/bucket — the 5 chosen this session, with the reason for each\n"
            "/pool — the pool\'s 5, ranked with no size quota\n"
            "/clusters — the full ranking they were chosen from\n\n"
            "*Is any of this actually working?*\n"
            "/findings — every result recorded, and what it can prove\n"
            "/review — tonight\'s read: holdings, evidence, suggestions\n\n"
            "*Is the bot alive?*\n"
            "/health — every moving part, checked\n\n"
            "_Hyphens work too: /pending-orders, /open-orders, /closed-orders._\n"
            "_Read-only. I never place a trade or change a setting from here._")


# Telegram only autocompletes underscores, but the hyphen spellings are
# what was asked for and arrive intact as plain text, so both are bound.
# /portfolio and /picks are GONE, not renamed. Keeping an old spelling alive
# looks free and is not: /portfolio is the word rules.md R1 bans by name, and
# /picks was a second name for /bucket, so the same screen had two names and
# the codebase had two vocabularies. An unknown command already answers with
# /help, which is a better outcome than silently teaching the banned word.
COMMANDS = {"/wallet": cmd_wallet, "/clusters": cmd_clusters,
            "/bucket": cmd_bucket,
            "/pool": cmd_pool,
            "/pending_orders": cmd_pending_orders,
    "/pending-orders": cmd_pending_orders,
            "/open_orders": cmd_open_orders, "/open-orders": cmd_open_orders,
            "/closed_orders": cmd_closed_orders,
            "/closed-orders": cmd_closed_orders,
            "/findings": cmd_findings, "/review": cmd_review,
            "/health": cmd_health,
            "/help": cmd_help, "/start": cmd_help}

def canon(name):
    """-> one spelling for a command bound under two. /open-orders and
    /open_orders are the same screen, and both the selftest and the audit ask
    "is every command in /help" -- a question that must not depend on which
    spelling /help happens to print. It depended on it twice: the audit searched
    for `c.replace("_", "\\_")`, the escaped form, and the selftest dropped any
    match containing a hyphen. Both broke the moment /help switched to the hyphen
    spellings, and neither was wrong about anything a reader cares about.
    """
    return name.replace("-", "_")


# Bound and deliberately NOT in /help, because neither is a screen: /start is
# Telegram's handshake and /help is the map itself. Every other command must be
# findable there under one of its spellings. The hyphen forms used to be listed
# here too, which made this set a list of spellings rather than a list of
# exemptions -- see canon().
ALIASES = {"/start", "/help"}



def _offset(new=None):
    if new is not None:
        OFFSET.parent.mkdir(parents=True, exist_ok=True)
        OFFSET.write_text(json.dumps({"offset": new}))
        return new
    return json.loads(OFFSET.read_text())["offset"] if OFFSET.exists() else 0


def _beat():
    """Stamped before every poll, so /health need not ask pgrep about us.

    `pgrep -f "tg.py --listen"` was the old probe and it was wrong in BOTH
    directions.

    It reported the listener DOWN while pid 76945 was serving the very /health
    that printed the cross (data/telegram.log, 2026-08-19). A launchd-spawned
    process cannot reliably enumerate processes, and cmd_health tested only
    `r.stdout` and never `r.returncode` -- so "pgrep was not allowed to look"
    and "nothing is running" were one single observation. Exactly the blind spot
    _call had: the tool reported the failure and the caller discarded it.

    It was also wrong the other way. A wedged poll keeps the process alive, so
    pgrep answers yes about a listener that is answering nothing -- the failure
    the note in _selftest already describes.

    A stamp written by the loop cannot be wrong in either direction: nothing
    else writes it, and it stops the moment the loop stops going round.
    """
    import os
    LISTENER_BEAT.parent.mkdir(parents=True, exist_ok=True)
    LISTENER_BEAT.write_text(json.dumps({"at": datetime.now().isoformat(),
                                         "pid": os.getpid()}))


def poll_once(timeout=25):
    """-> number of messages handled. Ignores anything not on the allowlist."""
    allowed = readers()
    r = _call("getUpdates", {"offset": _offset(), "timeout": timeout},
              timeout=timeout + 10)
    if not r.get("ok"):
        return 0
    handled = 0
    for upd in r.get("result", []):
        _offset(upd["update_id"] + 1)
        msg = upd.get("message") or {}
        chat = str((msg.get("chat") or {}).get("id", ""))
        if chat not in allowed:
            continue                      # not on the allowlist: ignore silently
        raw = (msg.get("text") or "").strip()
        tok = raw.split()[0].lower() if raw.split() else ""
        # Telegram appends @botname when a command is chosen from the
        # autocomplete menu ("/pending_orders@swingalpha_bot"). Without stripping
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
    global OFFSET, ROOT, _call
    o_off = OFFSET
    try:
        with tempfile.TemporaryDirectory() as td:
            OFFSET = Path(td) / "off.json"
            assert _offset() == 0
            _offset(42)
            assert _offset() == 42, "offset not persisted; messages would repeat"
    finally:
        OFFSET = o_off

    # ------------------------------------------------------------------
    # A FAILED SEND MUST REACH THE LOG. It did not: poll_once discarded the
    # return value, so a rejected reply left "recv" as the last line and the
    # listener read as healthy while answering nothing.
    # ------------------------------------------------------------------
    import contextlib, io
    o_raw, _call._last = _call_raw, None
    try:
        globals()["_call_raw"] = lambda m, p, t=30: {
            "ok": False, "error": "HTTPError",
            "description": "Bad Request: can't parse entities"}
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            r = _call("sendMessage", {"text": "x"})
            _call("sendMessage", {"text": "x"})      # same failure again
        assert not r.get("ok")
        log = buf.getvalue()
        assert "can't parse entities" in log, \
            f"the reason Telegram gave was not logged: {log!r}"
        assert log.count("sendMessage failed") == 1, \
            f"a repeated outage flooded the log: {log!r}"
    finally:
        globals()["_call_raw"], _call._last = o_raw, None
    print("  a failed send reaches the log ok (once per distinct reason)")

    # ------------------------------------------------------------------
    # /health MUST NOT REPORT A LIVE LISTENER AS DOWN. pgrep did, while this
    # very process served the request. Both directions asserted, because the
    # replacement is only an improvement if it also catches a wedged loop.
    # ------------------------------------------------------------------
    global LISTENER_BEAT
    o_beat = LISTENER_BEAT
    try:
        with tempfile.TemporaryDirectory() as td:
            LISTENER_BEAT = Path(td) / "beat.json"
            _beat()
            assert "✅ Telegram listener" in cmd_health(), \
                f"a listener that just polled read as down: {cmd_health()!r}"
            stale = (datetime.now() - timedelta(minutes=30)).isoformat()
            LISTENER_BEAT.write_text(json.dumps({"at": stale, "pid": 1}))
            assert "❌ Telegram listener" in cmd_health(), \
                "a listener that stopped polling 30 min ago read as healthy"
            LISTENER_BEAT.unlink()
            assert "❌ Telegram listener" in cmd_health(), \
                "no heartbeat at all read as healthy"
    finally:
        LISTENER_BEAT = o_beat
    # The message grew a lot on 2026-08-19. send() rejects unbalanced * or _
    # and literal %% -- three past failures reached the phone, so assert the
    # rendered text survives its own validator rather than finding out live.
    o_raw2 = _call_raw
    try:
        globals()["_call_raw"] = lambda m, p, t=30: {"ok": True, "result": {}}
        assert send(cmd_health(), chat_id="1").get("ok"), \
            "cmd_health output was rejected by send()"
    finally:
        globals()["_call_raw"] = o_raw2
    print("  /health reads the listener from its own stamp ok (both directions)")

    # ------------------------------------------------------------------
    # ONE MESSAGE MUST NOT CONTRADICT ITSELF. /health ticked "Scheduler agent —
    # last ran 4 min ago" four lines above its own "no launchd job registered --
    # nothing runs on a schedule", because the stamp is written by any run,
    # including `agent.py --once` typed by hand. Driven through cmd_health with
    # _jobs_loaded stubbed, not grepped, so a correct rewrite still passes.
    # ------------------------------------------------------------------
    import agent as _ag
    o_root, o_jobs = ROOT, _ag._jobs_loaded
    try:
        with tempfile.TemporaryDirectory() as td:
            ROOT = Path(td)
            (ROOT / "data").mkdir()
            (ROOT / "data" / "agent_heartbeat.json").write_text(
                json.dumps({"at": datetime.now().isoformat()}))
            _ag._jobs_loaded = lambda: ["com.sudhanshu.tradingbot.agent"]
            assert "✅ Scheduler agent" in cmd_health(), \
                "a scheduled agent that just ran read as broken"
            _ag._jobs_loaded = lambda: []
            h = cmd_health()
            assert "✅ Scheduler agent" not in h, \
                "a fresh stamp with no scheduled job wore a tick: nothing will run it again"
            assert "no scheduled job is registered" in h, h[:400]
            # None is NOT []. _jobs_loaded returned [] for "launchctl could not
            # be run" too, so every container and every Linux box read as
            # "nothing is scheduled" on no evidence at all.
            _ag._jobs_loaded = lambda: None
            h = cmd_health()
            assert "✅ Scheduler agent" not in h and "· Scheduler agent" in h, \
                f"unknown schedule was reported as a fact: {h[:200]!r}"
            assert "no scheduled job is registered" not in h, \
                "claimed nothing is scheduled when launchctl could not be asked"
    finally:
        ROOT, _ag._jobs_loaded = o_root, o_jobs
    print("  /health ticks the scheduler only when a job is registered ok")

    # ------------------------------------------------------------------
    # THE ALLOWLIST IS THE WHOLE SECURITY MODEL, so it is tested by driving
    # poll_once itself rather than by grepping for the line that implements it.
    # The previous version asserted the source contained `if chat != owner:`,
    # which passes for any spelling of the check and fails for a correct
    # rewrite -- it protected the text, not the behaviour.
    o_root, o_off, o_call = ROOT, OFFSET, _call
    try:
        with tempfile.TemporaryDirectory() as td:
            ROOT, OFFSET = Path(td), Path(td) / "off.json"
            (ROOT / ".env").write_text("TELEGRAM_CHAT_ID= 111 , 222 \n")
            assert readers() == ["111", "222"], readers()
            assert owner() == "111", "pushes must go to one id, not the list"

            answered = []

            def _fake(method, params, timeout=30):
                if method == "getUpdates":
                    return {"ok": True, "result": [
                        {"update_id": i,
                         "message": {"chat": {"id": int(c)}, "text": "/help"}}
                        for i, c in enumerate(("111", "999", "222"), 1)]}
                answered.append(str(params.get("chat_id")))
                return {"ok": True}

            _call = _fake
            assert poll_once(timeout=0) == 2, "a stranger was served"
            assert answered == ["111", "222"], answered
            assert "999" not in answered, "the bot answered a chat it does not know"

            # fail-closed: no configured id must match NOBODY. An empty
            # allowlist reading as "allow anyone" would expose the whole
            # bucket to whoever finds the bot's handle.
            (ROOT / ".env").write_text("TELEGRAM_BOT_TOKEN=x\n")
            OFFSET.unlink()
            answered.clear()
            assert readers() == [] and owner() == ""
            assert poll_once(timeout=0) == 0 and not answered, \
                "an unset TELEGRAM_CHAT_ID served everyone"
    finally:
        ROOT, OFFSET, _call = o_root, o_off, o_call

    src = Path(__file__).read_text()
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
    listed = {canon(m) for m in _re.findall(r"/[a-z][a-z_-]*", cmd_help(None))}
    have = {canon(c) for c in COMMANDS} - {canon(a) for a in ALIASES}
    for a in ALIASES:
        assert a in COMMANDS, f"{a} is listed as an alias but is not bound"
    assert listed == have, (sorted(listed ^ have),
                            "help and COMMANDS disagree")
    assert canon("/open-orders") == canon("/open_orders") == "/open_orders"

    # Every command must survive an EMPTY bucket. The bot is at its most useful
    # before the first trade, which is exactly when every record is missing.
    import positions as _pb
    _orig = _pb.summary
    try:
        _pb.summary = lambda *a, **k: {"pending": 0, "open": 0, "closed": 0,
                                       "realised": 0.0, "equity": 0.0, "rows": []}
        for name in ("/wallet", "/open_orders",
                     "/closed_orders", "/findings"):
            out = COMMANDS[name](None)
            assert isinstance(out, str) and out, name
    finally:
        _pb.summary = _orig
    # The order layout. Asserted on the two helpers plus a source check that
    # all three commands route through them -- rendering a real command needs
    # the corpus and a live quote, and a check that needs the network is a
    # check that gets skipped.
    # a stop sits BELOW the price, so reaching it is a fall -- and both
    # distances must measure from the same place, or the two lines disagree
    # about what "away" means while looking identical
    assert _away(881.95, 810.0) < 0 < _away(881.95, 1080.0), \
        "the stop must read as a fall and the target as a rise"
    assert round(_away(881.95, 810.0), 1) == -8.2, "distance is measured from px"
    assert _away(0, 810.0) == 0 and _away(100.0, None) == 0, "no divide by zero"
    assert "_away(px" in src.split("def cmd_open_orders")[1].split("\ndef ")[0], \
        "open_orders computes its own distance again; the two will drift apart"

    # The chosen set is the top k of each cluster and nothing else. /bucket and
    # /clusters both render off this, and they disagreed on every name that had
    # not broken out yet while each computed it its own way.
    _rw = [{"symbol": "A", "cluster": "micro"}, {"symbol": "B", "cluster": "micro"},
           {"symbol": "C", "cluster": "micro"}, {"symbol": "D", "cluster": "small"}]
    assert [r["symbol"] for r in _chosen(_rw, {"micro": 2, "small": 1})] == \
        ["A", "B", "D"], "the bucket takes the top k of each cluster, in order"
    assert _chosen(_rw, {"micro": 0, "small": 9}) == [_rw[3]], \
        "k=0 takes none and k>n takes all of what there is"
    assert _chosen([], {"micro": 3}) == [], "an empty ranking chooses nothing"
    _cl = src.split("def cmd_clusters")[1].split("\ndef ")[0]
    assert "allocate(" not in _cl, \
        "/clusters marks names post-trigger again; it will disagree with /bucket"

    # The listener restarts when its source changes, and "its source" has to mean
    # every module it can import. Watching only src/ops/ is the state the src/
    # move silently left it in, and a listener serving stale selection rules is
    # the exact failure CLAUDE.md devotes a section to.
    _w = {p.name for d in paths.SRC + ("src",)
          for p in (paths.ROOT / d).glob("*.py")}
    for _need in ("selection.py", "positions.py", "features.py", "tg.py",
                  "paths.py", "engine.py"):
        assert _need in _w, f"the listener would not notice {_need} changing"

    assert _fields(("filled", "n/a"), ("entry", None), ("qty", 51)) == \
        ["filled - n/a", "qty - 51"], "a blank field must be dropped, not printed"
    assert _fields() == []
    _r = {"bucket": _pb.MAIN}
    assert _review({**_r, "exit_reason": "target"}, 20.0, 6).startswith("Worked.")
    assert "before the loss" in _review({**_r, "exit_reason": "stop"}, -10.2, 3)
    assert "10-day limit" in _review({**_r, "exit_reason": "time"}, 1.4, 10)
    # an unrecognised reason must still say what happened, not invent a rule
    assert "sold: void" in _review({**_r, "exit_reason": "void"}, 0.0, 1)
    for _c in ("cmd_pending_orders", "cmd_open_orders", "cmd_closed_orders"):
        assert "_fields(" in src.split(f"def {_c}")[1].split("\ndef ")[0], \
            f"{_c} does not use the shared layout; the three will drift apart"
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
        # Watch EVERY module this process can import, not just this file. tg.py
        # imports selection, positions, features and agent at request time and
        # holds them in memory, so editing one of them left the bot serving stale
        # logic while tg.py was untouched -- it kept reporting attention items
        # that had already been fixed.
        #
        # This globbed Path(__file__).parent, which MEANT the source tree while
        # tg.py sat at the repo root and meant src/ops/ alone the moment it moved
        # there: 10 files instead of 33, with selection.py, positions.py and
        # features.py -- the ones an edit actually changes the answers of -- no
        # longer watched. paths.SRC is the list of directories whose modules are
        # importable here, which is the set this loop was always describing, and
        # it cannot go stale when a file moves.
        _watched = {p: p.stat().st_mtime
                    for d in paths.SRC + ("src",)
                    for p in (paths.ROOT / d).glob("*.py")
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
                _beat()
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
                print(f"poll error: {type(e).__name__}: {e}", flush=True)
    else:
        print(__doc__)
