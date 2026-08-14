#!/usr/bin/env python3
"""Strategy specs: a bounded vocabulary the generator selects from.

The generator emits DATA, never code. Three things follow:
  - specs are hashable, so judge.spec_hash identifies a hypothesis
  - the search space is bounded by PREDICATES, not by prompt wording
  - no LLM-authored code ever executes in the money path

Predicates return None when history is insufficient. None propagates to "no
signal" -- unknown is never silently read as False, which would manufacture
entries at the start of every series.
"""
import json

import engine
import features


class Ctx:
    """Memoised indicators for one symbol. Many specs over one symbol share work."""

    def __init__(self, series, breadth=None):
        self.s = series
        self.breadth = breadth or {}
        self._m = {}

    def _get(self, key, fn):
        if key not in self._m:
            self._m[key] = fn()
        return self._m[key]

    def sma(self, n):   return self._get(("sma", n), lambda: features.sma(self.s.close, n))
    def ema(self, n):   return self._get(("ema", n), lambda: features.ema(self.s.close, n))
    def atr(self, n):   return self._get(("atr", n), lambda: features.atr(self.s.high, self.s.low, self.s.close, n))
    def hmax(self, n):  return self._get(("hmax", n), lambda: features.rolling_max(self.s.high, n))
    def lmin(self, n):  return self._get(("lmin", n), lambda: features.rolling_min(self.s.low, n))
    def vsma(self, n):  return self._get(("vsma", n), lambda: features.sma(self.s.volume, n))
    def dz(self, n):    return self._get(("dz", n), lambda: features.zscore(self.s.deliv_pct, n))
    def tsma(self, n):  return self._get(("tsma", n), lambda: features.sma(self.s.turnover, n))


# --- predicate vocabulary -------------------------------------------------
# name -> (fn(ctx, i, **params) -> bool|None, {param: (type, lo, hi)})
# The param ranges ARE the search space. A generator cannot propose outside them.

def _p_close_above_sma(c, i, period):
    v = c.sma(period)[i]
    return None if v is None else c.s.close[i] > v


def _p_close_above_ema(c, i, period):
    v = c.ema(period)[i]
    return None if v is None else c.s.close[i] > v


def _p_ema_slope_up(c, i, period, lookback):
    e = c.ema(period)
    if i < lookback or e[i] is None or e[i - lookback] is None:
        return None
    return e[i] > e[i - lookback]


def _p_breakout_prior_high(c, i, lookback):
    """Close exceeds the highest high of the PRIOR `lookback` bars (excludes today,
    or today's own high satisfies it trivially)."""
    if i < 1:
        return None
    h = c.hmax(lookback)[i - 1]
    return None if h is None else c.s.close[i] > h


def _p_vol_expansion(c, i, mult, window):
    v = c.vsma(window)[i]
    return None if not v else c.s.volume[i] > mult * v


def _p_deliv_zscore_above(c, i, z, window):
    v = c.dz(window)[i]
    return None if v is None else v > z


def _p_deliv_pct_above(c, i, pct):
    d = c.s.deliv_pct[i]
    return None if d is None or d < 0 else d > pct


def _p_turnover_above(c, i, rupees, window):
    v = c.tsma(window)[i]
    return None if v is None else v > rupees


def _p_atr_pct_below(c, i, pct, period):
    """Volatility contraction: ATR as a share of price is compressed."""
    a = c.atr(period)[i]
    if a is None or not c.s.close[i]:
        return None
    return (a / c.s.close[i]) * 100 < pct


def _p_range_contraction(c, i, window, max_pct):
    """The last `window` bars sit inside a tight band -- the VCP base test."""
    hi, lo = c.hmax(window)[i], c.lmin(window)[i]
    if hi is None or lo is None or not lo:
        return None
    return ((hi - lo) / lo) * 100 < max_pct


def _p_pullback_to_ema(c, i, period, tol_pct):
    e = c.ema(period)[i]
    if e is None or not e:
        return None
    return abs(c.s.low[i] - e) / e * 100 < tol_pct


def _p_ema_cluster_tight(c, i, fast, slow, max_spread_pct):
    f, s = c.ema(fast)[i], c.ema(slow)[i]
    if f is None or s is None or not s:
        return None
    return abs(f - s) / s * 100 < max_spread_pct


def _p_breadth_above(c, i, pct):
    b = c.breadth.get(c.s.days[i])
    return None if b is None else b * 100 > pct


def _p_above_prior_close(c, i):
    return None if i < 1 else c.s.close[i] > c.s.close[i - 1]


def _p_surveillance_known(c, i):
    """Guard for live trading: refuse when point-in-time surveillance is absent."""
    return bool(c.s.surveillance_known[i])


PREDICATES = {
    "close_above_sma":     (_p_close_above_sma,   {"period": (int, 5, 300)}),
    "close_above_ema":     (_p_close_above_ema,   {"period": (int, 5, 300)}),
    "ema_slope_up":        (_p_ema_slope_up,      {"period": (int, 5, 300), "lookback": (int, 1, 60)}),
    "breakout_prior_high": (_p_breakout_prior_high, {"lookback": (int, 5, 500)}),
    "vol_expansion":       (_p_vol_expansion,     {"mult": (float, 1.0, 10.0), "window": (int, 5, 200)}),
    "deliv_zscore_above":  (_p_deliv_zscore_above, {"z": (float, -3.0, 5.0), "window": (int, 5, 200)}),
    "deliv_pct_above":     (_p_deliv_pct_above,   {"pct": (float, 0.0, 100.0)}),
    "turnover_above":      (_p_turnover_above,    {"rupees": (float, 1e5, 1e10), "window": (int, 5, 200)}),
    "atr_pct_below":       (_p_atr_pct_below,     {"pct": (float, 0.1, 25.0), "period": (int, 5, 100)}),
    "range_contraction":   (_p_range_contraction, {"window": (int, 3, 120), "max_pct": (float, 1.0, 60.0)}),
    "pullback_to_ema":     (_p_pullback_to_ema,   {"period": (int, 5, 300), "tol_pct": (float, 0.1, 10.0)}),
    "ema_cluster_tight":   (_p_ema_cluster_tight, {"fast": (int, 5, 100), "slow": (int, 10, 300), "max_spread_pct": (float, 0.1, 15.0)}),
    "breadth_above":       (_p_breadth_above,     {"pct": (float, 0.0, 100.0)}),
    "above_prior_close":   (_p_above_prior_close, {}),
    "surveillance_known":  (_p_surveillance_known, {}),
}

ENTRY_RULES = {"prior_high", "close"}
STOP_RULES = {"swing_low", "atr"}
TARGET_RULES = {"r_multiple", "prior_swing_high"}


class SpecError(ValueError):
    pass


def validate(spec: dict):
    """Raise SpecError on anything outside the vocabulary. This is what keeps a
    generator inside the search space -- prompts are not a boundary."""
    if not spec.get("conditions"):
        raise SpecError("spec needs at least one condition")
    for cond in spec["conditions"]:
        name = cond.get("pred")
        if name not in PREDICATES:
            raise SpecError(f"unknown predicate: {name!r}")
        schema = PREDICATES[name][1]
        given = {k: v for k, v in cond.items() if k != "pred"}
        if set(given) != set(schema):
            raise SpecError(f"{name}: expected params {sorted(schema)}, got {sorted(given)}")
        for k, v in given.items():
            typ, lo, hi = schema[k]
            if not isinstance(v, (int, float)) or isinstance(v, bool):
                raise SpecError(f"{name}.{k}: not numeric")
            if not (lo <= v <= hi):
                raise SpecError(f"{name}.{k}={v} outside [{lo}, {hi}]")
    for key, allowed in (("entry", ENTRY_RULES), ("stop", STOP_RULES), ("target", TARGET_RULES)):
        rule = spec.get(key, {}).get("rule")
        if rule not in allowed:
            raise SpecError(f"{key}.rule {rule!r} not in {sorted(allowed)}")
    return True


def _entry_price(spec, c, i):
    r = spec["entry"]
    base = c.s.high[i] if r["rule"] == "prior_high" else c.s.close[i]
    return base * (1 + r.get("buffer_pct", 0.0) / 100)


def _stop_price(spec, c, i):
    r = spec["stop"]
    if r["rule"] == "swing_low":
        lo = c.lmin(r["lookback"])[i]
        if lo is None:
            return None
        a = c.atr(14)[i] or 0.0
        return lo - a * r.get("atr_mult", 0.0)
    a = c.atr(r.get("period", 14))[i]
    return None if a is None else c.s.close[i] - a * r["mult"]


def _target_price(spec, c, i, entry, stop):
    r = spec["target"]
    if r["rule"] == "r_multiple":
        # Note: this makes rr exactly r by construction, so engine.MIN_RR cannot
        # reject it. The binding question becomes whether the target is ever
        # reached -- which only the backtest answers.
        return entry + r["r"] * (entry - stop)
    h = c.hmax(r["lookback"])[i]
    return None if h is None else h


def evaluate(spec: dict, c: Ctx, i: int):
    """-> engine.Signal or None. None means no signal OR unknown; both are
    'do nothing', and conflating them is the safe direction."""
    for cond in spec["conditions"]:
        fn = PREDICATES[cond["pred"]][0]
        if fn(c, i, **{k: v for k, v in cond.items() if k != "pred"}) is not True:
            return None

    entry = _entry_price(spec, c, i)
    stop = _stop_price(spec, c, i)
    if stop is None or stop >= entry:
        return None
    target = _target_price(spec, c, i, entry, stop)
    if target is None or target <= entry:
        return None
    return engine.Signal(symbol=c.s.symbol, setup=spec.get("setup", "unnamed"),
                         entry=entry, stop=stop, target=target,
                         spec_version=spec.get("version", "v0"))


STAGE2_BREAKOUT = {
    "setup": "stage2_breakout", "version": "v1",
    "conditions": [
        {"pred": "close_above_sma", "period": 200},
        {"pred": "ema_slope_up", "period": 150, "lookback": 20},
        {"pred": "breakout_prior_high", "lookback": 250},
        {"pred": "vol_expansion", "mult": 2.0, "window": 50},
        {"pred": "deliv_zscore_above", "z": 1.5, "window": 60},
        {"pred": "turnover_above", "rupees": 5e7, "window": 20},
    ],
    "entry": {"rule": "prior_high", "buffer_pct": 0.1},
    "stop": {"rule": "swing_low", "lookback": 10, "atr_mult": 0.5},
    "target": {"rule": "r_multiple", "r": 3.0},
}


def _selftest():
    from datetime import date, timedelta

    validate(STAGE2_BREAKOUT)

    for bad, why in [
        ({**STAGE2_BREAKOUT, "conditions": [{"pred": "rm -rf /"}]}, "unknown predicate"),
        ({**STAGE2_BREAKOUT, "conditions": [{"pred": "close_above_sma", "period": 9999}]}, "out of range"),
        ({**STAGE2_BREAKOUT, "conditions": [{"pred": "close_above_sma"}]}, "missing param"),
        ({**STAGE2_BREAKOUT, "target": {"rule": "moon"}}, "unknown target rule"),
        ({**STAGE2_BREAKOUT, "conditions": []}, "empty conditions"),
    ]:
        try:
            validate(bad)
            raise AssertionError(f"validate accepted {why}")
        except SpecError:
            pass

    # a rising series that breaks out on the last bar with volume + delivery
    n = 300
    s = features.Series("T")
    d0 = date(2024, 1, 1)
    for k in range(n):
        px = 100 + k * 0.5
        s.days.append(d0 + timedelta(days=k))
        s.open.append(px); s.high.append(px + 1); s.low.append(px - 1); s.close.append(px)
        s.volume.append(1000); s.turnover.append(1e8)
        s.deliv_pct.append(40.0); s.surveillance_known.append(True)
    s.close[-1] = s.high[-1] = 400.0     # breakout bar
    s.volume[-1] = 9000
    s.deliv_pct[-1] = 95.0

    c = Ctx(s)
    sig = evaluate(STAGE2_BREAKOUT, c, n - 1)
    assert sig is not None, "clean breakout should signal"
    assert sig.entry > 400.0 and sig.stop < sig.entry
    assert abs(sig.rr - 3.0) < 1e-9, sig.rr

    # same bar without the volume expansion -> no signal
    s.volume[-1] = 1000
    assert evaluate(STAGE2_BREAKOUT, Ctx(s), n - 1) is None

    # insufficient history must yield None, not a signal
    assert evaluate(STAGE2_BREAKOUT, Ctx(s), 5) is None

    # predicates must return None (not False) before their window fills
    assert PREDICATES["close_above_sma"][0](Ctx(s), 3, period=200) is None
    assert Ctx(s).atr(14)[3] is None

    # spec must survive a JSON round-trip -- the judge hashes it
    assert json.loads(json.dumps(STAGE2_BREAKOUT)) == STAGE2_BREAKOUT
    print("spec selftest ok")


if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        _selftest()
    else:
        print(f"{len(PREDICATES)} predicates:")
        for k, (_, sch) in sorted(PREDICATES.items()):
            params = ", ".join(f"{p}:[{lo},{hi}]" for p, (_, lo, hi) in sch.items()) or "-"
            print(f"  {k:22} {params}")
