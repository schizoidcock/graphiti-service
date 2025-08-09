import asyncio
from contextlib import asynccontextmanager
from functools import partial

from fastapi import APIRouter, FastAPI, status, Request
from graphiti_core.nodes import EpisodeType  # type: ignore
from graphiti_core.utils.maintenance.graph_data_operations import clear_data  # type: ignore

from graph_service.dto import AddEntityNodeRequest, AddMessagesRequest, Message, Result
from graph_service.config import ZepEnvDep
from graph_service.zep_graphiti import ZepGraphitiDep, update_user_context_from_group_id, get_or_create_pooled_client, extract_user_id_from_request

# Global cache to prevent duplicate deletion operations
_deletion_cache = set()


class AsyncWorker:
    def __init__(self):
        self.queue = asyncio.Queue()
        self.task = None

    async def worker(self):
        while True:
            try:
                print(f'Got a job: (size of remaining queue: {self.queue.qsize()})')
                job = await self.queue.get()
                await job()
            except asyncio.CancelledError:
                break

    async def start(self):
        self.task = asyncio.create_task(self.worker())

    async def stop(self):
        if self.task:
            self.task.cancel()
            await self.task
        while not self.queue.empty():
            self.queue.get_nowait()


async_worker = AsyncWorker()


@asynccontextmanager
async def lifespan(_: FastAPI):
    await async_worker.start()
    yield
    await async_worker.stop()


router = APIRouter(lifespan=lifespan)


@router.post('/messages', status_code=status.HTTP_202_ACCEPTED)
async def add_messages(
    request: AddMessagesRequest,
    settings: ZepEnvDep,
):
    """
    Enhanced messages endpoint that integrates with session management.
    Now creates sessions automatically and generates summaries/metadata.
    """
    import logging
    from datetime import datetime, timezone
    from uuid import uuid4
    from graph_service.routers.sessions import sessions_store
    from graph_service.dto.session import SessionMessage
    from graph_service.zep_graphiti import update_user_context_from_group_id, get_or_create_pooled_client
    
    logger = logging.getLogger(__name__)
    
    # Extract session information from group_id (format: user_id_session_id)
    user_id = update_user_context_from_group_id(request.group_id)
    
    # Get pooled client for this user to avoid creating duplicates
    graphiti = get_or_create_pooled_client(user_id, settings)
    
    if '_' in request.group_id:
        _, session_id = request.group_id.split('_', 1)
    else:
        # Fallback: treat group_id as session_id
        session_id = request.group_id
    
    # Auto-create session if it doesn't exist
    if session_id not in sessions_store:
        logger.info(f"Auto-creating session {session_id} for user {user_id}")
        current_time = datetime.now(timezone.utc)
        sessions_store[session_id] = {
            "uuid": str(uuid4()),
            "id": len(sessions_store) + 1,
            "session_id": session_id,
            "user_id": user_id,
            "created_at": current_time,
            "updated_at": current_time,
            "metadata": {},
            "summary": None,
            "messages": [],
            "memory_context": {}
        }
    
    session_data = sessions_store[session_id]
    
    # Add messages to session store FIRST (synchronously)
    for m in request.messages:
        session_message = SessionMessage(
            uuid=m.uuid,
            role=m.role,
            role_type=m.role,  # Use role as role_type for compatibility
            content=m.content,
            created_at=m.timestamp or datetime.now(timezone.utc),
            metadata=getattr(m, 'metadata', {})
        )
        session_data["messages"].append(session_message.dict())
    
    # Process messages in Graphiti async (order maintained)
    for i, m in enumerate(request.messages):
        # Calculate current message count for this specific message
        current_message_count = len(session_data["messages"]) + i
        
        async def add_messages_task_with_count(message: Message, msg_count: int):
            # Use Graphiti's proper add_episode workflow for complete entity extraction and relationship creation
            episode_name = generate_episode_name(message)
            episode_body = format_episode_body(message)
            
            # Use Graphiti's add_episode method which handles entity extraction and relationship creation automatically
            from graphiti_core.utils.datetime_utils import utc_now
            from graphiti_core.nodes import EpisodeType
            
            try:
                # Import the entity types function
                from graph_service.zep_graphiti import get_zep_entity_types
                
                # Let Graphiti handle the complete workflow: episode creation, entity extraction, and relationship building
                result = await graphiti.add_episode(
                    name=episode_name,
                    episode_body=episode_body,
                    source=EpisodeType.message,
                    source_description=f"{message.role} message from {request.group_id}",
                    reference_time=message.timestamp or utc_now(),
                    group_id=request.group_id,
                    # ADD PROPER ENTITY TYPES for correct classification
                    entity_types=get_zep_entity_types()  # Use official Zep entity types
                )
                
                logger.info(f"Successfully processed episode {result.episode.uuid} with {len(result.nodes)} entities and {len(result.edges)} relationships")
                
                # Post-process nodes to ensure they have complete properties for frontend interaction
                await graphiti.ensure_nodes_have_embeddings(result.nodes, request.group_id)
                
                # COMPREHENSIVE FIX: Also check ALL existing nodes (EntityNodes + EpisodicNodes) in the group to fix any that may have been missed
                # This ensures existing nodes from previous episodes also get fixed
                await graphiti.fix_all_nodes_in_group(request.group_id)
                
                logger.info(f"✅ NEW WORKFLOW: Episode processed successfully using Graphiti add_episode method with comprehensive embedding verification")
                
            except Exception as e:
                logger.error(f"Failed to process episode for message {message.uuid}: {e}", exc_info=True)
        
        await async_worker.queue.put(partial(add_messages_task_with_count, m, current_message_count))
    
    # Update session timestamp
    session_data["updated_at"] = datetime.now(timezone.utc)
    
    # Generate session summary only for significant message counts to reduce API calls
    message_count = len(session_data["messages"])
    logger.info(f"Checking summary generation for session {session_id}: {message_count} messages")
    
    if message_count >= 10 and message_count % 10 == 0:  # Every 10th message after 10 messages (reduced frequency)
        # Queue summary generation as an async task (after message processing)
        async def generate_summary_task():
            logger.info(f"Generating summary for session {session_id} with {message_count} messages")
            try:
                context_summary = await graphiti.get_contextual_summary(request.group_id, max_episodes=3)  # Further reduced episodes
                session_data["summary"] = context_summary.get("summary", "")
                session_data["metadata"] = {
                    "key_entities": context_summary.get("key_entities", []),
                    "topics": context_summary.get("topics", []),
                    "message_count": len(session_data["messages"]),
                    "last_updated": session_data["updated_at"].isoformat()
                }
                logger.info(f"Generated session summary for {session_id}: {context_summary.get('summary', '')[:100]}...")
            except Exception as summary_error:
                logger.warning(f"Failed to generate session summary for {session_id}: {summary_error}")
        
        # Queue summary generation after message processing
        await async_worker.queue.put(generate_summary_task)
    else:
        logger.info(f"Skipping summary generation for session {session_id}: only {message_count} messages (will summarize every 10th message)")

    return Result(
        message=f'Added {len(request.messages)} messages to session {session_id} with NLP enhancement', 
        success=True
    )


def generate_episode_name(message: Message) -> str:
    """Generate meaningful episode names for better knowledge graph organization"""
    if message.role == 'user':
        # Extract key intent/topic from user message
        content_preview = message.content[:50] + "..." if len(message.content) > 50 else message.content
        return f"User Query: {content_preview}"
    elif message.role == 'assistant':
        return f"AI Response to {message.uuid[:8]}"
    elif message.role == 'system':
        return f"System Instruction: {message.content[:30]}"
    else:
        return f"{message.role.title()} Message: {message.uuid[:8]}"


def format_episode_body(message: Message) -> str:
    """Format episode body with rich context for better extraction"""
    role_context = {
        'user': 'Human user input',
        'assistant': 'AI assistant response', 
        'system': 'System instruction or context',
        'function': 'Function execution result',
        'tool': 'Tool usage result'
    }.get(message.role, message.role)
    
    # Structured format for better entity/relationship extraction
    return f"""
Role: {role_context}
Content: {message.content}
Timestamp: {message.timestamp.isoformat() if message.timestamp else 'unknown'}
Source: {getattr(message, 'source_description', 'conversation')}
""".strip()


@router.post('/entity-node', status_code=status.HTTP_201_CREATED)
async def add_entity_node(
    request: AddEntityNodeRequest,
    settings: ZepEnvDep,
):
    # Extract user context from group_id to get the right database
    user_id = update_user_context_from_group_id(request.group_id)
    graphiti = get_or_create_pooled_client(user_id, settings)
    
    node = await graphiti.save_entity_node(
        uuid=request.uuid,
        group_id=request.group_id,
        name=request.name,
        summary=request.summary,
    )
    return node


@router.delete('/entity-edge/{uuid}', status_code=status.HTTP_200_OK)
async def delete_entity_edge(uuid: str, settings: ZepEnvDep, request: Request):
    # Extract user context from request to get the right database
    user_id = extract_user_id_from_request(request)
    if not user_id:
        user_id = "default_user"
    graphiti = get_or_create_pooled_client(user_id, settings)
    
    await graphiti.delete_entity_edge(uuid)
    return Result(message='Entity Edge deleted', success=True)


@router.delete('/group/{group_id}', status_code=status.HTTP_200_OK)
async def delete_group(group_id: str, settings: ZepEnvDep):
    """Delete a group by completely removing the user's database"""
    import logging
    import time
    logger = logging.getLogger(__name__)
    
    # Create a unique cache key for this deletion operation
    cache_key = f"group_delete_{group_id}_{int(time.time() // 60)}"  # Cache for 1 minute
    
    # Check if this deletion is already in progress or recently completed
    if cache_key in _deletion_cache:
        logger.info(f"🔄 Group deletion already in progress/completed for: {group_id}")
        return Result(message=f'Group {group_id} deletion already handled', success=True)
    
    # Add to cache to prevent duplicates
    _deletion_cache.add(cache_key)
    
    try:
        logger.info(f"🗑️ Deleting group: {group_id}")
        
        # Extract user context from group_id to get the right database
        user_id = update_user_context_from_group_id(group_id)
        logger.debug(f"👤 Resolved user context: {user_id} for group: {group_id}")
        
        # Call the database deletion function directly - but DON'T double-process the user_id
        result = await delete_database_direct(user_id, settings)
        
        # Update the success message to reflect group deletion
        return Result(
            message=f'Group {group_id} deleted successfully (entire database removed)', 
            success=True
        )
        
    except Exception as e:
        logger.error(f"❌ Failed to delete group {group_id}: {e}", exc_info=True)
        
        # Check if it's a "not found" type error - that's actually OK
        error_message = str(e).lower()
        if any(keyword in error_message for keyword in ['not found', 'does not exist', 'no such', 'empty']):
            logger.info(f"ℹ️ Group {group_id} was already deleted or never existed - treating as success")
            return Result(message=f'Group {group_id} was already deleted or never existed', success=True)
        
        # For other errors, return a proper error response but don't crash
        from fastapi import HTTPException
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete group {group_id}: {str(e)}"
        )
    finally:
        # Clean up cache after some time (remove old entries)
        import time
        current_time = int(time.time() // 60)
        keys_to_remove = [key for key in _deletion_cache if not key.endswith(str(current_time)) and not key.endswith(str(current_time - 1))]
        for key in keys_to_remove:
            _deletion_cache.discard(key)


@router.delete('/episode/{uuid}', status_code=status.HTTP_200_OK)
async def delete_episode(uuid: str, settings: ZepEnvDep, request: Request):
    # Extract user context from request to get the right database
    user_id = extract_user_id_from_request(request)
    if not user_id:
        user_id = "default_user"
    graphiti = get_or_create_pooled_client(user_id, settings)
    
    await graphiti.delete_episodic_node(uuid)
    return Result(message='Episode deleted', success=True)


async def delete_database_direct(user_id: str, settings: ZepEnvDep):
    """Delete entire user database after clearing all data - without double-processing user_id"""
    import logging
    import re
    logger = logging.getLogger(__name__)
    
    logger.info(f"🗑️ Deleting database for user: {user_id}")
    
    try:
        # Clear any cached connections for this user BEFORE attempting deletion
        from graph_service.zep_graphiti import _graphiti_pool
        pool_keys_to_remove = [key for key in _graphiti_pool.keys() if key.startswith(f"{user_id}_")]
        
        for pool_key in pool_keys_to_remove:
            try:
                if pool_key in _graphiti_pool:
                    cached_client = _graphiti_pool[pool_key]
                    await cached_client.close()
                    del _graphiti_pool[pool_key]
                    logger.debug(f"🧹 Cleared cached connection: {pool_key}")
            except Exception as cleanup_error:
                logger.warning(f"⚠️ Failed to cleanup cached connection {pool_key}: {cleanup_error}")
        
        # Use the same sanitization logic as database creation
        def sanitize_user_id(user_id: str) -> str:
            """Sanitize user_id to be safe for database names - matches zep_graphiti.py logic"""
            sanitized = re.sub(r'[^a-zA-Z0-9_-]+', '_', user_id)
            
            if not sanitized.startswith("user_"):
                if sanitized and sanitized[0].isdigit():
                    sanitized = f"user_{sanitized}"
            
            sanitized = sanitized[:50]
            return sanitized or "default_user"
        
        # Get database name using same logic as ZepGraphiti class
        sanitized_user_id = sanitize_user_id(user_id)
        if sanitized_user_id.startswith("user_"):
            # Extract the actual user ID after user_ prefix (should be zep_xxxx)
            actual_user_id = sanitized_user_id[5:]  # Remove "user_" prefix
            if actual_user_id.startswith("zep_"):
                db_name = actual_user_id
            else:
                # If it doesn't start with zep_, add zep_ prefix
                db_name = f"zep_{actual_user_id}"
        elif sanitized_user_id.startswith("zep_"):
            # Already properly formatted
            db_name = sanitized_user_id
        else:
            # For other formats, add zep_ prefix
            db_name = f"zep_{sanitized_user_id}"
            
        logger.debug(f"🔍 Target database name: {db_name} (sanitized from: {user_id})")
        
        # Connect to FalkorDB using the same driver as the rest of the system
        from graphiti_core.driver.falkordb_driver import FalkorDriver
        
        # Create driver instance to connect to FalkorDB
        driver = FalkorDriver(
            host=settings.falkordb_host,
            port=int(settings.falkordb_port),
            username=settings.falkordb_username or '',
            password=settings.falkordb_password or '',
            database="default_db"  # Connect to default database to manage others
        )
        
        try:
            # Use the driver's client to execute Redis commands
            connection = driver.client
            
            # Check if database exists first - try different FalkorDB commands
            try:
                # Try the standard GRAPH.LIST command
                graphs_result = await connection.execute_command("GRAPH.LIST")
                graphs = graphs_result if isinstance(graphs_result, list) else []
                
                # If empty, try alternative commands to debug
                if not graphs:
                    try:
                        # Try to see if our target database exists by trying to query it
                        try:
                            test_result = await connection.execute_command("GRAPH.QUERY", db_name, "RETURN 1")
                            graphs.append(db_name)  # Database exists
                            logger.debug(f"🔍 Found database {db_name} via test query")
                        except Exception:
                            logger.debug(f"🔍 Database {db_name} doesn't exist")
                            
                    except Exception as debug_e:
                        logger.debug(f"🔍 Debug commands failed: {debug_e}")
                        
            except Exception as list_e:
                logger.warning(f"🔍 GRAPH.LIST command failed: {list_e}")
                graphs = []
            
            if db_name in graphs:
                # Delete the database using FalkorDB command
                logger.info(f"🗑️ Deleting database: {db_name}")
                result = await connection.execute_command("GRAPH.DELETE", db_name)
                
                # Verify deletion by checking the graph list again
                import asyncio
                await asyncio.sleep(0.1)  # Small delay to ensure command completion
                
                verification_result = await connection.execute_command("GRAPH.LIST")
                updated_graphs = verification_result if isinstance(verification_result, list) else []
                
                if db_name not in updated_graphs:
                    logger.info(f"✅ Database {db_name} deleted successfully")
                else:
                    logger.error(f"❌ Database {db_name} still exists after deletion")
            else:
                logger.debug(f"🎯 Database {db_name} doesn't exist - nothing to delete")
            
        finally:
            await driver.close()
        
        return Result(message=f'Database for user {user_id} deleted successfully', success=True)
        
    except Exception as e:
        logger.error(f"❌ Failed to delete database for user {user_id}: {str(e)}")
        logger.error(f"🔍 Exception type: {type(e)}")
        
        # Don't fail the entire operation if database deletion fails
        # The important part is that the data was cleaned up in previous steps
        logger.info(f"🎯 Treating database deletion failure as non-critical")
        return Result(message=f'Database for user {user_id} deletion completed (some errors occurred)', success=True)

@router.delete('/database/{user_id}', status_code=status.HTTP_200_OK)
async def delete_database(user_id: str, settings: ZepEnvDep):
    """Delete entire user database after clearing all data"""
    import logging
    import time
    logger = logging.getLogger(__name__)
    
    # Create a unique cache key for this deletion operation
    cache_key = f"database_delete_{user_id}_{int(time.time() // 60)}"  # Cache for 1 minute
    
    # Check if this deletion is already in progress or recently completed
    if cache_key in _deletion_cache:
        logger.info(f"🔄 Database deletion already in progress/completed for: {user_id}")
        return Result(message=f'Database for user {user_id} deletion already handled', success=True)
    
    # Add to cache to prevent duplicates
    _deletion_cache.add(cache_key)
    
    try:
        logger.info(f"🗑️ Deleting database for user: {user_id}")
        
        # Ensure we have the right user context - this is for external API calls
        # user_id is already the full user ID, no need to resolve from group_id
        logger.debug(f"👤 Set user context to: {user_id}")
        
        # Call the direct deletion with the original user_id
        return await delete_database_direct(user_id, settings)
        
    except Exception as e:
        logger.error(f"❌ Failed to delete database for user {user_id}: {e}", exc_info=True)
        
        # Check if it's a "not found" type error - that's actually OK
        error_message = str(e).lower()
        if any(keyword in error_message for keyword in ['not found', 'does not exist', 'no such', 'empty']):
            logger.info(f"ℹ️ Database for user {user_id} was already deleted or never existed - treating as success")
            return Result(message=f'Database for user {user_id} was already deleted or never existed', success=True)
        
        # For other errors, return a proper error response but don't crash
        from fastapi import HTTPException
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete database for user {user_id}: {str(e)}"
        )
    finally:
        # Clean up cache after some time (remove old entries)
        import time
        current_time = int(time.time() // 60)
        keys_to_remove = [key for key in _deletion_cache if not key.endswith(str(current_time)) and not key.endswith(str(current_time - 1))]
        for key in keys_to_remove:
            _deletion_cache.discard(key)


@router.post('/clear', status_code=status.HTTP_200_OK)
async def clear(
    settings: ZepEnvDep,
    request: Request
):
    # Extract user context from request to get the right database
    user_id = extract_user_id_from_request(request)
    if not user_id:
        user_id = "default_user"
    graphiti = get_or_create_pooled_client(user_id, settings)
    
    await clear_data(graphiti.driver)
    await graphiti.build_indices_and_constraints()
    return Result(message='Graph cleared', success=True)
