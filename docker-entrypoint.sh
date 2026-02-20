#!/bin/bash
# =============================================================================
# Whisper Studio Entrypoint (fully automated backend detection)
# =============================================================================
set -e

# --------------------------
# Detect host OS and architecture
# --------------------------
OS=$(uname -s)
ARCH=$(uname -m)
echo "Detected OS: $OS, architecture: $ARCH"

USE_METAL=0
USE_NVIDIA=0

# --------------------------
# Detect Metal (Mac ARM64)
# --------------------------
if [[ "$OS" == "Darwin" && "$ARCH" == "arm64" ]]; then
  USE_METAL=1
  echo "Metal detected: enabling Metal backend for whisper.cpp"
fi

# --------------------------
# Detect NVIDIA GPU (Linux)
# --------------------------
if [[ "$OS" == "Linux" ]]; then
  if command -v nvidia-smi &>/dev/null; then
    USE_NVIDIA=1
    echo "NVIDIA GPU detected: GPU acceleration available"
  else
    echo "No NVIDIA GPU detected: falling back to CPU"
  fi
fi

# --------------------------
# Setup whisper.cpp
# --------------------------
setup_whisper() {
  WHISPER_DIR="/app/vendor/whisper.cpp"

  # Clone if missing
  if [ ! -d "$WHISPER_DIR" ]; then
    echo "Cloning whisper.cpp..."
    git clone --depth 1 https://github.com/ggerganov/whisper.cpp.git "$WHISPER_DIR"
  fi

  WHISPER_CLI="$WHISPER_DIR/build/bin/whisper-cli"

  # Build if binary missing or if running in Docker (Linux container)
  # Always rebuild inside Docker to ensure correct architecture
  if [ ! -f "$WHISPER_CLI" ] || [ "$OS" = "Linux" ]; then
    # Remove existing build if it exists (may be wrong architecture)
    if [ -d "$WHISPER_DIR/build" ]; then
      echo "Removing existing build (may be wrong architecture)..."
      rm -rf "$WHISPER_DIR/build"
    fi

    echo "Building whisper.cpp for $OS ($ARCH)..."
    cd "$WHISPER_DIR"
    mkdir -p build

    BUILD_ARGS="-DCMAKE_BUILD_TYPE=Release"

    # Use Metal if detected (macOS ARM64)
    if [ "$USE_METAL" -eq 1 ]; then
      BUILD_ARGS="$BUILD_ARGS -DGGML_METAL=ON"
    else
      BUILD_ARGS="$BUILD_ARGS -DGGML_METAL=OFF"
    fi

    # On macOS, ensure we build for the correct architecture
    if [ "$OS" = "Darwin" ]; then
      cmake -B build $BUILD_ARGS -DCMAKE_OSX_ARCHITECTURES=$ARCH
    else
      # On Linux, let CMake detect architecture automatically
      cmake -B build $BUILD_ARGS
    fi
    
    cmake --build build --config Release -j$(nproc)
    cd /app
  fi

  # Download model if missing
  MODEL_NAME="${WHISPER_MODEL_NAME:-medium.en}"
  MODEL_FILE="$WHISPER_DIR/models/ggml-${MODEL_NAME}.bin"
  if [ ! -f "$MODEL_FILE" ]; then
    echo "Downloading Whisper model: $MODEL_NAME..."
    bash "$WHISPER_DIR/models/download-ggml-model.sh" "$MODEL_NAME"
  fi

  export WHISPER_BIN="$WHISPER_CLI"
  export WHISPER_MODEL="$MODEL_FILE"
  echo "Whisper ready: $WHISPER_BIN"
  echo "Model ready: $WHISPER_MODEL"
}

# --------------------------
# Main
# --------------------------
setup_whisper

# Execute the provided command (default: Flask app)
exec "$@"
