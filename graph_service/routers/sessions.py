import uuid as uuid_lib
import logging
import asyncio
from datetime import datetime, timezone
from typing import Dict, Any, Optional

from fastapi import APIRouter, HTTPException, status, Query
from graphiti_core.nodes import EpisodeType

from graph_service.dto.session import (
    SessionRequest,
    SessionResponse, 
    AddMemoryToSessionRequest,
    SessionMemoryResponse,
    SessionListResponse,
    SessionMessage,
    SessionMessagesResponse
)
from graph_service.zep_graphiti import ZepGraphitiDep, ZepGraphitiForUserDep, get_or_create_pooled_client, current_user_context, ZepEnvDep
from graph_service.search_cache import search_cache

# Async helper function for non-blocking entity extraction
async def extract_entities_async(graphiti, content: str, group_id: str, session_id: str):
    """Extract entities asynchronously without blocking the main response"""
    try:
        entities = await graphiti.extract_entities_from_text(content, group_id)
        if entities:
            logger.info(f"🔍 Extracted {len(entities)} entities from session {session_id} (async)")
    except Exception as e:
        logger.warning(f"⚠️ Async entity extraction failed for session {session_id}: {e}")

# Async helper function for non-blocking summary generation  
async def generate_summary_async(graphiti, group_id: str, session_id: str, session_data: dict):
    """Generate session summary asynchronously without blocking the main response"""
    try:
        context_summary = await graphiti.get_contextual_summary(group_id, max_episodes=5)  # Reduced from 10
        
        # Update session data with summary (in background)
        session_data["summary"] = context_summary.get("summary", "")
        session_data["metadata"].update({
            "key_entities": context_summary.get("key_entities", [])[:10],  # Limit entities
            "topics": context_summary.get("topics", [])[:5],  # Limit topics
        })
        logger.info(f"📝 Generated session summary for {session_id} (async): {context_summary.get('summary', '')[:100]}...")
    except Exception as e:
        logger.warning(f"⚠️ Async summary generation failed for session {session_id}: {e}")

router = APIRouter(prefix="/api/v2", tags=["sessions"])

# Logger for session management
logger = logging.getLogger(__name__)

# In-memory session storage (in production, use a database)
sessions_store: Dict[str, Dict[str, Any]] = {}


@router.post('/sessions', status_code=status.HTTP_201_CREATED, response_model=SessionResponse)
async def add_session(
    request: SessionRequest,
    settings: ZepEnvDep
):
    """Create a new session following Zep Cloud API structure"""
    
    logger.info(f"🔄 Creating session: session_id='{request.session_id}', user_id='{request.user_id}'")
    
    # Check if session already exists
    if request.session_id in sessions_store:
        logger.warning(f"⚠️ Session '{request.session_id}' already exists")
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Session with id '{request.session_id}' already exists"
        )
    
    # Generate server-side identifiers
    session_uuid = str(uuid_lib.uuid4())
    session_internal_id = len(sessions_store) + 1
    current_time = datetime.now(timezone.utc)
    
    logger.info(f"📝 Storing session data: uuid={session_uuid}, internal_id={session_internal_id}")
    
    # Store session data
    session_data = {
        "uuid": session_uuid,
        "id": session_internal_id,
        "session_id": request.session_id,
        "user_id": request.user_id,
        "created_at": current_time,
        "updated_at": current_time,
        "metadata": request.metadata,
        "summary": None,  # Will be generated when messages are added
        "messages": [],
        "memory_context": {}
    }
    
    sessions_store[request.session_id] = session_data
    logger.info(f"✅ Session stored in sessions_store with user_id: {request.user_id}")
    
    # Initialize Graphiti knowledge graph for this session
    try:
        # Create graphiti client for this user
        current_user_context.set(request.user_id)
        graphiti = get_or_create_pooled_client(request.user_id, settings)
        
        # Create initial context in knowledge graph
        group_id = f"{request.user_id}_{request.session_id}"  # Combine for uniqueness
        logger.info(f"🔧 Initializing Graphiti with group_id: {group_id}")
        
        # Add session initialization episode
        await graphiti.enhanced_add_episode(
            uuid=session_uuid,  # We'll pass this but it won't be used by the method
            group_id=group_id,
            name=f"Session Started: {request.session_id}",
            episode_body=f"User {request.user_id} started session {request.session_id}",
            reference_time=current_time,
            source=EpisodeType.message,
            source_description="Session initialization"
        )
        
    except Exception as e:
        # Log error but don't fail session creation
        print(f"Warning: Failed to initialize Graphiti context for session {request.session_id}: {e}")
    
    return SessionResponse(**session_data)


@router.get('/sessions/{session_id}', status_code=status.HTTP_200_OK, response_model=SessionResponse)
async def get_session(session_id: str):
    """Get session details by session ID with auto-creation fallback"""
    
    if session_id not in sessions_store:
        # Auto-create session if it doesn't exist (for compatibility)
        current_time = datetime.now(timezone.utc)
        auto_session = {
            "uuid": str(uuid_lib.uuid4()),
            "id": len(sessions_store) + 1,
            "session_id": session_id,
            "user_id": f"auto_user_{session_id[:8]}",  # Generate user_id from session_id
            "created_at": current_time,
            "updated_at": current_time,
            "metadata": {},
            "summary": None,
            "messages": [],
            "memory_context": {}
        }
        sessions_store[session_id] = auto_session
        logger.info(f"Auto-created session {session_id} for compatibility")
    
    return SessionResponse(**sessions_store[session_id])


@router.get('/sessions-ordered', status_code=status.HTTP_200_OK, response_model=SessionListResponse)  
async def list_sessions_ordered(
    pageNumber: int = Query(1, description="Page number starting from 1"),
    pageSize: int = Query(100, description="Number of sessions per page"),
    user_id: Optional[str] = Query(None, description="Filter sessions by user ID"),
    order_by: str = Query("created_at", description="Field to order by"),
    asc: bool = Query(False, description="Sort direction")
):
    """List sessions with ordering and pagination following official Zep API"""
    
    # Convert page-based to offset-based pagination
    offset = (pageNumber - 1) * pageSize
    
    # Filter sessions by user_id if provided
    filtered_sessions = []
    for session_data in sessions_store.values():
        if user_id is None or session_data["user_id"] == user_id:
            filtered_sessions.append(SessionResponse(**session_data))
    
    # Sort sessions (simple implementation)
    if order_by == "created_at":
        filtered_sessions.sort(key=lambda x: x.created_at, reverse=not asc)
    elif order_by == "updated_at":
        filtered_sessions.sort(key=lambda x: x.updated_at, reverse=not asc)
    
    # Apply pagination
    total_count = len(filtered_sessions)
    paginated_sessions = filtered_sessions[offset:offset + pageSize]
    
    return SessionListResponse(
        sessions=paginated_sessions,
        total_count=total_count,
        response_count=len(paginated_sessions)
    )

@router.get('/sessions', status_code=status.HTTP_200_OK, response_model=SessionListResponse)
async def list_sessions(
    user_id: Optional[str] = Query(None, description="Filter sessions by user ID"),
    limit: int = Query(100, description="Maximum number of sessions to return"),
    offset: int = Query(0, description="Number of sessions to skip")
):
    """List sessions with optional filtering and pagination (simple version)"""
    
    # Filter sessions by user_id if provided
    filtered_sessions = []
    for session_data in sessions_store.values():
        if user_id is None or session_data["user_id"] == user_id:
            filtered_sessions.append(SessionResponse(**session_data))
    
    # Apply pagination
    total_count = len(filtered_sessions)
    paginated_sessions = filtered_sessions[offset:offset + limit]
    
    return SessionListResponse(
        sessions=paginated_sessions,
        total_count=total_count,
        limit=limit,
        offset=offset
    )


@router.post('/sessions/{session_id}/memory', status_code=status.HTTP_201_CREATED)
async def add_memory_to_session(
    session_id: str,
    request: AddMemoryToSessionRequest,
    graphiti: ZepGraphitiDep
):
    """Add memory (messages) to a session"""
    
    # Auto-create session if it doesn't exist (for compatibility with zep-server)
    if session_id not in sessions_store:
        logger.info(f"🔄 Auto-creating session {session_id} for memory request")
        
        # Extract user_id from request, graphiti dependency, or generate auto_user
        user_id = request.user_id or getattr(graphiti, 'user_id', None) or f"auto_user_{session_id[:8]}"
        
        # Create session data
        session_uuid = str(uuid_lib.uuid4())
        session_internal_id = len(sessions_store) + 1
        current_time = datetime.now(timezone.utc)
        
        session_data = {
            "uuid": session_uuid,
            "id": session_internal_id,
            "session_id": session_id,
            "user_id": user_id,
            "created_at": current_time,
            "updated_at": current_time,
            "metadata": {},
            "summary": None,
            "messages": [],
            "memory_context": {}
        }
        
        sessions_store[session_id] = session_data
        logger.info(f"✅ Auto-created session {session_id} with user_id: {user_id}")
    
    session_data = sessions_store[session_id]
    group_id = f"{session_data['user_id']}_{session_id}"
    
    # Process and store messages
    processed_messages = []
    
    # Batch process messages for better performance
    logger.info(f"Processing {len(request.messages)} messages for session {session_id}")
    
    for message in request.messages:
        message_uuid = str(uuid_lib.uuid4())
        current_time = datetime.now(timezone.utc)
        
        # Create structured message
        session_message = SessionMessage(
            uuid=message_uuid,
            created_at=current_time,
            role=message.get('role', 'user'),
            role_type=message.get('role_type', message.get('role', 'user')),
            content=message.get('content', ''),
            metadata=message.get('metadata', {}),
            token_count=message.get('token_count')
        )
        
        processed_messages.append(session_message)
        
        # Add to Graphiti knowledge graph with optimized processing
        try:
            # Use optimized episode creation with selective NLP processing
            await graphiti.enhanced_add_episode(
                uuid=message_uuid,
                group_id=group_id,
                name=f"{session_message.role.title()} Message",
                episode_body=session_message.content,
                reference_time=current_time,
                source=EpisodeType.message,
                source_description=f"{session_message.role} message in session {session_id}"
            )
            
            # Optimized entity extraction - only for substantial user messages and with limits
            if (session_message.role == 'user' and 
                len(session_message.content) > 50 and  # Increased threshold
                len(session_message.content) < 1000):  # Skip very long messages for speed
                
                # Use async task for entity extraction to not block response
                asyncio.create_task(extract_entities_async(graphiti, session_message.content, group_id, session_id))
                    
        except Exception as e:
            print(f"Warning: Failed to process message {message_uuid} in Graphiti: {e}")
    
    # Update session storage with context and metadata
    session_data["messages"].extend([msg.dict() for msg in processed_messages])
    session_data["updated_at"] = datetime.now(timezone.utc)
    
    # Update basic metadata for fast response
    message_count = len(session_data["messages"])
    session_data["metadata"] = {
        "message_count": message_count,
        "last_updated": session_data["updated_at"].isoformat()
    }
    
    # Generate summary asynchronously for messages >= 3 to avoid blocking response
    if message_count >= 3:  # Increased threshold for better context
        asyncio.create_task(generate_summary_async(graphiti, group_id, session_id, session_data))
        logger.info(f"Session {session_id} updated with {message_count} messages (summary generating async)")
    else:
        logger.info(f"Session {session_id} updated with {message_count} messages (waiting for more messages to generate summary)")
    
    return {
        "message": f"Added {len(processed_messages)} messages to session {session_id}",
        "messages_processed": len(processed_messages),
        "session_id": session_id
    }


@router.get('/sessions/{session_id}/memory', status_code=status.HTTP_200_OK, response_model=SessionMemoryResponse)
async def get_session_memory(
    session_id: str,
    graphiti: ZepGraphitiDep,
    limit: int = Query(10, description="Maximum number of facts to return")
):
    """Get memory/context for a session using Graphiti's NLP capabilities"""
    
    if session_id not in sessions_store:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session '{session_id}' not found"
        )
    
    session_data = sessions_store[session_id]
    group_id = f"{session_data['user_id']}_{session_id}"
    
    try:
        # Get contextual memory using Graphiti's enhanced capabilities
        context_summary = await graphiti.get_contextual_summary(group_id, max_episodes=20)
        
        # Get relevant facts from recent conversation
        recent_messages = session_data["messages"][-5:] if session_data["messages"] else []
        
        if recent_messages:
            # Create query from recent messages for context retrieval
            query_content = " ".join([msg.get("content", "") for msg in recent_messages])
            
            # Search for relevant facts
            search_results = await graphiti.search(
                group_ids=[group_id],
                query=query_content[:500],  # Limit query length
                num_results=limit
            )
            
            # Convert to fact format
            from graph_service.zep_graphiti import get_fact_result_from_edge
            relevant_facts = [get_fact_result_from_edge(edge).dict() for edge in search_results]
        else:
            relevant_facts = []
        
        return SessionMemoryResponse(
            uuid=session_data["uuid"],
            session_id=session_id,
            messages=session_data["messages"],
            relevant_facts=relevant_facts,
            summary=context_summary["summary"],
            metadata={
                "key_entities": context_summary["key_entities"],
                "topics": context_summary["topics"],
                "message_count": len(session_data["messages"]),
                "last_updated": session_data["updated_at"].isoformat()
            }
        )
        
    except Exception as e:
        # Fallback response if Graphiti processing fails
        return SessionMemoryResponse(
            uuid=session_data["uuid"],
            session_id=session_id,
            messages=session_data["messages"],
            relevant_facts=[],
            summary="Memory processing unavailable",
            metadata={"error": str(e)}
        )


@router.get('/sessions/{session_id}/messages', status_code=status.HTTP_200_OK, response_model=SessionMessagesResponse)
async def get_session_messages(
    session_id: str,
    limit: int = Query(100, description="Maximum number of messages to return"),
    cursor: Optional[str] = Query(None, description="Cursor for pagination")
):
    """Get messages for a session with pagination"""
    
    if session_id not in sessions_store:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session '{session_id}' not found"
        )
    
    session_data = sessions_store[session_id]
    messages = session_data["messages"]
    
    # Simple cursor-based pagination (in production, use proper cursor implementation)
    start_index = 0
    if cursor:
        try:
            start_index = int(cursor)
        except ValueError:
            start_index = 0
    
    # Get paginated messages
    paginated_messages = messages[start_index:start_index + limit]
    
    # Generate next cursor
    next_cursor = None
    if start_index + limit < len(messages):
        next_cursor = str(start_index + limit)
    
    # Convert to proper message format
    session_messages = [SessionMessage(**msg) for msg in paginated_messages]
    
    return SessionMessagesResponse(
        messages=session_messages,
        session_id=session_id,
        total_count=len(messages),
        next_cursor=next_cursor
    )


@router.delete('/sessions/{session_id}/memory', status_code=status.HTTP_200_OK)
async def delete_session_memory(session_id: str, graphiti: ZepGraphitiDep):
    """Delete all memory/messages from a session but keep the session itself"""
    
    if session_id not in sessions_store:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session '{session_id}' not found"
        )
    
    session_data = sessions_store[session_id]
    group_id = f"{session_data['user_id']}_{session_id}"
    
    try:
        # Delete from Graphiti knowledge graph (group data)
        await graphiti.delete_group(group_id)
        logger.info(f"Deleted Graphiti group data for session {session_id}")
    except Exception as e:
        logger.warning(f"Failed to delete Graphiti data for session {session_id}: {e}")
    
    # Clear messages and reset session state but keep the session
    session_data["messages"] = []
    session_data["summary"] = None
    session_data["metadata"] = {}
    session_data["memory_context"] = {}
    session_data["updated_at"] = datetime.now(timezone.utc)
    
    logger.info(f"Cleared memory for session {session_id}")
    
    return {
        "message": f"Memory for session '{session_id}' deleted successfully",
        "session_id": session_id
    }


@router.delete('/sessions/{session_id}', status_code=status.HTTP_200_OK)
async def delete_session(session_id: str, graphiti: ZepGraphitiDep):
    """Delete a session and its associated data"""
    
    if session_id not in sessions_store:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session '{session_id}' not found"
        )
    
    session_data = sessions_store[session_id]
    group_id = f"{session_data['user_id']}_{session_id}"
    
    try:
        # Delete from Graphiti knowledge graph
        await graphiti.delete_group(group_id)
    except Exception as e:
        print(f"Warning: Failed to delete Graphiti data for session {session_id}: {e}")
    
    # Remove from session store
    del sessions_store[session_id]
    
    return {
        "message": f"Session '{session_id}' deleted successfully",
        "session_id": session_id
    }


@router.get('/sessions/{session_id}/search', status_code=status.HTTP_200_OK)
async def search_session_memory(
    session_id: str,
    graphiti: ZepGraphitiDep,
    query: str = Query(..., description="Search query for semantic retrieval"),
    top_k: int = Query(3, description="Number of results to return"),
    search_type: str = Query("similarity", description="Search type: similarity or mmr"),
    fast_mode: bool = Query(True, description="Use fast search for interactive queries")
):
    """Search session memory with optimized fast mode for interactive queries"""
    
    if session_id not in sessions_store:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session '{session_id}' not found"
        )
    
    session_data = sessions_store[session_id]
    group_id = f"{session_data['user_id']}_{session_id}"
    
    try:
        # Check cache first for fast responses
        cached_result = search_cache.get(session_id, query, top_k, search_type)
        if cached_result:
            logger.info(f"⚡ Cache HIT for session {session_id}: {query[:50]}...")
            cached_result["context"]["cache_hit"] = True
            return cached_result
        
        # Use fast mode for interactive queries to achieve <100ms response
        if fast_mode and top_k <= 10 and len(query) <= 200:
            logger.info(f"🚀 Using fast search mode for session {session_id}")
            
            # Use simplified search that bypasses complex hybrid algorithms
            search_results = await graphiti.search_(
                query=query,
                group_ids=[group_id],
                num_results=top_k
            )
            
            # Convert to expected format quickly
            results = []
            for edge in search_results.edges[:top_k]:
                results.append({
                    "uuid": edge.uuid,
                    "content": edge.fact,
                    "score": getattr(edge, 'fact_rating', 1.0),
                    "type": "edge"
                })
            
            for episode in search_results.episodes[:top_k-len(results)]:
                results.append({
                    "uuid": episode.uuid,
                    "content": getattr(episode, 'content', ''),
                    "score": 1.0,
                    "type": "episode"
                })
            
            response = {
                "session_id": session_id,
                "query": query,
                "search_type": "fast_" + search_type,
                "results": results[:top_k],
                "context": {"fast_mode": True, "cache_hit": False},
                "total_results": len(results)
            }
            
            # Cache the response for future requests
            search_cache.put(session_id, query, top_k, search_type, response)
            return response
        else:
            # Fall back to full search for complex queries
            logger.info(f"🐌 Using full search mode for session {session_id}")
            search_results = await graphiti.search_with_context(
                group_ids=[group_id],
                query=query,
                num_results=top_k,
                include_summary=True
            )
            
            response = {
                "session_id": session_id,
                "query": query,
                "search_type": search_type,
                "results": search_results["search_results"],
                "context": {**search_results.get("context", {}), "cache_hit": False},
                "total_results": search_results["total_results"]
            }
            
            # Cache the response for future requests
            search_cache.put(session_id, query, top_k, search_type, response)
            return response
        
    except Exception as e:
        logger.error(f"Search failed for session {session_id}: {e}")
        return {
            "session_id": session_id,
            "query": query,
            "search_type": search_type,
            "results": [],
            "context": {"error": str(e)},
            "total_results": 0,
            "error": str(e)
        }


@router.get('/cache/stats', status_code=status.HTTP_200_OK)
async def get_cache_stats():
    """Get search cache statistics for monitoring"""
    return {
        "search_cache": search_cache.get_stats(),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "service": "graphiti-service"
    }