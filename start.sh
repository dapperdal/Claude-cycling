#!/bin/bash
# Zone 2 Cycling App Launcher
# Kills any existing instance and starts fresh

cd "$(dirname "$0")"

echo "Stopping any existing instances..."
pkill -f "python app.py" 2>/dev/null
sleep 1

echo "Starting Zone 2 Cycling App..."
source venv/bin/activate
python app.py &

sleep 3

echo "Opening browser..."
open http://localhost:8080

echo ""
echo "App is running at http://localhost:8080"
echo "Press Ctrl+C to stop, or run ./stop.sh"
