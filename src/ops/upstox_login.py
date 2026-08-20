#!/usr/bin/env python3
"""Mint an Upstox access token and write it into .env.

Run this yourself -- it reads UPSTOX_API_KEY, UPSTOX_API_SECRET and
UPSTOX_REDIRECT_URI from .env, and writes UPSTOX_ACCESS_TOKEN back. Nothing is
printed except the login URL and a success line; the secret and the token are
never echoed.

    python3 upstox_login.py                 # step 1: prints the login URL
    python3 upstox_login.py <code>          # step 2: exchange it for a token

Step 1 opens in a browser. After logging in, Upstox redirects to your
UPSTOX_REDIRECT_URI with `?code=...` on the end. The page itself may fail to
load -- that does not matter, the code is in the address bar. Copy that value
into step 2.

WHY THIS EXISTS AT ALL: Upstox access tokens are not issued with the API key.
They come from an interactive login and they EXPIRE DAILY, around 03:30 IST.
So this has to be re-run each morning before the market opens, or the fill
falls through to Yahoo -- which is the arrangement that already works, and the
reason nothing here depends on Upstox being present.
"""

import sys as _sys, pathlib as _pl
_sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[1]))
import paths  # noqa: F401  -- puts the source dirs on sys.path
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import live_source

from paths import ROOT      # one definition; see paths.py
ENV = ROOT / ".env"
AUTH = "https://api.upstox.com/v2/login/authorization/dialog"
TOKEN = "https://api.upstox.com/v2/login/authorization/token"


def _write_env(key, value):
    """Replace one key's value in .env, leaving every other line untouched."""
    lines = ENV.read_text().splitlines() if ENV.exists() else []
    out, seen = [], False
    for line in lines:
        k, sep, _ = line.partition("=")
        if sep and k.strip() == key:
            out.append(f"{key}={value}")
            seen = True
        else:
            out.append(line)
    if not seen:
        out.append(f"{key}={value}")
    ENV.write_text("\n".join(out) + "\n")


def login_url():
    key = live_source.env_value("UPSTOX_API_KEY")
    uri = live_source.env_value("UPSTOX_REDIRECT_URI")
    missing = [n for n, v in (("UPSTOX_API_KEY", key),
                              ("UPSTOX_REDIRECT_URI", uri)) if not v]
    if missing:
        sys.exit(f"missing in .env: {', '.join(missing)}")
    q = urllib.parse.urlencode({"client_id": key, "redirect_uri": uri,
                                "response_type": "code"})
    return f"{AUTH}?{q}"


def exchange(code):
    key = live_source.env_value("UPSTOX_API_KEY")
    sec = live_source.env_value("UPSTOX_API_SECRET")
    uri = live_source.env_value("UPSTOX_REDIRECT_URI")
    if not (key and sec and uri):
        sys.exit("UPSTOX_API_KEY, UPSTOX_API_SECRET and UPSTOX_REDIRECT_URI "
                 "must all be set in .env")
    body = urllib.parse.urlencode({
        "code": code, "client_id": key, "client_secret": sec,
        "redirect_uri": uri, "grant_type": "authorization_code"}).encode()
    req = urllib.request.Request(TOKEN, data=body, headers={
        "accept": "application/json", "User-Agent": live_source.UA,
        "Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.loads(r.read())
    except urllib.error.HTTPError as e:
        # The error body can echo request fields, so report the code and the
        # message only -- never the whole payload.
        try:
            msg = json.loads(e.read())["errors"][0].get("message", "")
        except Exception:
            msg = ""
        sys.exit(f"token exchange failed: HTTP {e.code} {msg}")
    tok = data.get("access_token")
    if not tok:
        sys.exit("no access_token in the response")
    _write_env("UPSTOX_ACCESS_TOKEN", tok)
    return len(tok)


def main():
    if len(sys.argv) > 1 and sys.argv[1] not in ("--selftest",):
        n = exchange(sys.argv[1].strip())
        print(f"UPSTOX_ACCESS_TOKEN written to .env ({n} chars). "
              f"Expires around 03:30 IST; re-run before the next open.")
        q = live_source.upstox(["HAPPYFORGE"])
        print("live quote check:", "OK" if q else "still returning nothing")
        return
    print("1. Open this in a browser and log in:\n")
    print(f"   {login_url()}\n")
    print("2. You will be redirected to your redirect URI with ?code=... in the")
    print("   address bar (the page itself may not load -- that is fine).")
    print("   Copy that code and run:\n")
    print("       python3 upstox_login.py <code>\n")
    print("   The token is written straight into .env. It expires around")
    print("   03:30 IST, so this is a daily step until the refresh flow exists.")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        # _write_env must replace in place and never disturb its neighbours.
        import tempfile
        _orig = ENV
        try:
            with tempfile.TemporaryDirectory() as td:
                ENV = Path(td) / ".env"
                ENV.write_text("A=1\nUPSTOX_ACCESS_TOKEN=old\nB=2\n")
                _write_env("UPSTOX_ACCESS_TOKEN", "new")
                got = ENV.read_text().splitlines()
                assert got == ["A=1", "UPSTOX_ACCESS_TOKEN=new", "B=2"], got
                ENV.write_text("A=1\n")
                _write_env("UPSTOX_ACCESS_TOKEN", "x")
                assert ENV.read_text().splitlines() == ["A=1", "UPSTOX_ACCESS_TOKEN=x"]
        finally:
            ENV = _orig
        print("upstox_login selftest ok")
    else:
        main()
