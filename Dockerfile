# Graphiti API Service
# FastAPI service that connects to standalone FalkorDB
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install UV package manager
RUN pip install uv

# Copy dependency files
COPY pyproject.toml ./

# Install Python dependencies (without lock file for Railway compatibility)
RUN uv sync

# Copy application code
COPY graphiti_core/ ./graphiti_core/
COPY graph_service/ ./graph_service/

# Create non-root user
RUN groupadd -r app && useradd -r -g app app
RUN chown -R app:app /app
USER app

# Expose port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Set environment variables
ENV PYTHONPATH=/app
ENV PORT=8000

# Start the FastAPI server
CMD ["uv", "run", "uvicorn", "graph_service.main:app", "--host", "0.0.0.0", "--port", "8000"]