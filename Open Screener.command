#!/bin/bash
cd "$(dirname "$0")"

echo "=============================="
echo "  NSE F&O Screener"
echo "=============================="
echo ""

# Step 1: Refresh Kite token
echo "Step 1/2: Refreshing Kite token..."
echo "Your browser will open — log in to Zerodha, then come back here."
echo ""
python3 kite_auth.py

echo ""
echo "Token saved. Starting server..."
echo ""

# Step 2: Kill any existing instance
pkill -f "uvicorn app.main:app" 2>/dev/null
sleep 1

# Step 3: Start server
uvicorn app.main:app --host 0.0.0.0 --port 8001 &

# Step 4: Open browser once server is ready
sleep 3
open http://localhost:8001

echo ""
echo "Dashboard open at http://localhost:8001"
echo "Press Ctrl+C to stop the server."
echo ""

# Keep terminal open so server keeps running
wait
