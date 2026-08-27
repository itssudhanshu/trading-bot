#!/bin/bash
# Rebuild the static dashboard: build the React app that fetches from API at runtime.
# The API (dashboard_api.py) serves live data from Gold; no snapshot export needed.
set -euo pipefail
cd "$(dirname "$0")/.."

cd dashboard
if [ ! -d node_modules ]; then
    npm install
fi
npm run build
echo "dashboard built -> dashboard/dist/"
echo "serve it with:  npm run preview   (in dashboard/)"
