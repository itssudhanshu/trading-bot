#!/bin/sh
# Bootstrap on a fresh machine. Idempotent -- safe to re-run.
#
#   git clone https://github.com/itssudhanshu/trading-bot.git && cd trading-bot && ./setup.sh
#
# No pip install: the system is stdlib-only by design. The only hard
# requirements are Python >= 3.9 and outbound HTTPS to nsearchives.nseindia.com.
set -eu
cd "$(dirname "$0")"
PY="${PYTHON:-python3}"

echo "== python =="
$PY - <<'PYCHK'
import sys
if sys.version_info < (3, 9):
    sys.exit(f"need Python >= 3.9 (str.removesuffix, NormalDist); found {sys.version.split()[0]}")
print(f"  ok: {sys.version.split()[0]}")
PYCHK

echo "== TLS certificates =="
# The macOS python.org installer ships without a CA bundle: urllib fails with
# CERTIFICATE_VERIFY_FAILED while curl works, because curl uses the system store.
# Exercise snapshot.fetch itself, not a bare urlopen: NSE rejects requests
# without a browser User-Agent, so a naive check fails on a working install and
# sends you chasing a certificate problem you do not have.
if ! $PY -c "
import sys, snapshot
status, body = snapshot.fetch('https://nsearchives.nseindia.com/content/fo/fo_secban.csv')
sys.exit(0 if status == 200 and body else 1)
" 2>/dev/null; then
  echo "  FAILED to reach NSE over HTTPS."
  echo "  macOS python.org build: run '/Applications/Python 3.x/Install Certificates.command'"
  echo "  Linux: ensure ca-certificates is installed."
  exit 1
fi
echo "  ok"

echo "== selftests =="
fail=0
for f in *.py; do
  case "$f" in setup*|psearch.py) continue;; esac
  if $PY "$f" --selftest >/dev/null 2>&1; then
    printf "  ok   %s\n" "$f"
  else
    printf "  FAIL %s\n" "$f"; fail=1
  fi
done
[ "$fail" -eq 0 ] || { echo "selftests failed; not proceeding"; exit 1; }

echo "== history =="
# Bhavcopy is fully refetchable, so history does not need transferring between
# machines. Surveillance state (ASM/GSM/F&O ban) is NOT -- NSE publishes today
# only. Days collected on the other machine cannot be reconstructed here.
have=$(find data/raw -name bhavcopy_delivery.csv 2>/dev/null | wc -l | tr -d ' ')
echo "  have $have trading days"
if [ "$have" -lt 100 ]; then
  echo "  backfilling to the archive floor (2019-10-01)..."
  $PY backfill.py --from 2019-10-01
fi

echo "== today's snapshot =="
$PY snapshot.py || echo "  (non-trading day or partial; --catchup will retry)"

cat <<'NOTE'

== scheduling ==
  macOS : cp deploy/com.sudhanshu.tradingbot.*.plist ~/Library/LaunchAgents/
          launchctl load ~/Library/LaunchAgents/com.sudhanshu.tradingbot.daily.plist
          (edit the absolute paths inside first)
  Linux : mkdir -p ~/.config/systemd/user
          cp deploy/trading-bot-*.service deploy/trading-bot-*.timer ~/.config/systemd/user/
          systemctl --user daemon-reload
          systemctl --user enable --now trading-bot-daily.timer
          loginctl enable-linger "$USER"

  Use launchd/systemd, NOT cron: both re-run a job missed while the machine was
  asleep or off. cron silently skips it, and a missed session is a permanent
  hole in the surveillance record.

== BEFORE running searches on a second machine ==
  The holdout budget lives in data/judge_ledger*.json. Two machines searching
  with separate ledgers means two independent budgets against the SAME holdout
  -- which silently doubles the number of hypotheses tested and destroys the
  only defence against overfitting this project has.

  Either: keep searching on ONE machine, or commit and pull the ledger between
  runs so the count stays shared. Data collection is safe to run on both --
  it is idempotent and content-verified.
NOTE
