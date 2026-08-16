#!/usr/bin/env python3
"""Entry triggers for the cluster book.

Until now the book had none. The composite score RANKED candidates and the
book bought the top of that list at the next open, unconditionally -- no
confirmation that the move was underway, no volume check, nothing. Ranking is
not timing: a name can be the best in its cluster and still be mid-pullback on
the day you buy it.

Each trigger is evaluated on the SIGNAL day i. Entry remains the open of i+1,
so nothing here can see a price it could not have traded on.
"""
import features

_CACHE = {}


def _ind(s):
    """Per-symbol indicator arrays, computed once."""
    got = _CACHE.get(s.symbol)
    if got is None:
        got = {
            "rsi": features.rsi(s.close, 14),
            "ema20": features.ema(s.close, 20),
            "hi20": features.rolling_max(s.high, 20),
            "vol20": features.sma([float(v) for v in s.volume], 20),
        }
        _CACHE[s.symbol] = got
    return got


def _at(arr, i):
    v = arr[i] if arr and 0 <= i < len(arr) else None
    return v if v is not None else None


def none(s, i):
    """Control: buy the ranked name, no confirmation. The current behaviour."""
    return True


def volume(s, i):
    """Participation: the move must carry above-average volume."""
    a = _at(_ind(s)["vol20"], i)
    return a is not None and a > 0 and s.volume[i] > 1.5 * a


def breakout(s, i):
    """Confirmation: close takes out the prior 20-day high."""
    a = _at(_ind(s)["hi20"], i - 1)
    return a is not None and s.close[i] >= a


def not_overbought(s, i):
    """Avoid buying the blow-off top."""
    a = _at(_ind(s)["rsi"], i)
    return a is not None and a < 75.0


def rsi_band(s, i):
    """Momentum present but not exhausted."""
    a = _at(_ind(s)["rsi"], i)
    return a is not None and 50.0 <= a <= 70.0


def pullback(s, i):
    """Buy strength on a dip, not on extension: close within 4% of the 20-EMA."""
    e = _at(_ind(s)["ema20"], i)
    return e is not None and e > 0 and 0 <= (s.close[i] - e) / e <= 0.04


def vol_and_breakout(s, i):
    return volume(s, i) and breakout(s, i)


TRIGGERS = {"none": none, "volume": volume, "breakout": breakout,
            "not_overbought": not_overbought, "rsi_band": rsi_band,
            "pullback": pullback, "vol+breakout": vol_and_breakout}


def _selftest():
    from datetime import date, timedelta
    d0 = date(2024, 1, 1)
    days = [d0 + timedelta(days=k) for k in range(120)]
    s = features.Series("T", list(days))
    for k in range(120):
        px = 100.0 + k                      # clean uptrend
        s.open.append(px); s.high.append(px + 1); s.low.append(px - 1)
        s.close.append(px); s.volume.append(1000); s.turnover.append(1e6)
        s.deliv_pct.append(50.0)
    i = 119
    _CACHE.clear()
    assert none(s, i) is True
    assert breakout(s, i), "new high in a monotonic uptrend must trigger"
    assert not volume(s, i), "flat volume must not pass a 1.5x surge test"
    s.volume[i] = 5000
    _CACHE.clear()
    assert volume(s, i), "a 5x volume day must pass"
    # every trigger must be callable and return a bool at a valid index
    _CACHE.clear()
    for name, fn in TRIGGERS.items():
        assert isinstance(fn(s, i), bool), name
    print("entry selftest ok")


if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        _selftest()
