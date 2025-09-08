# Graphiti API Service - Optimized for fast builds
# FastAPI service that connects to standalone FalkorDB
FROM python:3.11-slim

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
RUN pip install --no-cache-dir -r requirements.txt

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

# Start the FastAPI server with debug logging - use Railway's PORT
# Use :: to bind to all IPv6 interfaces (Railway internal network uses IPv6)
CMD ["sh", "-c", "python -m uvicorn graph_service.main:app --host :: --port ${PORT} --log-level info --access-log"]