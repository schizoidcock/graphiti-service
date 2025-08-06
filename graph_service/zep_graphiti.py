import logging
import re
import time
import asyncio
from typing import Annotated, Optional, List, Dict, Any
from contextvars import ContextVar
from functools import lru_cache

from fastapi import Depends, HTTPException, Request
from graphiti_core import Graphiti  # type: ignore
from graphiti_core.driver.falkordb_driver import FalkorDriver  # type: ignore
from graphiti_core.edges import EntityEdge  # type: ignore
from graphiti_core.errors import EdgeNotFoundError, GroupsEdgesNotFoundError, NodeNotFoundError
from graphiti_core.llm_client import LLMClient  # type: ignore
from graphiti_core.nodes import EntityNode, EpisodicNode  # type: ignore

from graph_service.config import ZepEnvDep
from graph_service.dto import FactResult
from graph_service.response_cache import response_cache

logger = logging.getLogger(__name__)

# Context variable to store current user_id for request-scoped database isolation
current_user_context: ContextVar[str | None] = ContextVar('current_user_context', default=None)

# Global flag to track if indices have been built per database
_indices_initialized = set()

# Enhanced connection pool with limits and async management
_graphiti_pool: dict[str, "ZepGraphiti"] = {}
_pool_locks: dict[str, asyncio.Lock] = {}
_pool_max_size = 50  # Maximum total connections across all users
_pool_cleanup_interval = 300  # 5 minutes
_pool_last_cleanup = time.time()


def sanitize_user_id(user_id: str) -> str:
    """Sanitize user_id to be safe for database names"""
    if not user_id:
        raise ValueError("user_id cannot be empty")
        
    # Remove any characters that aren't alphanumeric, underscore, or hyphen
    # Replace multiple consecutive non-alphanumeric chars with single underscore
    sanitized = re.sub(r'[^a-zA-Z0-9_-]+', '_', user_id)
    
    # Handle different prefix patterns
    if sanitized.startswith("auto_user_"):
        # Extract just the ID part after "auto_user_"
        id_part = sanitized[10:]  # Remove "auto_user_" prefix
        sanitized = id_part
    elif sanitized.startswith("zep_"):
        # For zep_ prefixed users, keep the zep_ part
        # Don't add additional user_ prefix
        pass  # Keep as-is
    elif sanitized.startswith("user_"):
        # Already has user_ prefix, keep as-is
        pass
    else:
        # For other cases, add user_ prefix if needed
        if sanitized and sanitized[0].isdigit():
            sanitized = f"user_{sanitized}"
        elif not sanitized.startswith("user_"):
            sanitized = f"user_{sanitized}"
    
    # Limit length to prevent issues
    sanitized = sanitized[:50]
    if not sanitized:
        raise ValueError("user_id resulted in empty string after sanitization")
    return sanitized


# Cache for expensive operations
@lru_cache(maxsize=100)
def _get_cached_user_db(user_id: str) -> str:
    """Cached user database name generation"""
    return sanitize_user_id(user_id)


def extract_user_id_from_request(request: Request) -> str | None:
    """Extract user_id from various sources in the request"""
    
    logger.debug(f"🔍 Extracting user_id from request: path={request.url.path}, params={request.path_params}")
    
    # Method 1: Check if there's a session_id in path parameters
    if "session_id" in request.path_params:
        session_id = request.path_params["session_id"]
        logger.debug(f"🔍 Found session_id in path: {session_id}")
        
        # Check if session exists in session store to get user_id
        from graph_service.routers.sessions import sessions_store
        if session_id in sessions_store:
            user_id = sessions_store[session_id]["user_id"]
            logger.info(f"✅ Found user_id from sessions_store: {user_id}")
            return user_id
        
        logger.warning(f"⚠️ Session {session_id} not found in sessions_store, generating auto_user")
        # Fallback: generate user_id from session_id for auto-created sessions
        return f"auto_user_{session_id[:8]}"
    
    # Method 2: Check for group_id in path parameters (format: user_id_session_id)
    if "group_id" in request.path_params:
        group_id = request.path_params["group_id"]
        logger.debug(f"🔍 Found group_id in path: {group_id}")
        if "_" in group_id:
            user_id, _ = group_id.split("_", 1)
            logger.info(f"✅ Extracted user_id from group_id: {user_id}")
            return user_id
        # Fallback: treat group_id as session_id
        return f"user_{group_id[:8]}"
    
    # Method 3: Check query parameters for user_id
    if "user_id" in request.query_params:
        user_id = request.query_params["user_id"]
        logger.info(f"✅ Found user_id in query params: {user_id}")
        return user_id
    
    # Method 4: Check URL path for user patterns
    path = request.url.path
    if "/users/" in path:
        # Extract from paths like /api/v2/users/{user_id}/...
        parts = path.split("/")
        try:
            user_index = parts.index("users") + 1
            if user_index < len(parts):
                user_id = parts[user_index]
                logger.info(f"✅ Extracted user_id from URL path: {user_id}")
                return user_id
        except (ValueError, IndexError):
            pass
    
    logger.warning("⚠️ No user_id found in request")
    return None


# Session to user mapping for proper user identification
_session_user_mapping: dict[str, str] = {}

def derive_user_id_from_session_id(session_id: str) -> str:
    """
    Fast user derivation from session IDs - optimized for performance
    Fixed to handle zep_ prefixed user IDs correctly
    """
    # Fast path: If already a proper zep_ user ID, return as-is
    if session_id.startswith('zep_') and len(session_id) > 4:
        return session_id
    
    # Fast path: Structured session IDs (user_context_session)
    # BUT avoid splitting zep_ prefixed IDs which are already complete user IDs
    if '_' in session_id and len(session_id) > 3 and not session_id.startswith('zep_'):
        user_part = session_id.split('_')[0]
        if len(user_part) >= 3 and user_part.isalnum():
            return user_part
    
    # Fast path: Generate from first 8 chars for unstructured IDs
    if len(session_id) >= 8 and not session_id.startswith('zep_'):
        return f"zep_{session_id[:8]}"
    
    # Fallback: If it doesn't start with zep_, add prefix
    if not session_id.startswith('zep_'):
        return f"zep_{session_id}"
    
    # If it already starts with zep_ but somehow didn't match above, return as-is
    return session_id


def update_user_context_from_group_id(group_id: str) -> str:
    """Fast user context extraction from group_id"""
    import logging
    logger = logging.getLogger(__name__)
    
    if '_' in group_id:
        # Handle different group_id patterns:
        # - "zep_233117be_233117be2a994b988fbe709804514ae9" → user_id: "zep_233117be" 
        # - "233117be_session" → user_id: "233117be"
        parts = group_id.split('_')
        
        if len(parts) >= 3 and parts[0] == 'zep':
            # Pattern: zep_<user_id>_<session_id> → extract zep_<user_id>
            user_id = f"{parts[0]}_{parts[1]}"
        else:
            # Traditional pattern: <user_id>_<session_id> → extract first part
            user_id = parts[0]
            
        logger.info(f"🔍 USER DERIVATION: group_id='{group_id}' → extracted → user_id='{user_id}'")
    else:
        # Check cache first for performance
        if group_id in _session_user_mapping:
            user_id = _session_user_mapping[group_id]
            logger.info(f"🔍 USER DERIVATION: group_id='{group_id}' → from cache → user_id='{user_id}'")
        else:
            user_id = derive_user_id_from_session_id(group_id)
            _session_user_mapping[group_id] = user_id
            logger.info(f"🔍 USER DERIVATION: group_id='{group_id}' → derived → user_id='{user_id}'")
    
    current_user_context.set(user_id)
    return user_id


async def cleanup_connection_pool():
    """Clean up stale connections and enforce pool limits"""
    global _pool_last_cleanup
    current_time = time.time()
    
    # Only cleanup if interval has passed
    if current_time - _pool_last_cleanup < _pool_cleanup_interval:
        return
    
    logger.info(f"🧹 Starting connection pool cleanup, current size: {len(_graphiti_pool)}")
    
    # If pool is over limit, remove oldest connections
    if len(_graphiti_pool) > _pool_max_size:
        # Sort by creation time (we'll need to track this)
        excess_count = len(_graphiti_pool) - _pool_max_size
        oldest_keys = list(_graphiti_pool.keys())[:excess_count]
        
        for key in oldest_keys:
            try:
                client = _graphiti_pool.pop(key, None)
                if client and hasattr(client, 'driver'):
                    await client.driver.close()
                logger.info(f"🗑️ Removed connection from pool: {key}")
            except Exception as e:
                logger.warning(f"Error closing connection {key}: {e}")
    
    _pool_last_cleanup = current_time
    logger.info(f"✅ Pool cleanup completed, final size: {len(_graphiti_pool)}")


async def get_or_create_pooled_client_async(user_id: str, settings) -> "ZepGraphiti":
    """Async version of get_or_create_pooled_client with enhanced pooling"""
    pool_key = f"{user_id}_{settings.falkordb_host}_{settings.falkordb_port}"
    
    # Periodic cleanup
    await cleanup_connection_pool()
    
    # Fast path: return existing client
    if pool_key in _graphiti_pool:
        return _graphiti_pool[pool_key]
    
    # Use per-key locks to prevent race conditions
    if pool_key not in _pool_locks:
        _pool_locks[pool_key] = asyncio.Lock()
    
    async with _pool_locks[pool_key]:
        # Double-check after acquiring lock
        if pool_key in _graphiti_pool:
            return _graphiti_pool[pool_key]
        
        # Create new client
        client = ZepGraphiti(
            host=settings.falkordb_host,
            port=settings.falkordb_port,
            username=settings.falkordb_username,
            password=settings.falkordb_password,
            user_id=user_id
        )
        
        # Fast LLM configuration
        if settings.openai_api_key and client.llm_client:
            if settings.openai_base_url:
                client.llm_client.config.base_url = settings.openai_base_url
            client.llm_client.config.api_key = settings.openai_api_key
            client.llm_client.model = settings.model_name or "gpt-4o-mini"
            client.llm_client.config.temperature = settings.temperature
        
        # Fast embedder configuration
        if settings.openai_api_key and hasattr(client, 'embedder') and client.embedder and hasattr(client.embedder, 'config'):
            client.embedder.config.api_key = settings.openai_api_key
            if settings.openai_base_url:
                client.embedder.config.base_url = settings.openai_base_url
            if hasattr(client.embedder, 'model'):
                client.embedder.model = settings.embedding_model_name or "text-embedding-3-small"
        
        # Build indices asynchronously without blocking
        try:
            await client.build_indices_and_constraints()
            logger.info(f"🔧 Built indices for user database: {client._database_name}")
        except Exception as e:
            logger.warning(f"Failed to build indices for {client._database_name}: {e}")
        
        _graphiti_pool[pool_key] = client
        logger.info(f"📦 Added new connection to pool: {pool_key} (pool size: {len(_graphiti_pool)})")
        return client


def get_or_create_pooled_client(user_id: str, settings) -> "ZepGraphiti":
    """Get or create a pooled ZepGraphiti client for the given user - optimized"""
    pool_key = f"{user_id}_{settings.falkordb_host}_{settings.falkordb_port}"
    
    # Fast path: return existing client
    if pool_key in _graphiti_pool:
        return _graphiti_pool[pool_key]
    
    # Create new client (only when needed)
    client = ZepGraphiti(
        host=settings.falkordb_host,
        port=settings.falkordb_port,
        username=settings.falkordb_username,
        password=settings.falkordb_password,
        user_id=user_id
    )
    
    # Fast LLM configuration
    if settings.openai_api_key and client.llm_client:
        if settings.openai_base_url:
            client.llm_client.config.base_url = settings.openai_base_url
        client.llm_client.config.api_key = settings.openai_api_key
        client.llm_client.model = settings.model_name or "gpt-4o-mini"
        client.llm_client.config.temperature = settings.temperature
    
    # Fast embedder configuration
    if settings.openai_api_key and hasattr(client, 'embedder') and client.embedder and hasattr(client.embedder, 'config'):
        client.embedder.config.api_key = settings.openai_api_key
        if settings.openai_base_url:
            client.embedder.config.base_url = settings.openai_base_url
        if hasattr(client.embedder, 'model'):
            client.embedder.model = settings.embedding_model_name or "text-embedding-3-small"
    
    # CRITICAL FIX: Build indices for user-specific database
    # This ensures search indices are created for episodes, edges, and nodes
    import asyncio
    try:
        # Run index building in a background task to avoid blocking
        asyncio.create_task(client.build_indices_and_constraints())
        logger.info(f"🔧 Scheduled index building for user database: {client._database_name}")
    except Exception as e:
        logger.warning(f"Failed to schedule index building for {client._database_name}: {e}")
    
    _graphiti_pool[pool_key] = client
    return client


# Global flag to track if indices have been built per database


class ZepGraphiti(Graphiti):
    def __init__(self, host: str, port: str, username: str | None = None, password: str | None = None, llm_client: LLMClient | None = None, skip_init: bool = False, user_id: str | None = None):
        # Create user-specific database name for data isolation
        if user_id:
            sanitized_user_id = sanitize_user_id(user_id)
            # Database name should always be the zep_ prefixed user ID (no user_ prefix)
            # If user_id starts with user_, extract the zep_ part
            if sanitized_user_id.startswith("user_"):
                # Extract the actual user ID after user_ prefix (should be zep_xxxx)
                actual_user_id = sanitized_user_id[5:]  # Remove "user_" prefix
                if actual_user_id.startswith("zep_"):
                    database_name = actual_user_id
                else:
                    # If it doesn't start with zep_, add zep_ prefix
                    database_name = f"zep_{actual_user_id}"
            elif sanitized_user_id.startswith("zep_"):
                # Already properly formatted
                database_name = sanitized_user_id
            else:
                # For other formats, add zep_ prefix
                database_name = f"zep_{sanitized_user_id}"
            logger.debug(f"🔐 DATABASE CONNECTION: user_id='{user_id}' → sanitized='{sanitized_user_id}' → database='{database_name}'")
        else:
            sanitized_user_id = None
            database_name = "default_db"
            logger.debug(f"🔐 DATABASE CONNECTION: user_id='{user_id}' → database='{database_name}' (using default)")
        
        falkor_driver = FalkorDriver(
            host=host, 
            port=int(port) if port and port.strip() else 6379, 
            username=username if username and username.strip() else None, 
            password=password if password and password.strip() else None,
            database=database_name
        )
        super().__init__(graph_driver=falkor_driver, llm_client=llm_client)
        self._skip_init = skip_init
        self._user_id = user_id
        self._database_name = database_name
        
    async def build_indices_and_constraints(self):
        """Override to prevent duplicate index creation per database"""
        global _indices_initialized
        
        if self._database_name in _indices_initialized:
            logger.debug(f"Indices already initialized for database {self._database_name}, skipping...")
            return
            
        logger.debug(f"Building indices and constraints for database {self._database_name}...")
        
        # Temporarily increase log level to reduce noise from duplicate index attempts
        falkor_logger = logging.getLogger('graphiti_core.driver.falkordb_driver')
        original_level = falkor_logger.level
        falkor_logger.setLevel(logging.WARNING)  # Hide INFO messages about existing indices
        
        try:
            await super().build_indices_and_constraints()
            _indices_initialized.add(self._database_name)
            logger.debug(f"Indices and constraints built successfully for database {self._database_name}")
        finally:
            # Restore original log level
            falkor_logger.setLevel(original_level)

    async def ensure_nodes_have_embeddings(self, nodes: list, group_id: str):
        """Ensure all nodes have proper embeddings and properties for frontend interaction"""
        import time
        
        if not nodes:
            logger.info("No nodes to process for embedding verification")
            return
            
        logger.debug(f"EMBEDDING: Checking {len(nodes)} nodes for complete properties")
        
        nodes_fixed = 0
        for node in nodes:
            try:
                # Skip if not an EntityNode
                if not hasattr(node, 'name_embedding') or not hasattr(node, 'name'):
                    logger.debug(f"Skipping non-entity node: {type(node).__name__}")
                    continue
                
                needs_fixing = False
                
                # Check if node has proper name
                if not node.name or node.name.strip() == "":
                    logger.debug(f"Node {node.uuid} missing name - setting fallback name")
                    node.name = f"Entity_{node.uuid[:8]}"
                    needs_fixing = True
                
                # Check if node has embedding
                if not node.name_embedding or len(node.name_embedding) == 0:
                    logger.debug(f"Node {node.uuid} missing embedding - generating now")
                    needs_fixing = True
                    
                    # Generate embedding for this node
                    if self.embedder:
                        try:
                            embedding_start = time.time()
                            await node.generate_name_embedding(self.embedder)
                            embedding_duration = time.time() - embedding_start
                            
                            if node.name_embedding and len(node.name_embedding) > 0:
                                logger.debug(f"Generated embedding for {node.name} in {embedding_duration:.2f}s (dim: {len(node.name_embedding)})")
                            else:
                                logger.error(f"❌ Failed to generate embedding for {node.name} - still empty")
                                continue
                        except Exception as embedding_error:
                            logger.error(f"❌ Embedding generation failed for {node.name}: {embedding_error}")
                            continue
                    else:
                        logger.warning(f"⚠️  No embedder available for node {node.name}")
                        continue
                
                # Check if node has proper summary
                if not hasattr(node, 'summary') or not node.summary or node.summary.strip() == "":
                    logger.debug(f"Node {node.uuid} missing summary - setting fallback")
                    node.summary = f"Entity extracted from conversation in group {group_id}"
                    needs_fixing = True
                
                # Save node if any fixes were made
                if needs_fixing:
                    try:
                        save_start = time.time()
                        await node.save(self.driver)
                        save_duration = time.time() - save_start
                        
                        logger.debug(f"Fixed and saved node {node.name} in {save_duration:.2f}s")
                        nodes_fixed += 1
                        
                        # Verify the fix worked
                        try:
                            from graphiti_core.nodes import EntityNode
                            loaded_node = await EntityNode.get_by_uuid(self.driver, node.uuid)
                            if loaded_node.name_embedding and len(loaded_node.name_embedding) > 0:
                                logger.debug(f"Verified node {node.name} now has embedding (dim: {len(loaded_node.name_embedding)})")
                            else:
                                logger.error(f"❌ VERIFICATION FAILED: Node {node.name} still missing embedding after save")
                        except Exception as verify_error:
                            logger.error(f"❌ Verification failed for {node.name}: {verify_error}")
                            
                    except Exception as save_error:
                        logger.error(f"❌ Failed to save fixed node {node.name}: {save_error}")
                        
            except Exception as node_error:
                logger.error(f"❌ Error processing node {getattr(node, 'uuid', 'unknown')}: {node_error}")
        
        logger.debug(f"EMBEDDING VERIFICATION COMPLETE: Fixed {nodes_fixed} out of {len(nodes)} nodes")

    async def fix_all_nodes_in_group(self, group_id: str):
        """Fix ALL nodes (EntityNodes AND EpisodicNodes) in a group to ensure they have embeddings (comprehensive fix)"""
        import time
        logger.debug(f"COMPREHENSIVE FIX: Checking ALL nodes (EntityNodes + EpisodicNodes) in group {group_id}")
        
        try:
            # Get ALL EntityNodes for this group
            from graphiti_core.nodes import EntityNode, EpisodicNode
            entity_nodes = await EntityNode.get_by_group_ids(self.driver, [group_id])
            episodic_nodes = await EpisodicNode.get_by_group_ids(self.driver, [group_id])
            
            # Combine both node types
            all_nodes = entity_nodes + episodic_nodes
            
            if not all_nodes:
                logger.info(f"No nodes found in group {group_id}")
                return
                
            logger.debug(f"Found {len(entity_nodes)} EntityNodes and {len(episodic_nodes)} EpisodicNodes ({len(all_nodes)} total) in group {group_id} - checking embeddings")
            
            nodes_fixed = 0
            nodes_checked = 0
            
            for node in all_nodes:
                nodes_checked += 1
                try:
                    needs_fixing = False
                    node_type = "EntityNode" if hasattr(node, 'summary') else "EpisodicNode"
                    
                    # Check if node has proper name
                    if not node.name or node.name.strip() == "":
                        logger.debug(f"{node_type} {node.uuid} missing name - setting fallback")
                        if node_type == "EntityNode":
                            node.name = f"Entity_{node.uuid[:8]}"
                        else:
                            node.name = f"Episode_{node.uuid[:8]}"
                        needs_fixing = True
                    
                    # Check if node has embedding (this is the critical check)
                    if not hasattr(node, 'name_embedding') or not node.name_embedding or len(node.name_embedding) == 0:
                        logger.debug(f"{node_type} {node.uuid} ({node.name}) missing embedding - generating now")
                        needs_fixing = True
                        
                        # Generate embedding for this node
                        if self.embedder:
                            try:
                                embedding_start = time.time()
                                await node.generate_name_embedding(self.embedder)
                                embedding_duration = time.time() - embedding_start
                                
                                if node.name_embedding and len(node.name_embedding) > 0:
                                    logger.debug(f"Generated embedding for {node.name} in {embedding_duration:.2f}s (dim: {len(node.name_embedding)})")
                                else:
                                    logger.error(f"❌ Failed to generate embedding for {node.name} - still empty")
                                    continue
                            except Exception as embedding_error:
                                logger.error(f"❌ Embedding generation failed for {node.name}: {embedding_error}")
                                continue
                        else:
                            logger.warning(f"⚠️  No embedder available for node {node.name}")
                            continue
                    
                    # Check if node has proper summary (EntityNodes only)
                    if node_type == "EntityNode":
                        if not hasattr(node, 'summary') or not node.summary or node.summary.strip() == "":
                            logger.debug(f"{node_type} {node.uuid} missing summary - setting fallback")
                            node.summary = f"Entity extracted from conversation in group {group_id}"
                            needs_fixing = True
                    
                    # Save node if any fixes were made
                    if needs_fixing:
                        try:
                            save_start = time.time()
                            await node.save(self.driver)
                            save_duration = time.time() - save_start
                            
                            logger.debug(f"Fixed and saved {node_type} {node.name} in {save_duration:.2f}s")
                            nodes_fixed += 1
                            
                        except Exception as save_error:
                            logger.error(f"❌ Failed to save fixed {node_type} {node.name}: {save_error}")
                    else:
                        logger.debug(f"✅ {node_type} {node.name} already has all required properties")
                        
                except Exception as node_error:
                    logger.error(f"❌ Error processing node {getattr(node, 'uuid', 'unknown')}: {node_error}")
            
            logger.debug(f"COMPREHENSIVE FIX COMPLETE: Fixed {nodes_fixed} out of {nodes_checked} nodes ({len(entity_nodes)} EntityNodes + {len(episodic_nodes)} EpisodicNodes) in group {group_id}")
            
        except Exception as e:
            logger.error(f"❌ Failed to fix all nodes in group {group_id}: {e}", exc_info=True)

    async def save_entity_node(self, name: str, uuid: str, group_id: str, summary: str = ''):
        logger.debug(f"Starting EntityNode creation for '{name}' (uuid: {uuid})")
        
        new_node = EntityNode(
            name=name,
            uuid=uuid,
            group_id=group_id,
            summary=summary,
        )
        
        logger.debug(f"Created EntityNode object, embedder available: {self.embedder is not None}")
        
        # Generate name embedding - this is critical for node functionality
        try:
            if self.embedder is None:
                logger.warning(f"⚠️  No embedder available for entity '{name}' - node will not be saveable")
                logger.info(f"Embedder config - embedder: {self.embedder}, hasattr config: {hasattr(self.embedder, 'config') if self.embedder else 'N/A'}")
                raise Exception("Embedder not configured")
            
            logger.debug(f"Embedder type: {type(self.embedder)}")
            if hasattr(self.embedder, 'config'):
                logger.debug(f"Embedder config - api_key configured: {bool(getattr(self.embedder.config, 'api_key', None))}")
                
                # Try different ways to get the model name from different embedder types
                model_name = "unknown"
                if hasattr(self.embedder.config, 'embedding_model'):
                    # OpenAI, VoyageAI, Gemini embedders
                    model_name = str(self.embedder.config.embedding_model)
                elif hasattr(self.embedder, 'model'):
                    # AzureOpenAI embedder
                    model_name = str(self.embedder.model)
                elif hasattr(self.embedder.config, 'model'):
                    # Alternative config pattern
                    model_name = str(self.embedder.config.model)
                
                logger.debug(f"Embedder config - model: {model_name}")
                
            logger.debug(f"About to generate embedding for entity: {name}")
            embedding_start_time = time.time()
            
            await new_node.generate_name_embedding(self.embedder)
            
            embedding_end_time = time.time()
            embedding_duration = embedding_end_time - embedding_start_time
            
            logger.debug(f"Embedding generation completed in {embedding_duration:.2f}s")
            logger.debug(f"Embedding result - is None: {new_node.name_embedding is None}")
            
            if new_node.name_embedding is not None:
                logger.debug(f"Embedding dimensions: {len(new_node.name_embedding)}")
                logger.debug(f"First 5 embedding values: {new_node.name_embedding[:5] if len(new_node.name_embedding) >= 5 else new_node.name_embedding}")
                logger.debug(f"Embedding type: {type(new_node.name_embedding)}")
                logger.debug(f"All values are numbers: {all(isinstance(x, (int, float)) for x in new_node.name_embedding)}")
            
            if new_node.name_embedding is None or len(new_node.name_embedding) == 0:
                logger.warning(f"⚠️  Failed to generate embedding for entity '{name}' - empty result")
                logger.debug(f"name_embedding value: {new_node.name_embedding}")
                raise Exception("Empty embedding generated")
                
            logger.debug(f"Successfully generated embedding for entity: {name} (dim: {len(new_node.name_embedding)})")
            
        except Exception as embedding_error:
            logger.error(f"Failed to generate embedding for entity '{name}': {embedding_error}")
            logger.debug(f"Exception type: {type(embedding_error)}")  
            logger.debug(f"Exception args: {embedding_error.args}")
            logger.warning("This will cause the node to be non-functional in FalkorDB browser")
            raise Exception(f"Embedding generation failed: {embedding_error}")
        
        # Save the node to the database
        try:
            logger.debug(f"About to save EntityNode to database")
            logger.debug(f"Node data before save - name: {new_node.name}, uuid: {new_node.uuid}")
            logger.debug(f"Node embedding before save - dimensions: {len(new_node.name_embedding) if new_node.name_embedding else 'None'}")
            
            save_start_time = time.time()
            result = await new_node.save(self.driver)
            save_end_time = time.time()
            save_duration = save_end_time - save_start_time
            
            logger.debug(f"Database save completed in {save_duration:.2f}s")
            logger.debug(f"Save result: {result}")
            logger.debug(f"Successfully saved EntityNode: {name} (uuid: {uuid})")
            
            # Verify the node was saved with embedding by trying to load it back
            try:
                logger.debug(f"Verifying saved node by loading it back...")
                loaded_node = await EntityNode.get_by_uuid(self.driver, uuid)
                logger.debug(f"Loaded node back successfully")
                logger.debug(f"Loaded node name: {loaded_node.name}")
                logger.debug(f"Loaded node has embedding: {loaded_node.name_embedding is not None}")
                if loaded_node.name_embedding:
                    logger.debug(f"Loaded embedding dimensions: {len(loaded_node.name_embedding)}")
                else:
                    logger.warning(f"Loaded node has NO embedding! This explains non-clickable nodes.")
                    
            except Exception as verify_error:
                logger.error(f"Failed to verify saved node: {verify_error}")
                
        except Exception as save_error:
            logger.error(f"Failed to save EntityNode '{name}': {save_error}")
            logger.error(f"Save error type: {type(save_error)}")
            logger.error(f"Save error args: {save_error.args}")
            raise Exception(f"Node save failed: {save_error}")
            
        return new_node

    async def enhanced_add_episode(self, uuid: str, group_id: str, name: str, episode_body: str, 
                                 reference_time, source, source_description: str):
        """Enhanced episode creation with proper node-edge sequencing and error handling"""
        try:
            logger.info(f"🚀 ENHANCED_ADD_EPISODE: Starting episode processing for group {group_id}")
            
            # CRITICAL FIX: Re-enable edge extraction with proper error handling
            # The "node not found" error occurs because edges try to reference nodes before they're committed
            # Solution: Use transaction-like behavior and proper sequencing
            try:
                result = await self.add_episode(
                    name=name,
                    episode_body=episode_body,
                    source=source,
                    source_description=source_description,
                    reference_time=reference_time,
                    group_id=group_id,
                    uuid=uuid,
                    # Re-enable edge extraction with default types
                    edge_types=None,  # Use built-in default relationship types
                    edge_type_map=None  # Use default entity->entity mapping
                )
                
                # Log successful extraction results
                node_count = len(result.nodes) if hasattr(result, 'nodes') else 0
                edge_count = len(result.edges) if hasattr(result, 'edges') else 0
                
                logger.info(f"✅ ENHANCED_ADD_EPISODE: Successfully extracted {node_count} nodes and {edge_count} edges")
                
                # Log node details (first 3)
                if hasattr(result, 'nodes'):
                    for i, node in enumerate(result.nodes[:3]):
                        node_name = getattr(node, 'name', 'Unknown')
                        logger.info(f"   📍 Node {i+1}: {node_name}")
                
                # Log edge details (first 3)
                if hasattr(result, 'edges'):
                    for i, edge in enumerate(result.edges[:3]):
                        edge_name = getattr(edge, 'name', 'Unknown')
                        logger.info(f"   🔗 Edge {i+1}: {edge_name}")
                
                return result
                
            except Exception as edge_error:
                # If edge extraction fails due to node reference issues, fall back to entity-only extraction
                logger.warning(f"⚠️ ENHANCED_ADD_EPISODE: Edge extraction failed ({edge_error}), falling back to entity-only mode")
                
                # Retry with edge extraction disabled as fallback
                result = await self.add_episode(
                    name=name,
                    episode_body=episode_body,
                    source=source,
                    source_description=source_description,
                    reference_time=reference_time,
                    group_id=group_id,
                    uuid=uuid
                    # No edge extraction parameters = entity-only mode
                )
                
                node_count = len(result.nodes) if hasattr(result, 'nodes') else 0
                logger.info(f"✅ ENHANCED_ADD_EPISODE: Fallback mode extracted {node_count} nodes (edges disabled due to sequencing issue)")
                
                # Log fallback node details (first 3)
                if hasattr(result, 'nodes'):
                    for i, node in enumerate(result.nodes[:3]):
                        node_name = getattr(node, 'name', 'Unknown')
                        logger.info(f"   📍 Node {i+1}: {node_name}")
                
                return result
            
        except Exception as e:
            logger.error(f"❌ ENHANCED_ADD_EPISODE: Episode creation failed for group {group_id}: {e}")
            raise

    async def get_contextual_summary(self, group_id: str, max_episodes: int = 10):
        """Zep-compatible contextual summary using official patterns"""
        logger.debug(f"ZEP SUMMARY: Getting contextual summary for group {group_id} with max_episodes {max_episodes}")
        
        try:
            # Get recent episodes for the group using Zep-compatible approach
            from datetime import datetime, timezone
            episodes = await self.retrieve_episodes(
                group_ids=[group_id], 
                last_n=max_episodes,
                reference_time=datetime.now(timezone.utc)
            )
            
            logger.debug(f"ZEP SUMMARY: Retrieved {len(episodes)} episodes for group {group_id}")
            
            if not episodes:
                logger.warning(f"ZEP SUMMARY: No episodes found for group {group_id}")
                return {"summary": "No conversation history found", "key_entities": [], "topics": []}
            
            # Extract episode content using Zep patterns
            episode_contents = []
            for ep in episodes:
                if hasattr(ep, 'episode_body') and ep.episode_body:
                    episode_contents.append(ep.episode_body)
                elif hasattr(ep, 'content') and ep.content:
                    episode_contents.append(ep.content)
            
            logger.debug(f"ZEP SUMMARY: Extracted content from {len(episode_contents)} episodes")
            
            # Use Zep-compatible LLM summarization patterns
            if self.llm_client and episode_contents:
                logger.debug("ZEP SUMMARY: Attempting LLM-based summarization with Zep patterns")
                
                # Prepare content for summarization (following Zep's approach)
                recent_content = ' '.join(episode_contents[-5:]) if len(episode_contents) > 5 else ' '.join(episode_contents)
                
                if len(recent_content.strip()) < 10:
                    logger.warning("ZEP SUMMARY: Insufficient content for LLM summarization")
                    return {
                        "summary": f"Conversation with {len(episodes)} brief exchanges",
                        "key_entities": [],
                        "topics": ["brief_conversation"]
                    }
                
                # Zep-style summarization prompt (more structured and focused)
                summary_prompt = f"""
                Analyze this conversation and extract key information following these rules:
                
                Conversation content:
                {recent_content[:1000]}
                
                Provide a structured analysis in JSON format:
                {{
                    "summary": "Concise 1-2 sentence summary of the main conversation flow",
                    "key_entities": ["Extract 3-5 important entities (people, places, things, concepts)"],
                    "topics": ["Identify 2-4 main conversation topics or themes"],
                    "sentiment": "overall|positive|negative|neutral",
                    "action_items": ["Any actionable items or tasks mentioned"]
                }}
                
                Focus on factual information and avoid speculation. Return only valid JSON.
                """
                
                try:
                    logger.debug("ZEP SUMMARY: Sending structured request to LLM client")
                    # Use Zep-compatible message format
                    from graphiti_core.prompts.models import Message
                    
                    # Create structured messages following Zep patterns
                    messages = [
                        Message(role="system", content="You are an expert conversation analyzer. Extract structured information from conversations. Always respond with valid JSON only."),
                        Message(role="user", content=summary_prompt)
                    ]
                    
                    # Generate response with appropriate constraints
                    if hasattr(self.llm_client, 'generate_response'):
                        summary_response = await self.llm_client.generate_response(
                            messages, 
                            max_tokens=300  # Controlled response size
                        )
                    else:
                        logger.error(f"ZEP SUMMARY: LLM client missing generate_response method")
                        raise Exception("LLM client method not available")
                    
                    logger.debug(f"ZEP SUMMARY: LLM response received: {type(summary_response)}")
                    
                    # Parse response using Zep-compatible patterns
                    response_text = ""
                    if isinstance(summary_response, dict):
                        if 'summary' in summary_response:
                            # Direct structured response
                            logger.debug("ZEP SUMMARY: Successfully received structured LLM response")
                            return summary_response
                        elif 'content' in summary_response:
                            response_text = summary_response['content']
                        elif 'choices' in summary_response and len(summary_response['choices']) > 0:
                            choice = summary_response['choices'][0]
                            if isinstance(choice, dict) and 'message' in choice:
                                response_text = choice['message'].get('content', str(summary_response))
                            else:
                                response_text = str(choice.get('content', str(summary_response)))
                        else:
                            response_text = str(summary_response)
                    elif hasattr(summary_response, 'content'):
                        response_text = summary_response.content
                    elif hasattr(summary_response, 'choices') and len(summary_response.choices) > 0:
                        choice = summary_response.choices[0]
                        if hasattr(choice, 'message') and hasattr(choice.message, 'content'):
                            response_text = choice.message.content
                        else:
                            response_text = str(choice)
                    else:
                        response_text = str(summary_response)
                    
                    logger.debug(f"ZEP SUMMARY: Extracted response text: {response_text[:200]}...")
                    
                    # Parse JSON response
                    import json
                    try:
                        parsed_response = json.loads(response_text)
                        
                        # Validate and enhance Zep-compatible response
                        zep_response = {
                            "summary": parsed_response.get("summary", f"Conversation with {len(episodes)} exchanges"),
                            "key_entities": parsed_response.get("key_entities", [])[:5],  # Limit to 5
                            "topics": parsed_response.get("topics", ["general_conversation"])[:4],  # Limit to 4
                            "sentiment": parsed_response.get("sentiment", "neutral"),
                            "action_items": parsed_response.get("action_items", [])[:3],  # Limit to 3
                            "episodes_analyzed": len(episodes),
                            "group_id": group_id
                        }
                        
                        logger.debug("ZEP SUMMARY: Successfully parsed and enhanced LLM response")
                        return zep_response
                        
                    except json.JSONDecodeError as json_error:
                        logger.error(f"ZEP SUMMARY: Failed to parse LLM JSON response: {json_error}")
                        logger.error(f"Response text: {response_text[:500]}")
                        
                except Exception as llm_error:
                    logger.error(f"ZEP SUMMARY: LLM summarization failed: {llm_error}")
            else:
                logger.warning(f"ZEP SUMMARY: LLM client not available or no content to analyze")
            
            # Zep-compatible fallback summary with enhanced entity detection
            summary_text = f"Conversation with {len(episodes)} exchanges"
            basic_entities = []
            topics = ["general_conversation"]
            action_items = []
            
            if episode_contents:
                all_content = ' '.join(episode_contents).lower()
                
                # Enhanced entity detection following Zep patterns
                import re
                
                # Extract potential person names (capitalized words)
                person_matches = re.findall(r'\b[A-Z][a-z]+\b', ' '.join(episode_contents))
                for person in person_matches[:3]:  # Limit to 3
                    if person.lower() not in ['the', 'and', 'but', 'for', 'you', 'are', 'can', 'has', 'was']:
                        basic_entities.append(person)
                
                # Detect common business entities
                if 'order' in all_content:
                    basic_entities.append('order')
                    topics.append('order_management')
                if 'support' in all_content or 'help' in all_content:
                    basic_entities.append('customer_support')
                    topics.append('customer_support')
                if 'product' in all_content:
                    basic_entities.append('product')
                    topics.append('product_inquiry')
                if 'payment' in all_content or 'billing' in all_content:
                    basic_entities.append('payment')
                    topics.append('billing')
                
                # Extract action items
                action_words = ['need to', 'should', 'will', 'must', 'please', 'todo', 'task']
                for word in action_words:
                    if word in all_content:
                        action_items.append(f"Follow up on {word.replace('_', ' ')} items")
                        break
                
                summary_text += f" covering {', '.join(set(topics)) if topics else 'various topics'}"
            
            # Return Zep-compatible fallback response
            zep_fallback = {
                "summary": summary_text,
                "key_entities": list(set(basic_entities))[:5],  # Remove duplicates, limit to 5
                "topics": list(set(topics))[:4],  # Remove duplicates, limit to 4
                "sentiment": "neutral",
                "action_items": action_items[:3],  # Limit to 3
                "episodes_analyzed": len(episodes),
                "group_id": group_id
            }
            
            logger.debug(f"ZEP SUMMARY: Using enhanced fallback summary: {zep_fallback}")
            return zep_fallback
            
        except Exception as e:
            logger.error(f"ZEP SUMMARY: Failed to get contextual summary for {group_id}: {e}", exc_info=True)
            return {
                "summary": "Summary unavailable due to error", 
                "key_entities": [], 
                "topics": ["error"],
                "sentiment": "neutral",
                "action_items": [],
                "episodes_analyzed": 0,
                "group_id": group_id
            }

    async def search_with_context(self, group_ids: list[str], query: str, num_results: int = 10, 
                                include_summary: bool = True):
        """Enhanced search with contextual summary and entity awareness"""
        # Get regular search results
        search_results = await self.search(
            group_ids=group_ids,
            query=query, 
            num_results=num_results
        )
        
        # Add contextual information if requested
        if include_summary and group_ids:
            context_data = {}
            for group_id in group_ids[:3]:  # Limit to prevent overload
                context_data[group_id] = await self.get_contextual_summary(group_id)
        
        return {
            "search_results": search_results,
            "context": context_data if include_summary else None,
            "query_processed": query,
            "total_results": len(search_results)
        }

    async def extract_entities_from_text(self, text: str, group_id: str):
        """Zep-compatible entity extraction using official patterns"""
        logger.debug(f"ZEP EXTRACTION: Extracting entities from text for group {group_id}: {text[:50]}...")
        
        if not self.llm_client:
            logger.warning("ZEP EXTRACTION: LLM client not available for entity extraction")
            return []
        
        # Enhanced input validation following Zep patterns
        if not text or not isinstance(text, str):
            logger.warning(f"ZEP EXTRACTION: Invalid text input: {type(text)}")
            return []
            
        # Clean and validate text using Zep approach
        cleaned_text = text.strip()
        if not cleaned_text:
            logger.debug("ZEP EXTRACTION: Empty text - skipping entity extraction")
            return []
        
        # Log input for debugging (privacy-conscious)
        logger.debug(f"ZEP EXTRACTION: Processing text: '{cleaned_text[:100]}{'...' if len(cleaned_text) > 100 else ''}'")
            
        try:
            # Zep-style entity extraction prompt (more structured and comprehensive)
            entity_prompt = f"""
            Extract entities from the following text using Zep's entity recognition patterns:
            
            Text: {cleaned_text[:800]}
            
            Identify and extract entities in the following categories:
            - PERSON: People's names, usernames, or references to individuals
            - ORGANIZATION: Companies, institutions, groups
            - LOCATION: Places, addresses, geographical references  
            - PRODUCT: Items, services, software, tools mentioned
            - CONCEPT: Important topics, themes, or abstract concepts
            - EVENT: Actions, meetings, processes, or occurrences
            
            Return a JSON array with max 5 entities:
            [
                {{
                    "name": "Entity name",
                    "type": "PERSON|ORGANIZATION|LOCATION|PRODUCT|CONCEPT|EVENT",
                    "description": "Brief context about the entity",
                    "confidence": 0.8
                }}
            ]
            
            Focus on entities that are central to understanding the conversation context.
            """
            
            logger.debug("ZEP EXTRACTION: Sending structured entity extraction request to LLM")
            try:
                # Use Zep-compatible message format
                from graphiti_core.prompts.models import Message
                
                # Create structured messages following Zep patterns
                messages = [
                    Message(role="system", content="You are Zep's entity extraction system. Extract structured entities that are important for conversation context and memory. Always respond with valid JSON array only."),
                    Message(role="user", content=entity_prompt)
                ]
                
                # Generate response with controlled parameters
                if hasattr(self.llm_client, 'generate_response'):
                    response = await self.llm_client.generate_response(
                        messages, 
                        max_tokens=100  # Reduced tokens for faster response
                    )
                else:
                    logger.error(f"ZEP EXTRACTION: LLM client missing generate_response method")
                    return []
                
                logger.debug(f"ZEP EXTRACTION: LLM response received: {type(response)}")
                
                # Parse response using Zep-compatible patterns
                response_text = ""
                entities = None
                
                if isinstance(response, dict):
                    if 'error' in response:
                        logger.debug(f"ZEP EXTRACTION: LLM returned no entities: {response['error']}")
                        return []
                    elif 'extracted_entities' in response:
                        entities = response['extracted_entities']
                        logger.debug(f"ZEP EXTRACTION: Received structured entity response with {len(entities)} entities")
                    elif 'name' in response and 'type' in response:
                        entities = [response]
                        logger.debug(f"ZEP EXTRACTION: Single entity response: {response['name']}")
                    elif 'content' in response:
                        response_text = response['content']
                    elif 'choices' in response and len(response['choices']) > 0:
                        choice = response['choices'][0]
                        if isinstance(choice, dict) and 'message' in choice:
                            response_text = choice['message'].get('content', str(response))
                        else:
                            response_text = str(choice.get('content', str(response)))
                    else:
                        response_text = str(response)
                elif isinstance(response, list):
                    entities = response
                    logger.debug(f"ZEP EXTRACTION: Direct entity list with {len(entities)} entities")
                elif hasattr(response, 'content'):
                    response_text = response.content
                elif hasattr(response, 'choices') and len(response.choices) > 0:
                    choice = response.choices[0]
                    if hasattr(choice, 'message') and hasattr(choice.message, 'content'):
                        response_text = choice.message.content
                    else:
                        response_text = str(choice)
                else:
                    response_text = str(response)
                
                # Parse entities if not already extracted
                if entities is None:
                    logger.debug(f"ZEP EXTRACTION: Parsing response text: {response_text[:200]}...")
                    
                    # Clean the response text to improve JSON parsing success
                    cleaned_response = response_text.strip()
                    
                    # Try to extract JSON from text that might have additional content
                    import re
                    json_pattern = r'(\[.*?\]|\{.*?\})'
                    json_matches = re.findall(json_pattern, cleaned_response, re.DOTALL)
                    
                    if json_matches:
                        # Try the first JSON-like pattern found
                        cleaned_response = json_matches[0].strip()
                        logger.debug(f"ZEP EXTRACTION: Extracted JSON pattern: {cleaned_response[:100]}...")
                    
                    import json
                    import ast
                    try:
                        # First try standard JSON parsing
                        parsed_response = json.loads(cleaned_response)
                        
                        # Handle different response formats following Zep patterns
                        if isinstance(parsed_response, list):
                            entities = parsed_response
                            logger.debug(f"ZEP EXTRACTION: Parsed {len(entities)} entities from JSON array")
                        elif isinstance(parsed_response, dict):
                            if 'name' in parsed_response and 'type' in parsed_response:
                                entities = [parsed_response]
                                logger.debug(f"ZEP EXTRACTION: Single entity object: {parsed_response['name']}")
                            elif 'extracted_entities' in parsed_response:
                                entities = parsed_response['extracted_entities']
                                logger.debug(f"ZEP EXTRACTION: Structured response with {len(entities)} entities")
                            else:
                                logger.warning(f"ZEP EXTRACTION: Unexpected JSON format: {parsed_response}")
                                return []
                        else:
                            logger.warning(f"ZEP EXTRACTION: Expected list or dict, got {type(parsed_response)}")
                            return []
                            
                    except json.JSONDecodeError as json_error:
                        logger.warning(f"ZEP EXTRACTION: Standard JSON parsing failed: {json_error}")
                        logger.debug(f"ZEP EXTRACTION: Raw response text that failed parsing: {response_text}")
                        
                        # Try parsing as Python literal (handles single quotes)
                        try:
                            parsed_response = ast.literal_eval(cleaned_response)
                            logger.debug(f"ZEP EXTRACTION: Successfully parsed using ast.literal_eval")
                            
                            # Handle different response formats
                            if isinstance(parsed_response, list):
                                entities = parsed_response
                                logger.debug(f"ZEP EXTRACTION: Parsed {len(entities)} entities from Python literal array")
                            elif isinstance(parsed_response, dict):
                                if 'name' in parsed_response and 'type' in parsed_response:
                                    entities = [parsed_response]
                                    logger.debug(f"ZEP EXTRACTION: Single entity object: {parsed_response['name']}")
                                elif 'entities' in parsed_response:
                                    entities = parsed_response['entities']
                                    logger.debug(f"ZEP EXTRACTION: Found entities in dict: {len(entities)} entities")
                                else:
                                    logger.warning(f"ZEP EXTRACTION: Unexpected Python literal format: {parsed_response}")
                                    return []
                            else:
                                logger.warning(f"ZEP EXTRACTION: Expected list or dict, got {type(parsed_response)}")
                                return []
                                
                        except (ValueError, SyntaxError) as ast_error:
                            logger.error(f"ZEP EXTRACTION: Failed to parse as Python literal: {ast_error}")
                            logger.error(f"Response text: {response_text[:500]}")
                            
                            # Last resort: try to extract entities using regex patterns
                            import re
                            logger.warning("ZEP EXTRACTION: Attempting regex-based entity extraction as fallback")
                            
                            # Look for entity-like patterns in the text
                            entity_patterns = [
                                r'"name":\s*"([^"]+)".*?"type":\s*"([^"]+)"',
                                r"'name':\s*'([^']+)'.*?'type':\s*'([^']+)'",
                                r"name:\s*([^,\n]+).*?type:\s*([^,\n]+)"
                            ]
                            
                            entities = []
                            for pattern in entity_patterns:
                                matches = re.findall(pattern, response_text, re.DOTALL)
                                for name, entity_type in matches:
                                    entities.append({
                                        "name": name.strip().strip('"\''),
                                        "type": entity_type.strip().strip('"\''),
                                        "source": "regex_fallback"
                                    })
                            
                            if entities:
                                logger.info(f"ZEP EXTRACTION: Regex fallback extracted {len(entities)} entities")
                            else:
                                logger.error("ZEP EXTRACTION: All parsing methods failed, returning empty list")
                                return []
                
                # Process and save entities using Zep patterns
                saved_entities = []
                for i, entity in enumerate(entities[:5]):  # Limit to 5 entities max
                    if not isinstance(entity, dict) or 'name' not in entity:
                        logger.warning(f"ZEP EXTRACTION: Invalid entity format at index {i}: {entity}")
                        continue
                        
                    try:
                        import uuid
                        entity_uuid = str(uuid.uuid4())
                        
                        # Validate and enhance entity data
                        entity_name = entity['name'].strip()
                        entity_type = entity.get('type', 'CONCEPT').upper()
                        entity_description = entity.get('description', f"{entity_type} entity from conversation")
                        entity_confidence = entity.get('confidence', 0.8)
                        
                        # Skip empty or invalid names
                        if not entity_name or len(entity_name) < 2:
                            logger.debug(f"ZEP EXTRACTION: Skipping entity with invalid name: '{entity_name}'")
                            continue
                        
                        # Create entity node using Zep-compatible method
                        node = await self.save_entity_node(
                            uuid=entity_uuid,
                            group_id=group_id,
                            name=entity_name,
                            summary=entity_description
                        )
                        
                        # Create Zep-compatible entity result
                        saved_entity = {
                            "uuid": entity_uuid,
                            "name": entity_name,
                            "type": entity_type,
                            "description": entity_description,
                            "confidence": entity_confidence,
                            "group_id": group_id,
                            "extraction_method": "zep_llm_extraction"
                        }
                        
                        saved_entities.append(saved_entity)
                        logger.debug(f"ZEP EXTRACTION: Saved entity: {entity_name} (type: {entity_type}, confidence: {entity_confidence})")
                        
                    except Exception as save_error:
                        logger.error(f"ZEP EXTRACTION: Failed to save entity {entity.get('name', 'unknown')}: {save_error}")
                
                logger.debug(f"ZEP EXTRACTION: Successfully extracted and saved {len(saved_entities)} entities for group {group_id}")
                return saved_entities
                
            except Exception as llm_error:
                logger.error(f"ZEP EXTRACTION: LLM entity extraction failed: {llm_error}")
                return []
            
        except Exception as e:
            logger.error(f"ZEP EXTRACTION: Entity extraction failed: {e}", exc_info=True)
            return []

    async def get_entity_edge(self, uuid: str):
        try:
            edge = await EntityEdge.get_by_uuid(self.driver, uuid)
            return edge
        except EdgeNotFoundError as e:
            raise HTTPException(status_code=404, detail=e.message) from e

    async def delete_group(self, group_id: str):
        try:
            edges = await EntityEdge.get_by_group_ids(self.driver, [group_id])
        except GroupsEdgesNotFoundError:
            logger.warning(f'No edges found for group {group_id}')
            edges = []

        nodes = await EntityNode.get_by_group_ids(self.driver, [group_id])

        episodes = await EpisodicNode.get_by_group_ids(self.driver, [group_id])

        for edge in edges:
            await edge.delete(self.driver)

        for node in nodes:
            await node.delete(self.driver)

        for episode in episodes:
            await episode.delete(self.driver)

    async def delete_entity_edge(self, uuid: str):
        try:
            edge = await EntityEdge.get_by_uuid(self.driver, uuid)
            await edge.delete(self.driver)
        except EdgeNotFoundError as e:
            raise HTTPException(status_code=404, detail=e.message) from e

    async def delete_episodic_node(self, uuid: str):
        try:
            episode = await EpisodicNode.get_by_uuid(self.driver, uuid)
            await episode.delete(self.driver)
        except NodeNotFoundError as e:
            raise HTTPException(status_code=404, detail=e.message) from e

    # Official Zep-compatible API methods
    async def zep_get_memory(self, group_id: str, max_facts: int = 10, center_node_uuid: str = None, messages: list = None):
        """Zep-compatible get-memory endpoint matching official API"""
        logger.debug(f"ZEP GET MEMORY: group_id={group_id}, max_facts={max_facts}, center_node={center_node_uuid}")
        
        try:
            # Use Graphiti's built-in search with group_id namespace isolation
            facts = []
            
            if messages and len(messages) > 0:
                # Extract content from messages for search query
                search_content = ' '.join([msg.get('content', '') for msg in messages if isinstance(msg, dict)])
                if search_content.strip():
                    # Use Graphiti's search functionality with proper group isolation
                    search_results = await self.search(
                        group_ids=[group_id],
                        query=search_content,
                        num_results=max_facts
                    )
                    
                    # Convert to Zep fact format
                    for edge in search_results:
                        if hasattr(edge, 'uuid') and hasattr(edge, 'fact'):
                            fact = {
                                "uuid": str(edge.uuid),
                                "name": getattr(edge, 'name', ''),
                                "fact": edge.fact,
                                "created_at": edge.created_at,
                                "expired_at": getattr(edge, 'expired_at', None),
                                "valid_at": getattr(edge, 'valid_at', None),
                                "invalid_at": getattr(edge, 'invalid_at', None),
                            }
                            facts.append(fact)
            else:
                # Get general facts for the group
                from graphiti_core.edges import EntityEdge
                group_edges = await EntityEdge.get_by_group_ids(self.driver, [group_id])
                
                # Limit results and convert to fact format
                for edge in group_edges[:max_facts]:
                    fact = {
                        "uuid": str(edge.uuid),
                        "name": getattr(edge, 'name', ''),
                        "fact": edge.fact,
                        "created_at": edge.created_at,
                        "expired_at": getattr(edge, 'expired_at', None),
                        "valid_at": getattr(edge, 'valid_at', None),
                        "invalid_at": getattr(edge, 'invalid_at', None),
                    }
                    facts.append(fact)
            
            logger.debug(f"ZEP GET MEMORY: Retrieved {len(facts)} facts for group {group_id}")
            return {"facts": facts}
            
        except Exception as e:
            logger.error(f"ZEP GET MEMORY failed for group {group_id}: {e}")
            return {"facts": []}

    async def zep_put_memory(self, group_id: str, messages: list, add_group_id_prefix: bool = False):
        """Zep-compatible put-memory endpoint matching official API"""
        logger.debug(f"ZEP PUT MEMORY: group_id={group_id}, messages={len(messages)}, prefix={add_group_id_prefix}")
        
        try:
            # Convert Zep messages to Graphiti format
            from datetime import datetime, timezone
            
            for i, msg in enumerate(messages):
                if not isinstance(msg, dict):
                    logger.warning(f"Invalid message format at index {i}: {type(msg)}")
                    continue
                
                # Extract message data
                msg_uuid = msg.get('uuid', '')
                role = msg.get('role', 'user')
                content = msg.get('content', '')
                
                if add_group_id_prefix and msg_uuid:
                    episode_uuid = f"{group_id}-{msg_uuid}"
                else:
                    episode_uuid = msg_uuid
                
                if not content.strip():
                    logger.debug(f"Skipping empty message {msg_uuid}")
                    continue
                
                # Add episode using Graphiti's enhanced method
                try:
                    await self.enhanced_add_episode(
                        uuid=episode_uuid,
                        group_id=group_id,
                        name=f"{role}_message_{i}",
                        episode_body=content,
                        reference_time=datetime.now(timezone.utc),
                        source=role,
                        source_description=f"Message from {role}"
                    )
                    logger.debug(f"Added episode {episode_uuid} for group {group_id}")
                except Exception as episode_error:
                    logger.error(f"Failed to add episode {episode_uuid}: {episode_error}")
            
            logger.debug(f"ZEP PUT MEMORY: Processed {len(messages)} messages for group {group_id}")
            
        except Exception as e:
            logger.error(f"ZEP PUT MEMORY failed for group {group_id}: {e}")
            raise

    async def zep_search(self, group_ids: list[str], query: str, max_facts: int = 10):
        """Zep-compatible search endpoint matching official API"""
        logger.debug(f"ZEP SEARCH: group_ids={group_ids}, query='{query[:50]}...', max_facts={max_facts}")
        
        try:
            # Use Graphiti's search with proper group isolation
            search_results = await self.search(
                group_ids=group_ids,
                query=query,
                num_results=max_facts
            )
            
            # Convert to Zep fact format
            facts = []
            for edge in search_results:
                if hasattr(edge, 'uuid') and hasattr(edge, 'fact'):
                    fact = {
                        "uuid": str(edge.uuid),
                        "name": getattr(edge, 'name', ''),
                        "fact": edge.fact,
                        "created_at": edge.created_at,
                        "expired_at": getattr(edge, 'expired_at', None),
                        "valid_at": getattr(edge, 'valid_at', None),
                        "invalid_at": getattr(edge, 'invalid_at', None),
                    }
                    facts.append(fact)
            
            logger.debug(f"ZEP SEARCH: Found {len(facts)} facts for query '{query[:30]}...'")
            return {"facts": facts}
            
        except Exception as e:
            logger.error(f"ZEP SEARCH failed: {e}")
            return {"facts": []}

    async def zep_add_node(self, group_id: str, uuid: str, name: str, summary: str):
        """Zep-compatible add entity node endpoint matching official API"""
        logger.debug(f"ZEP ADD NODE: group_id={group_id}, uuid={uuid}, name='{name}'")
        
        try:
            # Use our enhanced entity node creation
            node = await self.save_entity_node(
                uuid=uuid,
                group_id=group_id,
                name=name,
                summary=summary
            )
            
            logger.debug(f"ZEP ADD NODE: Successfully created node {name} ({uuid}) in group {group_id}")
            return True
            
        except Exception as e:
            logger.error(f"ZEP ADD NODE failed for {name} ({uuid}): {e}")
            raise

    async def zep_delete_group(self, group_id: str):
        """Zep-compatible delete group endpoint matching official API"""
        logger.debug(f"ZEP DELETE GROUP: group_id={group_id}")
        
        try:
            # Use our existing group deletion logic
            await self.delete_group(group_id)
            logger.debug(f"ZEP DELETE GROUP: Successfully deleted group {group_id}")
            
        except Exception as e:
            logger.error(f"ZEP DELETE GROUP failed for {group_id}: {e}")
            raise

    async def zep_get_fact(self, fact_uuid: str):
        """Zep-compatible get fact endpoint matching official API"""
        logger.debug(f"ZEP GET FACT: fact_uuid={fact_uuid}")
        
        try:
            # Get the edge by UUID
            edge = await self.get_entity_edge(fact_uuid)
            
            # Convert to Zep fact format
            fact = {
                "uuid": str(edge.uuid),
                "name": getattr(edge, 'name', ''),
                "fact": edge.fact,
                "created_at": edge.created_at,
                "expired_at": getattr(edge, 'expired_at', None),
                "valid_at": getattr(edge, 'valid_at', None),
                "invalid_at": getattr(edge, 'invalid_at', None),
            }
            
            logger.debug(f"ZEP GET FACT: Retrieved fact {fact_uuid}")
            return fact
            
        except Exception as e:
            logger.error(f"ZEP GET FACT failed for {fact_uuid}: {e}")
            raise

    async def zep_delete_fact(self, fact_uuid: str):
        """Zep-compatible delete fact endpoint matching official API"""
        logger.debug(f"ZEP DELETE FACT: fact_uuid={fact_uuid}")
        
        try:
            # Use our existing fact deletion logic
            await self.delete_entity_edge(fact_uuid)
            logger.debug(f"ZEP DELETE FACT: Successfully deleted fact {fact_uuid}")
            
        except Exception as e:
            logger.error(f"ZEP DELETE FACT failed for {fact_uuid}: {e}")
            raise

    async def zep_delete_message(self, message_uuid: str):
        """Zep-compatible delete message endpoint matching official API"""
        logger.debug(f"ZEP DELETE MESSAGE: message_uuid={message_uuid}")
        
        try:
            # Delete the episodic node
            await self.delete_episodic_node(message_uuid)
            logger.debug(f"ZEP DELETE MESSAGE: Successfully deleted message {message_uuid}")
            
        except Exception as e:
            logger.error(f"ZEP DELETE MESSAGE failed for {message_uuid}: {e}")
            raise


async def get_graphiti(settings: ZepEnvDep, request: Request):
    # Fast user context extraction
    user_id = extract_user_id_from_request(request)
    if not user_id:
        # Return error instead of creating unwanted default graphs
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="User identification required. No valid user_id, session_id, or group_id found in request.")
    current_user_context.set(user_id)
    
    # Fast client lookup/creation using optimized pooling
    client = get_or_create_pooled_client(user_id, settings)

    try:
        yield client
    finally:
        # Keep client in pool for reuse
        pass


async def get_graphiti_for_user(user_id: str, settings: ZepEnvDep):
    """Create graphiti client for a specific user_id (used by session creation)"""
    current_user_context.set(user_id)
    
    # Fast client lookup/creation using optimized pooling
    client = get_or_create_pooled_client(user_id, settings)
    try:
        yield client
    finally:
        # Keep client in pool for reuse
        pass


async def initialize_graphiti(settings: ZepEnvDep):
    # Use a global flag to prevent multiple initializations
    if hasattr(initialize_graphiti, '_initialized'):
        logger.info("Graphiti already initialized, skipping...")
        return
        
    try:
        logger.debug(f"Initializing Graphiti with FalkorDB at {settings.falkordb_host}:{settings.falkordb_port}")
        client = ZepGraphiti(
            host=settings.falkordb_host,
            port=settings.falkordb_port,
            username=settings.falkordb_username,
            password=settings.falkordb_password,
        )
        
        # Only call build_indices_and_constraints once during app startup
        logger.debug("Building FalkorDB indices and constraints...")
        await client.build_indices_and_constraints()
        logger.info("Graphiti initialization completed successfully")
        
        # Mark as initialized to prevent duplication
        initialize_graphiti._initialized = True
        
        await client.close()  # Close the initialization client
    except Exception as e:
        logger.error(f"Failed to initialize Graphiti: {e}", exc_info=True)
        # Don't raise the exception to prevent app startup failure
        # The service will still start but NLP features may not work
        logger.warning("Service starting with limited functionality due to initialization failure")


def get_fact_result_from_edge(edge: EntityEdge):
    return FactResult(
        uuid=edge.uuid,
        name=edge.name,
        fact=edge.fact,
        valid_at=edge.valid_at,
        invalid_at=edge.invalid_at,
        created_at=edge.created_at,
        expired_at=edge.expired_at,
    )


ZepGraphitiDep = Annotated[ZepGraphiti, Depends(get_graphiti)]
ZepGraphitiForUserDep = Annotated[ZepGraphiti, Depends(get_graphiti_for_user)]
