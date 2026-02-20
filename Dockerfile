# =============================================================================
# Whisper Studio Dockerfile (cross-platform)
# =============================================================================
FROM python:3.12-slim-bookworm AS base

# System dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    curl \
    git \
    build-essential \
    cmake \
    && rm -rf /var/lib/apt/lists/*

# uv package manager
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:$PATH"

WORKDIR /app

# Copy Python dependencies first for caching
COPY pyproject.toml uv.lock .python-version* ./

# Install Python dependencies
RUN uv sync --frozen --no-dev

# Copy application code
COPY . .

# Prepare vendor directories
RUN mkdir -p /app/vendor uploads outputs store recordings

# Entrypoint
COPY docker-entrypoint.sh /docker-entrypoint.sh
RUN chmod +x /docker-entrypoint.sh

EXPOSE 5343
ENTRYPOINT ["/docker-entrypoint.sh"]
CMD ["uv", "run", "app.py"]
