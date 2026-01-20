#!/bin/bash
# Zone 2 Cycling App Stopper

echo "Stopping Zone 2 Cycling App..."
pkill -f "python app.py" 2>/dev/null

if [ $? -eq 0 ]; then
    echo "App stopped successfully."
else
    echo "No running instance found."
fi
