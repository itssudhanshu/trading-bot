#!/usr/bin/env python3
"""Check the Upstox access token pasted into .env -- that is the only login.

Upstox access tokens are not issued from an API key. They come from an
interactive login in Upstox's developer console, and they EXPIRE DAILY,
around 03:30 IST. Generate a fresh token there, paste it into .env as
UPSTOX_ACCESS_TOKEN, then run this to confirm it works:

    python3 src/ops/upstox_login.py

It prints how long the token has left and runs one live quote check, and it
exits loudly on a missing or expired token. The token itself is never echoed.
"""

import sys as _sys, pathlib as _pl
_sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[1]))
import paths  # noqa: F401  -- puts the source dirs on sys.path
import sys
from pathlib import Path

import live_source


def main():
    tok = live_source.env_value("UPSTOX_ACCESS_TOKEN")
    if not tok:
        sys.exit("no UPSTOX_ACCESS_TOKEN in .env -- generate a fresh token in "
                 "Upstox's developer console and paste it there")
    left = live_source.token_hours_left(tok)
    if left is None:
        print("could not read an expiry from the token -- trying a live "
              "quote check anyway")
    elif left <= 0:
        sys.exit(f"UPSTOX_ACCESS_TOKEN expired {-left:.0f}h ago -- paste a "
                 f"fresh token into .env (they expire daily around 03:30 IST)")
    else:
        print(f"token expires in {left:.1f}h (they expire daily around 03:30 IST)")
    q = live_source.upstox(["HAPPYFORGE"])
    if q:
        print("live quote check: OK")
    else:
        sys.exit(f"live quote check failed: {live_source.upstox.last_error}")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        # The missing-token path must exit loudly BEFORE any network call,
        # reading only os.environ and paths.ROOT/.env. Point both at nothing
        # and prove it.
        import os
        _tok = os.environ.pop("UPSTOX_ACCESS_TOKEN", None)
        _root = paths.ROOT
        try:
            paths.ROOT = Path("/nonexistent-selftest-root")
            try:
                main()
                raise AssertionError("missing token did not exit")
            except SystemExit as e:
                assert "UPSTOX_ACCESS_TOKEN" in str(e), e
        finally:
            if _tok is not None:
                os.environ["UPSTOX_ACCESS_TOKEN"] = _tok
            paths.ROOT = _root
        print("upstox_login selftest ok")
    else:
        main()
