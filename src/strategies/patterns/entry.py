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



# --- H13: candlestick geometry of the SIGNAL BAR ----------------------------
# PRE-REGISTERED 2026-08-26, frozen in this commit BEFORE any return was
# computed against any arm below. H5 asked whether the shape of a 20-60 bar
# WINDOW carries information the single-bar breakout misses; it never looked at
# the signal bar itself. These detectors do exactly that, and nothing else.
#
# Every arm is a GATE on `breakout`, never a standalone signal: this book is
# long-only momentum above the 200-DMA, and a hammer or morning star is a
# bottom-reversal call that contradicts that design. The question is whether the
# QUALITY of the breakout bar adds anything to knowing a breakout happened.
#
# The confound runs the OPPOSITE way from H5's. H5's arms were looser than the
# incumbent and could win by reaching deeper down a ranking whose depth is
# known to cost -1.12%/step. These arms are TIGHTER, so they fill the bucket
# more slowly and reach LESS deep -- a mechanical tailwind available to any
# tightening rule with no information in it at all. The `coin` arm exists to
# price that tailwind: it tightens by a deterministic pseudo-random coin whose
# rate is matched to the primary's measured firing RATE (rates only; it cannot
# see returns). A candle arm beats the hypothesis test only if it also beats
# the coin at its own game.
#
# Thresholds below are canonical values fixed now and not tuned afterwards:
#   STRONG_CLOSE_POS  0.50  close in the top half of the bar's range -- the
#                           plainest strength test there is, no free parameter
#   engulf            body engulfs prior body, trigger bar bullish (the strict
#                     bearish-prior-bar version contradicts a breakout context)
#   inside_break      the bar before the breakout was an inside bar (the skill's
#                     own "inside bar breakout" setup)
#   three_push        three consecutive higher closes into the breakout (Three
#                     White Soldiers adapted to closes; bodies/openings variant
#                     deliberately NOT also run -- one form per shape)

STRONG_CLOSE_POS = 0.50


def _rng(s, i):
    """crc32 of symbol|date in [0,1) -- stable across processes, unlike hash()."""
    import zlib
    key = f"{s.symbol}|{s.days[i].isoformat()}"
    return zlib.crc32(key.encode()) / 2 ** 32


P_COIN = None    # set at run time from measured RATES only; never from returns


def set_coin_rate(p):
    global P_COIN
    P_COIN = float(p)


def strong_close(s, i):
    """The breakout closed in the top half of its own range."""
    h, l = s.high[i], s.low[i]
    if h is None or l is None or h <= l:
        return False
    return (s.close[i] - l) / (h - l) >= STRONG_CLOSE_POS


def engulf(s, i):
    """Breakout whose body swallows the previous bar's body."""
    if i < 1:
        return False
    bt, bb = max(s.open[i], s.close[i]), min(s.open[i], s.close[i])
    pt, pb = max(s.open[i - 1], s.close[i - 1]), min(s.open[i - 1], s.close[i - 1])
    return bt >= pt and bb <= pb and s.close[i] > s.open[i]


def inside_break(s, i):
    """Breakout on the bar after an inside bar."""
    if i < 2:
        return False
    return (s.high[i - 1] <= s.high[i - 2]) and (s.low[i - 1] >= s.low[i - 2])


def three_push(s, i):
    """Three consecutive higher closes ending at the breakout."""
    if i < 3:
        return False
    return (s.close[i] > s.close[i - 1] > s.close[i - 2] > s.close[i - 3])


def _gated(base):
    def g(s, i):
        return breakout(s, i) and base(s, i)
    return g


def coin(s, i):
    """MECHANISM reference: breakout AND a deterministic coin at P_COIN.

    Exists to price the mechanical tailwind every tighter gate earns through
    rank depth. No adoption path; raises if the rate was never set so it can
    never silently run wide open or fully shut. The research test that needs
    it sets the rate from firing counts only.
    """
    if P_COIN is None:
        raise RuntimeError("coin rate unset -- candle_test sets it from rates")
    return breakout(s, i) and _rng(s, i) < P_COIN


candle_strong_close = _gated(strong_close)
candle_engulf = _gated(engulf)
candle_inside = _gated(inside_break)
candle_three_push = _gated(three_push)


# --- H14: fair-value gaps around the breakout ---------------------------------
# PRE-REGISTERED 2026-08-26, frozen in this commit BEFORE any return was
# computed against any arm below. Second family from the operator's
# chart-pattern review that H5/H13 had not touched.
#
# A bullish fair-value gap (FVG) is a three-bar imbalance: the signal bar's
# low sits entirely ABOVE the high two bars back, so the impulse bar between
# them left a zone no one traded. Three readings exist and all are run:
#   fvg          the break itself completes a fresh gap -- the urgency claim
#   fvg_recent   an UNFILLED gap exists inside the window -- lingering support
#   gap_fill     a recent gap was REVISITED before today's break -- the
#                literature's own entry ("price returns to fill the gap"),
#                adapted as a gate on the live trigger rather than a
#                standalone mean-reversion buy this book cannot make
#
# `fvg` is the PRIMARY because it is one clause with no window parameter;
# the two windowed arms carry an extra frozen choice (FVG_WINDOW=5) each and
# take description only. `coin` prices the tightening confound again -- H14
# runs it because it costs nothing, whatever it read last time (L74: ~zero).

FVG_WINDOW = 5


def fvg_bull(s, i):
    """Bullish FVG completing on bar i: low[i] entirely above high[i-2]."""
    if i < 2:
        return False
    return s.low[i] > s.high[i - 2]


def _recent_gap(s, i):
    """-> the newest j within the window completing a bullish FVG, else None."""
    for j in range(i, i - FVG_WINDOW, -1):
        if j >= 2 and fvg_bull(s, j):
            return j
    return None


def fvg_recent(s, i):
    """An in-window bullish FVG whose zone has not been traded back into."""
    j = _recent_gap(s, i)
    if j is None:
        return False
    lid = s.low[j]
    return all(lo > lid for lo in s.low[j + 1:i + 1])


def gap_fill(s, i):
    """An in-window bullish FVG that WAS revisited, today closing above it."""
    j = _recent_gap(s, i)
    if j is None or j == i:
        return False
    lid, floor = s.low[j], s.high[j - 2]
    touched = any(lo <= lid for lo in s.low[j + 1:i + 1])
    return touched and s.close[i] > floor


fvg_gated = _gated(fvg_bull)
fvg_recent_gated = _gated(fvg_recent)
gap_fill_gated = _gated(gap_fill)


# --- H15: how deep was the pullback that preceded the breakout ----------------
# PRE-REGISTERED 2026-08-26, frozen in this commit BEFORE any return was
# computed against any arm below. Third family from the operator's
# chart-pattern review.
#
# The literatures genuinely OPPOSE each other here and both are tested:
#   Fibonacci practice buys DEEP retracements (61.8% "golden" -- the discount
#   reading); trend-following practice prefers SHALLOW pullbacks (the
#   high-tight-flag reading). This book is long-only momentum above a 200-DMA
#   buying new highs, so the momentum-consistent direction carries the
#   adoption path: if the deep-discount side won instead, adopting it would
#   need its own fresh pre-registration, not a quiet swap after seeing data.
#
# Depth is mechanical and point-in-time: over PB_WINDOW bars ending at i-1,
# find the window high H and the deepest low AFTER it; depth = (H - low)/H.
# The signal bar itself is excluded -- today's breakout pop must not erase
# the dip it is recovering from. A high on the window's last bar is a pullback
# of zero: nothing has happened yet to recover from.

PB_WINDOW = 40
PB_SHALLOW_PCT = 15.0
PB_DEEP_PCT = 30.0


def pullback_pct(s, i):
    """-> % decline from the trailing-window high to its deepest later low,
    measured on bars up to i-1; 0.0 when the high is the newest bar. Equal
    highs resolve to the MOST RECENT touch: a flat top is no pullback yet,
    not a whole base of dipping."""
    lo_idx = max(i - PB_WINDOW, 0)
    hi_val, hi_pos = None, None
    for k in range(lo_idx, i):
        if hi_val is None or s.high[k] >= hi_val:
            hi_val, hi_pos = s.high[k], k
    if hi_val is None or not hi_val:
        return None
    after = s.low[hi_pos + 1:i]
    if not after:
        return 0.0
    return (hi_val - min(after)) / hi_val * 100.0


def pb_shallow(s, i):
    """Breakout preceded by only a shallow dip (< PB_SHALLOW_PCT)."""
    d = pullback_pct(s, i)
    return d is not None and d < PB_SHALLOW_PCT


def pb_deep(s, i):
    """Breakout preceded by a real flush (>= PB_DEEP_PCT)."""
    d = pullback_pct(s, i)
    return d is not None and d >= PB_DEEP_PCT


pb_shallow_gated = _gated(pb_shallow)
pb_deep_gated = _gated(pb_deep)


# --- H16: swing structure into the breakout -----------------------------------
# PRE-REGISTERED 2026-08-26, frozen in this commit BEFORE any return was
# computed against any arm below. Fourth and last family from the operator's
# chart-pattern review.
#
# The skill's structure reading: an uptrend is a series of higher highs and
# higher lows; a break BELOW the newest higher low is a change of character
# (CHoCH) and damages the trend. The testable claim for this book: a breakout
# that arrives with intact higher-low structure outperforms one arriving
# after structure broke.
#
# Definitions, frozen:
#   SWING_FRINGE = 3      a pivot needs the extreme over 3 bars either side,
#                         so it is only KNOWABLE 3 bars later -- nothing here
#                         can see a swing before it is confirmed
#   STRUCT_LOOKBACK = 60  the window searched for confirmed swings
#
# `hl_intact` (PRIMARY): at least two confirmed swing lows in the window, the
# newer ABOVE the older (ascending lows), and NO close after the newest swing
# low's own trough bar back through its level -- a close through a known
# higher low is visible damage the moment it prints, confirmation or not.
# A monotonic run with no pullbacks has NO readable structure and is
# rejected: excluding it is part of the hypothesis, not an oversight (H15
# measured those trades as fine).
#
# `hh_hl` (description only): hl_intact AND ascending confirmed swing highs
# too -- the stricter full-structure reading.

SWING_FRINGE = 3
STRUCT_LOOKBACK = 60


def _confirmed_swing_lows(s, i):
    """-> indices of confirmed swing lows fully visible at bar i. A bar whose
    low merely EQUALS the previous bar's low is part of the same trough, not
    a second one: plateaus register once, on their first touch."""
    out = []
    lo = max(i - STRUCT_LOOKBACK, SWING_FRINGE)
    for k in range(lo, i - SWING_FRINGE + 1):
        w = s.low[k - SWING_FRINGE:k + SWING_FRINGE + 1]
        if s.low[k] == min(w) and s.low[k] < s.low[k - 1]:
            out.append(k)
    return out


def _confirmed_swing_highs(s, i):
    """-> indices of confirmed swing highs fully visible at bar i, plateau
    rule as for lows: strictly above the prior bar's high."""
    out = []
    lo = max(i - STRUCT_LOOKBACK, SWING_FRINGE)
    for k in range(lo, i - SWING_FRINGE + 1):
        w = s.high[k - SWING_FRINGE:k + SWING_FRINGE + 1]
        if s.high[k] == max(w) and s.high[k] > s.high[k - 1]:
            out.append(k)
    return out


def hl_intact(s, i):
    """Ascending confirmed swing lows, no close back through the newest one."""
    lows = _confirmed_swing_lows(s, i)
    if len(lows) < 2:
        return False
    newer, older = lows[-1], lows[-2]
    if not s.low[newer] > s.low[older]:
        return False
    return all(c >= s.low[newer] for c in s.close[newer + 1:i + 1])


def hh_hl(s, i):
    """hl_intact plus ascending confirmed swing highs."""
    if not hl_intact(s, i):
        return False
    highs = _confirmed_swing_highs(s, i)
    return len(highs) >= 2 and s.high[highs[-1]] > s.high[highs[-2]]


hl_intact_gated = _gated(hl_intact)
hh_hl_gated = _gated(hh_hl)


TRIGGERS = {"none": none, "volume": volume, "breakout": breakout,
            "not_overbought": not_overbought, "rsi_band": rsi_band,
            "pullback": pullback, "vol+breakout": vol_and_breakout,
            # H5, frozen above. `pattern` is the pre-registered arm; the three
            # individual detectors are registered so they can be DESCRIBED, not
            # so they can each be adopted.
            "flag": flag, "asc_triangle": ascending_triangle,
            "cup_handle": cup_handle, "pattern": any_pattern,
            # H13, frozen above. `strong_close` carries the adoption path;
            # engulf / inside / three_push are description only; `coin` is the
            # mechanism reference and can never be adopted.
            "strong_close": candle_strong_close, "engulf": candle_engulf,
            "inside_break": candle_inside, "three_push": candle_three_push,
            "coin": coin,
            # H14, frozen above. `fvg` carries the adoption path; fvg_recent /
            # gap_fill are description only.
            "fvg": fvg_gated, "fvg_recent": fvg_recent_gated,
            "gap_fill": gap_fill_gated,
            # H15, frozen above. `pb_shallow` carries the adoption path (the
            # momentum-consistent direction); pb_deep is the OPPOSING
            # literature, description only.
            "pb_shallow": pb_shallow_gated, "pb_deep": pb_deep_gated,
            # H16, frozen above. `hl_intact` carries the adoption path;
            # hh_hl is the stricter full-structure reading, description only.
            "hl_intact": hl_intact_gated, "hh_hl": hh_hl_gated}


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
    set_coin_rate(0.5)                  # coin must never run with an unset rate
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

    # --- H13 candle gates: fire on the shape, and ONLY on the shape ---------
    # Same defence as H5, both ways per detector. A gate here is `breakout AND
    # shape`, so each negative case is still a genuine breakout bar -- a weak
    # one -- and must be rejected on the shape alone.

    def _hist(top=100.0):
        """20 quiet bars under `top`, so close > top is a clean breakout."""
        b = [(top - 5, top - 4.5, top - 5.5, top - 5) for _ in range(21)]
        return b

    # strong_close: a fade-that-still-breaks-out closes at its LOW -> reject;
    # the same breakout closing near its high -> accept.
    bars = _hist() + [(106, 106.0, 104.0, 104.1)]          # gap up, fade all day
    g = _mk(bars)
    j = len(bars) - 1
    _CACHE.clear()
    assert breakout(g, j), "fixture must itself be a breakout"
    assert not strong_close(g, j), "strong_close accepted a close at the bar's low"
    bars = _hist() + [(101, 106.0, 100.8, 105.5)]
    g2 = _mk(bars)
    assert strong_close(g2, j), "strong_close rejected a close near the high"

    # engulf: today's body swallows yesterday's; shrinking bodies are rejected.
    bars = _hist() + [(93.5, 94.8, 93.2, 94.5), (93.0, 97.0, 92.8, 96.5)]
    e = _mk(bars)
    k = len(bars) - 1
    _CACHE.clear()
    assert engulf(e, k), "engulf missed a body that swallowed the prior body"
    bars = _hist() + [(93.5, 94.8, 93.2, 94.5), (94.0, 96.0, 93.9, 95.5)]
    e2 = _mk(bars)
    assert not engulf(e2, k), "engulf accepted a smaller body"

    # inside_break: an inside bar then the break; expanding ranges rejected.
    bars = _hist(90.0) + [(91, 92.0, 90.0, 91.5),
                          (91, 91.8, 90.2, 91.3),
                          (91.5, 93.5, 91.4, 93.0)]
    ib = _mk(bars)
    m = len(bars) - 1
    _CACHE.clear()
    assert inside_break(ib, m), "inside_break missed inside-bar-then-breakout"
    bars = _hist(90.0) + [(91, 92.0, 90.0, 91.5),
                          (91.2, 92.6, 89.8, 91.3),
                          (91.5, 93.5, 91.4, 93.0)]
    ib2 = _mk(bars)
    assert not inside_break(ib2, m), "inside_break fired on an expanding range"

    # three_push: rising closes qualify (a plain uptrend legitimately does);
    # alternating closes do not.
    bars = _hist() + [(98, 99.0, 97.8, 98.8), (98.8, 100.0, 98.6, 99.7),
                      (99.7, 101.0, 99.5, 100.8)]
    tp = _mk(bars)
    q = len(bars) - 1
    _CACHE.clear()
    assert three_push(tp, q), "three_push missed three rising closes"
    bars = _hist() + [(98, 99.0, 97.8, 98.8), (98.8, 99.9, 97.6, 97.9),
                      (98.2, 101.0, 98.0, 100.8)]
    tp2 = _mk(bars)
    assert not three_push(tp2, q), "three_push accepted alternating closes"

    # coin: deterministic for a given key, bounded by its rate, equal to
    # breakout at p=1, silent nowhere without a rate.
    set_coin_rate(1.0)
    assert all(coin(g, jj) == breakout(g, jj) for jj in range(len(g.close)))
    set_coin_rate(0.0)
    assert not any(coin(g, jj) for jj in range(len(g.close)))
    set_coin_rate(0.5)
    assert coin(g, j) == coin(g, j), "coin is not deterministic for a fixed bar"
    saved = P_COIN
    globals()["P_COIN"] = None
    try:
        try:
            coin(g, j)
            raise AssertionError("coin ran with no rate set")
        except RuntimeError:
            pass
    finally:
        globals()["P_COIN"] = saved

    # --- H14 fair-value gaps: fire on the imbalance, and ONLY on it -----------
    bars = [(100, 100.5, 99.5, 100), (101, 104.0, 100.8, 103.5),
            (104.5, 106.0, 102.0, 105.5)]          # low[2] clears high[0]
    fv = _mk(bars)
    _CACHE.clear()
    assert fvg_bull(fv, 2), "fvg missed a clean three-bar imbalance"
    assert fvg_recent(fv, 2) and gap_fill(fv, 2) is False, \
        "a fresh gap is unfilled and cannot yet have been filled"

    bars = [(100, 100.5, 99.5, 100), (101, 102.0, 100.4, 101.5),
            (101.6, 103.0, 100.2, 102.5)]          # low[2] dips to high[0]
    fv2 = _mk(bars)
    assert not fvg_bull(fv2, 2), "fvg fired on overlapping bars"

    # A later bar holding above the gap lid keeps it unfilled; a dip back to
    # the lid fills it (and closes the fvg_recent case while opening the
    # gap_fill one).
    bars = [(100, 100.5, 99.5, 100), (101, 104.0, 100.8, 103.5),
            (104.5, 106.0, 102.0, 105.5), (105.6, 107.0, 103.0, 106.0)]
    fv3 = _mk(bars)
    assert fvg_recent(fv3, 3), "an untouched zone read as filled"
    assert not gap_fill(fv3, 3), "gap_fill fired with no revisit"
    bars = [(100, 100.5, 99.5, 100), (101, 104.0, 100.8, 103.5),
            (104.5, 106.0, 102.0, 105.5), (105.0, 105.8, 101.9, 105.2)]
    fv4 = _mk(bars)
    assert not fvg_recent(fv4, 3), "fvg_recent ignored a revisit to the lid"
    assert gap_fill(fv4, 3), "gap_fill missed a revisit that closed back above"

    # A close below the floor destroys the zone -- neither reading survives.
    bars = [(100, 100.5, 99.5, 100), (101, 104.0, 100.8, 103.5),
            (104.5, 106.0, 102.0, 105.5), (104.0, 104.6, 100.2, 100.4)]
    fv5 = _mk(bars)
    assert not gap_fill(fv5, 3), "gap_fill accepted a close through the floor"

    # The plain uptrend's bars touch edge to edge; strict inequality means
    # no FVG -- a detector that fires there matches everything.
    assert not fvg_bull(s, i), "fvg fired on an edge-touching uptrend"

    # --- H15 pullback depth ---------------------------------------------------
    # Shallow: rise to 100.5, dip ~8%, break out. The dip must read as depth,
    # so the window high must sit BEFORE the dip bars.
    def _rise(start, n):
        return [(start + k, start + k + 0.5, start + k - 0.5, start + k)
                for k in range(n)]

    bars = [(95, 95.5, 94.5, 95) for _ in range(8)]          # base, h=95.5
    bars += _rise(96, 5)                                      # h ends 100.5
    bars += [(99, 99.4, 92.4, 99.2),                          # dip: low 92.4
             (99.2, 99.6, 92.6, 99.4),
             (99.4, 99.8, 92.8, 99.6)]
    sh = _mk(bars)
    j = len(bars)
    sh.open.append(101); sh.high.append(102); sh.low.append(100.9)
    sh.close.append(101.5); sh.volume.append(1000)
    sh.turnover.append(1e6); sh.deliv_pct.append(50.0)
    _CACHE.clear()
    assert abs(pullback_pct(sh, j) - (100.5 - 92.4) / 100.5 * 100) < 1e-9, \
        "depth is not measured off the window high"
    assert pb_shallow(sh, j), "shallow rejected an ~8% dip"
    assert not pb_deep(sh, j), "deep accepted an ~8% dip"

    # Deep: same frame, the dip runs to 64 (~36%).
    bars = [(95, 95.5, 94.5, 95) for _ in range(8)]
    bars += _rise(96, 5)
    bars += [(99, 99.4, 84.0, 99.0),
             (98, 98.4, 74.0, 98.0),
             (90, 90.4, 64.0, 90.0),
             (89, 89.4, 88.0, 89.0),
             (89.2, 89.8, 88.2, 89.5)]
    dp = _mk(bars)
    j = len(bars)
    dp.open.append(101); dp.high.append(102); dp.low.append(100.9)
    dp.close.append(101.5); dp.volume.append(1000)
    dp.turnover.append(1e6); dp.deliv_pct.append(50.0)
    _CACHE.clear()
    assert abs(pullback_pct(dp, j) - (100.5 - 64.0) / 100.5 * 100) < 1e-9
    assert pb_deep(dp, j), "deep rejected a ~36% flush"
    assert not pb_shallow(dp, j), "shallow accepted a ~36% flush"

    # A monotonic uptrend has its window high on the newest bar: no pullback
    # exists yet, which IS the extreme shallow case, never the deep one.
    assert pullback_pct(s, i) == 0.0
    assert pb_shallow(s, i) and not pb_deep(s, i)

    # Equal-high ties resolve to the most recent touch, so a flat top is no
    # pullback rather than a base-sized dip.
    fl = _mk([(95, 95.5, 94.5, 95)] * 30)
    _CACHE.clear()
    assert pullback_pct(fl, 29) == 0.0

    # --- H16 swing structure ---------------------------------------------------
    def _path_bars(path):
        bars = []
        prev = path[0]
        for c in path[1:]:
            bars.append((prev, max(prev, c) + 0.3, min(prev, c) - 0.3, c))
            prev = c
        return bars

    def _breakout(bars):
        bars = list(bars)
        bars.append((113, 113.8, 112.2, 113.5))
        return bars, len(bars) - 1

    # Intact staircase: troughs 92 -> 97 ascending, peaks 106 -> 112
    # ascending, nothing closes back through the newest higher low.
    up = [100, 98, 96, 94, 92, 95, 99, 103, 106, 104,
          101, 98, 97, 100, 104, 108, 112, 109, 106, 104.5]
    st_bars, j = _breakout(_path_bars(up))
    st = _mk(st_bars)
    _CACHE.clear()
    assert len(_confirmed_swing_lows(st, j)) >= 2, "fixture has no pivots"
    assert hl_intact(st, j), "an intact staircase read as broken"
    assert hh_hl(st, j), "ascending highs missed on an intact staircase"

    # Descending lows: second trough BELOW the first -- structure fails on
    # the slope test before any violation matters.
    dn = [100, 98, 96, 99, 102, 98, 94, 97, 100, 103, 106, 104, 101]
    dn_bars, j2 = _breakout(_path_bars(dn))
    dn_s = _mk(dn_bars)
    _CACHE.clear()
    assert not hl_intact(dn_s, j2), "descending lows passed as intact"
    assert not hh_hl(dn_s, j2)

    # CHoCH inside the unconfirmed zone: a close back through the known
    # higher low (96.7) prints damage immediately -- it cannot hide by being
    # too recent to confirm as its own pivot.
    ch = up[:13] + [99, 96.4, 98]
    ch_bars, j3 = _breakout(_path_bars(ch))
    ch_s = _mk(ch_bars)
    _CACHE.clear()
    assert not hl_intact(ch_s, j3), "a close through the HL read as intact"
    assert not hh_hl(ch_s, j3)

    # A monotonic uptrend has no pullback pivots at all: no readable
    # structure, so the gate rejects it BY DESIGN.
    _CACHE.clear()
    assert not hl_intact(s, i), "structure fired on a pivotless run"

    print("entry selftest ok (H5 detectors fire on their shape, not on a trend;"
          " H13-H16 candle/FVG/pullback/structure gates fire on theirs)")


if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        _selftest()
