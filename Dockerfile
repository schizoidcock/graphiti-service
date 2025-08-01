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

# Copy application code first
COPY graphiti_core/ ./graphiti_core/
COPY graph_service/ ./graph_service/

# Install Python dependencies directly with pip
RUN pip install \
    fastapi>=0.104.0 \
    uvicorn[standard]>=0.24.0 \
    pydantic>=2.4.0 \
    pydantic-settings>=2.0.0 \
    python-multipart>=0.0.6 \
    redis>=5.0.0 \
    falkordb>=1.1.2 \
    neo4j>=5.26.0 \
    diskcache>=5.6.3 \
    openai>=1.0.0 \
    anthropic>=0.7.0 \
    google-generativeai>=0.3.0 \
    groq>=0.4.0 \
    voyageai>=0.2.0 \
    numpy>=1.24.0 \
    scikit-learn>=1.3.0 \
    sentence-transformers>=2.2.0 \
    httpx>=0.25.0 \
    aiohttp>=3.8.0 \
    requests>=2.31.0 \
    tenacity>=8.2.0 \
    asyncio-throttle>=1.0.2 \
    async-timeout>=4.0.0 \
    python-dotenv>=1.0.0 \
    python-dateutil>=2.8.0 \
    pytz>=2023.3 \
    posthog>=3.0.0

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

# Start the FastAPI server with IPv6 support for Railway internal network
CMD ["python", "-m", "uvicorn", "graph_service.main:app", "--host", "::", "--port", "8000", "--log-level", "warning", "--no-access-log"]