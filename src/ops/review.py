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
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # -> src/
import paths

VERSION = 1
LEDGER = paths.SDATA / "reviews.jsonl"      # strategy-scoped and append-only
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


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--file", help="a file holding one verdict block")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return _selftest()
    if not a.file:
        ap.error("--file is required (or --selftest)")
    rec, problems = accept(Path(a.file).read_text(encoding="utf-8"))
    if problems:
        print("REFUSED:\n  " + "\n  ".join(problems))
        raise SystemExit(1)
    print(json.dumps(rec, indent=1, sort_keys=True))
    print(f"\n(not recorded -- pass a dossier to accept() from a caller that has one)")


if __name__ == "__main__":
    main()
