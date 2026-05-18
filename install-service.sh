#!/bin/bash
# Installs the NSE F&O Screener as a macOS LaunchAgent so it starts automatically
# on login and restarts if it crashes. After installation, open http://localhost:9000.

set -e
PLIST_SRC="$(cd "$(dirname "$0")" && pwd)/com.nse.screener.plist"
PLIST_DEST="$HOME/Library/LaunchAgents/com.nse.screener.plist"

echo "Installing NSE F&O Screener as a background service..."

# Stop any existing instance
launchctl unload "$PLIST_DEST" 2>/dev/null || true

# Kill anything already on port 9000
lsof -ti:9000 | xargs kill -9 2>/dev/null || true

# Install the plist
cp "$PLIST_SRC" "$PLIST_DEST"
launchctl load "$PLIST_DEST"

echo ""
echo "Done. The screener is now running and will auto-start on every login."
echo ""
echo "  Dashboard  → http://localhost:9000"
echo "  Logs       → tail -f /tmp/nse-screener.log"
echo ""
echo "To stop:      launchctl unload ~/Library/LaunchAgents/com.nse.screener.plist"
echo "To uninstall: launchctl unload ~/Library/LaunchAgents/com.nse.screener.plist && rm ~/Library/LaunchAgents/com.nse.screener.plist"

# Open dashboard in browser after a short delay to let the server start
sleep 3 && open http://localhost:9000 &
