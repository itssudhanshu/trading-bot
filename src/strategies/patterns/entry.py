#!/usr/bin/env python3
"""Entry triggers for the bucket.

Until now the bucket had none. The composite score RANKED candidates and the
bucket bought the top of that list at the next open, unconditionally -- no
confirmation that the move was underway, no volume check, nothing. Ranking is
not timing: a name can be the best in its cluster and still be mid-pullback on
the day you buy it.

Each trigger is evaluated on the SIGNAL day i. Entry remains the open of i+1,
so nothing here can see a price it could not have traded on.
"""

import sys as _sys, pathlib as _pl
_sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[2]))  # -> src/
import paths  # noqa: F401  -- puts the source dirs on sys.path
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


# --- H5: named chart patterns -----------------------------------------------
# FROZEN 2026-08-21, before any return was computed against them. Committed in
# their own commit for that reason.
#
# These are a different SHAPE from the triggers above, not different numbers for
# them: `breakout` asks one question about one bar (does the close clear the
# prior 20-day high), while these describe the geometry of a WINDOW. That is
# what makes H5 a legal experiment rather than another turn of a dial.
#
# Pattern detectors are the easiest artefact in this field to write so that they
# only fire on charts that already worked. Three defences, all of them in place
# before the first run:
#   1. every threshold below is fixed here and is not tuned afterwards;
#   2. each detector's selftest fires on a SYNTHETICALLY CONSTRUCTED example and
#      is asserted NOT to fire on a plain uptrend, so "matches everything" fails;
#   3. only bars up to and including i are read, so nothing can see its own
#      outcome.

# Flag: a sharp advance (the pole), then a shallow, quiet pause.
FLAG_POLE = 20            # bars the advance is measured over
FLAG_LEN = 5              # bars of consolidation after it
FLAG_MIN_GAIN = 15.0      # % the pole must have gained
FLAG_MAX_RETRACE = 0.50   # fraction of the pole the pause may give back
FLAG_MAX_RANGE = 0.60     # pause's daily range, as a fraction of the pole's

# Ascending triangle: flat resistance overhead, lows walking up into it.
TRI_WINDOW = 20
TRI_TOP_TOL = 0.03        # the two halves' highs within 3% = one flat ceiling
TRI_MIN_RISE = 0.02       # the later half's low must sit 2% above the earlier

# Cup and handle: a rounded recovery to the old high, then a small pause.
CUP_WINDOW = 60
CUP_DEPTH_MIN = 0.15      # shallower than this is not a cup, it is drift
CUP_DEPTH_MAX = 0.50      # deeper than this is a crash with a recovery
CUP_RIM_TOL = 0.06        # right rim must return to within 6% of the left
CUP_HANDLE = 5
CUP_HANDLE_MAX = 0.35     # the handle may give back this much of the depth


def _win(arr, i, n):
    """-> the n bars ending at i, or None if the history is short."""
    lo = i - n + 1
    return arr[lo:i + 1] if lo >= 0 and i < len(arr) else None


def flag(s, i):
    """A sharp advance followed by a shallow, quiet pause."""
    pole_end = i - FLAG_LEN
    pole_start = pole_end - FLAG_POLE
    if pole_start < 0:
        return False
    base = s.close[pole_start]
    if not base:
        return False
    if (s.close[pole_end] / base - 1.0) * 100 < FLAG_MIN_GAIN:
        return False
    pole_hi = max(s.high[pole_start:pole_end + 1])
    pole_lo = min(s.low[pole_start:pole_end + 1])
    pole_range = pole_hi - pole_lo
    if pole_range <= 0:
        return False
    fh = s.high[pole_end + 1:i + 1]
    fl = s.low[pole_end + 1:i + 1]
    if not fh or not fl:
        return False
    # The pause must not undo the advance...
    if (pole_hi - min(fl)) / pole_range > FLAG_MAX_RETRACE:
        return False
    # ...and must be quieter than it. A pause as wide as the pole is not a flag,
    # it is the move continuing.
    flag_range = max(fh) - min(fl)
    return flag_range <= FLAG_MAX_RANGE * pole_range


def ascending_triangle(s, i):
    """Flat resistance overhead, with the lows walking up into it."""
    w = TRI_WINDOW
    if i - w + 1 < 0:
        return False
    half = w // 2
    a_hi = max(s.high[i - w + 1:i - half + 1])
    b_hi = max(s.high[i - half + 1:i + 1])
    a_lo = min(s.low[i - w + 1:i - half + 1])
    b_lo = min(s.low[i - half + 1:i + 1])
    if not a_hi or not a_lo:
        return False
    if abs(b_hi - a_hi) / a_hi > TRI_TOP_TOL:     # ceiling must be flat
        return False
    return (b_lo - a_lo) / a_lo >= TRI_MIN_RISE   # floor must be rising


def cup_handle(s, i):
    """A rounded recovery back to an old high, then a small pause."""
    w = CUP_WINDOW
    cup_end = i - CUP_HANDLE
    if cup_end - w + 1 < 0:
        return False
    lo = cup_end - w + 1
    q = w // 4
    left_rim = max(s.high[lo:lo + q])
    trough = min(s.low[lo + q:cup_end - q + 1])
    right_rim = max(s.high[cup_end - q + 1:cup_end + 1])
    if not left_rim or not trough:
        return False
    depth = (left_rim - trough) / left_rim
    if not (CUP_DEPTH_MIN <= depth <= CUP_DEPTH_MAX):
        return False
    if abs(right_rim - left_rim) / left_rim > CUP_RIM_TOL:
        return False
    # The handle: a shallow pause near the rim, not a second collapse.
    hl = min(s.low[cup_end + 1:i + 1])
    if not hl:
        return False
    give_back = (right_rim - hl) / (left_rim - trough)
    return 0 <= give_back <= CUP_HANDLE_MAX


def any_pattern(s, i):
    """THE pre-registered H5 arm: any of the three, on the signal bar.

    One arm, not four, on purpose. Testing each detector separately would add
    three more comparisons to a family of five and push the Bonferroni bar up
    for every hypothesis in the spec. The three are reported individually as
    DESCRIPTION, with no adoption path of their own.
    """
    return flag(s, i) or ascending_triangle(s, i) or cup_handle(s, i)



TRIGGERS = {"none": none, "volume": volume, "breakout": breakout,
            "not_overbought": not_overbought, "rsi_band": rsi_band,
            "pullback": pullback, "vol+breakout": vol_and_breakout,
            # H5, frozen above. `pattern` is the pre-registered arm; the three
            # individual detectors are registered so they can be DESCRIBED, not
            # so they can each be adopted.
            "flag": flag, "asc_triangle": ascending_triangle,
            "cup_handle": cup_handle, "pattern": any_pattern}


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

    # --- H5 detectors: must fire on the shape, and ONLY on the shape ---------
    # A detector that fires on everything is worse than no detector: it passes
    # every smoke test, adds trades, and looks like a finding. So each is
    # asserted BOTH ways, against a synthetic example and against a plain
    # uptrend that contains none of these shapes.
    def _mk(bars):
        """bars = [(open, high, low, close)] -> a Series."""
        t = features.Series("P", [d0 + timedelta(days=k) for k in range(len(bars))])
        for o, h, l, c in bars:
            t.open.append(o); t.high.append(h); t.low.append(l); t.close.append(c)
            t.volume.append(1000); t.turnover.append(1e6); t.deliv_pct.append(50.0)
        return t

    # A clean monotonic uptrend has no pause, no flat ceiling and no cup.
    for nm in ("flag", "asc_triangle", "cup_handle"):
        assert not TRIGGERS[nm](s, i), f"{nm} fired on a plain uptrend"

    # Flag: 20 bars up 20%, then 5 quiet bars drifting slightly back.
    bars = [(100 + k, 100 + k + 0.5, 100 + k - 0.5, 100 + k) for k in range(21)]
    bars += [(120, 120.4, 119.6, 120 - 0.2 * k) for k in range(1, 6)]
    f = _mk(bars)
    assert flag(f, len(bars) - 1), "flag did not fire on a constructed flag"

    # Ascending triangle: flat ceiling at 100, lows walking 90 -> 96.
    bars = []
    for k in range(20):
        lo = 90 + k * 0.35
        bars.append((lo + 1, 100.0, lo, 99.0))
    t3 = _mk(bars)
    assert ascending_triangle(t3, 19), "triangle did not fire on a flat top and rising lows"
    assert not flag(t3, 19), "flag fired on a triangle"

    # Cup and handle: 100 -> 76 -> 100 over 60 bars, then a shallow handle.
    import math
    bars = []
    for k in range(60):
        px = 100 - 24 * math.sin(math.pi * k / 59)      # rounded, not a V
        bars.append((px, px + 0.5, px - 0.5, px))
    for k in range(1, 6):
        px = 100 - 1.2 * k
        bars.append((px, px + 0.3, px - 0.3, px))
    c = _mk(bars)
    assert cup_handle(c, len(bars) - 1), "cup did not fire on a constructed cup"
    assert any_pattern(c, len(bars) - 1), "any_pattern missed a firing detector"

    # And the combined arm must be exactly the disjunction, never broader.
    for series, idx in ((f, len(f.close) - 1), (t3, 19), (s, i)):
        assert any_pattern(series, idx) == (flag(series, idx)
                                            or ascending_triangle(series, idx)
                                            or cup_handle(series, idx))
    print("entry selftest ok (H5 detectors fire on their shape, not on a trend)")


if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        _selftest()
