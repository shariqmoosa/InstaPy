web: Xvfb :99 -screen 0 1366x768x24 -ac +extension GLX +render -noreset & export DISPLAY=:99 && uvicorn shopping_agent.api:app --host 0.0.0.0 --port ${PORT:-8000}
