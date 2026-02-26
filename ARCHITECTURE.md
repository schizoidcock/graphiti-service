# Graphiti-Service Architecture

## Overview

**graphiti-service** is an extended implementation of the [getzep/graphiti](https://github.com/getzep/graphiti) server framework, enhanced to provide **full Zep Cloud API v2 compatibility** with FalkorDB as the graph database backend.

## Relationship to Original Graphiti

### Original Graphiti Server (getzep/graphiti/server)

The original graphiti server is a minimal FastAPI wrapper around graphiti-core:

```
server/
├── graph_service/
│   ├── main.py           # 30 lines - basic FastAPI app
│   ├── config.py         # Neo4j configuration
│   ├── zep_graphiti.py   # ~115 lines - basic Graphiti wrapper
│   ├── dto/              # 3 files - basic DTOs
│   └── routers/
│       ├── ingest.py     # 6 endpoints
│       └── retrieve.py   # 4 endpoints
```

**Original Endpoints (10 total):**
- `POST /messages` - Add messages to queue
- `POST /entity-node` - Create entity node
- `DELETE /entity-edge/{uuid}` - Delete edge
- `DELETE /group/{group_id}` - Delete group
- `DELETE /episode/{uuid}` - Delete episode
- `POST /clear` - Clear graph
- `POST /search` - Search facts
- `GET /entity-edge/{uuid}` - Get edge
- `GET /episodes/{group_id}` - Get episodes
- `POST /get-memory` - Get memory context

**Original Database:** Neo4j

---

### Extended graphiti-service (this repository)

This service significantly extends the original to be a production-ready Zep Cloud API v2 compatible server:

```
graph_service/
├── main.py               # ~480 lines - full startup coordination
├── config.py             # FalkorDB + Railway configuration
├── zep_graphiti.py       # ~1800 lines - extended wrapper
├── dto/                  # 6 files - full Zep-compatible DTOs
│   ├── common.py
│   ├── ingest.py
│   ├── retrieve.py
│   ├── session.py        # NEW: Session management DTOs
│   ├── graph.py          # NEW: Graph API DTOs
│   └── user.py           # NEW: User management DTOs
└── routers/
    ├── ingest.py         # Extended: 7 endpoints
    ├── retrieve.py       # Extended: 6 endpoints
    ├── graph.py          # NEW: 14 endpoints (Zep v2 API)
    ├── sessions.py       # NEW: 11 endpoints (Zep v2 API)
    ├── users.py          # NEW: 7 endpoints (Zep v2 API)
    ├── episodes.py       # NEW: 3 endpoints
    └── maintenance.py    # NEW: 2 endpoints
```

**Database:** FalkorDB (Redis-based graph database)

---

## Endpoints Comparison

### Original Graphiti (10 endpoints)

| Method | Path | Description |
|--------|------|-------------|
| POST | /messages | Add messages |
| POST | /entity-node | Create entity |
| DELETE | /entity-edge/{uuid} | Delete edge |
| DELETE | /group/{group_id} | Delete group |
| DELETE | /episode/{uuid} | Delete episode |
| POST | /clear | Clear graph |
| POST | /search | Search facts |
| GET | /entity-edge/{uuid} | Get edge |
| GET | /episodes/{group_id} | Get episodes |
| POST | /get-memory | Get memory |

### graphiti-service (50+ endpoints)

#### Zep Cloud API v2 - Graph Operations (`/api/v2/graph`)
| Method | Path | Description |
|--------|------|-------------|
| POST | /search | Hybrid graph search |
| POST | / | Add graph data |
| POST | /batch | Batch operations |
| GET | /nodes/{uuid} | Get node |
| GET | /edges/{uuid} | Get edge |
| GET | /episodes/{uuid} | Get episode |
| DELETE | /edges/{uuid} | Delete edge |
| DELETE | /episodes/{uuid} | Delete episode |
| POST | /nodes/{uuid}/relationships | Get node relationships |
| GET | /users/{user_id}/triplets | Get user's graph triplets |
| GET | /episodes/user/{user_id} | Get user's episodes |
| GET | /episodes/session/{session_id} | Get session episodes |
| POST | /search-legacy | Legacy search format |

#### Zep Cloud API v2 - Sessions (`/api/v2/sessions`)
| Method | Path | Description |
|--------|------|-------------|
| POST | /sessions | Create session |
| GET | /sessions/{id} | Get session |
| GET | /sessions | List sessions |
| GET | /sessions-ordered | List ordered |
| POST | /sessions/{id}/memory | Add memory |
| GET | /sessions/{id}/memory | Get memory |
| GET | /sessions/{id}/messages | Get messages |
| DELETE | /sessions/{id}/memory | Delete memory |
| DELETE | /sessions/{id} | Delete session |
| GET | /sessions/{id}/search | Search in session |
| GET | /cache/stats | Cache statistics |

#### Zep Cloud API v2 - Users (`/api/v2/users`)
| Method | Path | Description |
|--------|------|-------------|
| POST | /users | Create user |
| GET | /users/{id} | Get user |
| GET | /users-ordered | List ordered |
| PATCH | /users/{id} | Update user |
| DELETE | /users/{id} | Delete user |
| GET | /users/{id}/sessions | Get user sessions |
| GET | /users/{id}/node | Get user's entity node |

#### Episodes Management (`/api/v2/graph/episodes`)
| Method | Path | Description |
|--------|------|-------------|
| GET | /episodes/user/{user_id} | Get user episodes |
| GET | /episodes/session/{session_id} | Get session episodes |
| GET | /episodes/{uuid}/mentions | Get episode mentions |

#### Legacy/Internal Endpoints
| Method | Path | Description |
|--------|------|-------------|
| POST | /messages | Add messages (legacy) |
| POST | /entity-node | Create entity |
| DELETE | /entity-edge/{uuid} | Delete edge |
| DELETE | /group/{group_id} | Delete group |
| DELETE | /episode/{uuid} | Delete episode |
| DELETE | /database/{user_id} | Delete user database |
| POST | /clear | Clear graph |
| POST | /search | Search (legacy) |
| GET | /entity-edge/{uuid} | Get edge |
| GET | /episodes/{group_id} | Get episodes |
| POST | /get-memory | Get memory |
| GET | /context-summary/{group_id} | Get context summary |
| POST | /extract-entities | Extract entities |

#### Maintenance
| Method | Path | Description |
|--------|------|-------------|
| POST | /cleanup-corrupted-files | Cleanup |
| GET | /data-directory-status | Status check |

#### Health & Debug
| Method | Path | Description |
|--------|------|-------------|
| GET | / | Service info |
| GET | /healthcheck | Health check |
| GET | /ping | Connectivity test |
| GET | /api/v2/health | API health |
| GET | /debug/config | Configuration |
| GET | /debug/status | Component status |
| GET | /debug/nlp-test | NLP test |
| POST | /search | Root search proxy |

---

## Key Architectural Differences

### 1. Database Backend

| Aspect | Original | graphiti-service |
|--------|----------|------------------|
| Database | Neo4j | FalkorDB |
| Protocol | Bolt | Redis |
| Connection | URI-based | Host:Port |
| Multi-tenant | No | Yes (per-user databases) |

### 2. ZepGraphiti Class

**Original (~115 lines):**
```python
class ZepGraphiti(Graphiti):
    def __init__(self, uri: str, user: str, password: str, llm_client=None):
        super().__init__(uri, user, password, llm_client)

    # 5 basic methods: save_entity_node, get_entity_edge,
    # delete_group, delete_entity_edge, delete_episodic_node
```

**Extended (~1800 lines):**
```python
class ZepGraphiti(Graphiti):
    def __init__(self, host, port, username, password, llm_client=None,
                 skip_init=False, user_id=None):
        # Per-user database isolation
        database_name = f"zep_{sanitize_user_id(user_id)}" if user_id else "default_db"
        falkor_driver = FalkorDriver(host, port, username, password, database=database_name)
        super().__init__(graph_driver=falkor_driver, llm_client=llm_client)

    # 50+ methods including:
    # - Multi-tenant database management
    # - Enhanced search with filters
    # - Session/User management
    # - Episode handling with mentions
    # - Background task coordination
    # - Connection pooling
    # - Embedding verification
```

### 3. Multi-Tenant Architecture

graphiti-service implements per-user database isolation:

```
FalkorDB
├── default_db          # Default/shared database
├── zep_abc123...       # User 1's isolated database
├── zep_def456...       # User 2's isolated database
└── zep_ghi789...       # User 3's isolated database
```

Each user gets their own graph database, ensuring complete data isolation.

### 4. Connection Pooling

```python
_graphiti_pool: dict[str, ZepGraphiti] = {}  # Connection cache
_pool_max_size = 50                           # Max connections
_pool_cleanup_interval = 300                  # 5 minute cleanup
```

### 5. Background Task Coordination

```python
_background_tasks: Dict[str, asyncio.Task] = {}
_task_lock = asyncio.Lock()
_startup_mode = True  # Suppress logging during startup

async def _managed_background_task(task_name, coro):
    # Coordinated background task execution
    # Prevents race conditions during startup
```

---

## Deployment Architecture

### Railway Configuration

```
┌─────────────────────────────────────────────────────────────┐
│                    Railway Project                          │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  ┌─────────────────┐         ┌─────────────────┐           │
│  │ zep-server      │ ──────► │ graphiti-service│           │
│  │ (Go backend)    │  HTTP   │ (this service)  │           │
│  │                 │         │                 │           │
│  │ Port: 8000      │         │ Port: 8080      │           │
│  └─────────────────┘         └────────┬────────┘           │
│                                       │                     │
│                                       │ Redis Protocol      │
│                                       ▼                     │
│                              ┌─────────────────┐           │
│                              │ FalkorDB        │           │
│                              │ (Graph DB)      │           │
│                              │                 │           │
│                              │ Port: 6379      │           │
│                              └─────────────────┘           │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### Internal Networking

- **zep-server** → `graphiti-service.railway.internal:8080`
- **graphiti-service** → `falkordb.railway.internal:6379`

### Environment Variables

```bash
# FalkorDB Connection
FALKORDB_HOST=falkordb.railway.internal
FALKORDB_PORT=6379
FALKORDB_USERNAME=
FALKORDB_PASSWORD=

# OpenAI
OPENAI_API_KEY=sk-...
OPENAI_BASE_URL=https://api.openai.com/v1
MODEL_NAME=gpt-4o-mini
EMBEDDING_MODEL_NAME=text-embedding-3-small

# Service
PORT=8080
DEBUG=false
```

---

## Integration with graphiti-core

This service uses a customized version of `graphiti-core` with:

1. **FalkorDB-only support** - Removed Neo4j/Neptune/Kuzu providers
2. **Driver Operations Redesign** - 11 ABC operation classes + FalkorDB implementations
3. **CVE Fix** - Replaced vulnerable diskcache with SQLite-based LLMCache
4. **PII Protection** - Removed sensitive data from logs
5. **PropertyFilter** - Property-based search filtering
6. **Token Tracking** - LLM token usage monitoring

See [CHANGELOG-v0.28.1.md](./CHANGELOG-v0.28.1.md) for full integration details.

---

## Version History

| Version | Base graphiti-core | Key Changes |
|---------|-------------------|-------------|
| 0.22.0 | Original | Initial FalkorDB port |
| 0.28.1 | v0.28.1 | Full integration, CVE fix, Zep v2 API |

---

## Files Structure

```
graphiti-service/
├── graph_service/           # FastAPI application
│   ├── main.py             # Application entry, startup coordination
│   ├── config.py           # Configuration management
│   ├── zep_graphiti.py     # Extended Graphiti wrapper (1800+ lines)
│   ├── dto/                # Data Transfer Objects
│   ├── routers/            # API route handlers
│   ├── adapters/           # External service adapters
│   └── *.py                # Utilities (cache, workers, etc.)
│
├── graphiti_core/           # Customized graphiti-core library
│   ├── driver/             # Database drivers
│   │   ├── falkordb/       # FalkorDB implementation
│   │   └── operations/     # Abstract operation classes
│   ├── llm_client/         # LLM integrations
│   ├── search/             # Search functionality
│   └── ...
│
├── Dockerfile              # Railway-optimized container
├── requirements.txt        # Python dependencies
├── pyproject.toml          # Project configuration
└── railway.json            # Railway deployment config
```
