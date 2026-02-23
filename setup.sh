#!/usr/bin/env bash
# setup.sh — one-shot setup for Whisper Studio on macOS (Metal/GPU + Ollama)
set -e

BOLD="\033[1m"
GREEN="\033[32m"
YELLOW="\033[33m"
RED="\033[31m"
RESET="\033[0m"

info() { echo -e "${GREEN}▶${RESET} $*"; }
warn() { echo -e "${YELLOW}⚠${RESET} $*"; }
err() {
  echo -e "${RED}✗ $*${RESET}"
  exit 1
}
section() { echo -e "\n${BOLD}── $* ──${RESET}"; }

# Model used for summarize / grammar fix — fast and high quality on M-series
OLLAMA_MODEL="qwen2.5:14b-instruct-q4_K_M"

# ── 0. Prereq checks ──────────────────────────────────────────────────────────
section "Checking prerequisites"

for cmd in git cmake ffmpeg python3; do
  if command -v "$cmd" &>/dev/null; then
    info "$cmd  $(command -v $cmd)"
  else
    case $cmd in
    cmake | ffmpeg) err "$cmd not found — install with: brew install $cmd" ;;
    git) err "git not found — run: xcode-select --install" ;;
    python3) err "python3 not found — install with: brew install python" ;;
    esac
  fi
done

# ── 1. Init git repo if needed ────────────────────────────────────────────────
section "Git repo"
if [ ! -d .git ]; then
  git init
  info "Initialised new git repo"
else
  info "Existing repo detected"
fi

# ── 2. whisper.cpp submodule ──────────────────────────────────────────────────
section "whisper.cpp submodule"
if [ -f vendor/whisper.cpp/CMakeLists.txt ]; then
  info "Submodule already present"
else
  if grep -q "whisper.cpp" .gitmodules 2>/dev/null; then
    info "Submodule registered — pulling…"
    git submodule update --init --recursive
  else
    git submodule add https://github.com/ggerganov/whisper.cpp.git vendor/whisper.cpp
    info "Submodule added at vendor/whisper.cpp"
  fi
fi

# ── 3. Build whisper.cpp (Metal, no CoreML) ───────────────────────────────────
section "Building whisper.cpp with Metal"
cd vendor/whisper.cpp

WHISPER_CLI="build/bin/whisper-cli"
if [ -f "$WHISPER_CLI" ]; then
  info "Binary already built — skipping"
else
  cmake -B build \
    -DGGML_METAL=ON \
    -DCMAKE_BUILD_TYPE=Release

  cmake --build build --config Release -j$(sysctl -n hw.logicalcpu)
  info "Build complete → vendor/whisper.cpp/$WHISPER_CLI"
fi

cd ../..

# ── 4. Download Whisper model ─────────────────────────────────────────────────
section "Whisper model"
MODEL_NAME="${WHISPER_MODEL_NAME:-medium.en}"
MODEL_FILE="vendor/whisper.cpp/models/ggml-${MODEL_NAME}.bin"

if [ -f "$MODEL_FILE" ]; then
  info "Model already present — skipping"
else
  info "Downloading $MODEL_NAME…"
  bash vendor/whisper.cpp/models/download-ggml-model.sh "$MODEL_NAME"
  info "Downloaded: $MODEL_FILE"
fi

# ── 5. Ollama ─────────────────────────────────────────────────────────────────
section "Ollama"

if command -v ollama &>/dev/null; then
  info "Ollama already installed"
else
  info "Installing Ollama…"
  brew install ollama
fi

# Start Ollama in the background if not already running
if ! ollama list &>/dev/null 2>&1; then
  info "Starting Ollama service…"
  brew services start ollama
  # Give it a moment to come up
  sleep 3
fi

# Pull the model if not already downloaded
if ollama list 2>/dev/null | grep -q "${OLLAMA_MODEL%%:*}"; then
  info "Ollama model already present: $OLLAMA_MODEL"
else
  info "Pulling $OLLAMA_MODEL (this may take a minute)…"
  ollama pull "$OLLAMA_MODEL"
  info "Model ready: $OLLAMA_MODEL"
fi

# ── 6. Write .env ─────────────────────────────────────────────────────────────
section "Writing .env"

WHISPER_BIN_PATH="$(pwd)/vendor/whisper.cpp/build/bin/whisper-cli"
WHISPER_MODEL_PATH="$(pwd)/$MODEL_FILE"

cat >.env <<EOF
WHISPER_BIN=$WHISPER_BIN_PATH
WHISPER_MODEL=$WHISPER_MODEL_PATH

# Ollama — used for Summarize and Fix Grammar
AI_BASE_URL=http://localhost:11434/v1
AI_API_KEY=ollama
AI_MODEL=$OLLAMA_MODEL
EOF

info ".env written"

# ── 7. Python deps ────────────────────────────────────────────────────────────
section "Python dependencies"
uv sync
info "Dependencies installed"

# ── Done ──────────────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}${GREEN}✓ Setup complete!${RESET}"
echo ""
echo "  Whisper : $WHISPER_BIN_PATH"
echo "  Model   : $WHISPER_MODEL_PATH"
echo "  Ollama  : $OLLAMA_MODEL"
echo ""
echo "  Start   : ./run.sh"
echo ""
echo "  To use a larger Whisper model (more accurate, slower):"
echo "    WHISPER_MODEL_NAME=small ./setup.sh"
echo "    # Options: tiny tiny.en base base.en small small.en medium medium.en large-v3"
echo ""
