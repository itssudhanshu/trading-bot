#!/usr/bin/env python3
"""Classify EQ-series symbols that NSE's company master can no longer speak for.

WHY THIS EXISTS. `universe.non_equity_symbols()` builds its denylist as
`traded - master`, and both sides come from a snapshot that has BOTH files.
Only 7 snapshots do, all inside one week (2026-08-14..2026-08-20), so a fund
that stopped trading before that week is never in `traded`, never enters the
denylist, and appears in the universe for every historical date. 22 of the live
bucket's 195 trades were gold, silver and index ETFs bought this way.

Taking the UNION of the masters does not help: those 7 cover one week and their
union adds 15 names, none of them funds. There is no point-in-time record of
what a 2021 symbol WAS. So this file infers it, from three signals, and reports
what the inference costs on data where the answer is known.

    positives  the 343 symbols in today's EQ bhavcopy that today's company
               master omits -- NSE's own statement that they are not companies
    negatives  the 2,568 symbols in the company master

THE SIGNALS, and why none is enough alone:

  name    An instrument-type word (GOLD, NIFTY, ETF, SDL...). Measured on the
          labelled sets: catches 64% of funds and 12 REAL COMPANIES --
          DECNGOLD is a gold MINER, JETFREIGHT merely contains "ETF",
          PNBGILTS is a primary dealer. Deleting those from history is the
          survivorship bias `universe.py` exists to avoid.

  track   A fund's price IS a basket, so its daily returns track some reference
          fund almost exactly. Measured: zero false positives above 0.92, but
          recall only 25% there, and below 0.90 it starts eating INFY and TCS,
          which dominate ITBEES.

  still   A bond, gilt or liquid fund barely moves, so it tracks nothing and
          the second signal goes blind on it. Measured: no company is that
          still. Alone it would take any suspended stock whose close is
          carried forward.

Together they resolve each other. Every one of the 12 name false positives
tracks at 0.51 or below -- the gold miner does not move like gold. So:

  tier A  the symbol ENDS in ETF / BEES / IETF.  0 hits on the 2,568 labelled
          companies, 84 funds. "Ends in" and not "contains", because
          JETFREIGHT contains ETF.
  tier B  an instrument token AND tracking >= 0.60.  0 hits on the labelled
          companies, and it is the conjunction that earns the 0: at 0.50
          GOLDIAM and PNBGILTS come back.
  tier C  an instrument token AND a daily return sd under 0.50%. Bond, gilt,
          SDL and liquid funds correlate with NOTHING in the reference set --
          EBBETF0425 tracks its best match at 0.10 -- because they barely
          move, so tier B cannot see them and the live bucket went on buying
          Bharat Bond ETFs after the first two tiers shipped. Stillness is
          the evidence instead: the quietest company on the exchange is PGHH
          at 1.372% daily sd, and 0 of 2,214 labelled companies fall under
          0.50. A share of an operating company does not move 0.15% a day.

TOKENS THAT WERE TESTED AND REJECTED, and the rule they cost. AUTO
(BAJAJ-AUTO), PHARM (SUNPHARMA,
AUROPHARMA), TECH (TECHM, HCLTECH), INFRA (MANINFRA, JSWINFRA), FIN (JIOFIN,
LICHSGFIN...), MCAP (DAMCAPITAL) each fail on the labelled companies. METAL and
ENERGY pass there and fail on the DELISTED population, which is the population
that matters: they take TATAMETALI and SWANENERGY. BANKN/BANKP take ICICIBANKN
and ICICIBANKP, which track ICICI Bank itself (0.46/0.73) and no index at all.
A token is not safe because it is safe against companies that still exist.

DIV, CONSU and VALUE were rejected on MARGIN rather than on a false positive:
each scored zero, and each left a real company one bad quarter from the bar --
DIVISLAB tracked PHARMABEES at 0.591 against a bar of 0.60. A rule that is
correct by 0.009 is correct by luck. The funds they used to catch moved into
REVIEWED, where the judgement is visible and carries its own number.

WHAT IS DELIBERATELY LEFT IN. Anything the evidence does not carry stays in the
universe, because a wrongly deleted company is the worse error and is invisible
once made. `--report` prints the residue.

    python3 src/ops/classify_non_equity.py --validate   # the numbers above
    python3 src/ops/classify_non_equity.py --report     # what is left in
    python3 src/ops/classify_non_equity.py --write      # rewrite the artifact
"""

import sys as _sys, pathlib as _pl
_sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[1]))
import paths  # noqa: F401  -- puts the source dirs on sys.path
import csv
import io
import json
import math
import re
import statistics
import sys
from pathlib import Path

import universe
from paths import RAW

OUT = paths.DATA / "non_equity_history.json"

# Ends in, never contains: JETFREIGHT contains "ETF".
SUFFIX = re.compile(r"(ETF|BEES|IETF)$")

# Instrument, index and asset words only -- never a SPONSOR. A sponsor prefix
# would take HDFC (0.70) and IDFC (0.67), both real companies that vanished by
# merger. Every token here was checked against the 2,568 labelled companies AND
# against the delisted population; see the docstring for the ones that failed.
TOKENS = ("ETF", "BEES", "IETF", "GOLD", "SILVER", "NIFTY", "SENSEX", "SNX",
          "GILT", "GSEC", "SDL", "LIQUID", "LIQ", "NV20", "N50", "N200",
          "NEXT50", "NXT50", "MID150", "SMALL250", "SC250", "MOMENTUM",
          "MOM30", "MOM50", "MOM100", "LOWVOL", "LOVOL", "ALPHA", "QUALITY",
          "QLTY", "Q50", "DIVOPP", "PSUBANK", "PSUBK", "BANKETF",
          "MSCI", "HNGSNG", "BSE500", "EQUAL", "SHARIA", "JUNIOR", "FMCG",
          "COMMO", "MNC", "ESG")

# How far under TRACK_MIN the closest labelled company must sit. Zero false
# positives is not the same as a safe rule: DIV, CONSU and VALUE all scored
# zero and left DIVISLAB at 0.591 against a 0.60 bar -- a 0.009 margin, one
# quarter of pharma away from deleting Divi's Laboratories from history.
# `--validate` fails if any company carrying a token gets this close.
MARGIN_MIN = 0.05

# The conjunction threshold. 0.60 is where the labelled companies stop being
# caught (GOLDIAM 0.51 and PNBGILTS 0.50 are the last two out); it is not a
# fitted value and must not be re-tuned to admit a particular symbol.
TRACK_MIN = 0.60
MIN_OVERLAP = 60        # sessions two series must share before correlating

# Daily return sd, in percent, under which an instrument is too still to be a
# share. Set at a factor of 2.7 below the quietest labelled company (PGHH,
# 1.372%) rather than just below it, because the margin is the protection.
# Only ever applied WITH a name token: a suspended stock whose close is carried
# forward is also very still, and stillness alone would take it.
MAX_STILLNESS = 0.50
MIN_STILL_SESSIONS = 120    # below this the sd is a small-sample artefact

# Long-lived labelled funds, spanning the asset classes NSE lists in EQ. These
# are references, not a rule: a candidate is compared against all of them and
# scored on its best match.
REFERENCES = ("NIFTYBEES", "BANKBEES", "JUNIORBEES", "GOLDBEES", "SILVERBEES",
              "LIQUIDBEES", "SETFGOLD", "SETF10GILT", "PHARMABEES", "ITBEES",
              "CPSEETF", "INFRABEES", "CONSUMBEES", "MID150BEES", "PSUBNKBEES",
              "SHARIABEES", "HNGSNGBEES", "MON100", "ICICIB22", "GSEC10IETF",
              "LTGILTBEES", "MOM100", "NV20BEES", "AUTOBEES", "MASPTOP50",
              "QNIFTY", "SETFNIF50", "MOM50", "MIDCAPETF", "SMALLCAP")


def _closes() -> dict:
    """-> {symbol: {isodate: close}} for every EQ bar in every snapshot.

    Reads the bhavcopies directly rather than through `features.load_corpus`,
    which applies the very denylist this file exists to compute.
    """
    out = {}
    for p in sorted(RAW.glob("*/bhavcopy_delivery.csv")):
        day = p.parent.name
        for r in csv.DictReader(io.StringIO(p.read_text(errors="replace")),
                                skipinitialspace=True):
            if (r.get("SERIES") or "").strip() not in universe.TRADEABLE_SERIES:
                continue
            sym = (r.get("SYMBOL") or "").strip()
            try:
                c = float((r.get("CLOSE_PRICE") or "").strip())
            except ValueError:
                continue
            if sym and c > 0:
                out.setdefault(sym, {})[day] = c
    return out


def _returns(series: dict) -> dict:
    days = sorted(series)
    return {days[i]: series[days[i]] / series[days[i - 1]] - 1.0
            for i in range(1, len(days))}


def _corr(a: dict, b: dict):
    """Pearson on the days both series traded, or None if they barely overlap.

    Fewer than MIN_OVERLAP shared sessions is not a weak answer, it is no
    answer -- and returning a number there would let a two-week instrument
    score 0.99 by luck.
    """
    keys = [k for k in a if k in b]
    n = len(keys)
    if n < MIN_OVERLAP:
        return None
    xs = [a[k] for k in keys]
    ys = [b[k] for k in keys]
    mx, my = sum(xs) / n, sum(ys) / n
    sxy = sxx = syy = 0.0
    for x, y in zip(xs, ys):
        dx, dy = x - mx, y - my
        sxy += dx * dy
        sxx += dx * dx
        syy += dy * dy
    if sxx <= 0 or syy <= 0:
        return None
    return sxy / math.sqrt(sxx * syy)


def tracking(closes: dict) -> dict:
    """-> {symbol: (best correlation, which reference)} against REFERENCES."""
    refs = {r: _returns(closes[r]) for r in REFERENCES
            if len(closes.get(r, ())) > 200}
    out = {}
    for sym, series in closes.items():
        if len(series) < MIN_OVERLAP + 1:
            continue
        rets = _returns(series)
        best, who = None, None
        for name, ref in refs.items():
            if name == sym:
                continue
            c = _corr(rets, ref)
            if c is not None and (best is None or c > best):
                best, who = c, name
        if who is not None:
            out[sym] = (round(best, 4), who)
    return out


def stillness(closes: dict) -> dict:
    """-> {symbol: (daily return sd in %, sessions)} for anything long enough."""
    out = {}
    for sym, series in closes.items():
        days = sorted(series)
        if len(days) < MIN_STILL_SESSIONS:
            continue
        r = [series[days[i]] / series[days[i - 1]] - 1.0
             for i in range(1, len(days))]
        out[sym] = (statistics.pstdev(r) * 100, len(days))
    return out


def tokens_in(sym: str) -> list:
    return [t for t in TOKENS if t in sym]


def classify(sym: str, track, still=None):
    """-> (tier, reason) or (None, why not).

    `track` is (corr, ref) or None; `still` is (daily sd %, sessions) or None.
    """
    if SUFFIX.search(sym):
        return "A", "symbol ends in ETF/BEES -- no company in the master does"
    hits = tokens_in(sym)
    if not hits:
        return None, "no instrument word in the name"
    name = "+".join(hits)
    if track is not None and track[0] >= TRACK_MIN:
        return "B", f"{name} in the name and tracks {track[1]} {track[0]:+.2f}"
    if still is not None and still[1] >= MIN_STILL_SESSIONS and still[0] < MAX_STILLNESS:
        return "C", (f"{name} in the name and moves {still[0]:.3f}%/day over "
                     f"{still[1]} sessions -- no share is that still")
    if track is None:
        return None, f"{name} in the name, but too few sessions to correlate"
    return None, f"{name} in the name, but tracks {track[1]} only {track[0]:+.2f}"


# Hand-reviewed, and every one carries its own tracking evidence above
# TRACK_MIN. These are AMC-plus-index constructions whose instrument word is
# not in TOKENS and could not be added to it: ICICIAUTO would need "AUTO",
# which takes BAJAJ-AUTO. Reviewed one at a time BECAUSE the mechanical rule
# could not be widened safely, and listed here so the judgement is visible
# rather than buried in a regex. `--validate` re-checks each against the same
# 0.60 bar the mechanical tier B uses.
REVIEWED = ("AXISVALUE", "ICICI500", "ICICIALPLV", "ICICIAUTO", "ICICICONSU",
            "ICICIFIN", "ICICIINFRA", "ICICIM150", "ICICIMCAP", "ICICINF100",
            "ICICIPHARM", "ICICIQTY30", "ICICISENSX", "ICICISILVE", "KOTAKIT",
            "KOTAKMID50", "M50", "M100")


def labelled():
    """-> (funds, companies): the two sets NSE itself labels, today.

    funds     in today's EQ bhavcopy, absent from today's company master
    companies in any company master this repo holds
    """
    companies, funds = set(), set()
    for m in sorted(RAW.glob("*/equity_master.csv")):
        rows = csv.DictReader(io.StringIO(m.read_text(errors="replace")))
        companies |= {r["SYMBOL"].strip() for r in rows if r.get("SYMBOL")}
    newest = universe.master_snapshot()
    if newest is not None:
        rows = csv.DictReader(
            io.StringIO((newest / "bhavcopy_delivery.csv").read_text(errors="replace")),
            skipinitialspace=True)
        traded = {r["SYMBOL"].strip() for r in rows
                  if r.get("SYMBOL")
                  and (r.get("SERIES") or "").strip() in universe.TRADEABLE_SERIES}
        funds = traded - companies
    return funds, companies


def derive():
    """-> (entries, residue, labelled counts). The whole job, one pass."""
    closes = _closes()
    track = tracking(closes)
    still = stillness(closes)
    funds, companies = labelled()

    # Candidates: ever traded EQ, and no master this repo holds calls them a
    # company. That is DELISTED-OR-FUND -- the classification below is what
    # separates the two, and getting it wrong in the company direction is the
    # survivorship bias universe.py exists to avoid.
    candidates = sorted(s for s in closes if s not in companies)

    entries, residue = {}, {}
    for sym in candidates:
        tier, why = classify(sym, track.get(sym), still.get(sym))
        c, ref = track.get(sym, (None, None))
        if tier is None and sym in REVIEWED and c is not None and c >= TRACK_MIN:
            tier, why = "reviewed", f"AMC index fund, tracks {ref} {c:+.2f}"
        (entries if tier else residue)[sym] = {
            "tier": tier, "why": why, "track": c, "tracks": ref,
            "sd": round(still[sym][0], 4) if sym in still else None,
            "sessions": len(closes[sym])}
    return entries, residue, (funds, companies, track, still)


def _validate(entries, residue, lab):
    """The claim in the docstring, re-derived. Prints, and returns the FP count."""
    funds, companies, track, still = lab
    scored_f = [s for s in funds if s in track]
    scored_c = [s for s in companies if s in track]
    name_fp = sorted(s for s in companies if tokens_in(s))
    conj_fp = sorted(s for s in scored_c
                     if tokens_in(s) and track[s][0] >= TRACK_MIN)
    # Not just "none caught" -- none NEARLY caught. See MARGIN_MIN.
    near = sorted(((track[s][0], s) for s in scored_c
                   if tokens_in(s) and track[s][0] >= TRACK_MIN - MARGIN_MIN),
                  reverse=True)
    suf_fp = sorted(s for s in companies if SUFFIX.search(s))
    caught = sum(1 for s in funds if classify(s, track.get(s), still.get(s))[0])
    print(f"labelled: {len(funds)} funds, {len(companies)} companies "
          f"({len(scored_f)}/{len(scored_c)} have enough history to correlate)")
    print(f"  name alone      -> {len(name_fp)} companies wrongly called funds: "
          f"{name_fp}")
    print(f"  tier A suffix   -> {len(suf_fp)} companies")
    print(f"  tier B at {TRACK_MIN:.2f}  -> {len(conj_fp)} companies {conj_fp}")
    closest = max((track[s][0] for s in scored_c if tokens_in(s)), default=0.0)
    print(f"  closest company to the bar: {closest:+.3f} "
          f"(margin {TRACK_MIN - closest:.3f}, floor {MARGIN_MIN:.2f})"
          + (f"  TOO CLOSE: {near}" if near else ""))
    print(f"  recall on labelled funds: {caught}/{len(funds)} "
          f"= {caught / max(len(funds), 1) * 100:.0f}%")
    # Tier C's own margin: the quietest real company, against the bar.
    co_sd = sorted((v[0], s) for s, v in still.items()
                   if s in companies and v[1] >= MIN_STILL_SESSIONS)
    quietest, quiet_sym = co_sd[0] if co_sd else (float("inf"), "-")
    still_fp = [s for sd, s in co_sd if sd < MAX_STILLNESS and tokens_in(s)]
    print(f"  tier C at {MAX_STILLNESS:.2f}%  -> {len(still_fp)} companies "
          f"{still_fp}; quietest company is {quiet_sym} at {quietest:.3f}%/day "
          f"({quietest / MAX_STILLNESS:.1f}x the bar)")
    missing = [s for s in REVIEWED if s not in entries]
    print(f"  reviewed entries that still clear {TRACK_MIN:.2f}: "
          f"{len(REVIEWED) - len(missing)}/{len(REVIEWED)}"
          + (f"  LOST: {missing}" if missing else ""))
    print(f"\nderived {len(entries)} historical non-equity symbols, "
          f"{len(residue)} candidates deliberately left in the universe")
    return len(suf_fp) + len(conj_fp) + len(missing) + len(near) + len(still_fp)


def _report(entries, residue):
    by_tier = {}
    for s, e in sorted(entries.items()):
        by_tier.setdefault(e["tier"], []).append(s)
    for t, syms in sorted(by_tier.items()):
        print(f"\ntier {t} ({len(syms)}):")
        for i in range(0, len(syms), 6):
            print("   " + " ".join(f"{x:<13}" for x in syms[i:i + 6]))
    # Two filters, and the second is the one that matters. A residue symbol
    # that TODAY'S snapshot already denies is not in the universe whatever
    # this file decides; only the ones no snapshot can speak for are a real
    # residual risk, and features.load_corpus drops anything under 200 bars
    # before clustering.
    covered = universe._seen_non_equity()
    live = {s: e for s, e in residue.items()
            if e["sessions"] >= 200 and tokens_in(s)}
    risk = {s: e for s, e in live.items() if s not in covered}
    print(f"\nLEFT IN and able to reach the corpus: {len(live)} of "
          f"{len(residue)} candidates. {len(live) - len(risk)} of those are "
          f"denied anyway by a snapshot that still lists them.")
    print(f"\nTHE ACTUAL RESIDUE ({len(risk)}) -- no snapshot can speak for "
          f"these and the evidence does not carry them, so they stay in the "
          f"universe. A wrongly deleted company is the worse error:")
    for s, e in sorted(risk.items(), key=lambda kv: -(kv[1]["track"] or -9)):
        sd = f"{e['sd']:.3f}%/day" if e.get("sd") is not None else "sd n/a"
        print(f"   {s:<13} {e['sessions']:>5} sessions  {sd:>11}  {e['why']}")


def main(argv):
    entries, residue, lab = derive()
    if "--report" in argv:
        _report(entries, residue)
        return 0
    bad = _validate(entries, residue, lab)
    if "--write" in argv:
        if bad:
            print(f"\nREFUSING to write: {bad} labelled failures", file=sys.stderr)
            return 1
        # Only what the snapshots CANNOT say. A fund that still trades is
        # already derived from today's master every run, and writing it here
        # too would churn the file on every listing and blur what it is for:
        # a record of a judgement about instruments nobody can look up any more.
        #
        # _seen_non_equity(), NOT non_equity_symbols(): the latter already
        # includes THIS FILE, so filtering against it drops everything the
        # last run wrote and the artifact collapses to whatever is new since.
        # It did exactly that once -- 93 symbols rewritten as 7, and the next
        # run wrote 93 again. A --write that does not converge is worse than
        # no --write, because the file it leaves depends on how often it ran.
        covered = universe._seen_non_equity()
        entries = {s: e for s, e in entries.items() if s not in covered}
        OUT.write_text(json.dumps(
            {"note": ("Historical non-equity symbols -- funds that stopped "
                      "trading before any snapshot held a company master, so "
                      "universe.non_equity_symbols() cannot see them. Derived "
                      "by src/ops/classify_non_equity.py; every entry carries "
                      "the evidence that put it here."),
             "threshold": TRACK_MIN,
             "derived_by": "python3 src/ops/classify_non_equity.py --write",
             "symbols": {s: entries[s] for s in sorted(entries)}},
            indent=1, sort_keys=False) + "\n")
        print(f"wrote {OUT.relative_to(paths.ROOT)}: {len(entries)} symbols")
    return 0


def _selftest():
    # The rule's mechanics, on a fixture that names the exact traps. Correctness
    # against the real 2,568 companies is `--validate`; it reads 1,699
    # bhavcopies and cannot live in a sweep that has to stay quick.
    assert SUFFIX.search("GOLDBEES") and SUFFIX.search("NETFAUTO") is None
    assert not SUFFIX.search("JETFREIGHT"), "contains ETF but does not end in it"
    assert not SUFFIX.search("BEESLTD")
    assert classify("SILVERBEES", None)[0] == "A", "suffix needs no tracking"
    assert classify("DECNGOLD", (0.408, "MOM100"))[0] is None, \
        "a gold MINER does not track gold"
    assert classify("AXISGOLD", (0.804, "SILVERBEES"))[0] == "B"
    assert classify("AXISGOLD", (0.59, "SILVERBEES"))[0] is None, \
        f"below {TRACK_MIN} the name alone must not be enough"
    assert classify("AXISGOLD", None)[0] is None, "no overlap is not a low score"
    # tier C: too still to be a share, but only WITH a name token and only on
    # enough sessions. A suspended stock is also still.
    assert classify("EBBETF0425", (0.10, "GSEC10IETF"), (0.138, 900))[0] == "C"
    assert classify("EBBETF0425", (0.10, "GSEC10IETF"), (0.138, 30))[0] is None, \
        "30 sessions of sd is a small-sample artefact, not evidence"
    assert classify("SOMECO", (0.10, "NIFTYBEES"), (0.001, 900))[0] is None, \
        "stillness alone must not classify -- a suspended stock is still"
    assert classify("PGHH", (0.5, "NIFTYBEES"), (1.372, 1500))[0] is None
    assert classify("GOLDIAM", (0.511, "SMALLCAP"), (2.8, 1500))[0] is None, \
        "a jeweller is neither tracking gold nor still"
    assert classify("RELIANCE", (0.99, "NIFTYBEES"))[0] is None, \
        "tracking alone must not classify -- INFY and TCS track ITBEES"
    for bad in ("AUTO", "PHARM", "TECH", "INFRA", "FIN", "MCAP", "METAL",
                "ENERGY", "BANKN", "BANKP"):
        assert bad not in TOKENS, f"{bad} was measured to take a real company"
    for thin in ("DIV", "CONSU", "VALUE"):
        assert thin not in TOKENS, \
            f"{thin} took no company but left one inside {MARGIN_MIN} of the bar"
    # Two different protections, and they must not be confused. A company with
    # no instrument word in its name is safe whatever it tracks -- that is what
    # keeps INFY, TCS, MINDTREE and LTI out at 0.85-0.91, and HDFC and IDFC out
    # at ~0.70. Each of these was measured to be taken by a token that was
    # therefore rejected, so the guarantee is the ABSENCE of that token.
    for co in ("BAJAJ-AUTO", "SUNPHARMA", "TECHM", "HCLTECH", "MANINFRA",
               "JIOFIN", "LICHSGFIN", "DAMCAPITAL", "TATAMETALI", "SWANENERGY",
               "ICICIBANKN", "ICICIBANKP", "HDFC", "IDFC", "MINDTREE", "LTI",
               "INFY", "TCS", "GMRINFRA", "IBULHSGFIN"):
        assert not tokens_in(co) and not SUFFIX.search(co), \
            f"{co} is a real company; a token that reaches it must be dropped"
        assert classify(co, (0.99, "NIFTYBEES"))[0] is None, co
    # A company that DOES carry an instrument word is protected only by the
    # 0.60 bar, so assert it at the score actually measured. The margin is the
    # finding: the widest is GOLDIAM at 0.51, and a jeweller is the closest
    # anything named for a metal gets to moving like one.
    for co, seen in (("GOLDENTOBC", 0.183), ("SILVERTUC", 0.174),
                     ("JETFREIGHT", 0.206),   # "ETF" inside "freight"
                     ("GOLDTECH", 0.319), ("ALPHAGEO", 0.373),
                     ("SKYGOLD", 0.395), ("DECNGOLD", 0.408),
                     ("BALPHARMA", 0.412), ("SHANTIGOLD", 0.447),
                     ("SSDL", 0.494), ("PNBGILTS", 0.503), ("GOLDIAM", 0.511)):
        assert tokens_in(co), f"{co} should be reaching the tracking gate"
        assert classify(co, (seen, "SMALLCAP"))[0] is None, \
            f"{co} tracks {seen:+.2f} and must stay a company at bar {TRACK_MIN}"
        assert seen < TRACK_MIN, f"{co} has no margin left under {TRACK_MIN}"
    # The shipped artifact must agree with the rule that is supposed to have
    # produced it -- a hand-edited entry is exactly how a curated list rots.
    if OUT.exists():
        doc = json.loads(OUT.read_text())
        assert doc["threshold"] == TRACK_MIN, "artifact was built at another bar"
        for sym, e in doc["symbols"].items():
            assert e["tier"] in ("A", "B", "C", "reviewed"), (sym, e)
            if e["tier"] == "A":
                assert SUFFIX.search(sym), sym
            elif e["tier"] == "C":
                assert e["sd"] is not None and e["sd"] < MAX_STILLNESS, (sym, e)
            else:
                assert e["track"] is not None and e["track"] >= TRACK_MIN, (sym, e)
            if e["tier"] == "reviewed":
                assert sym in REVIEWED, f"{sym} is reviewed but not in REVIEWED"
    print("classify_non_equity selftest ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    else:
        sys.exit(main(sys.argv))
