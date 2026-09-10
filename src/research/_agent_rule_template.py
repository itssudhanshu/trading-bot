#!/usr/bin/env python3
"""@@RULE_ID@@: @@ONE_LINE@@

Written by Agent 5 from the template, BEFORE the run. A docstring written
afterwards is a rationalisation, so `pipeline.py --new-research` creates this
file at proposal time and the gate refuses a validation whose research file has
no hypothesis in it.

The leading underscore keeps the TEMPLATE out of the selftest sweep. A generated
`agent_RNNN.py` has no underscore and IS swept, which is why `--selftest` below
must stay cheap -- it asserts the pre-registration, never runs the backtest.

hypothesis: @@HYPOTHESIS@@

control: @@CONTROL@@
    Whatever the live setting was a decision AGAINST, not "the live setting".
    weight_test.py controls on neutral 1/1/1/1 because raising deliv was a
    decision against neutral.

adoption bar (pre-registered, and it may be tightened, never loosened):
    primary metric:     @@PRIMARY_METRIC@@
    minimum effect:     @@MINIMUM_EFFECT@@
    minimum sample:     @@MINIMUM_SAMPLE@@
    secondary check:    @@SECONDARY_CHECK@@
    impact sensitivity: @@IMPACT_SENSITIVITY@@

failure mode: @@FAILURE_MODE@@

what would change the decision: an effect that clears the bar above AND leaves
the rank-depth slope intact. Read the slope from
data/breakout/rank_slope_baseline.json; do not quote one from a document.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # -> src/
import paths                                                    # noqa: F401

BATCH = "@@BATCH@@"
RULE_ID = "@@RULE_ID@@"

# Read the live constants, never copy them. impact_test.py carried a copy that
# said 15 for three months after selection.HOLD_DAYS moved to 10.
import selection                                                # noqa: E402
import simulate                                                 # noqa: E402

BASE = {"hold": selection.HOLD_DAYS}

# Impact is not calibrated, so it is reported as a sensitivity and never as one
# number. These are the five points the gate requires.
C_GRID = (0.0, 0.5, 1.0, 2.0, 3.0)


def variant(cfg):
    """The proposed rule, applied. Set variant constants INSIDE this fork so a
    variant cannot leak into the live weights file or into its siblings, and
    never vary anything in engine.py -- risk invariants are not searchable."""
    raise NotImplementedError("Agent 5 fills this in from the rule text")


def main():
    raise NotImplementedError("Agent 5 fills this in: both arms, same corpus, "
                              "same guard, same impact model, then the c grid "
                              "and the rank-depth slope re-measure")


def _selftest():
    """Cheap on purpose -- this runs in every sweep. It asserts the
    PRE-REGISTRATION, which is the thing that must exist before the run."""
    doc = (__doc__ or "")
    assert doc.strip(), "no docstring: the hypothesis was never registered"
    assert "hypothesis:" in doc, "the docstring states no hypothesis"
    assert "@@" not in doc, "the template placeholders were never filled in"
    assert "adoption bar" in doc, "the docstring states no adoption bar"
    assert "control:" in doc, "the docstring names no control"
    assert BATCH and "@@" not in BATCH, "no batch tag: the result cannot be compared"
    assert tuple(C_GRID) == (0.0, 0.5, 1.0, 2.0, 3.0), \
        "the impact sensitivity grid was narrowed"
    print(f"{Path(__file__).stem} selftest ok (pre-registered, batch {BATCH})")


if __name__ == "__main__":
    _selftest() if "--selftest" in sys.argv else main()
