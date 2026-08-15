#!/bin/zsh
# Weekly research pass. Search, validate, aggregate -- then stop.
#
# The holdout is NOT consulted here. Budget is 50 for the lifetime of the
# project; spending it must be a deliberate act, not something a cron job does
# while you sleep. This job's output is a shortlist and a report.
set -u
cd "$(dirname "$0")"
PY=/usr/local/bin/python3
STAMP=$(date +%Y-%m-%d)
REPORT="data/report_${STAMP}.txt"

echo "=== weekly pass ${STAMP} ==="
$PY snapshot.py --catchup

# New seed each week: re-running one seed forever re-tests identical hypotheses
# and explores nothing. The seed is recorded so any result can be reproduced.
SEED=$(date +%Y%V)
echo "--- search (seed ${SEED}) ---"
$PY generator.py -n 200 --seed "$SEED"

echo "--- walk-forward ---"
$PY validate.py | tee -a "$REPORT"

echo "--- aggregate ---"
$PY postmortem.py | tee -a "$REPORT"

PROMOTED=$(wc -l < data/promoted.jsonl 2>/dev/null | tr -d ' ')
echo
echo "promoted this pass: ${PROMOTED:-0}"
echo "holdout budget: $($PY judge.py --status)"
echo "report: ${REPORT}"
echo
echo "To spend a consultation on a promoted spec (deliberate, irreversible):"
echo "  python3 judge.py <spec.json> <result.json>"
