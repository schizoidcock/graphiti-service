# Zep-Compatible Graphiti API Service

A FastAPI service that provides **full Zep Cloud API v2 compatibility** with enhanced Graphiti knowledge graph functionality using a remote FalkorDB backend.

## Overview

This service separates the Graphiti API layer from the database layer, connecting to a standalone FalkorDB instance. This architecture provides:

- **Better Performance**: Dedicated database service
- **Easier Scaling**: Independent scaling of API and database
- **Simpler Debugging**: Isolated components
- **Better Reliability**: Database issues don't affect API startup

## Features

### 🔥 **Zep Cloud API v2 Compatibility**
- **Complete API Surface**: All Zep Cloud endpoints (`/api/v2/*`)
- **Sessions Management**: Create, update, and manage user sessions
- **Graph Operations**: Nodes, edges, episodes with full CRUD operations
- **Search & Retrieval**: Hybrid search with semantic + BM25 + RRF
- **User Management**: Multi-tenant user isolation and management

### 🚀 **Enhanced Graphiti Functionality**
- **Entity Nodes**: Intelligent entity extraction with summaries
- **Entity Edges**: Semantic relationship facts between entities  
- **Episodic Nodes**: Raw conversational data with temporal tracking
- **Multi-tenant Isolation**: Per-user database isolation for data security
- **Temporal Knowledge**: Time-aware knowledge graph updates

### 🛠️ **Service Features**
- **FastAPI Framework**: Modern, fast API with automatic documentation
- **FalkorDB Integration**: Connects to remote FalkorDB via Redis protocol
- **Health Monitoring**: Comprehensive health checks and debug endpoints
- **Multiple LLM Support**: OpenAI, Anthropic, Google, Groq providers
- **Performance Optimized**: Async processing, connection pooling, caching
- **Railway Ready**: Optimized for Railway cloud deployment

## Configuration

### Environment Variables

#### Required
- `OPENAI_API_KEY`: OpenAI API key for LLM operations

#### FalkorDB Connection
- `FALKORDB_HOST`: FalkorDB host (default: localhost)
- `FALKORDB_PORT`: FalkorDB port (default: 6379)
- `FALKORDB_USERNAME`: FalkorDB username (optional)
- `FALKORDB_PASSWORD`: FalkorDB password (optional)

#### OpenAI Configuration
- `OPENAI_BASE_URL`: OpenAI API base URL (default: https://api.openai.com/v1)
- `MODEL_NAME`: LLM model name (default: gpt-4o-mini)
- `EMBEDDING_MODEL_NAME`: Embedding model (default: text-embedding-3-small)

#### Service Settings
- `PORT`: Service port (default: 8000)
- `DEBUG`: Debug mode (default: false)

## Railway Deployment

1. **Create new Railway service**
2. **Connect this repository**
3. **Set environment variables**:
   ```
   OPENAI_API_KEY=your_openai_key
   FALKORDB_HOST=your-falkordb-service.railway.internal
   FALKORDB_PORT=6379
   ```
4. **Deploy**

## Local Development

### Using Docker Compose

```bash
# Build and run
docker-compose up --build

# Run in background
docker-compose up -d
```

### Using UV (Python)

```bash
# Install dependencies
uv sync

# Run development server
uv run uvicorn graph_service.main:app --reload --host 0.0.0.0 --port 8000
```

## API Endpoints

### 🏥 Health & Status
- `GET /` - Service information and available endpoints
- `GET /healthcheck` - Simple health check
- `GET /debug/status` - Comprehensive component status
- `GET /debug/config` - Service configuration (non-sensitive)
- `GET /debug/nlp-test` - Test LLM and NLP capabilities

### 📖 Documentation
- `GET /docs` - Interactive API documentation (Swagger UI)
- `GET /redoc` - Alternative API documentation

### 🔥 Zep Cloud API v2 Compatible Endpoints

#### Sessions Management
- `POST /api/v2/sessions` - Create new session
- `GET /api/v2/sessions/{session_id}` - Get session details
- `PATCH /api/v2/sessions/{session_id}` - Update session
- `DELETE /api/v2/sessions/{session_id}` - Delete session
- `GET /api/v2/sessions` - List sessions
- `POST /api/v2/sessions/{session_id}/memory` - Add memory to session

#### Graph Operations  
- `POST /api/v2/graph/search` - Hybrid graph search
- `POST /api/v2/graph` - Add graph data
- `POST /api/v2/graph/batch` - Batch graph operations
- `GET /api/v2/graph/nodes/{uuid}` - Get node by UUID
- `GET /api/v2/graph/edges/{uuid}` - Get edge by UUID
- `GET /api/v2/graph/episodes/{uuid}` - Get episode by UUID

#### User Management
- `POST /api/v2/users` - Create user
- `GET /api/v2/users/{user_id}` - Get user details
- `PATCH /api/v2/users/{user_id}` - Update user
- `DELETE /api/v2/users/{user_id}` - Delete user
- `GET /api/v2/users` - List users

#### Episodes Management
- `POST /api/v2/episodes` - Create episode
- `GET /api/v2/episodes/{episode_id}` - Get episode
- `PATCH /api/v2/episodes/{episode_id}` - Update episode
- `DELETE /api/v2/episodes/{episode_id}` - Delete episode
- `GET /api/v2/episodes` - List episodes

#### Maintenance & Utilities
- `POST /api/v2/maintenance/cleanup` - Database cleanup operations
- `POST /api/v2/maintenance/reindex` - Rebuild search indexes
- `GET /api/v2/maintenance/stats` - Database statistics

## Usage Examples

### Health Check
```bash
curl http://localhost:8000/health
```

### Service Info
```bash
curl http://localhost:8000/
```

### Configuration
```bash
curl http://localhost:8000/config
```

## Architecture

```
┌─────────────────┐    Redis Protocol    ┌──────────────────┐
│                 │◄──────────────────────┤                  │
│ Graphiti API    │                       │ FalkorDB         │
│ Service         │                       │ Standalone       │
│ (This Service)  │                       │ Service          │
│                 │                       │                  │
└─────────────────┘                       └──────────────────┘
       │                                           │
       │ FastAPI/HTTP                              │ Volume Mount
       ▼                                           ▼
┌─────────────────┐                       ┌──────────────────┐
│                 │                       │                  │
│ Client Apps     │                       │ Persistent       │
│ (Web, Mobile)   │                       │ Storage          │
│                 │                       │ (Railway Volume) │
└─────────────────┘                       └──────────────────┘
```

## Dependencies

- **FastAPI**: Web framework
- **graphiti-core**: Core Graphiti functionality
- **falkordb**: FalkorDB Python client
- **redis**: Redis client for FalkorDB connection
- **openai**: OpenAI API client
- **uvicorn**: ASGI server

## Development

### Code Structure
```
graphiti-api-service/
├── graph_service/
│   ├── main.py          # FastAPI application
│   ├── config.py        # Configuration management
│   └── routers/         # API route handlers
├── graphiti_core/       # Core Graphiti library
├── Dockerfile           # Container definition
├── pyproject.toml       # Python dependencies
└── README.md           # This file
```

### Testing

```bash
# Run tests (if available)
uv run pytest

# Type checking
uv run mypy graph_service/

# Linting
uv run ruff check graph_service/
```

## Troubleshooting

### FalkorDB Connection Issues
1. Verify FalkorDB service is running
2. Check `FALKORDB_HOST` and `FALKORDB_PORT` environment variables
3. Test connectivity: `redis-cli -h <host> -p <port> ping`

### OpenAI API Issues
1. Verify `OPENAI_API_KEY` is set correctly
2. Check API quota and billing
3. Test with simple API call

### Service Startup Issues
1. Check logs for specific error messages
2. Verify all required environment variables are set
3. Test FalkorDB connectivity separately