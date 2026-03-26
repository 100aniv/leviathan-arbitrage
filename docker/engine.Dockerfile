# =============================================================================
# LEVIATHAN Engine — Multi-stage Dockerfile
# =============================================================================

# ---------------------------------------------------------------------------
# Stage 1: builder — installs dependencies
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS builder

WORKDIR /build

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy only dependency files first (layer cache optimisation)
COPY engine/pyproject.toml ./

# Create minimal package stub so hatchling can resolve dependencies
RUN mkdir -p src && touch src/__init__.py

# Install all dependencies (including dev for test stage)
RUN pip install --upgrade pip && \
    pip install --no-cache-dir ".[dev,ml]"

# ---------------------------------------------------------------------------
# Stage 2: development — hot reload + debugpy
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS development

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy installed packages from builder
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Copy source + config
COPY engine/src ./src
COPY engine/config ./config
COPY engine/settings.toml ./settings.toml
COPY engine/pyproject.toml ./

RUN pip install --no-cache-dir debugpy

ENV PYTHONPATH=/app
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

EXPOSE 8000 8001 5678

CMD ["python", "-m", "src.main"]

# ---------------------------------------------------------------------------
# Stage 3: test — runs pytest
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS test

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

COPY engine/src ./src
COPY engine/tests ./tests
COPY engine/pyproject.toml ./

ENV PYTHONPATH=/app
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

CMD ["pytest", "tests/", "-v", "--tb=short"]

# ---------------------------------------------------------------------------
# Stage 4: production — minimal runtime image
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS production

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    curl \
    && rm -rf /var/lib/apt/lists/* \
    && useradd -r -s /bin/false -u 1001 leviathan

COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin
COPY --chown=leviathan:leviathan engine/src ./src
COPY --chown=leviathan:leviathan engine/config ./config
COPY --chown=leviathan:leviathan engine/settings.toml ./settings.toml
COPY --chown=leviathan:leviathan engine/pyproject.toml ./

ENV PYTHONPATH=/app
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

USER leviathan

EXPOSE 8000 8001

HEALTHCHECK --interval=10s --timeout=5s --start-period=30s --retries=5 \
    CMD curl -f http://localhost:8000/health || exit 1

CMD ["python", "-m", "src.main"]
