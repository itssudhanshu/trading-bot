#!/usr/bin/env python3
"""Render signals onto the live TradingView chart via the tradingview-mcp CLI.

Read-and-draw only: this never places an order anywhere. TradingView is the
review surface, not an execution venue.

Drawing IDs are recovered by diffing `draw list` before and after, because
`draw shape` returns entity_id: null. We never call `draw clear` -- that would
delete the user's own annotations along with ours.
"""
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

TV_CLI = Path.home() / "Documents" / "Repo" / "tradingview-mcp" / "src" / "cli" / "index.js"

ENTRY_COLOR = "#26a69a"   # teal
STOP_COLOR = "#ef5350"    # red
TARGET_COLOR = "#42a5f5"  # blue


def tv(*args, timeout=30) -> dict:
    """Run the tv CLI, return parsed JSON. Never raises on a failed command."""
    try:
        p = subprocess.run(["node", str(TV_CLI), *map(str, args)],
                           capture_output=True, text=True, timeout=timeout)
        return json.loads(p.stdout) if p.stdout.strip() else {
            "success": False, "error": p.stderr.strip()[:200]}
    except (subprocess.TimeoutExpired, json.JSONDecodeError) as e:
        return {"success": False, "error": f"{type(e).__name__}: {e}"}


def connected() -> bool:
    return tv("status").get("cdp_connected") is True


def shape_ids() -> set:
    r = tv("draw", "list")
    return {s["id"] for s in r.get("shapes", [])} if r.get("success") else set()


def _line(price, color, width=2, style=0):
    import time
    return tv("draw", "shape", "-t", "horizontal_line", "-p", f"{price:.2f}",
              "--time", int(time.time()),
              "--overrides", json.dumps({"linecolor": color, "linewidth": width,
                                         "linestyle": style, "showPrice": True}))


def plot_signal(signal, symbol=None) -> dict:
    """Draw entry/stop/target on the chart. Returns {ok, ids, errors}.

    `ids` are only the shapes this call created -- safe to pass to erase().
    """
    if not connected():
        return {"ok": False, "ids": [], "errors": ["CDP not connected"]}

    if symbol:
        r = tv("symbol", symbol)
        if not r.get("success"):
            return {"ok": False, "ids": [], "errors": [f"symbol: {r.get('error')}"]}

    before = shape_ids()
    errors = []
    for price, color in ((signal.entry, ENTRY_COLOR),
                         (signal.stop, STOP_COLOR),
                         (signal.target, TARGET_COLOR)):
        r = _line(price, color)
        if not r.get("success"):
            errors.append(r.get("error"))
    created = sorted(shape_ids() - before)
    return {"ok": not errors, "ids": created, "errors": errors}


def erase(ids) -> int:
    """Remove only the given drawing IDs. Returns how many were removed."""
    return sum(1 for i in ids if tv("draw", "remove", "--id", i).get("removed"))


def _selftest():
    """Offline: verifies the diff logic isolates our own drawings, using a fake CLI."""
    import engine
    global tv
    real, state = tv, {"shapes": ["preexisting_user_drawing"], "n": 0}

    def fake(*args, **kw):
        a = list(map(str, args))
        if a[:1] == ["status"]:
            return {"cdp_connected": True}
        if a[:2] == ["draw", "list"]:
            return {"success": True, "shapes": [{"id": i} for i in state["shapes"]]}
        if a[:2] == ["draw", "shape"]:
            state["n"] += 1
            state["shapes"].append(f"bot{state['n']}")
            return {"success": True}
        if a[:2] == ["draw", "remove"]:
            state["shapes"].remove(a[3])
            return {"removed": True}
        if a[:1] == ["symbol"]:
            return {"success": True}
        return {"success": False}

    try:
        tv = fake
        sig = engine.Signal("TESTSYM", "vcp", entry=100, stop=90, target=130)
        r = plot_signal(sig, symbol="NSE:TESTSYM")
        assert r["ok"], r
        assert r["ids"] == ["bot1", "bot2", "bot3"], r["ids"]
        assert "preexisting_user_drawing" not in r["ids"], "would delete user's drawing"
        assert erase(r["ids"]) == 3
        assert state["shapes"] == ["preexisting_user_drawing"], \
            f"user drawing must survive, got {state['shapes']}"
    finally:
        tv = real
    print("tv selftest ok")


if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        _selftest()
    else:
        print(json.dumps(tv("status"), indent=2))
