#!/bin/sh
set -e

# Start Xvfb and wait until it's accepting connections
Xvfb :99 -screen 0 1366x768x24 -ac +extension GLX +render -noreset &
XVFB_PID=$!

echo "Waiting for Xvfb to be ready..."
for i in $(seq 1 20); do
    if xdpyinfo -display :99 >/dev/null 2>&1; then
        echo "Xvfb is ready."
        break
    fi
    sleep 0.5
done

export DISPLAY=:99

exec uvicorn shopping_agent.api:app --host 0.0.0.0 --port "${PORT:-8000}"
