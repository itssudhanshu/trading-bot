#!/usr/bin/env python3
"""Live intraday prices.

The bucket is otherwise driven by the end-of-day bhavcopy, which is the right
source for anything that must be reproducible. This module exists only for the
two things that genuinely need the market to be open: filling an order at the
morning's actual opening price, and showing a running position's profit while
it is still running.

`live()` walks CHAIN and returns the first source that answers. Registering a
provider with set_provider() replaces the chain entirely; nothing needs to be
registered for the built-ins to be tried.

WHICH SOURCE ANSWERS IS NOT KNOWABLE FROM READING THIS FILE. It depends on the
token, the hour and whoever is rate-limiting today, so the only honest record
here is dated observations, never a standing claim:

    2026-08-17  www.nseindia.com/api/quote-equity   403 (its reports are 200)
    2026-08-17  query1/query2.finance.yahoo.com     429
    2026-08-19  all four hosts unreachable from a sandboxed session
                (URLError: Tunnel connection failed: 403) -- which is the
                sandbox, not the source, and says nothing about either

Run `python3 core/quotes.py` to see what actually answers now. A previous
version of this docstring concluded from the two 2026-08-17 measurements that
`live()` "returns {} until a provider is registered", which was never true of
the code and sent a reader looking for a provider to register.

Everything downstream must treat an empty quote as "unknown", never as zero --
a position whose price cannot be fetched is not a position worth nothing.
"""

import sys as _sys, pathlib as _pl
_sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[1]))
import paths  # noqa: F401  -- puts the source dirs on sys.path

import paths
import json
import urllib.request

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

_PROVIDER = None


def set_provider(fn):
    """Register a callable: symbols -> {symbol: {"ltp", "open", "high", "low"}}.

    Kept as a hook rather than a hard-coded broker so the credentialed source
    can be swapped without touching the bucket.
    """
    global _PROVIDER
    _PROVIDER = fn


def provider_name():
    return getattr(_PROVIDER, "__name__", None) if _PROVIDER else None


# --------------------------------------------------------------- providers
# Two sources, and the difference between them is the whole design:
#
#   upstox  official API, documented fields, needs a token. AUTHORITATIVE --
#           may be used to fill orders.
#   google  scraped HTML. The fields are inferred from their POSITION on the
#           page, not from documentation: on 2026-08-17 the second rupee value
#           behaved like the open for 7 of 7 symbols, but YUKEN's was also
#           within a rupee of the previous close, so the inference is not safe.
#           DISPLAY ONLY -- never fills an order.

INSTRUMENTS = paths.DATA / "upstox_instruments.json"


def token_hours_left(tok):
    """-> hours until an Upstox JWT expires, or None if it is not a JWT.

    Only the `exp` claim is read. Nothing identifying is decoded or logged.
    """
    import base64
    import datetime as _dt
    import json as _j
    parts = (tok or "").split(".")
    if len(parts) != 3:
        return None
    try:
        pad = parts[1] + "=" * (-len(parts[1]) % 4)
        exp = _j.loads(base64.urlsafe_b64decode(pad)).get("exp")
    except Exception:
        return None
    if not exp:
        return None
    return (exp - _dt.datetime.now().timestamp()) / 3600


def env_value(name):
    """-> a value from the environment or .env, or "" if unset/blank.

    Tolerant of `export `, surrounding quotes and stray whitespace, and treats
    a key present with an EMPTY value as unset -- which is exactly how an
    unfilled `UPSTOX_ACCESS_TOKEN=` line presented, while every check for "is
    the key there?" said yes.
    """
    import os
    v = os.environ.get(name) or ""
    if not v.strip():
        p = paths.ROOT / ".env"
        if p.exists():
            for line in p.read_text().splitlines():
                s = line.strip()
                if s.startswith("export "):
                    s = s[7:].lstrip()
                k, sep, val = s.partition("=")
                if sep and k.strip() == name:
                    v = val.strip().strip("'\"")
                    break
    return v.strip()


def instrument_keys(symbols=None, refresh=False):
    """-> {trading_symbol: instrument_key} for NSE equities.

    Upstox keys are ISIN-based -- NSE_EQ|INE330T01021, not NSE_EQ|HAPPYFORGE.
    The original code built the symbol form, which resolves to nothing, so a
    valid token would still have returned an empty quote and the morning fill
    would have declined for a reason that looked like "no token". Verified
    against the published instrument master: HAPPYFORGE is INE330T01021.

    Cached on disk because the master is 82k rows and changes only when
    listings do. Refetched when a symbol is missing, which is what a new
    listing looks like.
    """
    import gzip
    import json as _j
    m = {}
    if INSTRUMENTS.exists() and not refresh:
        try:
            m = _j.loads(INSTRUMENTS.read_text())
        except Exception:
            m = {}
    if m and symbols and not set(symbols) - set(m):
        return m
    try:
        req = urllib.request.Request(
            "https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz",
            headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=60) as r:
            rows = _j.loads(gzip.decompress(r.read()))
        m = {x["trading_symbol"]: x["instrument_key"] for x in rows
             if x.get("segment") == "NSE_EQ" and x.get("trading_symbol")
             and x.get("instrument_key")}
        INSTRUMENTS.parent.mkdir(parents=True, exist_ok=True)
        INSTRUMENTS.write_text(_j.dumps(m))
    except Exception:
        pass
    return m


def upstox(symbols):
    """Official Upstox quotes. Needs UPSTOX_ACCESS_TOKEN in .env.

    NOTE: Upstox access tokens expire daily around 03:30 IST. A pasted token
    works for one session; unattended daily use needs the API key/secret login
    flow to mint a fresh one. Until then this returns {} after expiry and the
    fill falls through to Yahoo, which is why the chain is ordered that way.
    """
    upstox.last_error = None
    tok = env_value("UPSTOX_ACCESS_TOKEN")
    if not tok:
        upstox.last_error = "no UPSTOX_ACCESS_TOKEN in .env"
        return {}
    # Check the expiry OURSELVES. Upstox tokens are JWTs that die around 03:30
    # IST daily, and a stale one answers 401 "Invalid token" -- which reads as
    # "you pasted it wrong" and sent this debugging down the wrong path. A
    # token six days past expiry should say so.
    left = token_hours_left(tok)
    if left is not None and left <= 0:
        upstox.last_error = (f"UPSTOX_ACCESS_TOKEN expired {-left:.0f}h ago; "
                             f"run `python3 upstox_login.py`")
        return {}
    keymap = instrument_keys(symbols)
    keys = ",".join(keymap[s] for s in symbols if s in keymap)
    if not keys:
        upstox.last_error = (f"no instrument key for {','.join(symbols)} "
                             f"(new listing? try instrument_keys(refresh=True))")
        return {}
    # A User-Agent is REQUIRED. Without one urllib sends "Python-urllib/3.x"
    # and Cloudflare answers 403 error 1010 "browser_signature_banned" before
    # the request reaches Upstox at all -- which looks exactly like a bad
    # token and sent this debugging down the wrong path once.
    req = urllib.request.Request(
        f"https://api.upstox.com/v2/market-quote/quotes?instrument_key={keys}",
        headers={"Authorization": f"Bearer {tok}", "Accept": "application/json",
                 "User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            data = json.loads(r.read()).get("data", {})
    except Exception as e:
        # RECORD why, same as yahoo(). A bare `except: return {}` made a
        # blocked egress, a 401 and a 429 all read as "no data" -- and since
        # why_no_quote() then had nothing from upstox, the morning log blamed
        # whatever yahoo said, i.e. the token. That is the L57 trap again: four
        # distinct Upstox failures that all presented identically.
        code = getattr(e, "code", None)
        upstox.last_error = (f"HTTP {code}" if code
                             else f"{type(e).__name__}: {getattr(e, 'reason', e)}")
        return {}
    # Responses come back keyed by "NSE_EQ:SYMBOL", so map back by ISIN too.
    back = {v: k for k, v in keymap.items()}
    out = {}
    for k, v in data.items():
        sym = back.get(v.get("instrument_token") or "", k.split(":")[-1])
        ohlc = v.get("ohlc") or {}
        if v.get("last_price"):
            out[sym] = {"ltp": v["last_price"], "open": ohlc.get("open"),
                        "high": ohlc.get("high"), "low": ohlc.get("low")}
    return out


upstox.authoritative = True


def google(symbols):
    """Scraped from Google Finance. Display only.

    Sanity-checked against the day range: a parsed 'open' that falls outside
    the parsed high/low is a parse error, not a price, and is dropped rather
    than returned. Silence is safer than a plausible wrong number.
    """
    import re
    out = {}
    for s in symbols:
        try:
            req = urllib.request.Request(
                f"https://www.google.com/finance/quote/{s}:NSE",
                headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=15) as r:
                html = r.read().decode("utf-8", "replace")
            v = [float(x.replace(",", ""))
                 for x in re.findall(r"\u20b9([\d,]+\.\d{2})", html)[:4]]
            if len(v) < 4:
                continue
            cur, second, hi, lo = v
            if not (lo <= cur <= hi and lo <= second <= hi and lo < hi):
                continue                      # markup moved; do not guess
            out[s] = {"ltp": cur, "open": None, "high": hi, "low": lo}
        except Exception:
            continue
    return out


google.authoritative = False


def yahoo(symbols):
    """Daily bars from Yahoo's chart API. AUTHORITATIVE -- may fill orders.

    Unlike the Google scrape, the fields here are named in a structured JSON
    response rather than inferred from where a number sat on a page. Validated
    before being trusted: 220 of 220 daily opens across 10 symbols and one
    month matched the official NSE bhavcopy exactly, with zero disagreements.

    THE DATE IS CHECKED, NOT ASSUMED. Asked before 09:15 this endpoint happily
    returns YESTERDAY's bar, and filling today's order at yesterday's open
    would be an invisible, permanent error in the entry price. A quote is
    returned only when the newest bar is dated `on` (default: today). Same
    discipline as the bhavcopy holiday trap -- validate the date inside the
    payload, never the fact that a request succeeded.
    """
    import datetime as _dt
    import time as _t
    on = _dt.date.today()
    out = {}
    yahoo.last_error = None
    for s in symbols:
        try:
            req = urllib.request.Request(
                f"https://query1.finance.yahoo.com/v8/finance/chart/{s}.NS"
                f"?range=5d&interval=1d", headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=20) as r:
                d = json.loads(r.read())["chart"]["result"][0]
            bar = yahoo_bar(d, on)
            if bar:
                out[s] = bar
            else:
                yahoo.last_error = yahoo.last_error or "no bar dated today"
        except Exception as e:
            # RECORD why. A bare `except: continue` made "rate limited" look
            # identical to "the market has not opened yet", and the operator
            # could not tell a transient block from a stale bar. Yahoo returns
            # 429 readily -- a handful of symbols a morning is fine, a
            # validation sweep is not.
            code = getattr(e, "code", None)
            yahoo.last_error = (f"HTTP {code}" if code else type(e).__name__)
        _t.sleep(0.4)          # be a polite client; 429 is easy to trigger
    return out


yahoo.last_error = None


def yahoo_bar(result, on):
    """-> the quote for `on`, or None. Split out so the date guard is testable
    without a network call; it is the part that can silently corrupt a fill."""
    import datetime as _dt
    q = result["indicators"]["quote"][0]
    ts = result.get("timestamp") or []
    if not ts:
        return None
    i = len(ts) - 1
    if _dt.datetime.fromtimestamp(ts[i]).date() != on:
        return None                       # stale bar: refuse rather than guess
    o = q["open"][i]
    if not o:
        return None
    hi, lo = q["high"][i], q["low"][i]
    # A printed open outside the day's own range is a parse or feed error,
    # not a price.
    if hi and lo and not (lo - 0.05 <= o <= hi + 0.05):
        return None
    return {"ltp": q["close"][i] or o, "open": o, "high": hi, "low": lo}


yahoo.authoritative = True


upstox.last_error = None


def why_no_quote():
    """-> a human reason the authoritative sources produced nothing."""
    reasons = [r for r in (getattr(upstox, "last_error", None),
                           getattr(yahoo, "last_error", None)) if r]
    return "; ".join(reasons) or "no reason recorded"


def _nse(symbols):
    """NSE's own quote API. Returns {} while it answers 403."""
    out = {}
    for s in symbols:
        try:
            req = urllib.request.Request(
                f"https://www.nseindia.com/api/quote-equity?symbol={s}",
                headers={"User-Agent": UA, "Referer": "https://www.nseindia.com/",
                         "Accept": "*/*"})
            with urllib.request.urlopen(req, timeout=15) as r:
                p = json.loads(r.read()).get("priceInfo", {})
            hl = p.get("intraDayHighLow") or {}
            if p.get("lastPrice"):
                out[s] = {"ltp": p["lastPrice"], "open": p.get("open"),
                          "high": hl.get("max"), "low": hl.get("min")}
        except Exception:
            continue
    return out


# ONE list, because there were two. live() iterated its own tuple and
# authoritative() looked up its own dict, so a fifth source added to the first
# and forgotten in the second would answer quotes and then read as
# non-authoritative -- positions.py declines the fill, logs "no authoritative
# price", and a working feed looks like a missing one. Order matters: the
# authoritative sources come first so a fill never lands on the scrape.
CHAIN = (upstox, yahoo, _nse, google)


# NSE's own API, so the fields are NAMED by the exchange (lastPrice, open,
# intraDayHighLow) rather than inferred from where they sat on a page. That is
# the whole difference from google, and it is why this one may fill an order.
# Stated explicitly because it was fillable only by inheriting getattr()'s
# default -- correct by accident is not the same as correct.
_nse.authoritative = True


def live(symbols):
    """-> {symbol: quote}. Empty when no source is available. Never raises."""
    if not symbols:
        return {}
    if _PROVIDER:
        # Set the source HERE too. It used to be assigned only in the chain
        # loop below, so with a provider registered live.source kept whatever
        # the previous chain call left there -- a stale name that now goes into
        # the order record as the feed that set an entry price.
        live.source = provider_name()
        try:
            return _PROVIDER(list(symbols)) or {}
        except Exception:
            return {}
    for fn in CHAIN:
        try:
            q = fn(list(symbols))
        except Exception:
            q = {}
        if q:
            live.source = fn.__name__
            return q
    live.source = None
    return {}


def authoritative():
    """-> True only if the CURRENT source may be used to fill an order.

    Google's field positions are inferred, so it shows a running P&L but must
    never set an entry price.
    """
    fn = _PROVIDER or {f.__name__: f for f in CHAIN}.get(
        getattr(live, "source", None))
    return bool(fn) and getattr(fn, "authoritative", True)


def available():
    """-> True only if a real quote comes back. Never trust configuration."""
    return bool(live(["RELIANCE"]))


def _yahoo_selftest():
    import datetime as _dt
    today = _dt.date.today()
    t_now = int(_dt.datetime.combine(today, _dt.time(10)).timestamp())
    t_prev = t_now - 86400

    def payload(ts, o=100.0, hi=105.0, lo=99.0, cl=104.0):
        return {"timestamp": ts,
                "indicators": {"quote": [{"open": [o], "high": [hi],
                                          "low": [lo], "close": [cl]}]}}

    got = yahoo_bar(payload([t_now]), today)
    assert got and abs(got["open"] - 100.0) < 1e-9, got
    # yesterday's bar must be refused, not returned as today's open. Asked
    # before 09:15 this endpoint serves exactly that.
    assert yahoo_bar(payload([t_prev]), today) is None, \
        "a stale bar was accepted; a fill would use yesterday's open"
    # an open outside the day's own range is a feed error
    assert yahoo_bar(payload([t_now], o=200.0), today) is None
    # a missing open is not a price
    assert yahoo_bar(payload([t_now], o=None), today) is None
    assert yahoo_bar(payload([]), today) is None
    assert yahoo.authoritative is True and google.authoritative is False
    print("  yahoo date guard ok")


def _upstox_selftest():
    """The instrument key is the part that silently returns nothing when wrong."""
    m = instrument_keys(["HAPPYFORGE"])
    if not m:
        print("  upstox instrument map unavailable (offline?) — skipped")
        return
    k = m.get("HAPPYFORGE")
    assert k and k.startswith("NSE_EQ|INE"), \
        f"instrument key must be ISIN-based, got {k!r} -- NSE_EQ|SYMBOL "
    assert "|" in k and not k.endswith("|HAPPYFORGE")
    # no token configured must be a clean empty, never a crash or a bad fill
    import os
    had = os.environ.pop("UPSTOX_ACCESS_TOKEN", None)
    try:
        import pathlib
        env = paths.ROOT / ".env"
        if "UPSTOX_ACCESS_TOKEN=" not in (env.read_text() if env.exists() else ""):
            assert upstox(["HAPPYFORGE"]) == {}, "no token must yield {}"
    finally:
        if had:
            os.environ["UPSTOX_ACCESS_TOKEN"] = had
    # the expiry gate is what turns a useless 401 into a usable message
    import base64 as _b64, json as _j, time as _t
    def _jwt(exp_delta):
        p = _b64.urlsafe_b64encode(
            _j.dumps({"exp": int(_t.time()) + exp_delta}).encode()).decode().rstrip("=")
        return f"header.{p}.sig"
    assert token_hours_left(_jwt(3600)) > 0.9
    assert token_hours_left(_jwt(-3600)) < 0, "an expired token must read negative"
    assert token_hours_left("not-a-jwt") is None
    assert token_hours_left("") is None
    # A silent {} is the bug L57 kept re-learning: every upstox() failure must
    # leave a REASON behind, or the morning log blames the token by default.
    import urllib.request as _u
    _real = _u.urlopen
    try:
        upstox.last_error = None
        _u.urlopen = lambda *a, **k: (_ for _ in ()).throw(OSError("egress blocked"))
        assert upstox(["HAPPYFORGE"]) == {}, "a failed fetch must yield {}"
        assert upstox.last_error and "OSError" in upstox.last_error, (
            f"a network failure recorded no reason: {upstox.last_error!r}")
        assert "egress blocked" in upstox.last_error, upstox.last_error
    finally:
        _u.urlopen = _real
    # and the reason must not outlive the call that caused it
    assert token_hours_left(_jwt(-3600)) < 0
    upstox(["HAPPYFORGE"])
    assert "OSError" not in (upstox.last_error or ""), \
        "a stale error survived into the next call"
    print(f"  upstox instrument keys ok ({len(m)} cached)")


def _selftest():
    _yahoo_selftest()
    _upstox_selftest()
    assert live([]) == {}
    # a provider that fails must degrade to {}, not explode
    set_provider(lambda syms: (_ for _ in ()).throw(RuntimeError("down")))
    assert live(["X"]) == {}, "a broken provider must return empty"
    # a working provider is used
    set_provider(lambda syms: {s: {"ltp": 10.0, "open": 9.0} for s in syms})
    q = live(["A", "B"])
    assert q["A"]["ltp"] == 10.0 and len(q) == 2, q
    assert provider_name() == "<lambda>"
    # the recorded source must be the provider, not whatever the chain last set
    assert live.source == "<lambda>", (
        f"live.source is {live.source!r}; a stale name would be written into "
        f"the order record as the feed that set an entry price")
    set_provider(None)
    # EVERY source in the chain must declare whether it can fill an order, and
    # authoritative() must recognise every name live() can set. This is the
    # drift that would make a new working feed decline fills silently.
    for _fn in CHAIN:
        assert isinstance(getattr(_fn, "authoritative", None), bool), \
            f"{_fn.__name__} does not say whether it may fill an order"
        live.source = _fn.__name__
        assert authoritative() is _fn.authoritative, \
            f"authoritative() does not recognise {_fn.__name__}"
    live.source = None
    assert CHAIN[0].authoritative and not CHAIN[-1].authoritative, \
        "the chain must try an authoritative source before a display-only one"
    assert getattr(upstox, "authoritative") is True
    assert getattr(google, "authoritative") is False, "google must never fill"
    print("quotes selftest ok")


if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        _selftest()
    else:
        print(f"provider: {provider_name() or 'none (built-in NSE attempt)'}")
        print("live(RELIANCE, HAPPYFORGE):", live(["RELIANCE", "HAPPYFORGE"]) or
              "EMPTY — no working quote source")
