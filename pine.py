#!/usr/bin/env python3
"""Translate a strategy spec into Pine Script v6 for visual review.

Pine is a RENDER TARGET, never the source of truth. The Python evaluator decides
what trades; this exists so a promoted spec can be scrolled through years of
charts and eyeballed -- does the setup look like real structure, or like a
curve-fit accident? That question is not answerable from summary statistics.

THE IMPORTANT PART: several predicates cannot be expressed in Pine, because
TradingView does not have the data.

  deliv_pct_above / deliv_zscore_above  NSE settlement statistic, not a price series
  breadth_above                         needs the whole NSE universe per date
  rs_rank_above                         cross-sectional percentile across 2,486 names
  surveillance_known                    ASM/GSM state, not market data

Silently dropping those would make the Pine overlay fire on MORE bars than the
real system -- and you would conclude the Python side was broken, or worse,
trust the Pine version. So untranslatable conditions are emitted as comments AND
announced on the chart itself. An honest approximation says it is one.
"""
import json
import sys

# predicate -> (pine expression template, needs_data_pine_lacks)
TRANSLATORS = {
    "close_above_sma":     (lambda p: f"close > ta.sma(close, {p['period']})", False),
    "close_above_ema":     (lambda p: f"close > ta.ema(close, {p['period']})", False),
    "ema_slope_up":        (lambda p: f"ta.ema(close, {p['period']}) > ta.ema(close, {p['period']})[{p['lookback']}]", False),
    "breakout_prior_high": (lambda p: f"close > ta.highest(high, {p['lookback']})[1]", False),
    "vol_expansion":       (lambda p: f"volume > {p['mult']} * ta.sma(volume, {p['window']})", False),
    "turnover_above":      (lambda p: f"ta.sma(close * volume, {p['window']}) > {p['rupees']:.0f}", False),
    "atr_pct_below":       (lambda p: f"(ta.atr({p['period']}) / close) * 100 < {p['pct']}", False),
    "range_contraction":   (lambda p: f"((ta.highest(high, {p['window']}) - ta.lowest(low, {p['window']})) / ta.lowest(low, {p['window']})) * 100 < {p['max_pct']}", False),
    "pullback_to_ema":     (lambda p: f"math.abs(low - ta.ema(close, {p['period']})) / ta.ema(close, {p['period']}) * 100 < {p['tol_pct']}", False),
    "ema_cluster_tight":   (lambda p: f"math.abs(ta.ema(close, {p['fast']}) - ta.ema(close, {p['slow']})) / ta.ema(close, {p['slow']}) * 100 < {p['max_spread_pct']}", False),
    "above_prior_close":   (lambda p: "close > close[1]", False),
    "close_near_high":     (lambda p: f"(high - close) / math.max(high - low, syminfo.mintick) * 100 < {p['tol_pct']}", False),
    "rsi_below":           (lambda p: f"ta.rsi(close, {p['period']}) < {p['level']}", False),
    "pct_off_high":        (lambda p: f"(ta.highest(high, {p['lookback']}) - close) / ta.highest(high, {p['lookback']}) * 100 > {p['pct']}", False),
    "reclaim_prior_low":   (lambda p: f"low < ta.lowest(low, {p['lookback']})[1] and close >= ta.lowest(low, {p['lookback']})[1]", False),
    "down_days":           (lambda p: " and ".join(f"close[{k}] < close[{k+1}]" for k in range(p['n'])), False),
    # Data TradingView does not carry:
    "deliv_pct_above":     (lambda p: f"delivery % > {p['pct']}", True),
    "deliv_zscore_above":  (lambda p: f"delivery z-score > {p['z']} over {p['window']}d", True),
    "breadth_above":       (lambda p: f"NSE breadth > {p['pct']}%", True),
    "rs_rank_above":       (lambda p: f"RS rank > {p['pct']} pctile over {p['lookback']}d", True),
    "surveillance_known":  (lambda p: "point-in-time ASM/GSM known", True),
    # Quarterly filings with as-of dating. TradingView has no NSE fundamentals
    # keyed to broadcast date, so these are declared, never approximated.
    "revenue_growth_yoy":  (lambda p: f"revenue YoY > {p['min_pct']}% (as-of filed)", True),
    "net_margin_above":    (lambda p: f"net margin > {p['pct']}% (as-of filed)", True),
    "profitable_quarters": (lambda p: f"profitable in last {p['n']} filed quarters", True),
    "earnings_clear":      (lambda p: f"no results expected within {p['days']}d", True),
}


def translate(spec: dict) -> tuple[str, list]:
    """-> (pine_source, untranslatable_descriptions)."""
    lines, missing = [], []
    for cond in spec["conditions"]:
        name = cond["pred"]
        params = {k: v for k, v in cond.items() if k != "pred"}
        fn, unavailable = TRANSLATORS[name]
        text = fn(params)
        if unavailable:
            missing.append(f"{name}: {text}")
        else:
            lines.append((name, text))
    return lines, missing


def render(spec: dict, spec_hash="unknown") -> str:
    conds, missing = translate(spec)
    setup = spec.get("setup", "spec")
    stop, target = spec["stop"], spec["target"]

    body = [
        "//@version=6",
        f'indicator("{setup} [{spec_hash[:8]}]", overlay=true)',
        "",
        "// Generated from a strategy spec. Python is the source of truth; this is",
        "// a review overlay. Do NOT trade from it.",
        "",
    ]
    if missing:
        body += ["// ---------------------------------------------------------------",
                 "// CONDITIONS THIS CHART CANNOT EVALUATE (TradingView lacks the data):"]
        body += [f"//   - {m}" for m in missing]
        body += ["// This overlay therefore fires on MORE bars than the real system.",
                 "// ---------------------------------------------------------------", ""]

    for name, expr in conds:
        body.append(f"{name} = {expr}")
    body.append("")
    body.append("setup = " + (" and ".join(n for n, _ in conds) if conds else "false"))
    body.append("")

    # entry / stop / target geometry
    buf = spec["entry"].get("buffer_pct", 0.0)
    base = "high" if spec["entry"]["rule"] == "prior_high" else "close"
    body.append(f"entryPx = {base} * (1 + {buf} / 100)")
    if stop["rule"] == "swing_low":
        body.append(f"stopPx  = ta.lowest(low, {stop['lookback']}) - ta.atr(14) * {stop.get('atr_mult', 0)}")
    else:
        body.append(f"stopPx  = close - ta.atr({stop.get('period', 14)}) * {stop['mult']}")
    if target["rule"] == "r_multiple":
        body.append(f"tgtPx   = entryPx + {target['r']} * (entryPx - stopPx)")
    else:
        body.append(f"tgtPx   = ta.highest(high, {target['lookback']})")

    body += [
        "",
        "plotshape(setup, title=\"setup\", style=shape.triangleup, location=location.belowbar,",
        "          color=color.new(color.teal, 0), size=size.small)",
        "",
        "plot(setup ? entryPx : na, title=\"entry\",  color=color.new(color.teal, 0),   style=plot.style_linebr, linewidth=2)",
        "plot(setup ? stopPx  : na, title=\"stop\",   color=color.new(color.red, 0),    style=plot.style_linebr, linewidth=2)",
        "plot(setup ? tgtPx   : na, title=\"target\", color=color.new(color.blue, 0),   style=plot.style_linebr, linewidth=2)",
        "",
        f"// hold horizon: {spec.get('hold', {}).get('max_bars', '?')} bars"
        f"   |   rank: {spec.get('rank', {}).get('by', 'none')}",
    ]
    if missing:
        body += [
            "",
            "var table warn = table.new(position.top_right, 1, 1)",
            "if barstate.islast",
            f'    table.cell(warn, 0, 0, "APPROXIMATION — {len(missing)} condition(s) not evaluable in Pine",',
            "               bgcolor=color.new(color.orange, 80), text_size=size.small)",
        ]
    return "\n".join(body) + "\n"


def _selftest():
    import spec as specmod
    src = render(specmod.STAGE2_BREAKOUT, "abc12345")

    assert "//@version=6" in src and "indicator(" in src
    assert "close > ta.sma(close, 200)" in src, src
    assert "ta.ema(close, 150) > ta.ema(close, 150)[20]" in src
    assert "close > ta.highest(high, 250)[1]" in src
    assert "volume > 2.0 * ta.sma(volume, 50)" in src

    # the delivery condition must be announced, never silently dropped
    conds, missing = translate(specmod.STAGE2_BREAKOUT)
    assert any("deliv_zscore_above" in m for m in missing), missing
    assert "CANNOT EVALUATE" in src
    assert "APPROXIMATION" in src
    assert "deliv_zscore_above = " not in src, "untranslatable predicate leaked into logic"

    # every predicate in the vocabulary must have a translator, or a new one
    # would silently KeyError the first time a spec used it
    assert set(specmod.PREDICATES) == set(TRANSLATORS), (
        set(specmod.PREDICATES) ^ set(TRANSLATORS))

    # a fully-translatable spec must carry no warning table
    clean = {**specmod.STAGE2_BREAKOUT,
             "conditions": [{"pred": "close_above_sma", "period": 50},
                            {"pred": "rsi_below", "period": 14, "level": 30.0}]}
    csrc = render(clean, "clean001")
    assert "APPROXIMATION" not in csrc and "CANNOT EVALUATE" not in csrc
    assert "setup = close_above_sma and rsi_below" in csrc, csrc
    print("pine selftest ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    else:
        import spec as specmod
        sp = specmod.STAGE2_BREAKOUT
        if len(sys.argv) > 1 and sys.argv[1].endswith(".json"):
            sp = json.loads(open(sys.argv[1]).read())
        print(render(sp))


# --- compiler verification -------------------------------------------------

def minimal_script(name: str, params: dict) -> str:
    """Smallest valid script exercising exactly one predicate translation."""
    fn, unavailable = TRANSLATORS[name]
    if unavailable:
        return None
    return ("//@version=6\n"
            f'indicator("verify {name}", overlay=true)\n'
            f"cond = {fn(params)}\n"
            "plotshape(cond, style=shape.triangleup, location=location.belowbar)\n")


SAMPLE_PARAMS = {
    "close_above_sma": {"period": 200}, "close_above_ema": {"period": 50},
    "ema_slope_up": {"period": 150, "lookback": 20},
    "breakout_prior_high": {"lookback": 250},
    "vol_expansion": {"mult": 2.0, "window": 50},
    "turnover_above": {"rupees": 5e7, "window": 20},
    "atr_pct_below": {"pct": 5.0, "period": 14},
    "range_contraction": {"window": 20, "max_pct": 15.0},
    "pullback_to_ema": {"period": 50, "tol_pct": 2.0},
    "ema_cluster_tight": {"fast": 20, "slow": 50, "max_spread_pct": 2.0},
    "above_prior_close": {}, "close_near_high": {"tol_pct": 25.0},
    "rsi_below": {"period": 14, "level": 30.0},
    "pct_off_high": {"lookback": 60, "pct": 20.0},
    "reclaim_prior_low": {"lookback": 20}, "down_days": {"n": 3},
    "deliv_pct_above": {"pct": 50.0}, "deliv_zscore_above": {"z": 1.5, "window": 60},
    "breadth_above": {"pct": 60.0}, "rs_rank_above": {"lookback": 60, "pct": 80.0},
    "surveillance_known": {},
    "revenue_growth_yoy": {"min_pct": 10.0},
    "net_margin_above": {"pct": 5.0},
    "profitable_quarters": {"n": 4},
    "earnings_clear": {"days": 30},
}


def verify_all(push_fn, compile_fn, sleep=0.4):
    """Compile every translatable template against TradingView itself.

    Templates in this file were written from memory; that is exactly where an
    LLM hallucinates Pine syntax. The real compiler is ground truth, so each one
    is checked against it rather than trusted. Returns {name: (ok, errors)}.
    """
    import tempfile
    import time
    results = {}
    for name in sorted(TRANSLATORS):
        src = minimal_script(name, SAMPLE_PARAMS[name])
        if src is None:
            results[name] = (None, ["not expressible in Pine (by design)"])
            continue
        with tempfile.NamedTemporaryFile("w", suffix=".pine", delete=False) as f:
            f.write(src)
            path = f.name
        r = push_fn(path)
        if not r.get("success"):
            results[name] = (False, [f"push failed: {r.get('error')}"])
            continue
        time.sleep(sleep)
        c = compile_fn()
        errs = [e for e in (c.get("errors") or []) if e.get("severity", 0) < 4]
        results[name] = (not errs, [e.get("message", "")[:90] for e in errs])
    return results
