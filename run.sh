#!/usr/bin/env bash
# run.sh — start Whisper Studio
set -e

if [ -f .env ]; then
  export $(grep -v '^#' .env | xargs)
fi

if [ -z "$WHISPER_BIN" ] || [ ! -f "$WHISPER_BIN" ]; then
  echo "⚠  WHISPER_BIN not set or binary not found. Run ./setup.sh first."
  exit 1
fi

if [ -z "$WHISPER_MODEL" ] || [ ! -f "$WHISPER_MODEL" ]; then
  echo "⚠  WHISPER_MODEL not set or model file not found. Run ./setup.sh first."
  exit 1
fi

echo "▶ Starting Whisper Studio on http://localhost:5000"
echo "  Binary : $WHISPER_BIN"
echo "  Model  : $WHISPER_MODEL"
echo ""

uv run app.py &
SERVER_PID=$!

# Open browser
sleep 2
open "http://127.0.0.1:5343/"

# Wait for server; now Ctrl+C stops the server
trap "echo 'Stopping server...'; kill $SERVER_PID; exit" SIGINT
wait $SERVER_PID
