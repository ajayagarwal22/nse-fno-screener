#!/bin/bash
# Startup wrapper (lives in ~/Library/Scripts — never iCloud-synced).
# Copies iCloud-hosted dashboard.html to /tmp so Python avoids the
# CloudDocs EDEADLK lock, then starts uvicorn.

PROJECT="/Users/ajayagarwal/Desktop/Stocks project/nse-fno-screener"
STAGE=/tmp/nse-screener-static
SRC="$PROJECT/app/dashboard.html"

mkdir -p "$STAGE" /tmp/nse-fno-pycache

# Retry copying the HTML until iCloud releases its lock (up to ~60 s)
for i in $(seq 1 30); do
    if cp "$SRC" "$STAGE/dashboard.html" 2>/dev/null; then
        break
    fi
    sleep 2
done

export PYTHONPYCACHEPREFIX=/tmp/nse-fno-pycache
export DASHBOARD_HTML_PATH="$STAGE/dashboard.html"
export HOME=/Users/ajayagarwal

export PYTHONPATH="$PROJECT"

# Run from /tmp — avoids launchd needing to chdir into iCloud Drive
cd /tmp
exec /Library/Frameworks/Python.framework/Versions/3.13/bin/python3 \
    -m uvicorn app.main:app --host 0.0.0.0 --port 9000
