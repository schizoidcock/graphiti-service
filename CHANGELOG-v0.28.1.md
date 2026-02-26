# Graphiti Service - Integration Changelog v0.28.1

**Date:** 2026-02-25
**Branch:** development -> production
**Base Version:** 0.22.0 -> 0.28.1
**Source:** getzep/graphiti (Oct 2025 - Feb 2026)

---

## Major Features Integrated

### 1. Driver Operations Redesign
New architecture with 11 ABC operation classes + FalkorDB implementations.

**New directories:**
- `graphiti_core/driver/operations/` - Abstract base classes
- `graphiti_core/driver/falkordb/operations/` - FalkorDB implementations

**Operation classes:**

| ABC Class | FalkorDB Implementation |
|-----------|------------------------|
| EntityNodeOperations | FalkorEntityNodeOperations |
| EntityEdgeOperations | FalkorEntityEdgeOperations |
| EpisodeNodeOperations | FalkorEpisodeNodeOperations |
| EpisodicEdgeOperations | FalkorEpisodicEdgeOperations |
| CommunityNodeOperations | FalkorCommunityNodeOperations |
| CommunityEdgeOperations | FalkorCommunityEdgeOperations |
| SagaNodeOperations | FalkorSagaNodeOperations |
| HasEpisodeEdgeOperations | FalkorHasEpisodeEdgeOperations |
| NextEpisodeEdgeOperations | FalkorNextEpisodeEdgeOperations |
| GraphOperations | FalkorGraphOperations |
| SearchOperations | FalkorSearchOperations |

### 2. Sagas Feature
New node/edge types for grouping episodes:
- SagaNode - Groups related episodes
- HasEpisodeEdge - Links saga to episodes
- NextEpisodeEdge - Links sequential episodes

### 3. CVE Fix - LLMCache
Replaced vulnerable diskcache with SQLite-based cache.

**New file:** `graphiti_core/llm_client/cache.py`

**Removed dependency:** diskcache>=5.6.3

### 4. PII Protection
Removed entity names, facts, and user queries from log messages.

**Files modified:**
- llm_client/client.py - _get_failed_generation_log() no longer dumps message content
- search/search.py - Removed query from log
- community_operations.py - Changed to UUID-only logging

### 5. PropertyFilter for Search
New search filter capability for property-based queries.

**New class in search/search_filters.py:**
- PropertyFilter with property_name, property_value, comparison_operator

### 6. Token Tracker
LLM token usage tracking system.

**New file:** `graphiti_core/llm_client/token_tracker.py`
- TokenUsage - Single call usage
- PromptTokenUsage - Per-prompt accumulated usage
- TokenUsageTracker - Thread-safe tracker with summary printing

### 7. custom_extraction_instructions
New parameter in add_episode() for customizing entity/edge extraction.

### 8. new_edges Parameter
Passing only new edges (not duplicates) to node summarization for better context.

### 9. UUID Collision Guard
Prevents add_triplet() from overwriting edges with different source/target.

### 10. MAX_SUMMARY_CHARS Increase
Changed from 250 to 500 characters for entity summaries.

---

## FalkorDB-Only Cleanup

Removed all Neo4j/Neptune/Kuzu provider references:
- Simplified match/case statements to FalkorDB-only
- Removed provider parameter from query functions
- Kept GraphProvider.FALKORDB enum only

**Files cleaned:**
- edges.py
- nodes.py
- graph_queries.py
- node_db_queries.py
- edge_db_queries.py

---

## Railway Customizations Preserved

- _configure_redis_for_railway() in FalkorDriver
- MISCONF error handling for Railway Redis
- IPv6 binding support

---

## Deployment Fixes

### 1. neo4j Import Removed
**File:** helpers.py
- Removed neo4j time import
- Updated parse_db_date() for FalkorDB

### 2. Circular Import Fixed
**File:** helpers.py
- Moved GraphProvider import inside get_default_group_id() function
- Added TYPE_CHECKING for type hints

### 3. Missing Classes Added
**File:** prompts/extract_nodes.py
- Added SummarizedEntity
- Added SummarizedEntities
- Added extract_summaries_batch to Protocol

### 4. build_indices_and_constraints Fixed
**File:** graphiti.py
- Changed from standalone function import to driver method delegation

### 5. Railway Cache Mount
**File:** Dockerfile
- Updated Service ID: 144c49b2-ec1f-4c81-8284-7976a384234b

---

## Dependencies Updated

| Package | Before | After |
|---------|--------|-------|
| version | 0.22.0 | 0.28.1 |
| pydantic | >=2.4.0 | >=2.11.5 |
| openai | >=1.0.0 | >=1.91.0 |
| anthropic | >=0.7.0 | >=0.49.0 |
| google-genai | google-generativeai>=0.3.0 | google-genai>=1.62.0 |
| tenacity | >=8.2.0 | >=9.0.0 |
| sentence-transformers | >=2.2.0 | >=3.2.1 |
| falkordb | >=1.2.0 | >=1.1.2,<2.0.0 |
| diskcache | >=5.6.3 | REMOVED (CVE) |

---

## Files Added (32 new)

- graphiti_core/driver/falkordb/ (13 files)
- graphiti_core/driver/operations/ (13 files)
- graphiti_core/driver/graph_operations/ (2 files)
- graphiti_core/driver/query_executor.py
- graphiti_core/driver/record_parsers.py
- graphiti_core/llm_client/cache.py
- graphiti_core/llm_client/token_tracker.py

---

## Files Modified (21)

- graphiti_core/driver/driver.py
- graphiti_core/driver/falkordb_driver.py
- graphiti_core/edges.py
- graphiti_core/graph_queries.py
- graphiti_core/graphiti.py
- graphiti_core/helpers.py
- graphiti_core/llm_client/__init__.py
- graphiti_core/llm_client/client.py
- graphiti_core/llm_client/openai_base_client.py
- graphiti_core/models/edges/edge_db_queries.py
- graphiti_core/models/nodes/node_db_queries.py
- graphiti_core/nodes.py
- graphiti_core/prompts/extract_nodes.py
- graphiti_core/search/search.py
- graphiti_core/search/search_filters.py
- graphiti_core/search/search_utils.py
- graphiti_core/utils/bulk_utils.py
- graphiti_core/utils/maintenance/community_operations.py
- graphiti_core/utils/maintenance/edge_operations.py
- graphiti_core/utils/maintenance/node_operations.py
- graphiti_core/utils/text_utils.py
- pyproject.toml
- Dockerfile

---

## Railway Networking Configuration

### Important: IPv6 vs IPv4 Binding

Railway's networking has different behavior depending on uvicorn's host binding:

| Binding | Public Access | Internal Network | FalkorDB Connection |
|---------|---------------|------------------|---------------------|
| `--host ::` (IPv6) | ❌ 502 errors | ✅ Works | Use `falkordb.railway.internal:PORT` |
| `--host 0.0.0.0` (IPv4) | ✅ Works | ❌ Fails | Use `caboose.proxy.rlwy.net:PORT` (public proxy) |

### Configuration Options

**Option 1: Internal Network Only (IPv6)**
```dockerfile
CMD ["uvicorn", "...", "--host", "::", "--port", "${PORT}"]
```
- FalkorDB: `FALKORDB_HOST=falkordb.railway.internal`
- zep-server connects via: `graphiti-service.railway.internal:8080`
- Public access: Not available

**Option 2: Public Access (IPv4)**
```dockerfile
CMD ["uvicorn", "...", "--host", "0.0.0.0", "--port", "${PORT}"]
```
- FalkorDB: `FALKORDB_HOST=caboose.proxy.rlwy.net` (public proxy)
- Public URL: `https://graphiti-service-xxx.up.railway.app`
- Internal network: Requires public proxy for all connections

### Current Configuration

As of v0.28.1, the service uses **IPv4 binding** (`0.0.0.0`) for public access.
FalkorDB must be configured with the **public proxy URL**, not the internal Railway domain.

---

## Commits

- d0a7ffe Fix uvicorn binding: use 0.0.0.0 instead of :: (IPv6)
- c1a3104 Add ARCHITECTURE.md documenting service extension
- 37c61ef Sync requirements.txt with pyproject.toml v0.28.1
- a813c8c Integrate graphiti-core updates Oct 2025 - Feb 2026 (v0.28.1)
- cdc83b9 Remove neo4j import from helpers.py
- b33d0c3 Fix circular import in helpers.py
- d085b1a Add missing SummarizedEntity and SummarizedEntities classes
- afdf8cc Fix build_indices_and_constraints - delegate to driver method
- abf8e57 Update Railway cache mount with correct Service ID
