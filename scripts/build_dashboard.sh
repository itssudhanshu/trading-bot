#!/bin/bash
# Rebuild the static dashboard: export a fresh snapshot from disk, then build.
# The dashboard prefers live API /snapshot (Gold) at runtime and falls back to
# this static snapshot.json for `npm run preview` / Playwright e2e without a server.
set -euo pipefail
cd "$(dirname "$0")/.."

python3 src/ops/dashboard_export.py
cd dashboard
if [ ! -d node_modules ]; then
    npm install
fi
npm run build
echo "dashboard built -> dashboard/dist/"
echo "serve it with:  npm run preview   (in dashboard/)"
