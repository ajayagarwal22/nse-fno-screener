#!/bin/bash
set -e
cd "$(dirname "$0")"

echo "Starting NSE F&O Screener..."
echo "Dashboard → http://localhost:9000"
echo "Press Ctrl+C to stop."
echo ""

# Keep Python cache in /tmp so iCloud Drive doesn't sync or lock it
export PYTHONPYCACHEPREFIX=/tmp/nse-fno-pycache
mkdir -p /tmp/nse-fno-pycache

/Library/Frameworks/Python.framework/Versions/3.13/bin/python3 -m uvicorn app.main:app --host 0.0.0.0 --port 9000
