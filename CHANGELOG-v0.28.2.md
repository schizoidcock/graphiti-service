# Graphiti Service - Integration Changelog v0.28.2

**Date:** 2026-03-29
**Branch:** development
**Base Version:** 0.28.1 -> 0.28.2
**Source:** getzep/graphiti (Feb 2026 - Mar 2026)

---

## Security Fixes

### 1. Cypher Injection Hardening (7d65d5e)

Hardened search filters and node labels against Cypher injection attacks.

**New error class:**
- `NodeLabelValidationError` in `errors.py`

**New validation functions in helpers.py:**
- `SAFE_CYPHER_IDENTIFIER_PATTERN` - Regex for safe Cypher identifiers
- `validate_group_ids()` - Validates list of group IDs
- `validate_node_labels()` - Validates node labels are safe for Cypher

**Files modified:**
- `errors.py` - Added `NodeLabelValidationError`
- `helpers.py` - Added pattern and validation functions
- `search/search_filters.py` - Added `@field_validator` for node_labels, defense-in-depth in query constructors
- `nodes.py` - Added `model_config` with `validate_assignment=True`, `@field_validator` for labels
- `driver/falkordb_driver.py` - Added `validate_group_ids()` call in `build_fulltext_query()`
- `search/search.py` - Added `validate_group_ids()` call in `search()`
- `search/search_utils.py` - Added `validate_group_ids()` calls in fulltext search functions
- `models/nodes/node_db_queries.py` - Added `_validate_entity_labels()`, validation in save queries

---

## New Features

### 2. GLiNER2 Hybrid LLM Client (4b91076)

Added GLiNER2Client - a hybrid LLM client that uses GLiNER2 for local entity extraction.

**Benefits:**
- Local CPU-friendly entity extraction (205M-340M params)
- Reduces LLM API costs for entity extraction
- Lower latency for extraction operations
- Works offline for entity extraction

**New file:**
- `graphiti_core/llm_client/gliner2_client.py`

**Usage:**
```python
from graphiti_core.llm_client import GLiNER2Client, OpenAIClient, LLMConfig

# GLiNER2 handles entity extraction, delegates other tasks to OpenAI
llm_client = GLiNER2Client(
    config=LLMConfig(model='fastino/gliner2-base-v1'),
    llm_client=OpenAIClient(LLMConfig(model='gpt-4o')),
    threshold=0.5,
)
```

**Requirements:**
- Python 3.11+ (due to onnxruntime dependency)
- Install with: `pip install graphiti-core[gliner2]`

---

## Upstream Commits Integrated

| Commit | Description | Type |
|--------|-------------|------|
| **7d65d5e** | Harden search filters against Cypher injection | Security |
| **4b91076** | Add GLiNER2 hybrid LLM client | Feature |
| **98f5b5f** | Replace edge name with uuid in debug log (PII) | Already integrated |

---

## Commits

- Integrate upstream security fix 7d65d5e (Cypher injection hardening)
- Integrate GLiNER2 hybrid LLM client (4b91076)
- Bump version to 0.28.2
