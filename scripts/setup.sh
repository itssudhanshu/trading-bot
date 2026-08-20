#!/bin/sh
# Bootstrap on a fresh machine. Idempotent -- safe to re-run.
#
#   git clone https://github.com/itssudhanshu/trading-bot.git && cd trading-bot && ./scripts/setup.sh
#
# No pip install: the system is stdlib-only by design. The only hard
# requirements are Python >= 3.9 and outbound HTTPS to nsearchives.nseindia.com.
set -eu
cd "$(dirname "$0")/.."      # repo root: every path below is relative to it
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
import sys
sys.path.insert(0, 'src')
import paths      # puts src/core, src/ops, ... on sys.path
import snapshot
status, body = snapshot.fetch('https://nsearchives.nseindia.com/content/fo/fo_secban.csv')
sys.exit(0 if status == 200 and body else 1)
"; then
  # stderr is NOT swallowed. It used to be, and that is precisely how a broken
  # `import snapshot` -- the module moved under src/ops and this line was never
  # updated -- came out as "FAILED to reach NSE over HTTPS", sending the reader
  # to the certificate advice below for a problem they did not have. The comment
  # above warned about that exact misdiagnosis while the redirect caused it.
  echo "  FAILED to reach NSE over HTTPS (the error above says why)."
  echo "  macOS python.org build: run '/Applications/Python 3.x/Install Certificates.command'"
  echo "  Linux: ensure ca-certificates is installed."
  exit 1
fi
echo "  ok"

echo "== selftests =="
# One command, and it DISCOVERS the module list. The loop that used to be here
# globbed *.py in the script's own directory, which after the src/ move held
# none -- so it iterated nothing and printed no failures, which reads exactly
# like every test passing.
$PY tests/run_selftests.py || { echo "selftests failed; not proceeding"; exit 1; }

echo "== history =="
# Bhavcopy is fully refetchable, so history does not need transferring between
# machines. Surveillance state (ASM/GSM/F&O ban) is NOT -- NSE publishes today
# only. Days collected on the other machine cannot be reconstructed here.
have=$(find data/raw -name bhavcopy_delivery.csv 2>/dev/null | wc -l | tr -d ' ')
echo "  have $have trading days"
if [ "$have" -lt 100 ]; then
  echo "  backfilling to the archive floor (2019-10-01)..."
  $PY src/ops/backfill.py --from 2019-10-01
fi

echo "== today's snapshot =="
$PY src/ops/snapshot.py || echo "  (non-trading day or partial; --catchup will retry)"

cat <<'NOTE'

== scheduling ==
  Install each plist under its LABEL, not its repo filename. launchd finds a job
  by Label, so trading-bot-agent.plist loads by path and then answers to nothing:
  launchctl list shows no job and /health correctly says nothing is scheduled.

  macOS : cp scripts/deploy/trading-bot-agent.plist    ~/Library/LaunchAgents/com.sudhanshu.tradingbot.agent.plist
          cp scripts/deploy/trading-bot-telegram.plist ~/Library/LaunchAgents/com.sudhanshu.tradingbot.telegram.plist
          launchctl load ~/Library/LaunchAgents/com.sudhanshu.tradingbot.agent.plist
          launchctl load ~/Library/LaunchAgents/com.sudhanshu.tradingbot.telegram.plist
          (edit the absolute paths inside first -- they are this checkout's)
  Linux : mkdir -p ~/.config/systemd/user
          cp scripts/deploy/trading-bot-agent.service scripts/deploy/trading-bot-agent.timer ~/.config/systemd/user/
          systemctl --user daemon-reload
          systemctl --user enable --now trading-bot-agent.timer
          loginctl enable-linger "$USER"

  src/ops/agent.py is the only job that needs scheduling; it works out what is
  due and runs it. The Telegram listener is the second job and is KeepAlive.

  Use launchd/systemd, NOT cron: both re-run a job missed while the machine was
  asleep or off. cron silently skips it, and a missed session is a permanent
  hole in the point-in-time record.

== data on a second machine ==
  Bhavcopy is fully refetchable, so history needs no transferring. Surveillance
  state (ASM/GSM/F&O ban) is NOT -- NSE publishes today only, so a day nobody
  collected is a permanent hole. Collection is idempotent and content-verified,
  so running it on both machines is safe and is the point.

  data/sprout/ is different: strategies.jsonl and trade_features.jsonl are
  APPEND-ONLY and a mixed ledger cannot be un-mixed. One machine writes the
  bucket's record.
NOTE
