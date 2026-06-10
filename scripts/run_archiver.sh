#!/bin/bash
# Runs GitHub Archiver on macOS
# Launches Chrome with CDP port 9222 and runs the archiver

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

CHROME_PATH="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
USER_DATA_DIR="$HOME/Library/Application Support/Google/Chrome Debug"

echo "Launching Chrome with remote debugger (port 9222)..."

if [ ! -f "$CHROME_PATH" ]; then
    echo "[ERROR] Chrome not found at: $CHROME_PATH"
    echo "Install Google Chrome or update CHROME_PATH in this script"
    exit 1
fi

# Launch Chrome in background
"$CHROME_PATH" --remote-debugging-port=9222 --user-data-dir="$USER_DATA_DIR" >/dev/null 2>&1 &

echo "Waiting for Chrome to be ready on port 9222..."
ATTEMPTS=0
MAX_ATTEMPTS=30

while [ $ATTEMPTS -lt $MAX_ATTEMPTS ]; do
    if nc -z localhost 9222 2>/dev/null; then
        echo "Chrome is ready on port 9222 (${ATTEMPTS}s). Starting archiver..."
        cd "$PROJECT_ROOT"
        python github_archiver.py
        exit 0
    fi
    sleep 1
    ATTEMPTS=$((ATTEMPTS + 1))
done

echo "[ERROR] Chrome did not start in time (${MAX_ATTEMPTS}s timeout)"
exit 1