#!/usr/bin/env python3
"""Live intraday prices.

The book is otherwise driven by the end-of-day bhavcopy, which is the right
source for anything that must be reproducible. This module exists only for the
two things that genuinely need the market to be open: filling an order at the
morning's actual opening price, and showing a running position's profit while
it is still running.

NO FREE UNAUTHENTICATED SOURCE CURRENTLY WORKS. Measured 2026-08-17:

    www.nseindia.com/api/quote-equity        403  (its report endpoints are 200)
    query1/query2.finance.yahoo.com          429

So `live()` returns {} until a provider is registered. Everything downstream
must treat an empty quote as "unknown", never as zero -- a position whose price
cannot be fetched is not a position worth nothing.
"""
import json
import urllib.request

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

_PROVIDER = None


def set_provider(fn):
    """Register a callable: symbols -> {symbol: {"ltp", "open", "high", "low"}}.

    Kept as a hook rather than a hard-coded broker so the credentialed source
    can be swapped without touching the book.
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

def upstox(symbols):
    """Official Upstox quotes. Needs UPSTOX_ACCESS_TOKEN in .env."""
    import os
    tok = os.environ.get("UPSTOX_ACCESS_TOKEN")
    if not tok:
        p = __import__("pathlib").Path(__file__).resolve().parent / ".env"
        if p.exists():
            for line in p.read_text().splitlines():
                if line.startswith("UPSTOX_ACCESS_TOKEN="):
                    tok = line.split("=", 1)[1].strip()
    if not tok:
        return {}
    keys = ",".join(f"NSE_EQ|{s}" for s in symbols)
    req = urllib.request.Request(
        f"https://api.upstox.com/v2/market-quote/quotes?instrument_key={keys}",
        headers={"Authorization": f"Bearer {tok}", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            data = json.loads(r.read()).get("data", {})
    except Exception:
        return {}
    out = {}
    for k, v in data.items():
        sym = k.split(":")[-1]
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


def live(symbols):
    """-> {symbol: quote}. Empty when no source is available. Never raises."""
    if not symbols:
        return {}
    if _PROVIDER:
        try:
            return _PROVIDER(list(symbols)) or {}
        except Exception:
            return {}
    for fn in (upstox, _nse, google):
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
    fn = _PROVIDER or {"upstox": upstox, "_nse": _nse,
                       "google": google}.get(getattr(live, "source", None))
    return bool(fn) and getattr(fn, "authoritative", True)


def available():
    """-> True only if a real quote comes back. Never trust configuration."""
    return bool(live(["RELIANCE"]))


def _selftest():
    assert live([]) == {}
    # a provider that fails must degrade to {}, not explode
    set_provider(lambda syms: (_ for _ in ()).throw(RuntimeError("down")))
    assert live(["X"]) == {}, "a broken provider must return empty"
    # a working provider is used
    set_provider(lambda syms: {s: {"ltp": 10.0, "open": 9.0} for s in syms})
    q = live(["A", "B"])
    assert q["A"]["ltp"] == 10.0 and len(q) == 2, q
    assert provider_name() == "<lambda>"
    set_provider(None)
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
