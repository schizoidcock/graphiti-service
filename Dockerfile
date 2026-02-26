# Graphiti API Service - Optimized for fast builds
# FastAPI service that connects to standalone FalkorDB
FROM python:3.13.7-slim

# Set working directory
WORKDIR /app

# Install system dependencies in one layer and clean up
RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    curl \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

# CRITICAL OPTIMIZATION: Copy requirements.txt for better Docker layer caching
# This allows Docker to cache the pip install step when only code changes
COPY requirements.txt .

# Install Python dependencies BEFORE copying application code
# This creates a cached layer that only rebuilds when dependencies change
# Railway Service ID: 144c49b2-ec1f-4c81-8284-7976a384234b
RUN --mount=type=cache,id=s/144c49b2-ec1f-4c81-8284-7976a384234b-~/.cache/pip,target=/app/.cache/pip \
    pip install -r requirements.txt

# Copy application code AFTER dependencies are installed
# Code changes won't trigger dependency reinstalls
COPY graphiti_core/ ./graphiti_core/
COPY graph_service/ ./graph_service/

# Create non-root user
RUN groupadd -r app && useradd -r -g app app \
    && chown -R app:app /app

USER app

# Set environment variables
ENV PYTHONPATH=/app

# Add health check to verify service is responding
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD curl -f http://localhost:${PORT}/healthcheck || exit 1

# Start with Gunicorn + Uvicorn workers for dual-stack binding (IPv4 + IPv6)
# This enables both public access AND Railway private networking
CMD ["sh", "-c", "echo '🚀 Starting gunicorn with uvicorn workers...' && gunicorn -w 2 -k uvicorn.workers.UvicornWorker -b [::]:${PORT} --access-logfile - --error-logfile - graph_service.main:app"]
