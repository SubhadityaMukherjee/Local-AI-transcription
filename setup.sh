#!/usr/bin/env bash
# setup.sh — one-shot setup for Whisper Studio on macOS (Metal/GPU + AI Backend)
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

AI_BACKEND_TYPE="${AI_BACKEND_TYPE:-opencode}"
OPENCODE_MODEL="zai-coding-plan/glm-4.7"
OLLAMA_MODEL="deepseek-r1:latest"
WHISPER_MODEL_NAME="medium.en"

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

section "AI Backend: $AI_BACKEND_TYPE"

if [ "$AI_BACKEND_TYPE" = "ollama" ]; then
	if command -v ollama &>/dev/null; then
		info "ollama  $(command -v ollama)"
	else
		info "Installing Ollama…"
		brew install ollama
	fi

	if ! ollama list &>/dev/null 2>&1; then
		info "Starting Ollama service…"
		brew services start ollama
		sleep 3
	fi

	if ollama list 2>/dev/null | grep -q "${OLLAMA_MODEL%%:*}"; then
		info "Ollama model already present: $OLLAMA_MODEL"
	else
		info "Pulling $OLLAMA_MODEL (this may take a minute)…"
		ollama pull "$OLLAMA_MODEL"
		info "Model ready: $OLLAMA_MODEL"
	fi
elif [ "$AI_BACKEND_TYPE" = "opencode" ]; then
	if command -v opencode &>/dev/null; then
		info "opencode  $(command -v opencode)"
	else
		err "opencode not found — install with: npm install -g opencode"
	fi
else
	err "Invalid AI_BACKEND_TYPE: $AI_BACKEND_TYPE (must be 'opencode' or 'ollama')"
fi

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

# ── 5. Write .env ─────────────────────────────────────────────────────────────
section "Writing .env"

WHISPER_BIN_PATH="$(pwd)/vendor/whisper.cpp/build/bin/whisper-cli"
WHISPER_MODEL_PATH="$(pwd)/$MODEL_FILE"

if [ "$AI_BACKEND_TYPE" = "ollama" ]; then
	cat >.env <<EOF
WHISPER_BIN=$WHISPER_BIN_PATH
WHISPER_MODEL=$WHISPER_MODEL_PATH

# Ollama — used for Summarize and Fix Grammar
AI_SERVICE=ollama
AI_BASE_URL=http://localhost:11434/v1
AI_API_KEY=ollama
AI_MODEL=$OLLAMA_MODEL
EOF
else
	cat >.env <<EOF
WHISPER_BIN=$WHISPER_BIN_PATH
WHISPER_MODEL=$WHISPER_MODEL_PATH

# Opencode — used for Summarize and Fix Grammar
AI_SERVICE=opencode
AI_MODEL=$OPENCODE_MODEL
EOF
fi

info ".env written"

# ── 6. Python deps ────────────────────────────────────────────────────────────
section "Python dependencies"
uv sync
info "Dependencies installed"

# ── Done ──────────────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}${GREEN}✓ Setup complete!${RESET}"
echo ""
echo "  Whisper : $WHISPER_BIN_PATH"
echo "  Model   : $WHISPER_MODEL_PATH"

if [ "$AI_BACKEND_TYPE" = "ollama" ]; then
	echo "  AI      : Ollama ($OLLAMA_MODEL)"
else
	echo "  AI      : Opencode ($OPENCODE_MODEL)"
fi

echo ""
echo "  Start   : uv run cli.py -h"
echo ""
