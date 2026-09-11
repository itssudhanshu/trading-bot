#!/usr/bin/env python3
"""The eight-agent review cycle: shared state, and the gates between stages.

A chain of agents is a summariser of summarisers, and a summary of a summary is
where `n` and the error bar go to die. Agent 1 reads 26 rows, Agent 2 writes
"micro underperformed", Agent 3 reads that sentence and calls it a pattern, and
by Agent 4 a rule is being proposed against a number nobody can trace back to a
trade. That is this project's oldest failure -- deciding what a figure means
after seeing it -- with seven fresh places for it to happen.

So the handoff between agents is A FILE WITH A SCHEMA, not prose, and the rules
that must not bend are not in any prompt. An agent can be argued out of a
standard: that is what produced a weight table where two of five variants "beat"
the live bucket at t < 0.5. The same reasoning that keeps `engine.py`'s risk
invariants out of every search keeps these out of every prompt.

This module refuses:

  - a stage whose predecessor did not run IN THIS CYCLE (no skipping, and no
    re-reading last week's payload as if it were fresh);
  - an aggregate quoting a return with no trial count beside it, at any depth --
    `per_cluster.micro.pnl` is exactly the figure CLAUDE.md forbids reporting
    blended, and exactly the one a summariser drops the count from;
  - an `etf_trend` row inside the equity pipeline (Agent 1 task 1);
  - a trade carrying an unknown flag, because a misspelled flag silently
    matches no standing check and looks identical to a clean trade;
  - a trade with no category, or one outside Agent 2's four;
  - a second DISTINCT rule in one cycle, and more than two revisions of the one;
  - a rule whose improvement type is a dial, a minimum-score rule, a
    participation cap, or a rule text already rejected in an earlier cycle.

None of that judges whether a finding is TRUE. It cannot. It enforces that the
finding arrives in a shape a person can check, which is the part that keeps
going missing.

State lives in `data/<strategy>/agent_state.json`, strategy-scoped like the
weights and the baseline, because a second strategy's review cycle must not
append to breakout's registry.

    python3 src/ops/pipeline.py --status
    python3 src/ops/pipeline.py --open-cycle
    python3 src/ops/pipeline.py --gate data_steward
    python3 src/ops/pipeline.py --handoff data_steward --file payload.json
    python3 src/ops/pipeline.py --show data_steward
    python3 src/ops/pipeline.py --close-cycle --file summary.json
"""
import argparse
import json
import os
import re
import sys
import tempfile
import datetime as dt
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # -> src/
import paths
import analysis

# NOT agent_state.json: data/agent_state.json is already the SCHEDULER's state
# (agent.py:33, last_snapshot / last_runner / last_research). Two files with one
# basename meaning two things is rules.md R1's related failure -- the one where
# `rank2` printed beside a stock at rank 5 and both were called rank.
# PIPELINE_DIR redirects all three files, for a REHEARSAL -- proving the
# plumbing with a synthetic rule that must never reach the live registry or the
# live batch history. It is loud (main() prints a banner) and off by default,
# because a quiet way to write somewhere else is how fixture rows got into the
# live order book.
_DIR = Path(os.environ["PIPELINE_DIR"]) if os.environ.get("PIPELINE_DIR") \
    else paths.SDATA
REHEARSAL = _DIR != paths.SDATA
STATE = _DIR / "pipeline_state.json"      # cycle state, registry, checks
CURRENT = _DIR / "pipeline_current.json"  # status board, overwritten
RUNS = _DIR / "pipeline_runs.jsonl"       # audit trail, append-only
VERSION = 1
STALE_MIN = 5          # a status board older than this with an agent named is hung
# ...except where the work legitimately takes longer. Agent 5 runs both arms of
# a backtest plus a five-point impact sensitivity plus the slope re-measure;
# impact_test.py and trigger_test.py are excluded from the sweep precisely
# because they take ~3 and ~4 minutes on their own. A single 5-minute threshold
# would report normal operation as a hang, and an alarm that cries wolf during
# normal operation is worse than no alarm.
# Per stage, in minutes. Every stage needs an entry and the selftest asserts it:
# a stage missing from here would silently inherit a threshold nobody chose.
AGENT_BUDGETS = {
    "orchestrator": 5, "data_steward": 5, "trade_auditor": 5,
    "pattern_miner": 5, "rule_proposer": 10,
    "backtest_validator": 30,          # both arms + five-point sensitivity + slope
    "performance_tracker": 5, "forward_manager": 5,
}

# --- the pipeline, and what each stage owes the next -------------------------
# `after` is the ordering gate: every named stage must have written a handoff in
# the CURRENT cycle before this one may write. Agents 6 and 7 both hang off the
# validator -- that is the fan-out in the architecture, and it is also why
# neither can be reached by skipping Agent 5.
STAGES = {
    "orchestrator": {
        "agent": "agent-0-orchestrator",
        "after": (),
        "requires": ("batch_id", "opened_at", "standing_checks_active",
                     "rule_registry_size", "scope"),
    },
    "data_steward": {
        "agent": "agent-1-data-steward",
        "after": ("orchestrator",),
        "requires": ("batch_id", "date", "records_received", "etf_trend_excluded",
                     "independent_paths", "dedup_notes", "trades", "batch_summary"),
    },
    "trade_auditor": {
        "agent": "agent-2-trade-auditor",
        "after": ("data_steward",),
        "requires": ("batch_id", "trades", "error_profile", "unearned_pnl",
                     "adjusted_pnl", "per_cluster", "standing_check_hits",
                     "non_strategy_positions"),
    },
    "pattern_miner": {
        "agent": "agent-3-pattern-miner",
        "after": ("trade_auditor",),
        "requires": ("batch_id", "actionable_findings", "shapes_monitored",
                     "noise_discarded", "dial_only_archived",
                     "no_actionable_pattern", "delta_vs_previous",
                     "temporal_concentration_flags"),
    },
    "rule_proposer": {
        "agent": "agent-4-rule-proposer",
        "after": ("pattern_miner",),
        "requires": ("batch_id", "decision"),
    },
    "backtest_validator": {
        "agent": "agent-5-backtest-validator",
        "after": ("rule_proposer",),
        "requires": ("batch_id", "rule_id", "batch_tag", "verdict",
                     "research_file",
                     "baseline_read_from", "baseline_value", "with_rule",
                     "effect", "adoption_bar_met", "impact_sensitivity",
                     "rank_slope", "affected_trades", "output_inspection",
                     "forward_paper_trade_required"),
    },
    "performance_tracker": {
        "agent": "agent-6-performance-tracker",
        # No stage dependency: the protocol runs 1 -> 6 -> 0 weekly, in cycles
        # where Agent 5 does not run at all. The invariant it used to encode --
        # "Agent 5 cannot be skipped" -- now lives on the RULE instead: nothing
        # reaches `applied` without a recorded PASS (see cmd_rule). That is
        # strictly stronger, because stage order only governed one cycle.
        "after": (),
        "requires": ("batch_id", "rule_id", "trades_since_application",
                     "rule_triggered_count", "actual_effect", "predicted_effect",
                     "within_tolerance", "failure_mode_triggered", "impact_tail",
                     "rank_slope", "verdict", "rollback_recommended",
                     "next_review", "feedback_for_pattern_miner"),
    },
    "forward_manager": {
        "agent": "agent-7-forward-paper-trade-manager",
        # Same: Agent 7 tracks paper trades DAILY, long after the cycle that
        # queued them. Intake still requires an INCONCLUSIVE, enforced on the
        # rule's status transition rather than on stage order.
        "after": (),
        "requires": ("batch_id", "queue", "active_count",
                     "promotions_this_cycle", "rejections_this_cycle",
                     "expirations_this_cycle", "next_reviews"),
    },
}
ORDER = tuple(STAGES)
AGENT_NO = {s: str(i) for i, s in enumerate(ORDER)}      # stage -> "0".."7"

EQUITY_BUCKETS = ("main", "pooled", "capped")
# pos.origin is NULL for a live pick and names the experiment otherwise. Two
# closed rows carry 'rank-cohort' -- positions from the retired deeper buckets,
# real forward trades that the CURRENT strategy did not choose. They stay in the
# order book because they happened; they are kept out of the error profile
# because the rule that made them no longer exists. Without this, every future
# Agent 2 re-classifies them as Thesis Errors and the profile is permanently
# unreadable.
LIVE_ORIGIN = "breakout"
EXCLUDED_BUCKET = "etf_trend"          # its own strategy, its own directory

CATEGORIES = ("Thesis Error", "Execution Error", "Process Deviation", "Variance")
PROFILE_KEYS = ("thesis", "execution", "variance", "process_deviation")

# Agent 1's flag vocabulary. Closed on purpose: a misspelled flag matches no
# standing check and is indistinguishable from a clean trade.
FLAGS = ("outage_window", "stop_at_nominal", "premium_outlier",
         "time_exit_mismatch", "duplicate_path", "feature_gap", "circuit_lock",
         "non_equity", "data_gap")

PATTERN_TAGS = ("Finding", "Shape", "Noise")
# Agent 3's significance rule, enforced rather than requested: n >= 5 across the
# lookback AND either |t| > 2 or the same mechanism in 3+ consecutive batches.
# Criteria may be tightened, never loosened -- so this is a floor, not a target.
MIN_PATTERN_N = 5
MIN_CONSECUTIVE = 3

# Agent 5's sealed evaluation, in code so no upstream agent can move it.
C_GRID = (0.0, 0.5, 1.0, 2.0, 3.0)     # the full sensitivity, never one number
C_PROFITABLE_AT = 2.0                  # a rule must still pay here to PASS
# READ, never transcribed. This was the literal `-1.12  # (se 0.28, t=-3.95)`
# and it was stale TWICE over: rank_slope_baseline.json said -1.13 and the
# measured value was -1.08. A number in code that does not read the file is the
# defect that let baseline.json say +7.59% for three days (L61), one layer down
# -- and the error bar beside it was a transcription of a batch nobody named.
#
# None when the ACTIVE strategy has never measured one. SLOPE_BASELINE is
# strategy-scoped (paths.SDATA) and only breakout has the file, so a bare
# subscript here would crash `STRATEGY=etf_trend python3 src/ops/pipeline.py`
# at import -- which is the case the literal was silently covering.
_SLOPE = analysis.load_rank_slope()
RANK_SLOPE_BASELINE = _SLOPE["slope_pct_per_step"] if _SLOPE else None
RANK_SLOPE_TOLERANCE = 0.3             # degrade by more than this and it FAILS
MIN_AFFECTED_N = 30                    # affected trades needed in the test set
# Agent 6's floor: below either of these, the verdict is always INCONCLUSIVE.
MIN_TRIGGERED = 10
MIN_TOTAL = 30

# CLAUDE.md's four legal improvements. Agent 4's hard gate is that it must be
# allowed to answer "none" -- a proposer that must produce a rule is a noise
# search with a changelog.
# Singular, matching Agent 4's enum. Agent 3 originally emitted the plural and
# Agent 4 would have rejected every forward-trade Finding at the handoff -- two
# names for one thing, which is the R1 failure in its most expensive form.
IMPROVEMENT_KINDS = ("new_input", "new_rule_shape", "forward_paper_trade",
                     "removal", "none")

RULE_STATUS = ("proposed", "validated", "applied", "rolled_back", "rejected",
               "paper_trade")
# What may follow what. This is where "Agent 5 is never skipped" actually lives:
# `validated` requires a recorded PASS and `paper_trade` a recorded INCONCLUSIVE,
# both stamped onto the rule by the validator's own handoff. Stage ordering used
# to carry this and could only ever police one cycle -- and the protocol runs
# Agent 6 weekly and Agent 7 daily, in cycles where Agent 5 does not run.
TRANSITIONS = {
    "proposed": ("validated", "rejected", "paper_trade"),
    "validated": ("applied", "rejected"),
    "paper_trade": ("applied", "rejected"),   # promoted on FORWARD evidence
    "applied": ("rolled_back",),
    "rejected": (),
    "rolled_back": (),
}
CYCLE_STATUS = RULE_STATUS + ("no_actionable_pattern",)
MAX_REVISIONS = 2                      # original + 2, then archive as rejected

VERDICTS = {
    "rule_proposer": ("rule", "no_valid_improvement", "no_actionable_pattern"),
    "backtest_validator": ("PASS", "FAIL", "INCONCLUSIVE"),
    "performance_tracker": ("CONFIRM", "REJECT", "INCONCLUSIVE"),
}

# An aggregate carrying any of these is quoting performance and must carry a
# trial count too. A single record (it has a ticker) is exempt: n=1 beside one
# trade is noise, not honesty.
PERF_KEYS = ("cagr", "mean", "per_trade", "edge", "avg", "win_rate", "maxdd",
             "total_return", "spread", "pnl", "unearned_pnl", "adjusted_pnl",
             "total_pnl_main_only")
N_KEYS = ("n", "independent_paths", "records_received", "trades_processed")
RECORD_KEYS = ("ticker", "pos_id", "path_id", "symbol")

# Rules the operator has already banned, matched on the rule text. Both were
# banned on evidence: a minimum-score rule is invalid by construction (the score
# is a within-cluster percentile and RISES in weak markets), and a participation
# cap was tested at 10/5/2/1%, came back non-monotonic, and its 2% arm produced
# a HIGHER maximum impact than no cap at all.
# The five sub-keys that make an adoption bar quantified rather than qualitative.
ADOPTION_BAR_KEYS = ("primary_metric", "minimum_effect", "minimum_sample",
                     "secondary_check", "impact_sensitivity")
CONFIDENCE = ("high", "medium", "low")

BANNED_RULE_SHAPES = (
    (r"\bmin(imum)?[ _-]?score\b|\bscore[ _-]?(threshold|floor|cut ?off|minimum)\b"
     r"|\bscore\s*(>=|>|≥)", "a minimum-score rule -- invalid by construction: "
     "the score is a within-cluster percentile and goes UP in weak markets"),
    (r"\bparticipation[ _-]?cap\b|\b(pct|percent|%)\s*of\s*adv\b|\badv[ _-]?cap\b",
     "a participation cap -- tested at 10/5/2/1%, non-monotonic, and the 2% arm "
     "produced a HIGHER maximum impact than no cap"),
    (r"\bengine\.py\b|\brisk[ _-]?invariant|\bIMPACT_C\b",
     "a change to engine.py. Risk invariants are never searched: a generator "
     "that can move its own limits will discover that removing them improves "
     "returns"),
    (r"strategies[/.](etf_trend|patterns|sentiment)\b",
     "a cross-strategy import. paths.py puts only the active strategy on "
     "sys.path, and that isolation is the whole of it"),
)

# rules.md R1: "The canonical word is bucket." Enforced on what the pipeline
# WRITES. These four lines must name the words in order to ban them, which is
# the same bind docs/rules.md is in -- hence the marker rather than an exception.
BANNED_WORDS = {                                                # vocab-allow
    "portfolio": "bucket", "holdings": "bucket",                # vocab-allow
    "slot": "stock (or position)", "book": "bucket",            # vocab-allow
}
# Excluded BY NAME with a reason, the same convention run_selftests.py uses for
# modules with no selftest: an exclusion that has to be defended beats one that
# happens silently. These predate the four-bucket naming and are the operator's
# own text; rewriting them is a separate, deliberate job.
VOCAB_ALLOW = "vocab-allow"     # a line that must name a banned word to ban it
# "the order book" is CLAUDE.md's own name for data/positions.db -- a different
# thing from a bucket, with its own name, which is precisely what R1 asks for.
# Banning it would be inventing a second word for something already named.
VOCAB_COMPOUNDS = (r"order[ -]?books?",)                         # vocab-allow
LEGACY_VOCAB = {
    "CLAUDE.md": "the operator's own doc; says 'four books run forward'",  # vocab-allow
    "docs/rules.md": "defines the rule, and quotes the banned words to do it",
    "docs/lessons.md": "append-only history; earlier entries predate the naming",
    "docs/STATE.md": "status log written before the four-bucket naming",
    "docs/glossary.md": "glosses the old words on purpose",
    "docs/performance-change.md": "written before the four-bucket naming",
}


def now():
    return dt.datetime.now().replace(microsecond=0).isoformat()


def blank():
    # No "log" key: pipeline_runs.jsonl is the audit trail. One record per
    # thing, or the two disagree and neither can be trusted (R1).
    return {"version": VERSION, "strategy": paths.STRATEGY, "cycle": 0,
            "open": False, "batch_id": None, "revisions": 0,
            "standing_checks": [], "rule_registry": [], "batch_history": [],
            "paper_trade_queue": [], "handoff": {}}


def log_run(agent, batch, action, note="", duration_s=None):
    """Append one line to the audit trail. Never rewritten, never deleted."""
    RUNS.parent.mkdir(parents=True, exist_ok=True)
    row = {"ts": now(), "agent": str(agent), "batch": batch, "action": action,
           "note": note}
    if duration_s is not None:
        row["duration_s"] = duration_s
    with RUNS.open("a") as f:
        f.write(json.dumps(row) + "\n")
    return row


def write_current(**kw):
    """Overwrite the status board. It is a board, not a ledger -- the ledger is
    RUNS, and conflating them is how one of them stops being read."""
    cur = read_current()
    cur.update(kw)
    cur["last_heartbeat"] = now()
    CURRENT.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(CURRENT.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(cur, f, indent=1)
            f.write("\n")
        os.replace(tmp, CURRENT)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return cur


def read_current():
    if not CURRENT.exists():
        return {}
    try:
        return json.loads(CURRENT.read_text())
    except Exception:
        return {}


def push(text, enabled):
    """One Telegram message. Opt-in per call: sending is outward-facing, and a
    selftest or a dry run must never reach the operator's phone."""
    if not enabled:
        return False
    try:
        import tg
        tg.send(text)
        return True
    except Exception as e:
        print(f"  telegram push failed: {type(e).__name__}: {e}", file=sys.stderr)
        return False


def load():
    if not STATE.exists():
        return blank()
    s = json.loads(STATE.read_text())
    if s.get("version") != VERSION:
        raise SystemExit(f"agent_state.json is version {s.get('version')}, this "
                         f"module writes {VERSION} -- migrate deliberately")
    return s


def save(s):
    """Atomic. The repo lives under iCloud, and a half-written state file would
    take the next cycle's gate decisions with it."""
    STATE.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(STATE.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(s, f, indent=1)
            f.write("\n")
        os.replace(tmp, STATE)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def active_checks(s):
    return [c for c in s["standing_checks"] if c.get("status") == "active"]


# --- validation -------------------------------------------------------------

def missing_n(obj, trail="payload"):
    """-> [path, ...] for every AGGREGATE quoting performance with no count.

    Walks the whole payload, because the figure that matters is always nested
    one deeper. Individual records are exempt -- they carry a ticker, and "n=1"
    beside one trade is ceremony, not honesty.
    """
    bad = []
    if isinstance(obj, dict):
        if (any(k in obj for k in PERF_KEYS)
                and not any(k in obj for k in N_KEYS)
                and not any(k in obj for k in RECORD_KEYS)):
            bad.append(trail)
        for k, v in obj.items():
            bad += missing_n(v, f"{trail}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            bad += missing_n(v, f"{trail}[{i}]")
    return bad


def _check_orchestrator(p, state):
    """The orchestrator PUBLISHES the standing-checks list that Agent 2 then
    checks every trade against. Unvalidated, that list is a self-report: a stale
    read of pipeline_state.json between the write and the publish would hand
    Agent 2 checks that do not exist, or drop one that does, and every trade in
    the cycle would be checked against the wrong list with nothing to notice.

    That is not hypothetical here. positions.py's selftest wrote fixture rows
    into the live order book because it redirected two of three paths -- the
    same class of bug, "I thought I was reading X and I was reading Y".

    So the gate compares every self-reported figure against the source of truth
    before the next agent ever sees it.
    """
    out = []
    published = set(p.get("standing_checks_active") or [])
    actual = {c["id"] for c in active_checks(state)}
    if published != actual:
        out.append(f"orchestrator published {len(published)} standing checks but "
                   f"pipeline_state.json holds {len(actual)} active; "
                   f"diff={sorted(published ^ actual)}")
    n = p.get("rule_registry_size")
    if n != len(state["rule_registry"]):
        out.append(f"rule_registry_size={n} but the registry holds "
                   f"{len(state['rule_registry'])}")
    scope = p.get("scope") or {}
    if "closed_positions" in scope:
        try:
            got = _closed_equity_count()
        except Exception as e:
            # A check that no-ops when it cannot run is the "status message is
            # not evidence" trap. If the book is unreadable, say so loudly.
            out.append(f"could not verify scope against the order book: "
                       f"{type(e).__name__}: {e}")
        else:
            if scope["closed_positions"] != got:
                out.append(f"scope.closed_positions={scope['closed_positions']} "
                           f"but the order book holds {got} closed equity "
                           "positions; Agent 1 would inherit the wrong count")
    return out


def _closed_equity_count():
    """-> the order book's own count of closed equity positions, read-only."""
    import sqlite3
    import positions
    c = sqlite3.connect(f"file:{positions.DB}?mode=ro", uri=True)
    try:
        return c.execute(
            "SELECT COUNT(*) FROM pos WHERE status='closed' AND exit_px IS NOT NULL "
            "AND bucket != ?", (EXCLUDED_BUCKET,)).fetchone()[0]
    finally:
        c.close()


def _check_steward(p):
    out = []
    # The steward's records_received was taken on trust while the orchestrator's
    # identical figure was verified. A dropped row would have passed.
    if "records_received" in p:
        try:
            got = _closed_equity_count()
        except Exception as e:
            out.append(f"could not verify records_received against the order "
                       f"book: {type(e).__name__}: {e}")
        else:
            if p["records_received"] != got:
                out.append(f"records_received={p['records_received']} but the "
                           f"order book holds {got} closed equity positions; "
                           "a dropped row would reach Agent 2 unnoticed")
    trades = p.get("trades") or []
    for i, t in enumerate(trades):
        bks = t.get("buckets") or ([t["bucket"]] if t.get("bucket") else [])
        if EXCLUDED_BUCKET in bks:
            out.append(f"trades[{i}] {t.get('ticker', '?')} is {EXCLUDED_BUCKET}; "
                       "it is a separate strategy and must be excluded, not passed")
        for b in bks:
            if b not in EQUITY_BUCKETS:
                out.append(f"trades[{i}] unknown bucket {b!r}; "
                           f"expected one of {EQUITY_BUCKETS}")
        if "origin" not in t:
            out.append(f"trades[{i}] {t.get('ticker', '?')} has no origin; "
                       f"map a NULL pos.origin to {LIVE_ORIGIN!r} and name the "
                       "experiment otherwise -- an unlabelled trade is assumed "
                       "to be the live strategy's and cannot be separated later")
        for f in (t.get("flags") or []):
            if f not in FLAGS:
                out.append(f"trades[{i}] unknown flag {f!r} -- a misspelled flag "
                           f"matches no standing check; expected one of {FLAGS}")
    ip, rec = p.get("independent_paths"), p.get("records_received")
    if isinstance(ip, int) and isinstance(rec, int) and ip > rec:
        out.append(f"independent_paths {ip} > records_received {rec}; "
                   "dedup cannot create paths")
    if isinstance(ip, int) and trades and len(trades) != ip:
        out.append(f"{len(trades)} trade objects but independent_paths={ip}; "
                   "one object per independent path")
    bs = p.get("batch_summary")
    if isinstance(bs, dict):
        if not isinstance(bs.get("per_cluster"), dict):
            out.append("batch_summary.per_cluster missing -- a blended number is "
                       "not a finding")
        if any((t.get("origin") or LIVE_ORIGIN) != LIVE_ORIGIN for t in trades):
            for k in ("trades_main_origin_breakout", "trades_main_other_origin",
                      "other_origin_pnl"):
                if k not in bs:
                    out.append(f"a trade carries a non-{LIVE_ORIGIN} origin but "
                               f"batch_summary.{k} is missing; the split has to "
                               "be stated, not implied")
    return out


def _check_auditor(p):
    out = []
    named = {n.get("ticker") for n in (p.get("non_strategy_positions") or [])}
    for i, t in enumerate(p.get("trades") or []):
        cat, flags = t.get("category"), (t.get("flags") or [])
        origin = t.get("origin") or LIVE_ORIGIN
        if origin != LIVE_ORIGIN:
            # SC-004: reported separately, excluded from the error profile.
            if cat is not None:
                out.append(f"trades[{i}] {t.get('ticker', '?')} has origin="
                           f"{origin!r} but was categorised {cat!r}; a position "
                           "the current strategy did not choose is reported "
                           "separately, not counted in the error profile")
            if t.get("ticker") not in named:
                out.append(f"trades[{i}] {t.get('ticker', '?')} has origin="
                           f"{origin!r} and is missing from "
                           "non_strategy_positions")
            continue
        if cat is None and "non_equity" in flags:
            continue          # correctly reported as a data bug, not a trade
        if cat not in CATEGORIES:
            out.append(f"trades[{i}] {t.get('ticker', '?')} category={cat!r}; "
                       f"exactly one of {CATEGORIES}")
        if "outage_window" in flags and cat != "Process Deviation":
            out.append(f"trades[{i}] {t.get('ticker', '?')} is outage_window but "
                       f"category={cat!r}; an outage is automatically a "
                       "Process Deviation regardless of P&L sign")
        if "circuit_lock" in flags and cat != "Process Deviation":
            out.append(f"trades[{i}] {t.get('ticker', '?')} is circuit_lock but "
                       f"category={cat!r}; the guard should have rejected it")
    prof = p.get("error_profile")
    if isinstance(prof, dict) and set(prof) != set(PROFILE_KEYS):
        out.append(f"error_profile keys {sorted(prof)}; expected {list(PROFILE_KEYS)}")
    pc = p.get("per_cluster")
    if isinstance(pc, dict) and not pc:
        out.append("per_cluster is empty -- report per cluster, never blended")
    return out


def _check_miner(p):
    """Every observation tagged, and a Finding must actually clear the bar.

    The bar is here rather than in the prompt for the usual reason: "it looks
    like a pattern" is the most persuasive sentence an agent can write, and the
    weight table is what happens when something persuasive at t < 0.5 is allowed
    through.
    """
    out = []
    for key, want in (("actionable_findings", "Finding"),
                      ("shapes_monitored", "Shape"),
                      ("noise_discarded", "Noise")):
        for i, pat in enumerate(p.get(key) or []):
            where = f"{key}[{i}] {pat.get('id') or pat.get('pattern') or '?'}"
            if pat.get("tag") != want:
                out.append(f"{where} tag={pat.get('tag')!r}; everything in "
                           f"{key} must be tagged {want!r}")
            if want != "Finding":
                continue
            kind = pat.get("improvement_type")
            if kind not in IMPROVEMENT_KINDS:
                out.append(f"{where} improvement_type={kind!r}; a Finding must "
                           f"name one of {IMPROVEMENT_KINDS}")
            elif kind == "none":
                out.append(f"{where} improvement_type=none means the only fix is "
                           "a dial; archive it in dial_only_archived rather than "
                           "passing it to Agent 4")
            n = pat.get("n")
            if not isinstance(n, int) or n < MIN_PATTERN_N:
                out.append(f"{where} n={n}; an actionable finding needs "
                           f"n >= {MIN_PATTERN_N} across the lookback window")
            t, cons = pat.get("t"), pat.get("consecutive_batches")
            resolved = isinstance(t, (int, float)) and abs(t) > 2
            recurring = isinstance(cons, int) and cons >= MIN_CONSECUTIVE
            if not (resolved or recurring):
                out.append(f"{where} has neither |t| > 2 (t={t}) nor the same "
                           f"mechanism in {MIN_CONSECUTIVE}+ consecutive batches "
                           f"(consecutive_batches={cons}) -- that is a Shape")
    na, af = p.get("no_actionable_pattern"), p.get("actionable_findings") or []
    if not isinstance(na, bool):
        out.append(f"no_actionable_pattern={na!r}; must be true or false")
    elif na and af:
        out.append(f"no_actionable_pattern=true but {len(af)} actionable findings")
    elif na is False and not af:
        out.append("no_actionable_pattern=false but actionable_findings is empty; "
                   "no actionable pattern is a valid result -- say so")
    ids = {f.get("id") or f.get("pattern") for f in af}
    for i, d in enumerate(p.get("dial_only_archived") or []):
        did = d.get("id") or d.get("pattern")
        if did and did in ids:
            out.append(f"dial_only_archived[{i}] {did} is also in "
                       "actionable_findings; a dial does not reach Agent 4")
    return out


def _check_rule(p, state):
    out = []
    r = p.get("rule") or {}
    for k in ("rule_id", "pattern_addressed", "rule_text", "hypothesis",
              "adoption_bar", "failure_mode", "improvement_type",
              "rank_slope_impact", "confidence", "rationale"):
        if not r.get(k):
            out.append(f"decision=rule but rule.{k} is empty")
    bar = r.get("adoption_bar")
    if bar is not None and not isinstance(bar, dict):
        out.append("rule.adoption_bar must be an object with "
                   f"{list(ADOPTION_BAR_KEYS)} -- a bar in prose is a bar that "
                   "can be reinterpreted after the run")
    elif isinstance(bar, dict):
        for k in ADOPTION_BAR_KEYS:
            if not bar.get(k):
                out.append(f"rule.adoption_bar.{k} is empty; the bar must be "
                           "quantified before Agent 5 runs anything")
    if r.get("confidence") and r["confidence"] not in CONFIDENCE:
        out.append(f"rule.confidence={r['confidence']!r}; one of {CONFIDENCE}")
    if "affected_trades_in_batch" not in r:
        out.append("rule.affected_trades_in_batch is missing; name which trades "
                   "in THIS batch the rule would have changed, and to what")
    kind = r.get("improvement_type")
    if kind and kind not in IMPROVEMENT_KINDS:
        out.append(f"rule.improvement_type={kind!r} is not one of "
                   f"{IMPROVEMENT_KINDS} -- a new value for an existing "
                   "parameter is a dial, and a dial is not an experiment")
    if kind == "none":
        out.append("improvement_type=none contradicts decision=rule; "
                   "say no_valid_improvement instead")

    text = " ".join(str(r.get(k, "")) for k in ("rule_text", "hypothesis"))
    for pat, why in BANNED_RULE_SHAPES:
        m = re.search(pat, text, re.I)
        if m:
            out.append(f"rule text matches {m.group(0)!r}: {why}")

    norm = re.sub(r"\s+", " ", str(r.get("rule_text", "")).strip().lower())
    for e in state["rule_registry"]:
        if e.get("status") in ("rejected", "rolled_back") and norm and \
                re.sub(r"\s+", " ", str(e.get("rule_text", "")).strip().lower()) == norm:
            out.append(f"this rule_text was already {e['status']} as "
                       f"{e.get('rule_id')} in cycle {e.get('cycle')}")

    # One rule per cycle. A re-submission REVISES the cycle's proposal rather
    # than adding a second; a distinct rule_id alongside it is the thing barred.
    mine = [e for e in state["rule_registry"] if e.get("cycle") == state["cycle"]]
    if mine and r.get("rule_id") and mine[0].get("rule_id") != r["rule_id"]:
        out.append(f"cycle {state['cycle']} already holds {mine[0]['rule_id']}; "
                   "maximum 1 rule change per cycle")
    if mine and state.get("revisions", 0) >= MAX_REVISIONS:
        out.append(f"{state['revisions']} revisions already; the maximum is "
                   f"{MAX_REVISIONS} -- archive it as rejected instead")
    return out


def check_payload(stage, payload, state):
    """-> [complaint, ...]. Empty means the handoff may be written."""
    out = [f"missing required key: {k}" for k in STAGES[stage]["requires"]
           if k not in payload]
    out += [f"performance figure with no trial count: {p}"
            for p in missing_n(payload)]

    allowed = VERDICTS.get(stage)
    key = "decision" if stage == "rule_proposer" else "verdict"
    if allowed and key in payload and payload[key] not in allowed:
        out.append(f"{key}={payload[key]!r}, expected one of {allowed}")

    if stage == "orchestrator":
        out += _check_orchestrator(payload, state)
    elif stage == "data_steward":
        out += _check_steward(payload)
    elif stage == "trade_auditor":
        out += _check_auditor(payload)
    elif stage == "pattern_miner":
        out += _check_miner(payload)
    elif stage == "rule_proposer" and payload.get("decision") == "rule":
        out += _check_rule(payload, state)
    elif stage == "backtest_validator":
        out += _check_validator(payload)
    elif stage == "performance_tracker":
        out += _check_tracker(payload)
    elif stage == "forward_manager":
        out += _check_forward(payload, state)
    return out


def _check_validator(p):
    """The sealed half. Every rule here is one Agent 5 is told it may not move,
    which is exactly why it is not stored in Agent 5's prompt."""
    out = []
    b = p.get("baseline_value") or {}
    if not all(k in b for k in ("cagr", "n")):
        out.append("baseline_value must carry the cagr and n actually read from "
                   "data/breakout/baseline.json -- never a figure quoted from a "
                   "document. That file said +7.59% for three days after it "
                   "stopped being true")
    grid = [r.get("c") for r in (p.get("impact_sensitivity") or [])]
    if [float(c) for c in grid if isinstance(c, (int, float))] != list(C_GRID):
        out.append(f"impact_sensitivity must report exactly c={list(C_GRID)}, "
                   f"got {grid}: the constant is not calibrated, so one number "
                   "is not a result")
    slope = p.get("rank_slope") or {}
    v = p.get("verdict")
    if slope.get("pass") is False and v != "FAIL":
        out.append(f"rank_slope.pass is false but verdict={v!r}; a rule that "
                   "degrades the slope by more than "
                   f"{RANK_SLOPE_TOLERANCE}%/cohort step FAILS. The score is the "
                   "one surviving signal")
    if v == "INCONCLUSIVE" and not p.get("forward_paper_trade_required"):
        out.append("verdict=INCONCLUSIVE must set forward_paper_trade_required; "
                   "forward trades are the only thing that shrinks the error bar")
    if v == "PASS":
        if not p.get("adoption_bar_met"):
            out.append("verdict=PASS but adoption_bar_met is false")
        if slope.get("pass") is not True:
            out.append("verdict=PASS but the rank-depth slope check did not pass")
        eff = p.get("effect") or {}
        t = eff.get("t")
        if not (isinstance(t, (int, float)) and abs(t) > 2):
            out.append(f"verdict=PASS but effect.t={t}; a candidate that wins by "
                       "less than its standard error is a finding about this "
                       "price history, not about the market")
        n = (p.get("with_rule") or {}).get("n")
        if isinstance(n, int) and n < MIN_AFFECTED_N:
            out.append(f"verdict=PASS on n={n}; the minimum sample is "
                       f"{MIN_AFFECTED_N} affected trades")
        for r in (p.get("impact_sensitivity") or []):
            if r.get("c") == C_PROFITABLE_AT:
                pt = r.get("per_trade")
                if isinstance(pt, (int, float)) and pt <= 0:
                    out.append(f"verdict=PASS but per_trade={pt} at "
                               f"c={C_PROFITABLE_AT}; the rule must stay "
                               "profitable across the sensitivity")
    # Pre-registration, checked rather than requested. `--new-research` writes
    # the file at proposal time from the registry, so this verifies the file the
    # run actually used still carries the hypothesis and the same batch tag.
    rf = p.get("research_file")
    if rf:
        rp = paths.ROOT / rf if not str(rf).startswith("/") else Path(rf)
        if not rp.exists():
            out.append(f"research_file {rf} does not exist: "
                       "pre_registration_missing")
        else:
            src = rp.read_text()
            if "hypothesis:" not in src:
                out.append(f"{rf} states no hypothesis: pre_registration_missing")
            if re.search(PLACEHOLDER, src):
                out.append(f"{rf} still holds template placeholders "
                           f"{sorted(set(re.findall(PLACEHOLDER, src)))}; it was "
                           "generated and never filled in")
            tag = p.get("batch_tag")
            if tag and f'BATCH = "{tag}"' not in src:
                out.append(f"{rf} does not carry BATCH = {tag!r}; a figure and "
                           "the file that produced it must share a batch tag")
    if not p.get("output_inspection"):
        out.append("output_inspection is empty: a classifier is finished when "
                   "the OUTPUT is clean, not when the validation passes")
    return out


def _check_tracker(p):
    out = []
    trig, tot = p.get("rule_triggered_count"), p.get("trades_since_application")
    enough = ((isinstance(trig, int) and trig >= MIN_TRIGGERED)
              or (isinstance(tot, int) and tot >= MIN_TOTAL))
    if p.get("verdict") != "INCONCLUSIVE" and not enough:
        out.append(f"verdict={p.get('verdict')!r} on {trig} triggered / {tot} "
                   f"total; below {MIN_TRIGGERED} triggered or {MIN_TOTAL} total "
                   "the verdict is always INCONCLUSIVE")
    if p.get("verdict") == "REJECT" and not p.get("rollback_recommended"):
        out.append("verdict=REJECT must recommend rollback")
    return out


def _check_forward(p, state):
    """Criteria may be tightened, never loosened -- checked against the bar Agent
    4 pre-registered, not against the bar the queue happens to carry now."""
    out = []
    reg = {r.get("rule_id"): r for r in state["rule_registry"]}
    for i, q in enumerate(p.get("queue") or []):
        rid = q.get("rule_id")
        if q.get("status") not in ("active", "promoted", "rejected", "expired"):
            out.append(f"queue[{i}] status={q.get('status')!r}; one of "
                       "active/promoted/rejected/expired")
        known = reg.get(rid)
        if known and known.get("adoption_bar") and q.get("adoption_bar") \
                and q["adoption_bar"] != known["adoption_bar"]:
            out.append(f"queue[{i}] {rid} carries an adoption_bar that differs "
                       "from the one Agent 4 pre-registered. The bar was set "
                       "before the run and may be tightened, never loosened")
    act = [q for q in (p.get("queue") or []) if q.get("status") == "active"]
    if isinstance(p.get("active_count"), int) and p["active_count"] != len(act):
        out.append(f"active_count={p['active_count']} but {len(act)} queue "
                   "entries are active")
    return out


def gate(state, stage):
    """-> [reason, ...] why `stage` may not run. Empty means go."""
    if stage not in STAGES:
        return [f"unknown stage {stage!r}; expected one of {ORDER}"]
    out = []
    if not state["open"]:
        out.append("no cycle is open -- run --open-cycle first")
    for need in STAGES[stage]["after"]:
        h = state["handoff"].get(need)
        if not h:
            out.append(f"{need} has not run")
        elif h.get("cycle") != state["cycle"]:
            out.append(f"{need} last ran in cycle {h.get('cycle')}, not "
                       f"{state['cycle']} -- stale handoff")
    # This branch was `if v and ...: pass` -- it read the verdict, tested it, and
    # did nothing. It looked like a check and was a no-op, which is the exact
    # failure CLAUDE.md names: "a flag that prints enabled may do nothing". The
    # ordering above was real, so Agent 5 could not be SKIPPED; but its verdict
    # was never consulted, so a FAIL routed onward as freely as a PASS.
    if stage in ("performance_tracker", "forward_manager"):
        h = state["handoff"].get("backtest_validator") or {}
        fresh = h.get("cycle") == state["cycle"]
        v = h.get("verdict") if fresh else None
        if v == "FAIL":
            out.append(f"the validator returned FAIL this cycle; a failed rule "
                       f"is rejected and revised, not routed to {stage}")
        elif stage == "forward_manager" and v == "PASS":
            out.append("the validator returned PASS; a passing rule is applied "
                       "by the orchestrator, not queued for forward evidence. "
                       "Only INCONCLUSIVE routes to Agent 7")
        # ...and there must be something to do. A tracker with no applied rule
        # and a paper manager with an empty queue are not monitoring, they are
        # generating a report about nothing.
        if stage == "performance_tracker" and not any(
                r.get("status") == "applied" for r in state["rule_registry"]):
            out.append("no rule is at status `applied`; there is nothing to "
                       "monitor. Agent 6 runs against an applied rule")
        if stage == "forward_manager" and v != "INCONCLUSIVE" and not any(
                r.get("status") == "paper_trade" for r in state["rule_registry"]):
            out.append("no rule is at status `paper_trade` and the validator did "
                       "not return INCONCLUSIVE this cycle; the queue has "
                       "nothing to track and nothing to take in")
    return out


# --- commands ---------------------------------------------------------------

def cmd_open(state, batch_id=None, note=None, tg=False):
    if state["open"]:
        raise SystemExit(f"cycle {state['cycle']} is still open -- --close-cycle first")
    state["cycle"] += 1
    state["open"] = True
    state["batch_id"] = batch_id
    state["revisions"] = 0
    state["handoff"] = {}
    save(state)
    log_run("0", batch_id, "cycle_start",
            note or f"cycle {state['cycle']} open, "
                    f"{len(active_checks(state))} active standing checks")
    write_current(started=now(), batch_id=batch_id, cycle=state["cycle"],
                  current_agent="Agent 0: Orchestrator", step="cycle opened",
                  input_received_from=None, waiting_on=None)
    push(f"🔄 Pipeline {batch_id} started\n  cycle {state['cycle']} | "
         f"{len(active_checks(state))} standing checks", tg)
    print(f"cycle {state['cycle']} open"
          + (f" batch {batch_id}" if batch_id else "")
          + f" ({len(active_checks(state))} active standing checks, "
          f"{len(state['rule_registry'])} rules on the registry)")


def cmd_handoff(state, stage, payload, note=None, tg=False):
    no = AGENT_NO[stage]
    why = gate(state, stage)
    if why:
        log_run(no, state.get("batch_id"), "blocked", "; ".join(why))
        write_current(current_agent=f"Agent {no}: {stage}",
                      step="BLOCKED", waiting_on="; ".join(why))
        push(f"⚠️ Agent {no} ({stage}): BLOCKED\n  " + "\n  ".join(why), tg)
        raise SystemExit("BLOCKED " + stage + ":\n  " + "\n  ".join(why))
    bad = check_payload(stage, payload, state)
    if bad:
        log_run(no, payload.get("batch_id"), "rejected", "; ".join(bad))
        write_current(current_agent=f"Agent {no}: {stage}",
                      step="REJECTED — payload sent back", waiting_on=bad[0])
        push(f"⚠️ Agent {no} ({stage}): REJECTED\n  " + "\n  ".join(bad[:3]), tg)
        raise SystemExit("REJECTED " + stage + ":\n  " + "\n  ".join(bad))

    payload = dict(payload, cycle=state["cycle"], at=now())
    state["handoff"][stage] = payload

    if stage == "rule_proposer" and payload.get("decision") == "rule":
        r = dict(payload["rule"])
        mine = [e for e in state["rule_registry"] if e.get("cycle") == state["cycle"]]
        r.update(cycle=state["cycle"], status="proposed", proposed_at=now(),
                 applied_date=None, rollback_date=None,
                 forward_evidence={"trades": 0, "effect": None})
        if mine:
            state["rule_registry"][state["rule_registry"].index(mine[0])] = r
            state["revisions"] += 1
        else:
            state["rule_registry"].append(r)
    if stage == "backtest_validator":
        # Stamp the verdict onto the rule. cmd_rule reads it, so a status can
        # never claim evidence the validator did not produce.
        for r in state["rule_registry"]:
            if r.get("rule_id") == payload.get("rule_id"):
                r["validator_verdict"] = payload.get("verdict")
                r["validator_batch_tag"] = payload.get("batch_tag")
    if stage == "forward_manager":
        state["paper_trade_queue"] = payload.get("queue") or []
    if stage == "trade_auditor":
        for cid in payload.get("standing_check_hits") or []:
            for c in state["standing_checks"]:
                if c["id"] == cid:
                    c["hits"] = c.get("hits", 0) + 1
                    c["last_hit_batch"] = payload.get("batch_id")
    save(state)
    rev = f" (revision {state['revisions']})" if stage == "rule_proposer" and \
        state["revisions"] else ""
    v = payload.get("verdict") or payload.get("decision") or ""
    log_run(no, payload.get("batch_id"), "done",
            note or f"{v}{rev}".strip() or "handoff accepted")
    nxt = [s for s in ORDER if stage in STAGES[s]["after"]]
    write_current(stage=stage,
                  current_agent=f"Agent {no}: {stage}", step=f"done {v}".strip(),
                  input_received_from=(STAGES[stage]["after"] or (None,))[0],
                  waiting_on=None,
                  next_agent=", ".join(f"Agent {AGENT_NO[s]}" for s in nxt) or None)
    print(f"ok {stage} cycle {state['cycle']}{rev}")


def cmd_close(state, summary=None, abort=None, tg=False):
    if not state["open"]:
        raise SystemExit("no cycle is open")
    if summary is None and not abort:
        raise SystemExit("--close-cycle needs --file <summary.json>, or --abort "
                         "'<reason>'. A cycle nobody summarised gets "
                         "re-discovered and re-decided differently.")
    ran = [s for s in ORDER
           if state["handoff"].get(s, {}).get("cycle") == state["cycle"]]
    if summary is not None:
        need = ("batch_id", "date", "trades_processed", "independent_paths",
                "error_profile", "unearned_pnl", "adjusted_pnl", "patterns_found",
                "pattern_tags", "rule_proposed", "rule_status",
                "standing_checks_active", "standing_checks_added",
                "standing_checks_retired", "delta_vs_previous_batch", "next_step")
        bad = [f"missing required key: {k}" for k in need if k not in summary]
        bad += [f"performance figure with no trial count: {p}"
                for p in missing_n(summary)]
        if summary.get("rule_status") not in CYCLE_STATUS:
            bad.append(f"rule_status={summary.get('rule_status')!r}, expected one "
                       f"of {CYCLE_STATUS}")
        if bad:
            raise SystemExit("REJECTED cycle summary:\n  " + "\n  ".join(bad))
        state["batch_history"].append(dict(summary, cycle=state["cycle"],
                                           stages_ran=ran, at=now()))
    state["open"] = False
    save(state)
    s = summary or {}
    ep = s.get("error_profile") or {}
    log_run("0", state.get("batch_id"), "cycle_complete",
            abort or f"{s.get('rule_status', 'aborted')}; "
                     f"{len(ran)}/{len(ORDER)} stages")
    write_current(current_agent=None, step="cycle complete", waiting_on=None,
                  cycle=state["cycle"], next_agent=None)
    if summary:
        push(f"✅ Pipeline {s.get('batch_id')} complete\n"
             f"  {s.get('independent_paths')} independent paths | "
             + ", ".join(f"{k} {v}" for k, v in ep.items() if v)
             + f"\n  Rule: {s.get('rule_proposed') or 'not proposed'} "
               f"({s.get('rule_status')})\n"
               f"  Next: {s.get('next_step', '')[:120]}", tg)
    else:
        push(f"⚠️ Pipeline aborted: {abort}", tg)
    print(f"cycle {state['cycle']} closed -- {len(ran)}/{len(ORDER)} stages ran: "
          + (", ".join(ran) or "none")
          + (f"\n  ABORTED: {abort}" if abort else ""))


def cmd_current():
    """The one-line answer to "is it running and what is it doing"."""
    cur = read_current()
    if not cur:
        print("  no cycle has run (no pipeline_current.json)")
        return 0
    hb = cur.get("last_heartbeat")
    budget = AGENT_BUDGETS.get(cur.get("stage"), STALE_MIN)
    stale = None
    if hb:
        age = (dt.datetime.now() - dt.datetime.fromisoformat(hb)).total_seconds() / 60
        stale = age > budget
    print(f"  batch        : {cur.get('batch_id')}  cycle {cur.get('cycle')}")
    print(f"  current agent: {cur.get('current_agent') or 'idle'}")
    print(f"  step         : {cur.get('step')}")
    print(f"  from         : {cur.get('input_received_from') or '-'}")
    print(f"  next         : {cur.get('next_agent') or '-'}")
    print(f"  heartbeat    : {hb}"
          + (f"  ({age:.0f} min ago)" if hb else ""))
    if cur.get("waiting_on"):
        print(f"  BLOCKED ON   : {cur['waiting_on']}")
    if stale and cur.get("current_agent"):
        print(f"  !! stale: an agent is named but nothing has moved in "
              f"{age:.0f} min (budget {budget}) -- it hung")
        return 1
    return 0


def cmd_status(state):
    print(f"strategy {state['strategy']}  cycle {state['cycle']}  "
          f"{'OPEN' if state['open'] else 'closed'}"
          + (f"  batch {state['batch_id']}" if state.get("batch_id") else ""))
    for s in ORDER:
        h = state["handoff"].get(s, {})
        fresh = h.get("cycle") == state["cycle"]
        mark = "ok  " if fresh else ("--  " if not h else "old ")
        v = (h.get("verdict") or h.get("decision") or "") if fresh else ""
        print(f"  {mark}{s:<22}{h.get('at', ''):<22}{v}")
    act = active_checks(state)
    if act:
        print(f"\n  standing checks, active ({len(act)}):")
        for c in act:
            print(f"    [{c['id']}] hits {c.get('hits', 0):<3} {c['description'][:64]}")
    live = [r for r in state["rule_registry"]
            if r.get("status") not in ("rejected", "rolled_back")]
    if live:
        print(f"\n  rules not closed out ({len(live)}):")
        for r in live:
            print(f"    [{r['rule_id']}] c{r['cycle']} {r['status']:<11}"
                  f"({r.get('improvement_type')}) {r.get('rule_text', '')[:52]}")
    if state["batch_history"]:
        b = state["batch_history"][-1]
        print(f"\n  last batch {b.get('batch_id')}: "
              f"{b.get('trades_processed')} trades, "
              f"{b.get('independent_paths')} paths, {b.get('rule_status')}")


PLACEHOLDER = r"@@[A-Z_]+@@"
TEMPLATE = paths.ROOT / "src" / "research" / "_agent_rule_template.py"
RESEARCH_DIR = paths.ROOT / "src" / "research"


def _shown(p):
    """-> repo-relative when it is under the repo, absolute otherwise. The
    selftest redirects RESEARCH_DIR outside the repo so it never writes into
    src/research/, and relative_to() raises on that."""
    try:
        return p.relative_to(paths.ROOT)
    except ValueError:
        return p


def research_path(rule_id):
    """-> src/research/agent_RNNN.py. Greppable, and distinguishable from
    human-authored research at a glance."""
    return RESEARCH_DIR / f"agent_{rule_id.replace('-', '')}.py"


def cmd_new_research(state, rule_id, batch=None):
    """Create the pre-registered research file for a rule, FROM THE REGISTRY.

    Filled from the registry rather than from arguments so the file's hypothesis
    and bar cannot drift from what Agent 4 actually registered. A bar that can be
    restated on the way into the test is not pre-registered.
    """
    hit = [r for r in state["rule_registry"] if r.get("rule_id") == rule_id]
    if not hit:
        raise SystemExit(f"no such rule {rule_id}; Agent 4 registers it first")
    r = hit[0]
    dest = research_path(rule_id)
    if dest.exists():
        raise SystemExit(f"{_shown(dest)} already exists. A "
                         "pre-registration is written once; edit it deliberately "
                         "or use a new rule id")
    bar = r.get("adoption_bar") or {}
    missing = [k for k in ADOPTION_BAR_KEYS if not bar.get(k)]
    if missing:
        raise SystemExit(f"{rule_id}'s adoption bar is incomplete: {missing}")
    tag = batch or f"{dt.date.today():%Y%m%d}-agent5-{rule_id.replace('-', '')}"
    text = TEMPLATE.read_text()
    for token, value in (
            ("@@RULE_ID@@", rule_id),
            ("@@ONE_LINE@@", str(r.get("rule_text", ""))),
            ("@@HYPOTHESIS@@", str(r.get("hypothesis", ""))),
            ("@@CONTROL@@", str(r.get("control") or "the live rules, unchanged")),
            ("@@FAILURE_MODE@@", str(r.get("failure_mode", ""))),
            ("@@BATCH@@", tag),
            *((f"@@{k.upper()}@@", str(bar[k])) for k in ADOPTION_BAR_KEYS)):
        text = text.replace(token, value)
    # Match the PLACEHOLDER shape, not a bare "@@": the generated file carries
    # "@@" legitimately inside its own selftest, which asserts the placeholders
    # are gone. Checking for the literal flagged a correctly-filled file.
    left = sorted(set(re.findall(PLACEHOLDER, text)))
    if left:
        raise SystemExit(f"template placeholders left unfilled: {left}")
    dest.write_text(text)
    # relative_to() raises when RESEARCH_DIR is redirected (the selftest does
    # exactly that, so it does not write into src/research/).
    shown = _shown(dest)
    print(f"  {shown} written, batch {tag}")
    print(f"  it is now in the selftest sweep -- run it before the backtest:\n"
          f"    env -u PYTHONPATH python3 {shown} --selftest")
    return dest


def cmd_rule(state, rule_id, status, note=None):
    hit = [r for r in state["rule_registry"] if r.get("rule_id") == rule_id]
    if not hit:
        raise SystemExit(f"no such rule {rule_id}")
    if status not in RULE_STATUS:
        raise SystemExit(f"status must be one of {RULE_STATUS}")
    r = hit[0]
    was = r["status"]
    legal = TRANSITIONS.get(was, ())
    if status != was and status not in legal:
        raise SystemExit(f"{rule_id}: {was} -> {status} is not a legal "
                         f"transition; from {was} the only moves are "
                         f"{legal or '(none -- terminal)'}")
    v = r.get("validator_verdict")
    if status == "validated" and v != "PASS":
        raise SystemExit(f"{rule_id} cannot be validated: the validator "
                         f"recorded {v!r}, not PASS. Agent 5 is never skipped")
    if status == "paper_trade" and v != "INCONCLUSIVE":
        raise SystemExit(f"{rule_id} cannot enter the paper queue: the "
                         f"validator recorded {v!r}, not INCONCLUSIVE")
    r["status"] = status
    if status == "applied":
        r["applied_date"] = now()
    if status == "rolled_back":
        r["rollback_date"] = now()
    if note:
        r.setdefault("notes", []).append({"at": now(), "note": note})
    save(state)
    print(f"{rule_id}: {was} -> {status}")
    if status == "applied":
        print("  Telegram listener restart required if the rule touched code:\n"
              "    pkill -f \"tg.py --listen\"\n"
              "  positions_record.sql regeneration required if it touched "
              "positions.db\n  Run: python3 tests/run_selftests.py")


def cmd_add_check(state, text, batch):
    cid = f"SC-{len(state['standing_checks']) + 1:03d}"
    state["standing_checks"].append(
        {"id": cid, "description": text, "source_batch": batch,
         "added": dt.date.today().isoformat(), "status": "active",
         "retirement_reason": None, "hits": 0, "last_hit_batch": None})
    save(state)
    print(f"standing check {cid} added")


def cmd_retire_check(state, cid, why):
    hit = [c for c in state["standing_checks"] if c["id"] == cid]
    if not hit:
        raise SystemExit(f"no such standing check {cid}")
    hit[0]["status"] = "retired"
    hit[0]["retirement_reason"] = why
    save(state)
    print(f"{cid} retired: {why}")


def _added_lines(rel):
    """-> {lineno: text} for lines this working tree ADDS to a tracked file.

    A legacy file is grandfathered because its history predates the naming, not
    because it is exempt forever -- and docs/lessons.md is append-only, so the
    text that matters is always the newest. Skipping the whole file handed every
    future writer a free pass on exactly the lines the rule is for. Agent 0 hit
    this on cycle 2: its lessons entry used a banned word and only got caught
    because it copied the section to a scratch file by hand.
    """
    import re as _re
    import subprocess
    r = subprocess.run(["git", "diff", "-U0", "HEAD", "--", rel],
                       capture_output=True, text=True, cwd=paths.ROOT)
    if r.returncode != 0:
        return None                       # not a repo, or no HEAD: fall back
    out, ln = {}, 0
    for line in r.stdout.splitlines():
        if line.startswith("@@"):
            m = _re.search(r"\+(\d+)", line)
            ln = int(m.group(1)) if m else 0
        elif line.startswith("+++"):
            continue
        elif line.startswith("+"):
            out[ln] = line[1:]
            ln += 1
    return out


def cmd_vocab(files, check_legacy=False):
    """rules.md R1, mechanically. Reports; never rewrites.

    A line may carry the marker `vocab-allow` when it must NAME a banned word in
    order to ban it -- which is what every one of these prompts does in its own
    constraints section, and what docs/rules.md does in R1. The marker is on the
    line, is counted, and is printed in the summary: an escape a reader can see
    beats one that happens silently, which is the same reason run_selftests.py
    names its excluded modules instead of skipping them by exception.
    """
    errs = skipped = allowed = 0
    for f in files:
        rel = str(Path(f).resolve().relative_to(paths.ROOT)) \
            if str(Path(f).resolve()).startswith(str(paths.ROOT)) else str(f)
        lines = list(enumerate(Path(f).read_text().splitlines(), 1))
        if rel in LEGACY_VOCAB and not check_legacy:
            added = _added_lines(rel)
            if added is None:
                print(f"  skip  {rel} -- {LEGACY_VOCAB[rel]} (git unavailable)")
                skipped += 1
                continue
            if not added:
                print(f"  skip  {rel} -- {LEGACY_VOCAB[rel]} (nothing added)")
                skipped += 1
                continue
            print(f"  new   {rel} -- history grandfathered, "
                  f"checking {len(added)} added line(s)")
            lines = sorted(added.items())
        for i, line in lines:
            probe = line
            for c in VOCAB_COMPOUNDS:
                probe = re.sub(c, "", probe, flags=re.I)
            hits = [w for w in BANNED_WORDS if re.search(rf"\b{w}s?\b", probe, re.I)]
            if not hits:
                continue
            if VOCAB_ALLOW in line:
                print(f"  allow {rel}:{i}  names {', '.join(hits)} to ban them")
                allowed += 1
                continue
            for w in hits:
                print(f"  ERROR {rel}:{i}  {w!r} -> say {BANNED_WORDS[w]}")
                errs += 1
    print(f"  {errs} banned word(s), {allowed} allowed by marker, "
          f"{skipped} file(s) skipped as legacy")
    return 1 if errs else 0


# A FIXTURE value, deliberately frozen, and never RANK_SLOPE_BASELINE. The
# constant is now a live read of rank_slope_baseline.json, so wiring it into an
# assertion would make the selftest fail whenever rank_test.py re-measures --
# for a reason that has nothing to do with the property being protected. That is
# the hardcoded-mix failure CLAUDE.md records: assert the property, not the
# number. It happens to equal the 20260911-rankslope value and must not be
# updated when that moves.
_FROZEN_SLOPE = -1.08


def _selftest():
    """Every assertion is a rule the pipeline exists to enforce."""
    global STATE, CURRENT, RUNS, RESEARCH_DIR
    td = Path(tempfile.gettempdir())
    saved = (STATE, CURRENT, RUNS, RESEARCH_DIR)
    RESEARCH_DIR = td
    STATE = td / "pipeline_selftest_state.json"
    CURRENT = td / "pipeline_selftest_current.json"
    RUNS = td / "pipeline_selftest_runs.jsonl"
    for f in (STATE, CURRENT, RUNS):
        f.unlink(missing_ok=True)
    tmp = STATE
    try:
        for i, s in enumerate(ORDER):
            for a in STAGES[s]["after"]:
                assert a in ORDER[:i], f"{s} depends on {a}, declared later"
        # 6 and 7 used to hang off the validator by stage ORDER, and that
        # assertion is re-derived rather than deleted: the property it protected
        # -- Agent 5 is never skipped -- now lives on the rule's status, where it
        # holds across cycles instead of only within one.
        assert STAGES["performance_tracker"]["after"] == () and \
            STAGES["forward_manager"]["after"] == (), \
            "6 and 7 must run in cycles where 5 does not (weekly, daily)"
        # Agent 3's prompt STATES the significance threshold; _check_miner
        # ENFORCES it. Nothing kept the two in step, so a prompt could come to
        # say n >= 3 while the gate still refused anything under 5 -- an agent
        # reasoning to a bar the harness does not hold, which is the same shape
        # as a status message that is not evidence.
        _a3 = (paths.ROOT / "scripts" / "claude" / "agents"
               / "agent-3-pattern-miner.md").read_text()
        assert f"n >= {MIN_PATTERN_N}" in _a3, (
            f"agent-3's prompt no longer states n >= {MIN_PATTERN_N}, which is "
            "what _check_miner enforces")
        assert f"{MIN_CONSECUTIVE}+ consecutive" in _a3, (
            f"agent-3's prompt no longer states {MIN_CONSECUTIVE}+ consecutive "
            "batches, which is what _check_miner enforces")

        assert set(TRANSITIONS) == set(RULE_STATUS), \
            "a status with no transition row can never be left"
        assert TRANSITIONS["applied"] == ("rolled_back",), \
            "an applied rule leaves only by rollback"
        assert not TRANSITIONS["rejected"] and not TRANSITIONS["rolled_back"], \
            "terminal states are terminal"

        st = blank()
        assert gate(st, "data_steward"), "ran with no cycle open"
        cmd_open(st, "20260910")
        assert gate(st, "trade_auditor"), "trade_auditor ran before data_steward"
        assert not gate(st, "orchestrator")
        # The orchestrator's self-reports are each checked against the source
        # of truth. scope.closed_positions is exercised with a count that can
        # never match, so the test does not depend on how many trades are
        # closed today -- a selftest that moves with the order book is a
        # selftest that fails for the wrong reason.
        orch = {"batch_id": "20260910", "opened_at": now(),
                "standing_checks_active": [], "rule_registry_size": 0,
                "scope": {"buckets": list(EQUITY_BUCKETS)}}
        for bad, needle in (
                (dict(orch, standing_checks_active=["SC-999"]), "SC-999"),
                (dict(orch, rule_registry_size=7), "registry holds 0"),
                (dict(orch, scope={"closed_positions": -1}), "order book holds")):
            try:
                cmd_handoff(st, "orchestrator", bad)
                raise AssertionError(f"accepted a false self-report: {needle}")
            except SystemExit as e:
                assert needle in str(e), e
        cmd_handoff(st, "orchestrator", orch)

        # records_received is now verified against the order book, so the
        # fixture must state the book's own count rather than an invented one.
        # The NEGATIVE case below uses -1, which can never match, so the test
        # does not move when trades close.
        base = {"batch_id": "20260910", "date": "2026-09-10",
                "records_received": _closed_equity_count(),
                "etf_trend_excluded": 2,
                "independent_paths": 1, "dedup_notes": ["3 -> 1: NATCAPSUQ"],
                "trades": [{"ticker": "NATCAPSUQ", "buckets": ["main", "pooled",
                            "capped"], "flags": ["duplicate_path"],
                            "cluster": "micro", "pnl_pct": -10.0,
                            "origin": LIVE_ORIGIN}],
                "batch_summary": {"n": 1, "total_pnl_main_only": -4500,
                                  "per_cluster": {"micro": {"n": 1, "pnl": -4500}}}}
        # etf_trend must never reach the equity pipeline.
        try:
            cmd_handoff(st, "data_steward", json.loads(json.dumps(base)) |
                        {"trades": [dict(base["trades"][0],
                                         buckets=["main", "etf_trend"])]})
            raise AssertionError("accepted an etf_trend row")
        except SystemExit as e:
            assert "separate strategy" in str(e), e
        # a misspelled flag is indistinguishable from a clean trade
        try:
            cmd_handoff(st, "data_steward", json.loads(json.dumps(base)) |
                        {"trades": [dict(base["trades"][0], flags=["premium_outlyer"])]})
            raise AssertionError("accepted an unknown flag")
        except SystemExit as e:
            assert "unknown flag" in str(e), e
        # an aggregate with no trial count, at depth
        try:
            bad = json.loads(json.dumps(base))
            bad["batch_summary"]["per_cluster"]["micro"] = {"pnl": -4500}
            cmd_handoff(st, "data_steward", bad)
            raise AssertionError("accepted a nested figure with no count")
        except SystemExit as e:
            assert "no trial count" in str(e), e
        # An unlabelled trade is assumed to be the live strategy's, and cannot
        # be separated out afterwards.
        try:
            cmd_handoff(st, "data_steward", json.loads(json.dumps(base)) |
                        {"trades": [{k: v for k, v in base["trades"][0].items()
                                     if k != "origin"}]})
            raise AssertionError("accepted a trade with no origin")
        except SystemExit as e:
            assert "has no origin" in str(e), e
        # A non-live origin must be split out in the summary, not implied.
        try:
            cmd_handoff(st, "data_steward", json.loads(json.dumps(base)) |
                        {"trades": [dict(base["trades"][0], origin="rank-cohort")]})
            raise AssertionError("accepted a non-breakout origin with no split")
        except SystemExit as e:
            assert "has to be stated, not implied" in str(e), e
        try:
            cmd_handoff(st, "data_steward", dict(base, records_received=-1))
            raise AssertionError("accepted a records_received the book disagrees with")
        except SystemExit as e:
            assert "order book holds" in str(e), e
        cmd_handoff(st, "data_steward", base)

        aud = {"batch_id": "20260910",
               "trades": [{"ticker": "NATCAPSUQ", "category": "Variance",
                           "flags": ["duplicate_path"], "pnl_pct": -10.0,
                           "origin": LIVE_ORIGIN}],
               "non_strategy_positions": [],
               "error_profile": {"thesis": 0, "execution": 0, "variance": 1,
                                 "process_deviation": 0},
               "unearned_pnl": 0, "adjusted_pnl": -4500, "n": 1,
               "per_cluster": {"micro": {"n": 1, "pnl": -4500}},
               "standing_check_hits": []}
        # an outage trade is a Process Deviation regardless of P&L sign
        try:
            cmd_handoff(st, "trade_auditor", json.loads(json.dumps(aud)) |
                        {"trades": [dict(aud["trades"][0], flags=["outage_window"])]})
            raise AssertionError("accepted an outage trade as Variance")
        except SystemExit as e:
            assert "Process Deviation" in str(e), e
        # SC-004: a position the current strategy did not choose is reported,
        # never categorised. Both halves are checked.
        cohort = dict(aud["trades"][0], ticker="SAHYADRI", origin="rank-cohort")
        try:
            cmd_handoff(st, "trade_auditor", dict(aud, trades=[cohort]))
            raise AssertionError("categorised a non-strategy position")
        except SystemExit as e:
            assert "reported separately, not counted" in str(e), e
        try:
            cmd_handoff(st, "trade_auditor",
                        dict(aud, trades=[dict(cohort, category=None)]))
            raise AssertionError("accepted a non-strategy position nobody listed")
        except SystemExit as e:
            assert "missing from non_strategy_positions" in str(e), e
        cmd_handoff(st, "trade_auditor", aud)

        miner = {"batch_id": "20260910", "actionable_findings": [],
                 "shapes_monitored": [], "noise_discarded": [],
                 "dial_only_archived": [], "no_actionable_pattern": True,
                 "delta_vs_previous": None, "temporal_concentration_flags": []}
        good = {"id": "P1", "tag": "Finding", "n": 6, "t": -3.2,
                "improvement_type": "new_rule_shape"}
        for bad, needle in (
                (dict(good, improvement_type=None), "must name one of"),
                (dict(good, improvement_type="none"), "archive it in dial_only"),
                (dict(good, n=4), f"n >= {MIN_PATTERN_N}"),
                (dict(good, t=0.4), "that is a Shape"),
                (dict(good, tag="Shape"), "must be tagged 'Finding'")):
            try:
                cmd_handoff(st, "pattern_miner", dict(miner, actionable_findings=[bad],
                                                      no_actionable_pattern=False))
                raise AssertionError(f"accepted a finding that is {needle}")
            except SystemExit as e:
                assert needle in str(e), e
        # the two halves of the flag must agree with the list they describe
        try:
            cmd_handoff(st, "pattern_miner", dict(miner, actionable_findings=[good]))
            raise AssertionError("accepted no_actionable_pattern=true beside a finding")
        except SystemExit as e:
            assert "but 1 actionable findings" in str(e), e
        # a dial cannot be archived and passed at the same time
        try:
            cmd_handoff(st, "pattern_miner",
                        dict(miner, actionable_findings=[good],
                             no_actionable_pattern=False,
                             dial_only_archived=[{"id": "P1"}]))
            raise AssertionError("accepted a dial that also reached Agent 4")
        except SystemExit as e:
            assert "a dial does not reach Agent 4" in str(e), e
        cmd_handoff(st, "pattern_miner",
                    dict(miner, shapes_monitored=[{"id": "P1", "tag": "Shape", "n": 4}]))

        rule = {"rule_id": "R-001", "pattern_addressed": "P1",
                "rule_text": "skip an entry whose bar volatility exceeds 2x its "
                             "20-day average",
                "hypothesis": "h", "improvement_type": "new_rule_shape",
                "adoption_bar": {k: "stated" for k in ADOPTION_BAR_KEYS},
                "failure_mode": "f", "affected_trades_in_batch": [],
                "rank_slope_impact": "none", "confidence": "low",
                "rationale": "r"}
        prop = {"batch_id": "20260910", "decision": "rule", "rule": rule}
        for bad_rule, needle in (
                (dict(rule, improvement_type="hold_11_days"), "is a dial"),
                (dict(rule, adoption_bar="t > 2 eventually"), "bar in prose"),
                (dict(rule, adoption_bar={"primary_metric": "x"}),
                 "must be quantified before Agent 5"),
                (dict(rule, confidence="pretty sure"), "one of ('high'"),
                (dict(rule, rule_text="drop the circuit-lock guard in engine.py"),
                 "never searched"),
                (dict(rule, rule_text="read the score from strategies/sentiment"),
                 "cross-strategy import"),
                (dict(rule, rule_text="require a minimum score of 90"),
                 "invalid by construction"),
                (dict(rule, rule_text="skip a name whose order exceeds 2% of ADV"),
                 "participation cap")):
            try:
                cmd_handoff(st, "rule_proposer", dict(prop, rule=bad_rule))
                raise AssertionError(f"accepted {needle}")
            except SystemExit as e:
                assert needle in str(e), e

        cmd_handoff(st, "rule_proposer", prop)
        assert len(st["rule_registry"]) == 1 and st["revisions"] == 0
        # a re-submission REVISES; it does not add a second rule
        cmd_handoff(st, "rule_proposer",
                    dict(prop, rule=dict(rule, hypothesis="h2")))
        assert len(st["rule_registry"]) == 1, "a revision added a second rule"
        assert st["revisions"] == 1
        # a DIFFERENT rule alongside it is barred outright
        try:
            cmd_handoff(st, "rule_proposer",
                        dict(prop, rule=dict(rule, rule_id="R-002")))
            raise AssertionError("accepted a second distinct rule in one cycle")
        except SystemExit as e:
            assert "maximum 1 rule change per cycle" in str(e), e
        cmd_handoff(st, "rule_proposer", dict(prop, rule=dict(rule, hypothesis="h3")))
        try:
            cmd_handoff(st, "rule_proposer", dict(prop, rule=dict(rule, hypothesis="h4")))
            raise AssertionError("accepted a third revision")
        except SystemExit as e:
            assert "maximum is 2" in str(e), e

        # the validator must prove it read the baseline file, and must report a
        # sensitivity rather than one impact number
        # The pre-registration is WRITTEN from the registry, so the hypothesis
        # and the bar in the file cannot differ from the ones Agent 4 registered.
        rp = cmd_new_research(st, "R-001", "20260910-agent5-R001")
        src_text = rp.read_text()
        # The PLACEHOLDER shape, not a bare "@@" -- the generated file carries
        # "@@" inside its own selftest, which is what asserts they are gone.
        assert not re.search(PLACEHOLDER, src_text), \
            f"placeholders survived: {sorted(set(re.findall(PLACEHOLDER, src_text)))}"
        assert rule["hypothesis"] in src_text and \
            rule["adoption_bar"]["minimum_effect"] in src_text, \
            "the file does not carry the registered hypothesis and bar"
        try:
            cmd_new_research(st, "R-001", "20260910-agent5-R001")
            raise AssertionError("overwrote an existing pre-registration")
        except SystemExit as e:
            assert "already exists" in str(e), e

        val = {"batch_id": "20260910", "rule_id": "R-001",
               "research_file": str(rp),
               "batch_tag": "20260910-agent5-R001", "verdict": "INCONCLUSIVE",
               "baseline_read_from": "data/breakout/baseline.json",
               "baseline_value": {"cagr": 2.18, "maxdd": 32.5, "n": 194,
                                  "per_trade": 1.07},
               "with_rule": {"cagr": 2.9, "maxdd": 31.0, "n": 41,
                             "per_trade": 1.4, "t": 0.4},
               "effect": {"size": 0.33, "std_err": 0.9, "t": 0.4},
               "adoption_bar_met": False,
               "impact_sensitivity": [{"c": c, "cagr": 2.0, "per_trade": 1.0,
                                       "n": 41} for c in C_GRID],
               "rank_slope": {"baseline": _FROZEN_SLOPE, "with_rule": -1.10,
                              "delta": 0.02, "pass": True, "n": 1062},
               "affected_trades": [], "output_inspection": "clean",
               "forward_paper_trade_required": True}
        for bad, needle in (
                (dict(val, verdict="probably"), "expected one of"),
                (dict(val, impact_sensitivity=val["impact_sensitivity"][:2]),
                 "one number is not a result"),
                (dict(val, baseline_value={"cagr": 7.59}), "never a figure quoted"),
                (dict(val, forward_paper_trade_required=False),
                 "only thing that shrinks the error bar"),
                (dict(val, output_inspection=""), "OUTPUT is clean"),
                (dict(val, research_file="src/research/agent_NOPE.py"),
                 "pre_registration_missing"),
                (dict(val, batch_tag="20260910-something-else"),
                 "must share a batch tag"),
                (dict(val, verdict="FAIL", rank_slope=dict(val["rank_slope"],
                      **{"pass": False}), forward_paper_trade_required=False,
                      adoption_bar_met=False) | {"verdict": "PASS"},
                 "one surviving signal")):
            try:
                cmd_handoff(st, "backtest_validator", bad)
                raise AssertionError(f"accepted: {needle}")
            except SystemExit as e:
                assert needle in str(e), e
        # PASS carries its own burden: the bar met, the slope intact, |t| > 2,
        # n >= 30, and still profitable at the top of the impact sensitivity.
        ok = dict(val, verdict="PASS", adoption_bar_met=True,
                  forward_paper_trade_required=False,
                  effect={"size": 3.4, "std_err": 1.1, "t": 3.1})
        for bad, needle in (
                (dict(ok, effect={"size": 1.0, "std_err": 1.1, "t": 0.9}),
                 "less than its standard error"),
                (dict(ok, with_rule=dict(ok["with_rule"], n=12)),
                 f"minimum sample is {MIN_AFFECTED_N}"),
                (dict(ok, impact_sensitivity=[
                    dict(r, per_trade=(-1.0 if r["c"] == C_PROFITABLE_AT else 1.0))
                    for r in ok["impact_sensitivity"]]),
                 "across the sensitivity")):
            try:
                cmd_handoff(st, "backtest_validator", bad)
                raise AssertionError(f"accepted: {needle}")
            except SystemExit as e:
                assert needle in str(e), e
        cmd_handoff(st, "backtest_validator", val)

        # The verdict now ROUTES. This branch used to read the verdict into a
        # body of `pass` -- it looked like a check and was a no-op, and three
        # agents reported it independently before it was fixed.
        hv = st["handoff"]["backtest_validator"]
        for verdict, stage_, needle in (
                ("FAIL", "performance_tracker", "not routed to"),
                ("FAIL", "forward_manager", "not routed to"),
                ("PASS", "forward_manager", "Only INCONCLUSIVE routes")):
            hv["verdict"] = verdict
            assert any(needle in w for w in gate(st, stage_)), \
                f"{verdict} still routed onward to {stage_}"
        hv["verdict"] = "INCONCLUSIVE"
        # Neither stage may run on an empty book: nothing applied, nothing queued.
        assert any("nothing to monitor" in w
                   for w in gate(st, "performance_tracker")), \
            "the tracker ran with no applied rule"

        # The validator stamped INCONCLUSIVE on R-001, so `validated` is barred
        # and `paper_trade` is the only evidence-backed move.
        reg = [r for r in st["rule_registry"] if r["rule_id"] == "R-001"][0]
        assert reg.get("validator_verdict") == "INCONCLUSIVE", \
            "the validator's verdict never reached the rule"
        try:
            cmd_rule(st, "R-001", "validated")
            raise AssertionError("validated a rule the validator did not pass")
        except SystemExit as e:
            assert "not PASS" in str(e), e
        cmd_rule(st, "R-001", "paper_trade")
        assert not gate(st, "forward_manager"), \
            "a queued paper trade must reach Agent 7"
        cmd_rule(st, "R-001", "applied")        # promoted on forward evidence
        assert not gate(st, "performance_tracker"), \
            "an applied rule must reach Agent 6"
        try:
            cmd_rule(st, "R-001", "rejected")
            raise AssertionError("an applied rule left by a route other than rollback")
        except SystemExit as e:
            assert "not a legal transition" in str(e), e

        # Agent 6 cannot reach a verdict below its floor...
        trk = {"batch_id": "20260910", "rule_id": "R-001",
               "trades_since_application": 4, "rule_triggered_count": 2,
               "actual_effect": "n=2, inside the noise", "predicted_effect": "+1.5%",
               "within_tolerance": True, "failure_mode_triggered": False,
               "impact_tail": {"live_pct": "0%", "backtest_pct": "3.1%",
                               "match": True},
               "rank_slope": {"current": -1.10, "baseline": _FROZEN_SLOPE,
                              "delta": 0.02, "pass": True, "n": 1062},
               "verdict": "INCONCLUSIVE", "rollback_recommended": False,
               "next_review": "after 8 more triggered trades",
               "feedback_for_pattern_miner": "R-001: inconclusive at n=2."}
        try:
            cmd_handoff(st, "performance_tracker", dict(trk, verdict="CONFIRM"))
            raise AssertionError("accepted a verdict below the sample floor")
        except SystemExit as e:
            assert "always INCONCLUSIVE" in str(e), e
        cmd_handoff(st, "performance_tracker", trk)

        # ...and Agent 7 cannot loosen a bar that was set before the run.
        fwd = {"batch_id": "20260910",
               "queue": [{"rule_id": "R-001", "status": "active",
                          "adoption_bar": rule["adoption_bar"],
                          "trades_triggered": 2, "trades_total": 4,
                          "paper_pnl": 0, "n": 2}],
               "active_count": 1, "promotions_this_cycle": [],
               "rejections_this_cycle": [], "expirations_this_cycle": [],
               "next_reviews": []}
        loose = json.loads(json.dumps(fwd))
        loose["queue"][0]["adoption_bar"]["minimum_sample"] = "whatever we get"
        try:
            cmd_handoff(st, "forward_manager", loose)
            raise AssertionError("accepted a loosened adoption bar")
        except SystemExit as e:
            assert "tightened, never loosened" in str(e), e
        try:
            cmd_handoff(st, "forward_manager", dict(fwd, active_count=3))
            raise AssertionError("accepted an active_count that miscounts its own queue")
        except SystemExit as e:
            assert "entries are active" in str(e), e
        cmd_handoff(st, "forward_manager", fwd)
        assert len(st["paper_trade_queue"]) == 1, "the queue did not reach state"

        # a cycle nobody summarised cannot be closed
        try:
            cmd_close(st)
            raise AssertionError("closed with no summary")
        except SystemExit as e:
            assert "summary" in str(e), e
        summary = {"batch_id": "20260910", "date": "2026-09-10",
                   "trades_processed": 1, "independent_paths": 1,
                   "error_profile": aud["error_profile"], "unearned_pnl": 0,
                   "adjusted_pnl": -4500, "n": 1, "patterns_found": ["P1"],
                   "pattern_tags": {"P1": "Shape"}, "rule_proposed": "R-001",
                   "rule_status": "paper_trade", "standing_checks_active": [],
                   "standing_checks_added": [], "standing_checks_retired": [],
                   "delta_vs_previous_batch": "first cycle",
                   "next_step": "queue R-001 for forward evidence"}
        cmd_close(st, summary)
        assert len(st["batch_history"]) == 1 and not st["open"]

        # a stale handoff does not satisfy the next cycle's gate
        cmd_open(st, "20260917")
        assert st["cycle"] == 2 and st["revisions"] == 0
        assert any("stale" in w or "not run" in w
                   for w in gate(st, "trade_auditor"))
        # ...and a rejected rule_text cannot come back
        cmd_rule(st, "R-001", "rolled_back", "forward evidence went the other way")
        cmd_handoff(st, "orchestrator",
                    dict(orch, batch_id="20260917",
                         rule_registry_size=len(st["rule_registry"])))
        cmd_handoff(st, "data_steward", dict(base, batch_id="20260917"))
        cmd_handoff(st, "trade_auditor", dict(aud, batch_id="20260917"))
        cmd_handoff(st, "pattern_miner", dict(miner, batch_id="20260917"))
        try:
            cmd_handoff(st, "rule_proposer",
                        {"batch_id": "20260917", "decision": "rule",
                         "rule": dict(rule, rule_id="R-002")})
            raise AssertionError("accepted a rule_text already rejected")
        except SystemExit as e:
            assert "already rolled_back" in str(e), e
        # ...and returning nothing is a complete, successful result
        cmd_handoff(st, "rule_proposer", {"batch_id": "20260917",
                                          "decision": "no_valid_improvement"})

        again = load()
        assert again["cycle"] == 2 and len(again["rule_registry"]) == 1, \
            "state did not round-trip through disk"

        # Every stage must have its subagent definition on disk. A renamed or
        # missing file leaves the skill dispatching a subagent that does not
        # exist, and the failure lands where nobody reads it -- exactly the
        # failure agent.py._selftest was built to catch when the scripts moved.
        adir = paths.ROOT / ".claude" / "agents"
        missing = [s["agent"] for s in STAGES.values()
                   if not (adir / f"{s['agent']}.md").exists()]
        assert not missing, ("the pipeline is not runnable -- no definition for: "
                             + ", ".join(missing))
        known = {s["agent"] for s in STAGES.values()}
        orphan = [p.name for p in adir.glob("agent-*.md") if p.stem not in known]
        assert not orphan, f"agent files matching no stage: {orphan}"
        # .claude/ is gitignored on purpose; scripts/claude/ is the reviewable
        # source and one cp installs it. So the two must not drift -- this repo
        # has already held a settings.json contradicting the one in force, which
        # is the same bug with a different filename.
        sdir = paths.ROOT / "scripts" / "claude" / "agents"
        for s in STAGES.values():
            src, inst = sdir / f"{s['agent']}.md", adir / f"{s['agent']}.md"
            assert src.exists(), (
                f"{s['agent']} is installed but not staged in scripts/claude/"
                "agents/ -- it would not survive a fresh checkout")
            assert src.read_text() == inst.read_text(), (
                f"{s['agent']} differs between scripts/claude/agents/ and "
                ".claude/agents/: the installed copy has drifted from the "
                "reviewable source")

        # All three files must be redirected, not two. positions.py redirected
        # STATE and LEDGER but not DB, and its selftest wrote two fixture
        # positions into the live order book.
        assert STATE.parent == CURRENT.parent == RUNS.parent == td, \
            "the selftest is writing to a live pipeline file"
        assert RUNS.exists() and RUNS.read_text().count("\n") >= 4, \
            "the audit trail recorded nothing across a full cycle"
        rows = [json.loads(l) for l in RUNS.read_text().splitlines()]
        assert {r["action"] for r in rows} >= {"cycle_start", "done",
                                               "rejected", "cycle_complete"}, \
            f"the trail is missing transitions: {sorted({r['action'] for r in rows})}"
        cur = read_current()
        assert cur.get("last_heartbeat") and "cycle" in cur, \
            "the status board never got written"
        # Agent 5's own required work exceeds the default budget, so a single
        # threshold would flag normal operation as a hang.
        assert not REHEARSAL or os.environ.get("PIPELINE_DIR"), \
            "REHEARSAL is set without the env var that should be the only cause"
        assert set(AGENT_BUDGETS) == set(ORDER), (
            "every stage needs its own staleness budget, or it inherits one "
            f"nobody chose: missing {sorted(set(ORDER) - set(AGENT_BUDGETS))}")
        assert AGENT_BUDGETS["backtest_validator"] > AGENT_BUDGETS["data_steward"], (
            "the sealed validator runs two backtest arms and a five-point "
            "sensitivity; it cannot share the default staleness budget")
        assert push("unsent", False) is False, \
            "Telegram must be opt-in: a selftest cannot reach the operator"

        assert "book" in BANNED_WORDS, (                        # vocab-allow
            "rules.md R1: the canonical word is bucket")
        assert "CLAUDE.md" in LEGACY_VOCAB, \
            "legacy files are excluded by name with a reason, never silently"
        # Every agent prompt must name the banned words in order to ban them.
        # Without a visible marker the checker fails its own authors, which is
        # exactly what it did on the first run.
        vf = Path(tempfile.gettempdir()) / "pipeline_selftest_vocab.md"
        try:
            vf.write_text("never say portfolio\n"                # vocab-allow
                          f"never say portfolio {VOCAB_ALLOW}\n"  # vocab-allow
                          "the order book is append-only\n")
            assert cmd_vocab([str(vf)]) == 1, (
                "expected exactly the unmarked line to fail: the marker and "
                "the order book compound must both pass")
        finally:
            vf.unlink(missing_ok=True)
        print(f"pipeline selftest ok ({len(ORDER)} stages, "
              f"{len(IMPROVEMENT_KINDS) - 1} legal improvements, "
              f"{len(FLAGS)} flags)")
    finally:
        for f in (STATE, CURRENT, RUNS, research_path("R-001")):
            f.unlink(missing_ok=True)
        STATE, CURRENT, RUNS, RESEARCH_DIR = saved


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--selftest", action="store_true")
    p.add_argument("--status", action="store_true")
    p.add_argument("--current", action="store_true",
                   help="the live status board: who is running, on what")
    p.add_argument("--tg", action="store_true",
                   help="push to Telegram at cycle start, cycle end and any "
                        "gate failure. Off by default: sending is outward-facing")
    p.add_argument("--log-run", metavar="AGENT",
                   help="append one line to pipeline_runs.jsonl")
    p.add_argument("--action", default="note",
                   help="start | done | note, for --log-run")
    p.add_argument("--open-cycle", action="store_true")
    p.add_argument("--close-cycle", action="store_true")
    p.add_argument("--abort", metavar="REASON")
    p.add_argument("--batch", metavar="ID")
    p.add_argument("--note")
    p.add_argument("--gate", metavar="STAGE")
    p.add_argument("--handoff", metavar="STAGE")
    p.add_argument("--show", metavar="STAGE")
    p.add_argument("--file", metavar="PATH")
    p.add_argument("--json", metavar="TEXT")
    p.add_argument("--vocab", nargs="+", metavar="PATH")
    p.add_argument("--include-legacy", action="store_true")
    p.add_argument("--add-check", metavar="TEXT")
    p.add_argument("--retire-check", metavar="ID")
    p.add_argument("--why", default="", help="reason for --retire-check")
    p.add_argument("--rule", metavar="ID")
    p.add_argument("--new-research", metavar="RULE_ID",
                   help="write the pre-registered research file for a rule")
    p.add_argument("--set-status", metavar="STATUS")
    a = p.parse_args()

    if REHEARSAL and not a.selftest:
        print(f"  REHEARSAL: reading and writing {_DIR}, not "
              f"{paths.SDATA}. Nothing here reaches the live record.",
              file=sys.stderr)
    if a.selftest:
        return _selftest() or 0
    if a.vocab:
        return cmd_vocab(a.vocab, a.include_legacy)
    if a.current:
        return cmd_current()

    st = load()
    payload = None
    if a.file or a.json:
        payload = json.loads(Path(a.file).read_text() if a.file else a.json)

    if a.log_run:
        row = log_run(a.log_run, a.batch or st.get("batch_id"), a.action,
                      a.note or "")
        stage = ORDER[int(a.log_run)] if a.log_run.isdigit() and \
            int(a.log_run) < len(ORDER) else a.log_run
        write_current(stage=stage if not a.log_run.isdigit() else stage,
                      current_agent=f"Agent {a.log_run}: {stage}",
                      step=a.note or a.action,
                      waiting_on=None if a.action != "blocked" else a.note)
        print(f"  logged: {row['agent']} {row['action']} {row['note'][:60]}")
    elif a.open_cycle:
        cmd_open(st, a.batch, a.note, a.tg)
    elif a.close_cycle:
        cmd_close(st, payload, a.abort, a.tg)
    elif a.gate:
        why = gate(st, a.gate)
        print("\n".join("  " + w for w in why) if why else f"  {a.gate}: clear")
        return 1 if why else 0
    elif a.handoff:
        if payload is None:
            raise SystemExit("--handoff needs --file or --json")
        cmd_handoff(st, a.handoff, payload, a.note, a.tg)
    elif a.show:
        print(json.dumps(st["handoff"].get(a.show, {}), indent=1))
    elif a.add_check:
        cmd_add_check(st, a.add_check, a.batch or st.get("batch_id"))
    elif a.retire_check:
        cmd_retire_check(st, a.retire_check, a.why)
    elif a.new_research:
        cmd_new_research(st, a.new_research, a.batch)
    elif a.rule and a.set_status:
        cmd_rule(st, a.rule, a.set_status, a.note)
    else:
        cmd_status(st)
    return 0


if __name__ == "__main__":
    sys.exit(main())
