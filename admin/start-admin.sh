#!/bin/bash
# NSE F&O Screener — Admin Panel
# Runs on port 9001 (completely separate from the main screener on 9000)
# Usage: ./admin/start-admin.sh

PROJECT="/Users/ajayagarwal/Desktop/Stocks project/nse-fno-screener"
export PYTHONPATH="$PROJECT"
export PYTHONPYCACHEPREFIX=/tmp/nse-fno-pycache
export HOME=/Users/ajayagarwal

cd /tmp

echo "Starting Admin Panel at http://localhost:9001 ..."
open "http://localhost:9001" &

exec /Library/Frameworks/Python.framework/Versions/3.13/bin/python3 \
    -m uvicorn admin.main:app \
    --host 0.0.0.0 \
    --port 9001 \
    --reload \
    --app-dir "$PROJECT"
