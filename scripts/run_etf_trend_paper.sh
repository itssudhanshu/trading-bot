#!/bin/sh
# The etf_trend book's paper bucket, run by hand. Same entry point launchd uses.
cd "$(dirname "$0")/.."
STRATEGY=etf_trend exec python3 src/strategies/etf_trend/paper.py "${@:---update}"
