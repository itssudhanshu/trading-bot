#!/usr/bin/env python3
"""The gate behind the reviewer verdict. Parses it, refuses it, records it.

`reviewer-4-verdict.md` told the model its output block "is parsed, appended to
the review ledger, and carried forward". Nothing parsed it. The four reviewer
prompts shipped as text with their rules inside them, which is precisely what
this repo's own payload README forbids:

    The prompts hold the judgement. The invariants are NOT in them -- they are
    in src/ops/pipeline.py ... An agent can be argued out of a standard; that is
    what produced a weight table where two of five variants "beat" the live
    bucket at t < 0.5.

Every rule in those four prompts -- cite the channel, never count the prior,
quote a trial count, do not argue the dials, low coverage means low confidence
-- was a sentence a model could be talked out of. This module is the half that
cannot be argued with, and it is deliberately the same shape as `pipeline.py`:
a list of refusal reasons, and a caller that raises rather than defaults.

WHAT IT REFUSES, AND WHY EACH ONE IS MECHANICAL
-----------------------------------------------
None of these judges whether a verdict is RIGHT. That cannot be checked and
pretending otherwise is how a gate becomes a rubber stamp. They check that the
verdict arrived in a shape a later measurement can score.

  malformed            a missing or unreadable field makes the whole emission
                       REVIEW -- never a default grade. Taken from the source
                       framework's `rating.py`, where an unparseable decision
                       yields REVIEW "so a parsing failure is visible instead of
                       masquerading as a tradeable neutral Hold". It matters more
                       here: a verdict silently dropped or silently defaulted
                       biases the very measurement the layer exists to feed.
  REVIEW as a choice   REVIEW is what a broken emission BECOMES. A reviewer may
                       not select it to avoid committing.
  coverage mismatch    the claimed COVERAGE must equal the dossier's actual
                       independent coverage. This is the one claim in the block
                       that can be checked against the evidence it was made on,
                       so it is.
  confident on nothing at 0/4 independent channels every evidence channel read no
                       data, so the verdict rests entirely on the prior -- the
                       score that put the name on the list. Confidence above 0.5
                       there claims information nobody has.
  a return with no n   the repo rule, reused from `pipeline.PERF_KEYS` rather
                       than restated, so there is one list.
  a rule proposal      reused from `pipeline.BANNED_RULE_SHAPES`. A bear arguing
                       the stop is too wide is proposing a dial, which its own
                       prompt forbids and which an agent can be argued past.

WHAT IT CANNOT DO
-----------------
Change what is bought. `daily.py` queues and `positions.step` fills; nothing in
the selection or execution path reads this module, and `_selftest` asserts it
the way `newswatch.py` and `dossier.py` assert theirs. `stand-aside` still
stands nothing aside. The gate makes the record trustworthy; it does not
promote the record to a decision.

    python3 src/ops/review.py --selftest
"""
import argparse
import hashlib
import json
import os
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # -> src/
import paths

VERSION = 1

# REVIEW_DIR redirects all three files, for a REHEARSAL -- proving the runner
# with a synthetic candidate that must never reach the live ledger. Same shape
# as pipeline.PIPELINE_DIR, and added for the same reason it was: driving this
# CLI end to end the first time wrote a demo verdict and five case files into
# the live strategy directory. It is LOUD (main() prints a banner) and off by
# default, because a quiet way to write somewhere else is how fixture rows got
# into the live order book.
_DIR = Path(os.environ["REVIEW_DIR"]) if os.environ.get("REVIEW_DIR") else paths.SDATA
REHEARSAL = _DIR != paths.SDATA
LEDGER = _DIR / "reviews.jsonl"             # strategy-scoped and append-only
STATE = _DIR / "review_state.json"          # one open session; overwritten
CASES = _DIR / "reviews"                    # the argument, kept as an audit trail

# The order is the design. Round 1 is blind on BOTH sides, and the blindness is
# enforced by `context_for` -- the only thing that hands a reviewer its context
# -- rather than by a sentence in a prompt. A rule a model is asked to follow is
# a rule it can be argued out of; a case it is never shown is not.
STAGES = (("bull", 1), ("bear", 1), ("bull", 2), ("bear", 2),
          ("risk", None), ("verdict", None))
ROLES = ("bull", "bear")
QUOTE_RUN = 40      # chars of the opponent verbatim that prove round 1 was not blind
GRADES = ("proceed", "proceed-with-note", "stand-aside")
REVIEW = "REVIEW"                            # what a broken emission becomes
MAX_INDEPENDENT = 4        # the dossier's five channels less the prior one
BLIND_CONFIDENCE_CAP = 0.5

# HORIZONTAL whitespace only around the colon. With `\s*` there, a field left
# blank swallowed the newline and captured the NEXT line as its value: an empty
# FLIP silently recorded the NOTE text as the flip and made NOTE vanish, with no
# error anywhere. A blank field has to read as blank.
_FIELD = re.compile(
    r"^[ \t]*(VERDICT|CONFIDENCE|COVERAGE|BASIS|FLIP|NOTE)[ \t]*:[ \t]*(.*?)[ \t]*$",
    re.MULTILINE)
_COVERAGE = re.compile(r"^(\d+)\s*/\s*(\d+)")
_HAS_N = re.compile(r"\bn\s*=\s*\d+|\b\d+\s+trades?\b|\bover\s+\d[\d,]*\s+trades?\b",
                    re.IGNORECASE)
_HAS_NUMBER = re.compile(r"[-+]?\d+(?:\.\d+)?\s*%|[-+]?\d+(?:\.\d+)?")


def parse(text):
    """-> (fields, problems). Never raises, never guesses a missing field."""
    fields, problems = {}, []
    for key, val in _FIELD.findall(text or ""):
        if key in fields:
            problems.append(f"{key} given more than once")
        fields[key] = val
    for required in ("VERDICT", "CONFIDENCE", "COVERAGE", "BASIS", "FLIP"):
        if required not in fields:
            problems.append(f"{required} is missing")

    v = (fields.get("VERDICT") or "").strip().lower()
    if v == REVIEW.lower():
        problems.append("REVIEW is what a broken emission becomes, not a grade a "
                        "reviewer may choose")
    elif "VERDICT" in fields and v not in GRADES:
        problems.append(f"VERDICT {fields['VERDICT']!r} is not one of {GRADES}")

    raw_c = (fields.get("CONFIDENCE") or "").strip()
    conf = None
    if "CONFIDENCE" in fields:
        try:
            conf = float(raw_c)
        except ValueError:
            problems.append(f"CONFIDENCE {raw_c!r} is not a number")
        else:
            if not 0.0 <= conf <= 1.0:
                problems.append(f"CONFIDENCE {conf} is outside 0.00-1.00")

    cov = None
    if "COVERAGE" in fields:
        m = _COVERAGE.match((fields["COVERAGE"] or "").strip())
        if not m:
            problems.append(f"COVERAGE {fields['COVERAGE']!r} is not 'n/{MAX_INDEPENDENT}'")
        else:
            cov, denom = int(m.group(1)), int(m.group(2))
            if denom != MAX_INDEPENDENT:
                problems.append(f"COVERAGE denominator {denom}, expected "
                                f"{MAX_INDEPENDENT} independent channels")
            if cov > denom:
                problems.append(f"COVERAGE {cov}/{denom} claims more channels than exist")

    for field in ("BASIS", "FLIP"):
        if field in fields and not fields[field].strip():
            problems.append(f"{field} is empty -- "
                            + ("a verdict with no stated basis cannot be scored"
                               if field == "BASIS" else
                               "a verdict nothing could change is not a judgement"))

    out = {"verdict": v if v in GRADES else REVIEW,
           "confidence": conf, "coverage": cov,
           "basis": fields.get("BASIS", ""), "flip": fields.get("FLIP", ""),
           "note": fields.get("NOTE", "")}
    return out, problems


def check(parsed, dossier=None):
    """-> problems beyond the parse: the claims that can be checked against evidence."""
    # pipeline holds the operator's banned shapes and the perf-key list. Imported
    # lazily so `import review` stays cheap for the sweep, and reused rather than
    # restated so there is exactly one list to keep current.
    import pipeline

    out = []
    cov, conf = parsed.get("coverage"), parsed.get("confidence")

    if dossier is not None and cov is not None:
        actual = len(dossier.independent)
        if cov != actual:
            out.append(f"COVERAGE claims {cov}/{MAX_INDEPENDENT} but the dossier "
                       f"has {actual} independent channel(s) with data: "
                       f"{dossier.independent or 'none'}")

    if cov == 0 and conf is not None and conf > BLIND_CONFIDENCE_CAP:
        out.append(f"CONFIDENCE {conf} at 0/{MAX_INDEPENDENT} coverage -- every "
                   "independent channel read no data, so the verdict rests "
                   "entirely on the score that put the name on the list")

    text = " ".join(str(parsed.get(k) or "") for k in ("basis", "flip", "note"))
    low = text.lower()
    if any(k in low for k in pipeline.PERF_KEYS) and _HAS_NUMBER.search(text) \
            and not _HAS_N.search(text):
        out.append("a performance figure with no trial count beside it")
    for pattern, reason in pipeline.BANNED_RULE_SHAPES:
        if re.search(pattern, low, re.IGNORECASE):
            out.append(f"proposes {reason}")
    return out


def fingerprint(dossier):
    """-> a short digest of the evidence a verdict was made on.

    Without it the ledger cannot tell a verdict made on four covered channels
    from one made on none, and H12's calibration column would be unreadable.
    """
    return hashlib.sha256(dossier.render().encode("utf-8")).hexdigest()[:16]


def accept(text, dossier=None, symbol=None, as_of=None):
    """-> (record, problems). A record is returned ONLY when problems is empty."""
    parsed, problems = parse(text)
    problems = problems + (check(parsed, dossier) if not problems else [])
    if problems:
        return None, problems
    rec = {"version": VERSION,
           "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "symbol": (symbol or getattr(dossier, "symbol", None)),
           "as_of": (as_of or getattr(dossier, "as_of", None))}
    rec.update(parsed)
    if dossier is not None:
        rec["dossier"] = fingerprint(dossier)
        rec["channels_covered"] = sorted(dossier.covered)
    return rec, []


def record(rec, ledger=None):
    """Append one accepted verdict. Append-only: a mixed ledger cannot be un-mixed."""
    path = Path(ledger) if ledger else LEDGER
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, sort_keys=True) + "\n")
    return path


# --------------------------------------------------------------------------
# the session: what runs the four reviewers, in order, over a day's candidates
# --------------------------------------------------------------------------

def _as_date(day):
    """-> `day` as a `date`. Normalised once, at the boundary.

    `--open` handed the raw CLI string to `features.load_corpus(end=...)`, which
    compares it against a list of `date` -- a TypeError on the operator's first
    real run. The same string reached `dossier.build`, where it emptied the
    tradeable universe silently. One conversion, at the edge, is the fix for
    both.
    """
    if isinstance(day, date):
        return day
    return date.fromisoformat(str(day)[:10])


def build_candidates(day, symbols, loader=None, builder=None):
    """-> {symbol: dossier}, the corpus loaded once for all of them.

    `loader` and `builder` are injectable so this path has a test at all. The
    version inside `main()` had none -- it needed a corpus, so nothing exercised
    it, and the date-type defect above shipped. Same pattern as
    `benchmark_probe.probe(universe=...)`, for the same reason.
    """
    day = _as_date(day)
    if loader is None or builder is None:
        import dossier as _dos
        import features
        loader = loader or (lambda end: features.load_corpus(end=end))
        builder = builder or _dos.build
    corpus = loader(end=day)
    return {s: builder(s, day, corpus=corpus) for s in symbols}


def _key(role, rnd):
    return role if rnd is None else f"{role}-{rnd}"


def open_session(day, candidates):
    """-> a fresh session. `candidates` is {symbol: dossier}.

    One session per as-of date. A candidate is registered WITH the fingerprint
    of the dossier it will be reviewed against, so a case written against one
    evidence set cannot later be paired with another.
    """
    iso = day.isoformat() if hasattr(day, "isoformat") else str(day)
    cands = {}
    for s, d in (candidates or {}).items():
        # The evidence is COPIED into the session, not re-derived later. A
        # dossier rebuilt at verdict time is not guaranteed to match the one the
        # reviewers read -- the news channel admits items captured the same day,
        # so a capture between open and verdict changes the render and the
        # fingerprint check would then refuse every verdict it was built to
        # protect. Copying also means the verdict stage needs no corpus.
        cands[s] = {"dossier": fingerprint(d), "render": d.render(),
                    "independent": sorted(d.independent),
                    "covered": sorted(d.covered),
                    "cases": {}, "verdict": None}
    return {"version": VERSION, "session": iso,
            "opened_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "candidates": cands}


def context_for(state, symbol, role, rnd):
    """-> exactly what this reviewer may see, and nothing else.

    THIS is the blind round. At `rnd == 1` the opponent's case is not in the
    returned dict at all -- not redacted, not marked, absent -- so a runner that
    goes through this function cannot leak it even by accident. At `rnd == 2`
    the opponent's round-1 case is included in full, which is the rebuttal.

    The framework this was adapted from runs Bull -> Bear -> judge at its shipped
    default, so its bear sees the bull's argument and its bull never rebuts. The
    asymmetry is mitigated there by instructing the judge to ignore speaking
    order. Here there is no order to ignore.
    """
    cand = (state.get("candidates") or {}).get(symbol)
    if cand is None:
        raise KeyError(f"{symbol} is not in this session")
    if role not in ROLES:
        raise ValueError(f"{role} has no rounds; use the risk or verdict stage")
    ctx = {"symbol": symbol, "as_of": state["session"], "role": role,
           "round": rnd, "dossier_fingerprint": cand["dossier"],
           "dossier": cand.get("render", "")}
    if rnd == 1:
        ctx["opponent"] = None
        ctx["note"] = ("round 1 is blind -- the opposing case is withheld by the "
                       "session, not by instruction")
    else:
        other = ROLES[1 - ROLES.index(role)]
        ctx["opponent"] = cand["cases"].get(_key(other, 1), {}).get("text")
        ctx["note"] = f"round 2 -- the {other}'s round-1 case in full"
    return ctx


def _stage_problems(cand, role, rnd):
    """-> why this stage may not be submitted yet, in the STAGES order."""
    key = _key(role, rnd)
    if key in cand["cases"] or (role == "verdict" and cand["verdict"]):
        return [f"{key} was already submitted; a case is not revised in place"]
    want = STAGES.index((role, rnd))
    missing = [_key(r, n) for r, n in STAGES[:want]
               if _key(r, n) not in cand["cases"]]
    if missing:
        return [f"{key} cannot run before {', '.join(missing)} -- "
                "the order is the design, not a convention"]
    return []


def submit(state, symbol, role, rnd, text):
    """-> problems. Empty means the case was accepted and stored on the session."""
    cand = (state.get("candidates") or {}).get(symbol)
    if cand is None:
        return [f"{symbol} is not in this session"]
    if (role, rnd) not in STAGES:
        return [f"{_key(role, rnd)} is not a stage; expected one of "
                f"{[_key(r, n) for r, n in STAGES]}"]
    problems = _stage_problems(cand, role, rnd)
    if problems:
        return problems
    text = (text or "").strip()
    if not text:
        return [f"{_key(role, rnd)} is empty"]

    # Round 1 blindness, checked as well as enforced. `context_for` makes a leak
    # impossible through the session; this catches a runner that went around it.
    # A verbatim run of the opponent is proof, where a mention of "the bear"
    # would flag the word "bearish" and prove nothing.
    if rnd == 1 and role in ROLES:
        other = ROLES[1 - ROLES.index(role)]
        prior = (cand["cases"].get(_key(other, 1)) or {}).get("text") or ""
        for i in range(0, max(0, len(prior) - QUOTE_RUN), QUOTE_RUN // 2):
            if prior[i:i + QUOTE_RUN] in text:
                return [f"{_key(role, rnd)} quotes the {other}'s round-1 case "
                        f"verbatim; round 1 is blind and this one was not"]

    cand["cases"][_key(role, rnd)] = {
        "text": text,
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    return []


class _Evidence:
    """The session's copy of a dossier, in the shape `accept` reads.

    Not the Dossier itself: by verdict time the real one may have moved, and the
    verdict must be gated against what the reviewers actually saw.
    """

    def __init__(self, cand):
        self.independent = list(cand.get("independent") or [])
        self.covered = list(cand.get("covered") or [])
        self._render = cand.get("render", "")
        self._fp = cand["dossier"]

    def render(self):
        return self._render


def submit_verdict(state, symbol, text, dossier=None):
    """-> (record, problems). Runs the same gate as `accept`, plus the order.

    `dossier` is optional and is only used to CHECK: pass one and it must match
    the session's copy. The gate itself always runs against the copy, so a
    verdict is judged on the evidence its cases were written against even if the
    live dossier has since changed.
    """
    cand = (state.get("candidates") or {}).get(symbol)
    if cand is None:
        return None, [f"{symbol} is not in this session"]
    problems = _stage_problems(cand, "verdict", None)
    if problems:
        return None, problems
    if dossier is not None and fingerprint(dossier) != cand["dossier"]:
        return None, ["the dossier does not match the one this session opened "
                      "with; a verdict must be made on the evidence its cases "
                      "were written against"]
    ev = _Evidence(cand)
    rec, problems = accept(text, ev, symbol, state["session"])
    if problems:
        return None, problems
    cand["verdict"] = rec
    return rec, []


def close_session(state, ledger=None, cases_dir=None):
    """-> (rows, paths). Writes the cases, then appends one ledger row each.

    A candidate with no verdict is SKIPPED and named, never written with a
    default. The cases are written first so a row can never point at an audit
    trail that does not exist.
    """
    rows, written, skipped = [], [], []
    base = Path(cases_dir) if cases_dir else CASES
    for symbol, cand in sorted((state.get("candidates") or {}).items()):
        if not cand.get("verdict"):
            skipped.append(symbol)
            continue
        d = base / state["session"] / symbol
        d.mkdir(parents=True, exist_ok=True)
        # The evidence goes in beside the argument. A case file without the
        # dossier it answered is half an audit trail.
        dp = d / "dossier.md"
        dp.write_text(cand.get("render", ""), encoding="utf-8")
        written.append(dp)
        for key, case in sorted(cand["cases"].items()):
            p = d / f"{key}.md"
            p.write_text(case["text"], encoding="utf-8")
            written.append(p)
        rec = dict(cand["verdict"])
        rec["cases"] = str(d.relative_to(paths.ROOT)) if str(d).startswith(
            str(paths.ROOT)) else str(d)
        rec["cases_kept"] = sorted(cand["cases"])
        rows.append(rec)
        record(rec, ledger)
    return rows, {"cases": written, "skipped": skipped}


def save_state(state, path=None):
    """Write the open session. Temp file then replace, so a crash mid-write
    cannot leave a half-parsed session behind."""
    p = Path(path) if path else STATE
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=1, sort_keys=True), encoding="utf-8")
    tmp.replace(p)
    return p


def load_state(path=None):
    p = Path(path) if path else STATE
    if not p.exists():
        return None
    s = json.loads(p.read_text(encoding="utf-8"))
    if s.get("version") != VERSION:
        raise SystemExit(f"review_state.json is version {s.get('version')}, "
                         f"this module is {VERSION}")
    return s


def status(state):
    """-> a line per candidate: what has run and what is next."""
    if not state:
        return "no open review session"
    out = [f"session {state['session']}  "
           f"{len(state.get('candidates') or {})} candidate(s)"]
    for symbol, cand in sorted((state.get("candidates") or {}).items()):
        done = [_key(r, n) for r, n in STAGES
                if _key(r, n) in cand["cases"] or (r == "verdict" and cand["verdict"])]
        nxt = next((_key(r, n) for r, n in STAGES
                    if _key(r, n) not in cand["cases"]
                    and not (r == "verdict" and cand["verdict"])), None)
        out.append(f"  {symbol:<12} {len(done)}/{len(STAGES)} "
                   f"next: {nxt or 'complete'}")
    return "\n".join(out)


# --------------------------------------------------------------------------

GOOD = """VERDICT: proceed-with-note
CONFIDENCE: 0.55
COVERAGE: 1/4
BASIS: the announcement channel carries an order win inside the window; every
other independent channel read no data.
FLIP: a scheduled result date inside the ten-session window.
NOTE: thin coverage, so the confidence is deliberately low.
"""


def _selftest():
    import types

    dos = types.SimpleNamespace(
        symbol="TESTCO", as_of="2026-09-11",
        independent=["sentiment"], covered=["technical", "sentiment"],
        render=lambda: "# TESTCO dossier\nsentiment: covered\n")

    # --- the accept path, asserted FIRST ------------------------------------
    # A gate tested only on what it rejects is a gate nobody has seen open.
    rec, probs = accept(GOOD, dos)
    assert not probs, probs
    assert rec["verdict"] == "proceed-with-note" and rec["confidence"] == 0.55
    assert rec["coverage"] == 1 and rec["version"] == VERSION
    assert rec["symbol"] == "TESTCO" and rec["dossier"] == fingerprint(dos)
    assert rec["channels_covered"] == ["sentiment", "technical"]

    # --- malformed becomes REVIEW, never a grade ----------------------------
    p, probs = parse("VERDICT: proceed\nCONFIDENCE: 0.7\n")
    assert probs and any("COVERAGE is missing" in x for x in probs), probs
    p, probs = parse("VERDICT: buy\nCONFIDENCE: 0.7\nCOVERAGE: 1/4\nBASIS: x\nFLIP: y")
    assert p["verdict"] == REVIEW, p
    assert any("not one of" in x for x in probs), probs
    p, probs = parse("VERDICT: REVIEW\nCONFIDENCE: 0.7\nCOVERAGE: 1/4\nBASIS: x\nFLIP: y")
    assert any("not a grade a reviewer may choose" in x for x in probs), probs

    for bad, expect in (
            ("CONFIDENCE: high", "is not a number"),
            ("CONFIDENCE: 1.4", "outside"),
    ):
        _, probs = parse(GOOD.replace("CONFIDENCE: 0.55", bad))
        assert any(expect in x for x in probs), (bad, probs)

    _, probs = parse(GOOD.replace("COVERAGE: 1/4", "COVERAGE: 3/9"))
    assert any("denominator" in x for x in probs), probs
    _, probs = parse(GOOD.replace("COVERAGE: 1/4", "COVERAGE: 5/4"))
    assert any("more channels than exist" in x for x in probs), probs
    blank_flip = GOOD.replace(
        "FLIP: a scheduled result date inside the ten-session window.", "FLIP:")
    pf, probs = parse(blank_flip)
    assert any("not a judgement" in x for x in probs), probs
    # A blank field must not absorb the line under it. It did: `\s*` after the
    # colon crossed the newline, so FLIP took NOTE's text and NOTE disappeared
    # -- silently, with a well-formed-looking record as the output.
    assert pf["flip"] == "", f"blank FLIP absorbed the next line: {pf['flip']!r}"
    assert "thin coverage" in pf["note"], f"NOTE was swallowed: {pf['note']!r}"

    # --- the claim that can be checked against the evidence -----------------
    _, probs = accept(GOOD.replace("COVERAGE: 1/4", "COVERAGE: 3/4"), dos)
    assert any("but the dossier has 1 independent" in x for x in probs), probs

    # --- confident on nothing -----------------------------------------------
    blind = types.SimpleNamespace(symbol="X", as_of="2026-09-11", independent=[],
                                  covered=["technical"], render=lambda: "x")
    _, probs = accept(GOOD.replace("COVERAGE: 1/4", "COVERAGE: 0/4")
                          .replace("CONFIDENCE: 0.55", "CONFIDENCE: 0.9"), blind)
    assert any("rests entirely on the score" in x for x in probs), probs
    # ...and the same coverage at an honest confidence is accepted.
    rec2, probs = accept(GOOD.replace("COVERAGE: 1/4", "COVERAGE: 0/4")
                             .replace("CONFIDENCE: 0.55", "CONFIDENCE: 0.4"), blind)
    assert not probs and rec2["coverage"] == 0, probs

    # --- the repo's standing rules, reused not restated ---------------------
    _, probs = accept(GOOD.replace("BASIS: the announcement channel",
                                   "BASIS: the book's CAGR is 12.4% so this "
                                   "setup works; the announcement channel"), dos)
    assert any("no trial count" in x for x in probs), probs
    rec3, probs = accept(GOOD.replace("BASIS: the announcement channel",
                                      "BASIS: the book's CAGR is 12.4% over 195 "
                                      "trades; the announcement channel"), dos)
    assert not probs, probs

    for proposal in ("the 10% stop is too wide and should be a participation cap",
                     "add a minimum score of 80 before buying"):
        _, probs = accept(GOOD.replace("NOTE: thin coverage, so the confidence is "
                                       "deliberately low.", f"NOTE: {proposal}"), dos)
        assert any("proposes" in x for x in probs), (proposal, probs)

    # --- record is append-only and round-trips ------------------------------
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        led = Path(td) / "reviews.jsonl"
        record(rec, led)
        record(rec, led)
        lines = led.read_text().strip().splitlines()
        assert len(lines) == 2, "record must append, never overwrite"
        assert json.loads(lines[0])["verdict"] == "proceed-with-note"

    # --- a fingerprint tracks the evidence, not the symbol ------------------
    other = types.SimpleNamespace(symbol="TESTCO", as_of="2026-09-11",
                                  independent=["sentiment"], covered=["sentiment"],
                                  render=lambda: "# TESTCO dossier\nDIFFERENT\n")
    assert fingerprint(dos) != fingerprint(other), \
        "two different dossiers must not share a fingerprint"

    # --- --open converts the CLI string ONCE, at the boundary ---------------
    # The loader is handed whatever --open passes down. It was the raw string,
    # and `features.load_corpus` compares `end` against a list of dates, so the
    # operator's first real run died on a TypeError. Assert the TYPE the loader
    # receives, which is the thing that was wrong.
    seen = {}

    def _loader(end):
        seen["end"] = end
        return {"YUKEN": object()}

    def _builder(sym, day, corpus=None):
        seen["day"] = day
        return dos

    cands = build_candidates("2026-09-11", ["YUKEN"], _loader, _builder)
    assert isinstance(seen["end"], date), \
        f"load_corpus was handed {type(seen['end']).__name__}, not a date"
    assert isinstance(seen["day"], date), \
        f"dossier.build was handed {type(seen['day']).__name__}, not a date"
    assert list(cands) == ["YUKEN"], cands
    assert _as_date(date(2026, 9, 11)) == date(2026, 9, 11)

    # --- the session: the happy path end to end, FIRST ----------------------
    st = open_session("2026-09-11", {"TESTCO": dos})
    assert st["candidates"]["TESTCO"]["dossier"] == fingerprint(dos)
    # The session carries the evidence, so the verdict stage needs no corpus and
    # cannot be refused because the live dossier moved under it.
    assert st["candidates"]["TESTCO"]["render"] == dos.render()
    assert st["candidates"]["TESTCO"]["independent"] == ["sentiment"]

    # round 1 is blind because the context does not CONTAIN the opponent
    c1 = context_for(st, "TESTCO", "bull", 1)
    assert c1["opponent"] is None and "withheld by the session" in c1["note"]
    assert c1["dossier"] == dos.render(), "the reviewer must be handed the evidence"
    assert not submit(st, "TESTCO", "bull", 1, "BULL R1: the order win is real.")
    c1b = context_for(st, "TESTCO", "bear", 1)
    assert c1b["opponent"] is None, "the bear saw the bull in round 1"
    assert not submit(st, "TESTCO", "bear", 1, "BEAR R1: ATR is 3% against a 10% stop.")

    # round 2 hands over the opponent's round-1 case in full
    c2 = context_for(st, "TESTCO", "bull", 2)
    assert c2["opponent"] == "BEAR R1: ATR is 3% against a 10% stop.", c2
    assert not submit(st, "TESTCO", "bull", 2, "BULL R2: the ATR point stands.")
    assert not submit(st, "TESTCO", "bear", 2, "BEAR R2: the order is one quarter.")
    assert not submit(st, "TESTCO", "risk", None, "RISK: 3 of 5 held, no sector clash.")
    rec, probs = submit_verdict(st, "TESTCO", GOOD, dos)
    assert not probs and rec["verdict"] == "proceed-with-note", probs

    import tempfile
    with tempfile.TemporaryDirectory() as td:
        led, cdir = Path(td) / "reviews.jsonl", Path(td) / "cases"
        rows, info = close_session(st, led, cdir)
        assert len(rows) == 1 and not info["skipped"], (rows, info)
        files = sorted(p.name for p in (cdir / "2026-09-11" / "TESTCO").iterdir())
        assert files == ["bear-1.md", "bear-2.md", "bull-1.md", "bull-2.md",
                         "dossier.md", "risk.md"], files
        assert (cdir / "2026-09-11" / "TESTCO" / "dossier.md").read_text() \
            == dos.render(), "a case without its dossier is half an audit trail"
        assert (cdir / "2026-09-11" / "TESTCO" / "bull-1.md").read_text() \
            == "BULL R1: the order win is real."
        assert rows[0]["cases_kept"] == ["bear-1", "bear-2", "bull-1", "bull-2", "risk"]
        assert json.loads(led.read_text().strip())["verdict"] == "proceed-with-note"

    # --- the order is enforced, not suggested -------------------------------
    st2 = open_session("2026-09-11", {"TESTCO": dos})
    probs = submit(st2, "TESTCO", "bull", 2, "rebuttal before anyone spoke")
    assert probs and "cannot run before" in probs[0], probs
    _, probs = submit_verdict(st2, "TESTCO", GOOD, dos)
    assert probs and "cannot run before" in probs[0], probs
    assert not submit(st2, "TESTCO", "bull", 1, "BULL R1: x")
    probs = submit(st2, "TESTCO", "bull", 1, "BULL R1: a second attempt")
    assert probs and "already submitted" in probs[0], probs
    assert submit(st2, "TESTCO", "bull", 1, "")  # empty is refused at any stage
    assert submit(st2, "NOSUCH", "bull", 1, "x")[0].startswith("NOSUCH is not")

    # --- a runner that went around context_for is caught --------------------
    st3 = open_session("2026-09-11", {"TESTCO": dos})
    long_case = "BULL R1: delivery has run at 61% for six weeks, well above its own band."
    assert not submit(st3, "TESTCO", "bull", 1, long_case)
    probs = submit(st3, "TESTCO", "bear", 1,
                   "BEAR R1: answering -- " + long_case[10:70])
    assert probs and "round 1 is blind and this one was not" in probs[0], probs

    # --- a verdict is bound to the evidence its cases were written against --
    st4 = open_session("2026-09-11", {"TESTCO": dos})
    for role, rnd, txt in (("bull", 1, "a"), ("bear", 1, "b"), ("bull", 2, "c"),
                           ("bear", 2, "d"), ("risk", None, "e")):
        assert not submit(st4, "TESTCO", role, rnd, txt)
    _, probs = submit_verdict(st4, "TESTCO", GOOD, other)
    assert probs and "does not match the one this session opened" in probs[0], probs
    # ...and with no dossier passed at all, the gate still runs against the copy.
    rec4, probs = submit_verdict(st4, "TESTCO", GOOD)
    assert not probs and rec4["coverage"] == 1, probs
    # the coverage cross-check uses the session copy, so a wrong claim still fails
    st6 = open_session("2026-09-11", {"TESTCO": dos})
    for role, rnd, txt in (("bull", 1, "a"), ("bear", 1, "b"), ("bull", 2, "c"),
                           ("bear", 2, "d"), ("risk", None, "e")):
        assert not submit(st6, "TESTCO", role, rnd, txt)
    _, probs = submit_verdict(st6, "TESTCO", GOOD.replace("COVERAGE: 1/4",
                                                          "COVERAGE: 4/4"))
    assert any("but the dossier has 1 independent" in x for x in probs), probs

    # --- no verdict means skipped and NAMED, never a default ----------------
    st5 = open_session("2026-09-11", {"TESTCO": dos, "QUIET": dos})
    with tempfile.TemporaryDirectory() as td:
        rows, info = close_session(st5, Path(td) / "l.jsonl", Path(td) / "c")
        assert rows == [] and info["skipped"] == ["QUIET", "TESTCO"], info

    # --- state round-trips, and a version bump is refused -------------------
    with tempfile.TemporaryDirectory() as td:
        p = save_state(st, Path(td) / "review_state.json")
        back = load_state(p)
        assert back["session"] == "2026-09-11"
        assert back["candidates"]["TESTCO"]["cases"]["bull-1"]["text"].startswith("BULL R1")
        p.write_text(json.dumps({"version": VERSION + 1}))
        try:
            load_state(p)
            raise AssertionError("a future state version must not be loaded")
        except SystemExit:
            pass
    assert "next: complete" in status(st) and "no open review" in status(None)

    # --- the rehearsal redirect moves ALL THREE, not two --------------------
    # positions.py redirected STATE and LEDGER but not DB, and its selftest
    # wrote two fixture positions into the live order book. Asserted here so
    # the same shape of miss is visible rather than discovered later.
    if REHEARSAL:
        for p in (LEDGER, STATE, CASES):
            assert str(p).startswith(str(_DIR)), f"{p} escaped REVIEW_DIR"
    else:
        assert LEDGER.parent == STATE.parent == CASES.parent == paths.SDATA

    # --- THE guarantee: this cannot reach an order --------------------------
    pat = re.compile(r"^\s*(?:import\s+review|from\s+review\s+import)", re.MULTILINE)
    offenders = []
    for d in ("src/strategies", "src/research", "src/bucket", "src/core"):
        for p in sorted((paths.ROOT / d).rglob("*.py")):
            if pat.search(p.read_text(encoding="utf-8", errors="replace")):
                offenders.append(str(p.relative_to(paths.ROOT)))
    assert not offenders, \
        f"the selection/execution path imports the reviewer verdict: {offenders}"

    print("review selftest ok (accept path first; REVIEW never defaults; "
          "no order path imports it)")


def _refuse(problems):
    print("REFUSED:\n  " + "\n  ".join(problems))
    raise SystemExit(1)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--status", action="store_true", help="where each candidate stands")
    ap.add_argument("--open", dest="open_day", metavar="YYYY-MM-DD",
                    help="open a session; needs --symbols")
    ap.add_argument("--symbols", help="comma-separated, with --open")
    ap.add_argument("--context", nargs=2, metavar=("SYMBOL", "STAGE"),
                    help="what a reviewer may see, e.g. TESTCO bull-1")
    ap.add_argument("--submit", nargs=2, metavar=("SYMBOL", "STAGE"),
                    help="submit a case; the text comes from --file")
    ap.add_argument("--verdict", metavar="SYMBOL", help="submit the verdict from --file")
    ap.add_argument("--close", action="store_true", help="write cases and ledger rows")
    ap.add_argument("--file", help="a file holding the case or verdict text")
    a = ap.parse_args()
    if a.selftest:
        return _selftest()
    if REHEARSAL:
        print(f"*** REHEARSAL -- reading and writing {_DIR}, not the live "
              f"strategy directory ***")

    if a.open_day:
        if not a.symbols:
            ap.error("--open needs --symbols")
        syms = [x.strip().upper() for x in a.symbols.split(",") if x.strip()]
        cands = build_candidates(a.open_day, syms)
        st = open_session(a.open_day, cands)
        save_state(st)
        print(status(st))
        return

    st = load_state()
    if a.status or not any((a.context, a.submit, a.verdict, a.close)):
        print(status(st))
        return
    if st is None:
        raise SystemExit("no open review session -- --open one first")

    if a.context:
        symbol, stage = a.context[0].upper(), a.context[1]
        role, _, rnd = stage.partition("-")
        print(json.dumps(context_for(st, symbol, role, int(rnd) if rnd else None),
                         indent=1, sort_keys=True))
        return

    if a.submit or a.verdict:
        if not a.file:
            ap.error("--submit and --verdict read the text from --file")
        text = Path(a.file).read_text(encoding="utf-8")

    if a.submit:
        symbol, stage = a.submit[0].upper(), a.submit[1]
        role, _, rnd = stage.partition("-")
        problems = submit(st, symbol, role, int(rnd) if rnd else None, text)
        if problems:
            _refuse(problems)
        save_state(st)
        print(f"accepted {symbol} {stage}")
        return

    if a.verdict:
        symbol = a.verdict.upper()
        rec, problems = submit_verdict(st, symbol, text)
        if problems:
            _refuse(problems)
        save_state(st)
        print(json.dumps(rec, indent=1, sort_keys=True))
        return

    if a.close:
        rows, info = close_session(st)
        save_state(st)
        print(f"{len(rows)} verdict(s) recorded -> {LEDGER}")
        for p in info["cases"]:
            print(f"  {p}")
        if info["skipped"]:
            print(f"  skipped (no verdict): {', '.join(info['skipped'])}")


if __name__ == "__main__":
    main()
