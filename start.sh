#!/bin/bash
set -e
cd "$(dirname "$0")"

echo "Starting NSE F&O Screener..."
echo "Dashboard → http://localhost:8000"
echo "Press Ctrl+C to stop."
echo ""

uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload \
  --reload-exclude "*.log" \
  --reload-exclude "*.csv" \
  --reload-exclude "*.db" \
  --reload-exclude "*.db-wal" \
  --reload-exclude "*.db-shm"
