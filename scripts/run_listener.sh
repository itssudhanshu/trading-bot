#!/bin/sh
# Keeps the Telegram listener alive, restarting it when tg.py changes or it
# dies. launchd/systemd do this natively; this is for running by hand.
cd "$(dirname "$0")"
while true; do
  python3 src/ops/tg.py --listen
  echo "listener exited, restarting in 3s"
  sleep 3
done
