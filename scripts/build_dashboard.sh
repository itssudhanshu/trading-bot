#!/bin/bash
# Rebuild the static dashboard: export a fresh snapshot from disk, then build.
# The snapshot is the only bridge between the repo's data and the UI; if this
# fails, the dashboard must not be shipped with yesterday's numbers.
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
