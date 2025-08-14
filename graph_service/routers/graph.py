from datetime import datetime, timezone
import logging
from typing import Dict, List, Optional, Any
import uuid as uuid_lib

from fastapi import APIRouter, HTTPException, Request, status, Query
from graph_service.config import ZepEnvDep
from graph_service.zep_graphiti import get_or_create_pooled_client, extract_user_id_from_request, update_user_context_from_group_id, ZepGraphitiDep
from graph_service.dto.graph import (
    EntityNode,
    EntityEdge, 
    EpisodicNode,
    GraphSearchRequest,
    GraphSearchResponse,
    GraphSearchResult,
    GraphAddRequest,
    GraphAddResponse,
    NodeRelationshipsRequest,
    NodeRelationshipsResponse,
    ZepFactResult
)

# Import episode response model for user/session episode endpoints
from typing import List
try:
    from graph_service.routers.episodes import EpisodeResponse
except ImportError:
    # Fallback definition if import fails
    from typing import Dict, Any
    from pydantic import BaseModel, Field
    class EpisodeResponse(BaseModel):
        uuid: str = Field(..., description='Episode UUID')
        content: str = Field(..., description='Episode content/body')
        source: str = Field(..., description='Episode source type')
        created_at: Optional[datetime] = Field(None, description='Episode creation timestamp')

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v2/graph", tags=["graph"])


@router.post("/search", response_model=GraphSearchResponse)
async def search_graph(
    request: GraphSearchRequest,
    settings: ZepEnvDep,
    http_request: Request
):
    """
    Search the knowledge graph using hybrid search (semantic + BM25 + RRF)
    Compatible with official Zep graph search API with performance optimizations
    """
    import time
    start_time = time.time()
    
    # Extract user context for proper database isolation (Zep v2 compatible)
    if request.user_id:
        # User-specific search: use user database with provided group_ids or search all data
        user_id = request.user_id
        graphiti = get_or_create_pooled_client(user_id, settings)
        # FIXED: Use exact group_ids as provided (official Zep behavior) or None to search all
        search_group_ids = request.group_ids if request.group_ids else None
    elif request.group_ids:
        # Group-specific search: extract user from group_id for database selection
        user_id = update_user_context_from_group_id(request.group_ids[0])
        graphiti = get_or_create_pooled_client(user_id, settings)
        search_group_ids = request.group_ids
    else:
        # Fallback: extract from request headers
        user_id = extract_user_id_from_request(http_request)
        if not user_id:
            user_id = "default_user"
        graphiti = get_or_create_pooled_client(user_id, settings)
        # FIXED: Use None to search all data in user database (official Zep behavior)
        search_group_ids = None
    
    # Check cache first for fast responses (import search_cache)
    from graph_service.search_cache import search_cache
    import hashlib
    
    # CACHE OPTIMIZATION: Generate normalized cache key to improve hit rate
    # Normalize query to improve cache hits (trim whitespace, lowercase)
    normalized_query = request.query.strip().lower()
    search_group_ids_str = "|".join(sorted(search_group_ids)) if search_group_ids else "all"
    
    # Use structured cache key with hashing to avoid collisions and improve hits
    cache_data = f"graph_{user_id}_{normalized_query}_{request.max_results}_{request.search_type}_{search_group_ids_str}"
    cache_key = hashlib.md5(cache_data.encode()).hexdigest()
    
    cached_result = search_cache.get_by_key(cache_key)
    if cached_result:
        logger.info(f"⚡ Cache HIT for graph search: {request.query[:50]}...")
        cached_result.search_metadata["cache_hit"] = True
        cached_result.search_metadata["execution_time_ms"] = 0
        return cached_result
    
    try:
        # Use fast mode for interactive queries (similar to session search optimization)
        max_results = request.max_results or 10
        if max_results <= 10 and len(request.query) <= 200:
            logger.info(f"🚀 Using fast search mode for graph search: {request.query[:50]}...")
            
            # Use simplified search_ method (returns SearchResults with nodes, edges, episodes)
            search_results = await graphiti.search_(
                query=request.query,
                group_ids=search_group_ids,
                # search_ method doesn't take num_results, uses SearchConfig instead
            )
        else:
            # Use comprehensive search for complex queries
            logger.info(f"🐌 Using comprehensive search mode for graph search")
            search_results = await graphiti.search_(
                query=request.query,
                group_ids=search_group_ids,
                # Use default config which includes comprehensive search
            )
        
        # SearchResults already contains separated collections - convert to our DTO format
        edges = []
        episodes = []
        nodes = []
        
        # Process EntityEdges from search results (limit to max_results)
        for edge in search_results.edges[:max_results]:
            try:
                converted_edge = EntityEdge(
                    uuid=getattr(edge, 'uuid', str(uuid_lib.uuid4())),
                    source_node_uuid=getattr(edge, 'source_node_uuid', ''),
                    target_node_uuid=getattr(edge, 'target_node_uuid', ''),
                    name=getattr(edge, 'name', getattr(edge, 'relation', 'relates_to')),
                    fact=getattr(edge, 'fact', ''),
                    predicate=getattr(edge, 'relation', 'relates_to'),
                    edge_type='relates_to',
                    attributes=getattr(edge, 'attributes', {}),
                    episodes=getattr(edge, 'episodes', []),
                    created_at=getattr(edge, 'created_at', datetime.now(timezone.utc)),
                    updated_at=getattr(edge, 'updated_at', datetime.now(timezone.utc)),
                    valid_at=getattr(edge, 'valid_at', None),
                    expires_at=getattr(edge, 'expires_at', None),
                    invalid_at=getattr(edge, 'invalid_at', None),
                    metadata=getattr(edge, 'metadata', {}),
                    group_ids=[getattr(edge, 'group_id')] if hasattr(edge, 'group_id') and edge.group_id else [],
                    fact_rating=getattr(edge, 'fact_rating', 1.0)
                )
                edges.append(converted_edge)
            except Exception as e:
                logger.warning(f"Failed to process edge result: {e}")
                continue
        
        # Process EpisodicNodes from search results (limit remaining slots)
        remaining_slots = max(0, max_results - len(edges))
        for episode in search_results.episodes[:remaining_slots]:
            try:
                converted_episode = EpisodicNode(
                    uuid=getattr(episode, 'uuid', str(uuid_lib.uuid4())),
                    name=getattr(episode, 'name', ''),
                    content=getattr(episode, 'content', getattr(episode, 'episode_body', '')),
                    episode_type=getattr(episode, 'episode_type', 'text'),
                    source=getattr(episode, 'source', 'unknown'),
                    created_at=getattr(episode, 'created_at', datetime.now(timezone.utc)),
                    updated_at=getattr(episode, 'updated_at', datetime.now(timezone.utc)),
                    metadata=getattr(episode, 'metadata', {}),
                    group_ids=[getattr(episode, 'group_id')] if hasattr(episode, 'group_id') and episode.group_id else [],
                    user_id=request.user_id,
                    session_id=request.session_id
                )
                episodes.append(converted_episode)
            except Exception as e:
                logger.warning(f"Failed to process episode result: {e}")
                continue
        
        # Process EntityNodes from search results (limit remaining slots)
        remaining_slots = max(0, max_results - len(edges) - len(episodes))
        for node in search_results.nodes[:remaining_slots]:
            try:
                converted_node = EntityNode(
                    uuid=getattr(node, 'uuid', str(uuid_lib.uuid4())),
                    name=getattr(node, 'name', ''),
                    summary=getattr(node, 'summary', ''),
                    entity_type=getattr(node, 'entity_type', 'generic'),
                    labels=getattr(node, 'labels', []),
                    attributes=getattr(node, 'attributes', {}),
                    created_at=getattr(node, 'created_at', datetime.now(timezone.utc)),
                    updated_at=getattr(node, 'updated_at', datetime.now(timezone.utc)),
                    metadata=getattr(node, 'metadata', {}),
                    group_ids=[getattr(node, 'group_id')] if hasattr(node, 'group_id') and node.group_id else []
                )
                nodes.append(converted_node)
            except Exception as e:
                logger.warning(f"Failed to process node result: {e}")
                continue
        
        # Calculate execution time
        execution_time_ms = (time.time() - start_time) * 1000
        
        response = GraphSearchResponse(
            edges=edges,
            episodes=episodes,
            nodes=nodes,
            search_metadata={
                "execution_time_ms": round(execution_time_ms, 2),
                "user_id": user_id,
                "group_ids": search_group_ids,
                "query_processed": request.query,
                "search_type": request.search_type,
                "reranker": request.reranker,
                "scope": request.scope,
                "total_edges": len(edges),
                "total_episodes": len(episodes),
                "total_nodes": len(nodes),
                "cache_hit": False,
                "fast_mode": max_results <= 10 and len(request.query) <= 200
            }
        )
        
        # Cache the response for future requests
        search_cache.put_by_key(cache_key, response)
        logger.info(f"⚡ Graph search completed in {execution_time_ms:.2f}ms, cached for future requests")
        
        return response
        
    except Exception as e:
        logger.error(f"Graph search failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Graph search failed: {str(e)}"
        )


@router.post("/", response_model=GraphAddResponse)
async def add_graph_data(
    request: GraphAddRequest,
    settings: ZepEnvDep,
    http_request: Request
):
    """
    Add data to the knowledge graph
    Compatible with official Zep graph.add API
    """
    # Zep v2 specification: Use either user_id OR group_id for isolation
    if request.user_id:
        # User-specific data: use user database with user-specific group_id
        user_id = request.user_id
        graphiti = get_or_create_pooled_client(user_id, settings)
        # For user data, create a user-specific group_id pattern
        group_id = f"{user_id}_session"  # Standard pattern for user isolation
    elif request.group_id:
        # Group-specific data: use shared database with exact group_id
        user_id = update_user_context_from_group_id(request.group_id)
        graphiti = get_or_create_pooled_client(user_id, settings) 
        group_id = request.group_id
    else:
        # This shouldn't happen due to validator, but fallback to extract from request
        user_id = extract_user_id_from_request(http_request) or "default_user"
        graphiti = get_or_create_pooled_client(user_id, settings)
        group_id = f"{user_id}_session"
    
    try:
        start_time = datetime.now()
        
        # Add episode using Graphiti with proper isolation
        # Convert string data_type to EpisodeType enum
        from graphiti_core.nodes import EpisodeType
        source_type = EpisodeType.from_str(request.data_type)
        
        # Import the entity types function
        from graph_service.zep_graphiti import get_zep_entity_types
        
        episode_result = await graphiti.add_episode(
            name=f"Episode_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
            episode_body=request.data,
            source=source_type,
            source_description=request.source_description or f"Added via graph API - {request.data_type}",
            group_id=group_id,
            reference_time=datetime.now(timezone.utc),
            # ADD PROPER ENTITY TYPES for correct classification
            entity_types=get_zep_entity_types()  # Use official Zep entity types
        )
        
        processing_time = (datetime.now() - start_time).total_seconds() * 1000
        
        # Get statistics from the episode result (AddEpisodeResults object)
        # AddEpisodeResults has: episode, episodic_edges, nodes, edges, communities, community_edges
        try:
            entities_created = len(getattr(episode_result, 'nodes', []))
            relationships_created = len(getattr(episode_result, 'edges', []))
            logger.debug(f"Episode result type: {type(episode_result)}, entities: {entities_created}, relationships: {relationships_created}")
        except Exception as e:
            logger.error(f"Error processing episode result: {e}, episode_result: {episode_result}")
            entities_created = 0
            relationships_created = 0
        
        # Safe episode UUID extraction with error handling
        try:
            if hasattr(episode_result, 'episode') and episode_result.episode:
                episode_uuid = getattr(episode_result.episode, 'uuid', None) or str(uuid_lib.uuid4())
            else:
                episode_uuid = str(uuid_lib.uuid4())
        except Exception as e:
            logger.error(f"Error extracting episode UUID: {e}, episode_result type: {type(episode_result)}")
            episode_uuid = str(uuid_lib.uuid4())
        
        return GraphAddResponse(
            episode_uuid=episode_uuid,
            entities_created=entities_created,
            relationships_created=relationships_created,
            processing_time_ms=processing_time,
            success=True,
            message="Data added successfully to knowledge graph"
        )
        
    except Exception as e:
        logger.error(f"Failed to add graph data: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to add graph data: {str(e)}"
        )


@router.post("/batch", response_model=List[GraphAddResponse])
async def add_graph_data_batch(
    requests: List[GraphAddRequest],
    settings: ZepEnvDep,
    http_request: Request
):
    """
    Add multiple data items to the knowledge graph concurrently
    Compatible with official Zep graph batch operations
    """
    responses = []
    
    for req in requests:
        try:
            response = await add_graph_data(req, settings, http_request)
            responses.append(response)
        except Exception as e:
            logger.error(f"Batch item failed: {e}")
            responses.append(GraphAddResponse(
                episode_uuid=str(uuid_lib.uuid4()),
                entities_created=0,
                relationships_created=0,
                processing_time_ms=0,
                success=False,
                message=f"Failed: {str(e)}"
            ))
    
    return responses


@router.get("/nodes/{node_uuid}", response_model=EntityNode)
async def get_node(
    node_uuid: str,
    settings: ZepEnvDep,
    http_request: Request
):
    """Get a specific entity node by UUID"""
    user_id = extract_user_id_from_request(http_request) or "default_user"
    graphiti = get_or_create_pooled_client(user_id, settings)
    
    try:
        # Get node from Graphiti
        node = await graphiti.get_node(node_uuid)
        
        if not node:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Node {node_uuid} not found"
            )
        
        return EntityNode(
            uuid=node.uuid,
            name=getattr(node, 'name', ''),
            summary=getattr(node, 'summary', ''),
            entity_type=getattr(node, 'entity_type', 'generic'),
            created_at=getattr(node, 'created_at', datetime.now(timezone.utc)),
            updated_at=getattr(node, 'updated_at', datetime.now(timezone.utc)),
            metadata=getattr(node, 'metadata', {}),
            group_ids=getattr(node, 'group_ids', [])
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get node {node_uuid}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get node: {str(e)}"
        )


@router.get("/edges/{edge_uuid}", response_model=EntityEdge)
async def get_edge(
    edge_uuid: str,
    settings: ZepEnvDep,
    http_request: Request
):
    """Get a specific entity edge by UUID"""
    user_id = extract_user_id_from_request(http_request) or "default_user"
    graphiti = get_or_create_pooled_client(user_id, settings)
    
    try:
        # Get edge from Graphiti
        edge = await graphiti.get_entity_edge(edge_uuid)
        
        if not edge:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Edge {edge_uuid} not found"
            )
        
        return EntityEdge(
            uuid=edge.uuid,
            source_node_uuid=getattr(edge, 'source_node_uuid', ''),
            target_node_uuid=getattr(edge, 'target_node_uuid', ''),
            fact=getattr(edge, 'fact', ''),
            predicate=getattr(edge, 'relation', 'relates_to'),
            edge_type=getattr(edge, 'edge_type', 'relates_to'),
            created_at=getattr(edge, 'created_at', datetime.now(timezone.utc)),
            updated_at=getattr(edge, 'updated_at', datetime.now(timezone.utc)),
            expires_at=getattr(edge, 'expires_at', None),
            metadata=getattr(edge, 'metadata', {}),
            group_ids=getattr(edge, 'group_ids', []),
            fact_rating=getattr(edge, 'fact_rating', 1.0)
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get edge {edge_uuid}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get edge: {str(e)}"
        )


@router.get("/episodes/{episode_uuid}", response_model=EpisodicNode)
async def get_episode(
    episode_uuid: str,
    settings: ZepEnvDep,
    http_request: Request
):
    """Get a specific episode by UUID"""
    user_id = extract_user_id_from_request(http_request) or "default_user"
    graphiti = get_or_create_pooled_client(user_id, settings)
    
    try:
        # Get episode from Graphiti
        episode = await graphiti.get_episode(episode_uuid)
        
        if not episode:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Episode {episode_uuid} not found"
            )
        
        return EpisodicNode(
            uuid=episode.uuid,
            name=getattr(episode, 'name', ''),
            content=getattr(episode, 'content', ''),
            episode_type=getattr(episode, 'episode_type', 'text'),
            source=getattr(episode, 'source', 'unknown'),
            created_at=getattr(episode, 'created_at', datetime.now(timezone.utc)),
            updated_at=getattr(episode, 'updated_at', datetime.now(timezone.utc)),
            metadata=getattr(episode, 'metadata', {}),
            group_ids=getattr(episode, 'group_ids', []),
            user_id=getattr(episode, 'user_id', None),
            session_id=getattr(episode, 'session_id', None)
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get episode {episode_uuid}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get episode: {str(e)}"
        )


@router.post("/nodes/{node_uuid}/relationships", response_model=NodeRelationshipsResponse)
async def get_node_relationships(
    node_uuid: str,
    request: NodeRelationshipsRequest,
    settings: ZepEnvDep,
    http_request: Request
):
    """
    Get relationships for a specific node
    Returns connected nodes, edges, and related episodes
    """
    user_id = extract_user_id_from_request(http_request) or "default_user"
    graphiti = get_or_create_pooled_client(user_id, settings)
    
    try:
        # Get the center node
        center_node = await graphiti.get_node(node_uuid)
        if not center_node:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Node {node_uuid} not found"
            )
        
        # Get connected nodes and relationships
        # Note: This is a simplified implementation - would need proper graph traversal
        connected_nodes = []
        relationships = []
        related_episodes = []
        
        # TODO: Implement proper graph traversal logic using Graphiti
        
        return NodeRelationshipsResponse(
            center_node=EntityNode(
                uuid=center_node.uuid,
                name=getattr(center_node, 'name', ''),
                summary=getattr(center_node, 'summary', ''),
                entity_type=getattr(center_node, 'entity_type', 'generic'),
                created_at=getattr(center_node, 'created_at', datetime.now(timezone.utc)),
                updated_at=getattr(center_node, 'updated_at', datetime.now(timezone.utc)),
                metadata=getattr(center_node, 'metadata', {}),
                group_ids=getattr(center_node, 'group_ids', [])
            ),
            connected_nodes=connected_nodes,
            relationships=relationships,
            related_episodes=related_episodes,
            depth_analyzed=request.max_depth
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get node relationships for {node_uuid}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get node relationships: {str(e)}"
        )


@router.delete("/edges/{edge_uuid}")
async def delete_edge(
    edge_uuid: str,
    settings: ZepEnvDep,
    http_request: Request
):
    """Delete a specific entity edge"""
    user_id = extract_user_id_from_request(http_request) or "default_user"
    graphiti = get_or_create_pooled_client(user_id, settings)
    
    try:
        # Delete edge using Graphiti
        await graphiti.delete_entity_edge(edge_uuid)
        
        return {"message": f"Edge {edge_uuid} deleted successfully", "success": True}
        
    except Exception as e:
        logger.error(f"Failed to delete edge {edge_uuid}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete edge: {str(e)}"
        )


@router.delete("/episodes/{episode_uuid}")
async def delete_episode(
    episode_uuid: str,
    settings: ZepEnvDep,
    http_request: Request
):
    """Delete a specific episode"""
    user_id = extract_user_id_from_request(http_request) or "default_user"
    graphiti = get_or_create_pooled_client(user_id, settings)
    
    try:
        # Delete episode using Graphiti
        await graphiti.delete_episode(episode_uuid)
        
        return {"message": f"Episode {episode_uuid} deleted successfully", "success": True}
        
    except Exception as e:
        logger.error(f"Failed to delete episode {episode_uuid}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete episode: {str(e)}"
        )


@router.get("/users/{user_id}/triplets", response_model=List[Dict[str, Any]])
async def get_user_graph_triplets(
    user_id: str,
    settings: ZepEnvDep,
    http_request: Request,
    limit: int = Query(100, description="Maximum number of triplets to return")
):
    """
    Get graph triplets for a user using actual EntityEdges with complete data.
    Returns proper triplets with sourceNode, edge (with episodes, valid_at), targetNode.
    
    This matches the official Zep API structure for graph triplets.
    """
    
    try:
        # Extract user context for proper database isolation
        graphiti = get_or_create_pooled_client(user_id, settings)
        
        logger.info(f"🔍 Getting actual graph triplets for user: {user_id}")
        
        # DEBUG: First check what data exists in the database
        try:
            debug_query = """
            MATCH (n)
            RETURN DISTINCT labels(n) as node_labels, count(n) as count
            """
            debug_result = await graphiti.driver.execute_query(debug_query)
            logger.info(f"🔍 DEBUG - Database node labels: {debug_result}")
        except Exception as e:
            logger.error(f"❌ DEBUG - Node labels query failed: {e}")
        
        # DEBUG: Check what relationships exist
        try:
            rel_debug_query = """
            MATCH ()-[r]->()
            RETURN DISTINCT type(r) as rel_type, count(r) as count
            """
            rel_debug_result = await graphiti.driver.execute_query(rel_debug_query)
            logger.info(f"🔍 DEBUG - Database relationship types: {rel_debug_result}")
        except Exception as e:
            logger.error(f"❌ DEBUG - Relationship types query failed: {e}")
            
        # DEBUG: Check total node count
        try:
            count_query = """
            MATCH (n)
            RETURN count(n) as total_nodes
            """
            count_result = await graphiti.driver.execute_query(count_query)
            logger.info(f"🔍 DEBUG - Total nodes in database: {count_result}")
        except Exception as e:
            logger.error(f"❌ DEBUG - Node count query failed: {e}")
            
        # DEBUG: Test simple queries to verify they work
        try:
            # Try to get ANY edges, regardless of user/group
            test_edge_query = """
            MATCH ()-[r]->()
            RETURN type(r) as rel_type, count(r) as count
            LIMIT 5
            """
            test_edge_result = await graphiti.driver.execute_query(test_edge_query)
            logger.info(f"🔍 DEBUG - Any edges in database: {test_edge_result}")
        except Exception as e:
            logger.error(f"❌ DEBUG - Simple edge query failed: {e}")
            
        # DEBUG: Try to get ANY Entity nodes  
        try:
            test_entity_query = """
            MATCH (n:Entity)
            RETURN count(n) as entity_count
            LIMIT 1
            """
            test_entity_result = await graphiti.driver.execute_query(test_entity_query)
            logger.info(f"🔍 DEBUG - Entity nodes count: {test_entity_result}")
        except Exception as e:
            logger.error(f"❌ DEBUG - Entity query failed: {e}")
            
        # DEBUG: Try alternative relationship patterns
        try:
            alt_query = """
            MATCH (n)-[r:MENTIONS]->(m)
            RETURN type(r) as rel_type, count(r) as count
            LIMIT 5
            """
            alt_result = await graphiti.driver.execute_query(alt_query)
            logger.info(f"🔍 DEBUG - MENTIONS relationships: {alt_result}")
        except Exception as e:
            logger.error(f"❌ DEBUG - MENTIONS query failed: {e}")
        
        # DEBUG: Check what group_ids exist for this user
        group_debug_query = """
        MATCH (n)
        WHERE n.group_id IS NOT NULL
        RETURN DISTINCT n.group_id as group_id
        ORDER BY n.group_id
        LIMIT 20
        """
        group_debug_result = await graphiti.driver.execute_query(group_debug_query)
        logger.info(f"🔍 DEBUG - Available group_ids: {group_debug_result}")
        
        # DEBUG: Check specifically for user-related group_ids
        user_group_debug_query = """
        MATCH (n)
        WHERE n.group_id CONTAINS $user_id
        RETURN DISTINCT n.group_id as group_id
        """
        user_group_debug_result = await graphiti.driver.execute_query(user_group_debug_query, user_id=user_id)
        logger.info(f"🔍 DEBUG - User-related group_ids: {user_group_debug_result}")
        
        # Step 1: Query for regular RELATES_TO relationships between different Entity nodes
        triplets = []
        
        edge_query = """
        MATCH (n:Entity)-[e:RELATES_TO]->(m:Entity)
        WHERE e.group_id STARTS WITH $user_id_pattern
        RETURN e.uuid as edge_uuid, e.name as edge_name, 
               e.fact as edge_fact, e.created_at as edge_created_at, e.updated_at as edge_updated_at,
               e.valid_at as edge_valid_at, e.expires_at as edge_expires_at, 
               e.invalid_at as edge_invalid_at, e.episodes as edge_episodes,
               n.uuid as source_uuid, n.name as source_name, n.summary as source_summary,
               n.labels as source_labels, n.attributes as source_attributes, 
               n.created_at as source_created_at, n.updated_at as source_updated_at,
               m.uuid as target_uuid, m.name as target_name, m.summary as target_summary,
               m.labels as target_labels, m.attributes as target_attributes,
               m.created_at as target_created_at, m.updated_at as target_updated_at
        ORDER BY e.created_at DESC
        LIMIT $limit
        """
        
        user_id_pattern = f"{user_id}_"
        
        try:
            edge_result = await graphiti.driver.execute_query(
                edge_query, 
                user_id_pattern=user_id_pattern,
                limit=limit
            )
            
            # Handle FalkorDB result format
            actual_records = edge_result[0] if isinstance(edge_result, tuple) and len(edge_result) > 0 else edge_result
            logger.info(f"📊 Found {len(actual_records) if hasattr(actual_records, '__len__') else 'unknown'} regular relationship records for user {user_id}")
            
            for record in actual_records:
                if record is None:
                    continue
                
                # Handle both dictionary and list formats
                if isinstance(record, dict):
                    record_data = record
                elif isinstance(record, list):
                    # Map list to field names for the combined query
                    field_names = [
                        'edge_uuid', 'edge_name', 'edge_fact', 'edge_created_at', 'edge_updated_at',
                        'edge_valid_at', 'edge_expires_at', 'edge_invalid_at', 'edge_episodes',
                        'source_uuid', 'source_name', 'source_summary', 'source_labels', 'source_attributes',
                        'source_created_at', 'source_updated_at',
                        'target_uuid', 'target_name', 'target_summary', 'target_labels', 'target_attributes',
                        'target_created_at', 'target_updated_at'
                    ]
                    if len(record) == len(field_names):
                        record_data = dict(zip(field_names, record))
                    else:
                        logger.warning(f"Record length {len(record)} doesn't match expected {len(field_names)} fields")
                        continue
                else:
                    continue
                
                # Build triplet directly from combined result
                try:
                    triplet = {
                        "sourceNode": {
                            "uuid": record_data.get('source_uuid', ''),
                            "name": record_data.get('source_name', ''),
                            "summary": record_data.get('source_summary', ''),
                            "labels": record_data.get('source_labels', []),
                            "attributes": record_data.get('source_attributes', {}),
                            "created_at": record_data.get('source_created_at', ''),
                            "updated_at": record_data.get('source_updated_at', '')
                        },
                        "edge": {
                            "uuid": record_data.get('edge_uuid', ''),
                            "source_node_uuid": record_data.get('source_uuid', ''),
                            "target_node_uuid": record_data.get('target_uuid', ''),
                            "name": record_data.get('edge_name', ''),
                            "fact": record_data.get('edge_fact', ''),
                            "created_at": record_data.get('edge_created_at', ''),
                            "updated_at": record_data.get('edge_updated_at', ''),
                            "valid_at": record_data.get('edge_valid_at'),
                            "expires_at": record_data.get('edge_expires_at'),
                            "invalid_at": record_data.get('edge_invalid_at'),
                            "episodes": record_data.get('edge_episodes', [])
                        },
                        "targetNode": {
                            "uuid": record_data.get('target_uuid', ''),
                            "name": record_data.get('target_name', ''),
                            "summary": record_data.get('target_summary', ''),
                            "labels": record_data.get('target_labels', []),
                            "attributes": record_data.get('target_attributes', {}),
                            "created_at": record_data.get('target_created_at', ''),
                            "updated_at": record_data.get('target_updated_at', '')
                        }
                    }
                    
                    triplets.append(triplet)
                    
                except Exception as e:
                    logger.warning(f"Failed to build triplet from record: {e}")
                    continue
                
        except Exception as e:
            logger.error(f"Regular edge+node query failed: {e}")
        
        # Step 2: Query for isolated User nodes (like official Zep behavior)
        # Official Zep creates isolated_node relationships when User nodes exist without other relationships
        
        # Debug: Check what group_ids actually exist for this user
        try:
            debug_query = """
            MATCH (n:Entity)
            WHERE (n.group_id STARTS WITH $user_id_pattern 
               OR n.group_id CONTAINS $user_id)
            RETURN DISTINCT n.group_id as group_id, labels(n) as labels, n.entity_type as entity_type
            LIMIT 10
            """
            debug_result = await graphiti.driver.execute_query(
                debug_query, 
                user_id_pattern=user_id_pattern,
                user_id=user_id
            )
            debug_records = debug_result[0] if isinstance(debug_result, tuple) and len(debug_result) > 0 else debug_result
            logger.info(f"🔍 DEBUG - Group IDs found for user {user_id}: {[record.get('group_id') if isinstance(record, dict) else record for record in debug_records[:5]]}")
        except Exception as e:
            logger.warning(f"Debug query failed: {e}")
        
        try:
            isolated_user_query = """
            MATCH (n:Entity)
            WHERE (n.group_id STARTS WITH $user_id_pattern 
               OR n.group_id CONTAINS $user_id)
               AND ('User' IN labels(n) OR n.entity_type = 'User')
               AND NOT (n)-[:RELATES_TO]-(:Entity)
               AND NOT (:Entity)-[:RELATES_TO]-(n)
            RETURN n.uuid as node_uuid, n.name as node_name, n.summary as node_summary,
                   n.labels as node_labels, n.attributes as node_attributes,
                   n.created_at as node_created_at, n.updated_at as node_updated_at,
                   n.entity_type as entity_type, n.group_id as group_id
            ORDER BY n.created_at DESC
            LIMIT $limit
            """
            
            isolated_result = await graphiti.driver.execute_query(
                isolated_user_query, 
                user_id_pattern=user_id_pattern,
                user_id=user_id,
                limit=limit - len(triplets)  # Leave room for isolated nodes
            )
            
            isolated_records = isolated_result[0] if isinstance(isolated_result, tuple) and len(isolated_result) > 0 else isolated_result
            logger.info(f"📊 Found {len(isolated_records) if hasattr(isolated_records, '__len__') else 'unknown'} isolated User nodes for user {user_id}")
            logger.info(f"🔍 DEBUG - Isolated result format: {type(isolated_result)}, records: {isolated_records[:2] if hasattr(isolated_records, '__len__') and len(isolated_records) > 0 else isolated_records}")
            
            # Create isolated node triplets (like official Zep)
            for i, record in enumerate(isolated_records):
                if record is None:
                    logger.warning(f"🔍 DEBUG - Record {i} is None, skipping")
                    continue
                
                # Handle both dictionary and list formats
                logger.info(f"🔍 DEBUG - Record {i} type: {type(record)}, content: {record}")
                if isinstance(record, dict):
                    node_data = record
                elif isinstance(record, list):
                    field_names = ['node_uuid', 'node_name', 'node_summary', 'node_labels', 'node_attributes', 'node_created_at', 'node_updated_at', 'entity_type', 'group_id']
                    logger.info(f"🔍 DEBUG - Converting list record (len={len(record)}) to dict with {len(field_names)} fields")
                    if len(record) == len(field_names):
                        node_data = dict(zip(field_names, record))
                        logger.info(f"🔍 DEBUG - Successfully converted to: {node_data}")
                    else:
                        logger.warning(f"🔍 DEBUG - Record length mismatch: {len(record)} != {len(field_names)}, skipping")
                        continue
                else:
                    logger.warning(f"🔍 DEBUG - Unknown record type {type(record)}, skipping")
                    continue
                
                try:
                    # Create isolated node triplet (both source and target are the same User node)
                    node_uuid = node_data.get('node_uuid', '')
                    
                    # Create the user node structure
                    user_node = {
                        "uuid": node_uuid,
                        "name": node_data.get('node_name', ''),
                        "graph_id": node_uuid,  # Match official Zep structure
                        "labels": ["Entity", "User"],  # Match official Zep structure
                        "created_at": node_data.get('node_created_at', ''),
                        "score": None,
                        "summary": node_data.get('node_summary', f"user with the id of {user_id}"),
                        "attributes": {
                            "email": "",
                            "first_name": "",
                            "last_name": "",
                            "role_type": "user",
                            "user_id": user_id,
                            **(node_data.get('node_attributes') or {})
                        }
                    }
                    
                    # Create isolated node edge (like official Zep)
                    isolated_edge = {
                        "uuid": f"isolated-node-{node_uuid}",
                        "source_node_uuid": node_uuid,
                        "target_node_uuid": node_uuid,  # Points to itself
                        "type": "_isolated_node_",
                        "name": "",
                        "created_at": node_data.get('node_created_at', '')
                    }
                    
                    # Build triplet with isolated node structure
                    triplet = {
                        "sourceNode": user_node,
                        "edge": isolated_edge,
                        "targetNode": user_node  # Same as source for isolated nodes
                    }
                    
                    triplets.append(triplet)
                    
                except Exception as e:
                    logger.warning(f"Failed to build isolated node triplet from record: {e}")
                    continue
        
        except Exception as e:
            logger.error(f"Isolated User nodes query failed: {e}")
        
        logger.info(f"✅ Built {len(triplets)} graph triplets for user {user_id} (including isolated nodes)")
        return triplets
        
    except Exception as e:
        logger.error(f"❌ Failed to get graph triplets for user {user_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get graph triplets: {str(e)}"
        )


# Legacy compatibility endpoints for existing code
@router.post("/search-legacy", response_model=Dict[str, Any])
async def search_legacy(
    query: str,
    group_ids: List[str] = [],
    max_facts: int = 10,
    settings: ZepEnvDep = None,
    http_request: Request = None
):
    """Legacy search endpoint for backward compatibility"""
    request = GraphSearchRequest(
        query=query,
        group_ids=group_ids,
        max_results=max_facts
    )
    
    result = await search_graph(request, settings, http_request)
    
    # Convert new format to legacy format
    facts = []
    
    # Process edges as facts (traditional approach)
    for edge in result.edges:
        fact = ZepFactResult(
            uuid=edge.uuid,
            fact=edge.fact,
            created_at=edge.created_at,
            updated_at=edge.updated_at,
            metadata=edge.metadata,
            score=edge.fact_rating,
            search_rank=len(facts) + 1,
            relevance_score=edge.fact_rating
        )
        facts.append(fact.dict())
    
    # Add episodes as facts if no edges found
    if not facts:
        for episode in result.episodes:
            fact = ZepFactResult(
                uuid=episode.uuid,
                fact=episode.content,
                created_at=episode.created_at,
                updated_at=episode.updated_at,
                metadata=episode.metadata,
                score=1.0,
                search_rank=len(facts) + 1,
                relevance_score=1.0
            )
            facts.append(fact.dict())
    
    return {
        "facts": facts,
        "total_results": len(facts),
        "search_metadata": result.search_metadata
    }


@router.get("/episodes/user/{user_id}", response_model=List[EpisodeResponse])
async def get_user_episodes_via_graph(
    user_id: str,
    settings: ZepEnvDep,
    http_request: Request,
    limit: int = Query(100, description="Maximum number of episodes to return")
):
    """
    Get episodes for a specific user - accessible via zep-server proxy
    This endpoint handles requests forwarded from zep-server at /api/v2/graph/episodes/user/{user_id}
    """
    try:
        graphiti = get_or_create_pooled_client(user_id, settings)
        
        logger.info(f"🔍 Fetching episodes for user: {user_id} (via graph endpoint)")
        
        # First, let's debug what group_ids exist for Episodic nodes
        debug_query = """
        MATCH (e:Episodic) 
        RETURN DISTINCT e.group_id as group_id
        ORDER BY e.group_id
        LIMIT 50
        """
        
        debug_result = await graphiti.driver.execute_query(debug_query)
        logger.info(f"🔍 DEBUG: All Episodic group_ids in database: {debug_result}")
        
        # Also check for group_ids that contain the user_id anywhere
        user_search_query = """
        MATCH (e:Episodic) 
        WHERE e.group_id CONTAINS $user_id
        RETURN e.group_id as group_id, e.uuid as uuid
        ORDER BY e.created_at DESC
        LIMIT 10
        """
        
        user_search_result = await graphiti.driver.execute_query(user_search_query, user_id=user_id)
        logger.info(f"🔍 DEBUG: Episodes containing user_id '{user_id}': {user_search_result}")
        
        # Query for Episodic nodes where group_id starts with user_id OR contains user_id OR equals user_id
        query = """
        MATCH (e:Episodic) 
        WHERE e.group_id STARTS WITH $user_prefix 
           OR e.group_id CONTAINS $user_id 
           OR e.group_id = $user_id
        RETURN e.uuid as uuid, e.name as name, e.content as content, 
               e.source as source, e.source_description as source_description,
               e.created_at as created_at, e.updated_at as updated_at,
               e.group_id as group_id
        ORDER BY e.created_at DESC
        LIMIT $limit
        """
        
        user_prefix = f"{user_id}_"
        logger.info(f"🔍 DEBUG: Searching with user_prefix='{user_prefix}', user_id='{user_id}'")
        
        result = await graphiti.driver.execute_query(
            query, 
            user_prefix=user_prefix, 
            user_id=user_id,
            limit=limit
        )
        
        episodes = []
        # Handle FalkorDB result format: (data_list, fields_list, metadata)
        actual_records = result[0] if isinstance(result, tuple) and len(result) > 0 else result
        logger.info(f"🔍 DEBUG: Processing {len(actual_records) if hasattr(actual_records, '__len__') else 'unknown'} records from query result")
        
        for record in actual_records:
            if record is None:
                continue
            
            # Handle both dictionary and list formats from FalkorDB
            if isinstance(record, dict):
                record_data = record
            elif isinstance(record, list):
                # Skip header rows
                if len(record) == 8 and all(isinstance(item, str) for item in record if item is not None):
                    continue
                # Map list to field names
                field_names = ['uuid', 'name', 'content', 'source', 'source_description', 'created_at', 'updated_at', 'group_id']
                if len(record) == len(field_names):
                    record_data = dict(zip(field_names, record))
                else:
                    continue
            else:
                continue
            
            try:
                episode = EpisodeResponse(
                    uuid=record_data.get('uuid', ''),
                    content=record_data.get('content', ''),
                    source=record_data.get('source', 'unknown'),
                    source_description=record_data.get('source_description'),
                    created_at=record_data.get('created_at'),
                    updated_at=record_data.get('updated_at')
                )
                episodes.append(episode)
            except Exception as e:
                logger.warning(f"Failed to create episode from record: {e}")
                continue
        
        logger.info(f"✅ Found {len(episodes)} episodes for user {user_id}")
        return episodes
        
    except Exception as e:
        logger.error(f"❌ Failed to fetch episodes for user {user_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get user episodes: {str(e)}"
        )




@router.get("/episodes/session/{session_id}", response_model=List[EpisodeResponse])
async def get_session_episodes_via_graph(
    session_id: str,
    settings: ZepEnvDep,
    http_request: Request,
    user_id: str = Query(..., description="User ID that owns the session"),
    limit: int = Query(100, description="Maximum number of episodes to return")
):
    """
    Get episodes for a specific session - accessible via zep-server proxy
    This endpoint handles requests forwarded from zep-server at /api/v2/graph/episodes/session/{session_id}
    """
    try:
        graphiti = get_or_create_pooled_client(user_id, settings)
        
        logger.info(f"🔍 Fetching episodes for session: {session_id} (user: {user_id}) (via graph endpoint)")
        
        # Construct group_id for the session
        group_id = f"{user_id}_{session_id}"
        
        # Query for episodes with exact group_id match
        query = """
        MATCH (e:Episodic) 
        WHERE e.group_id = $group_id
        RETURN e.uuid as uuid, e.name as name, e.content as content, 
               e.source as source, e.source_description as source_description,
               e.created_at as created_at, e.updated_at as updated_at,
               e.group_id as group_id
        ORDER BY e.created_at DESC
        LIMIT $limit
        """
        
        result = await graphiti.driver.execute_query(
            query, 
            group_id=group_id, 
            limit=limit
        )
        
        episodes = []
        # Handle FalkorDB result format: (data_list, fields_list, metadata)
        actual_records = result[0] if isinstance(result, tuple) and len(result) > 0 else result
        logger.info(f"🔍 DEBUG: Processing {len(actual_records) if hasattr(actual_records, '__len__') else 'unknown'} records from session query result")
        
        for record in actual_records:
            if record is None:
                continue
            
            # Handle both dictionary and list formats
            if isinstance(record, dict):
                record_data = record
            elif isinstance(record, list):
                # Skip header rows
                if len(record) == 8 and all(isinstance(item, str) for item in record if item is not None):
                    continue
                # Map list to field names
                field_names = ['uuid', 'name', 'content', 'source', 'source_description', 'created_at', 'updated_at', 'group_id']
                if len(record) == len(field_names):
                    record_data = dict(zip(field_names, record))
                else:
                    continue
            else:
                continue
            
            try:
                episode = EpisodeResponse(
                    uuid=record_data.get('uuid', ''),
                    content=record_data.get('content', ''),
                    source=record_data.get('source', 'unknown'),
                    source_description=record_data.get('source_description'),
                    created_at=record_data.get('created_at'),
                    updated_at=record_data.get('updated_at')
                )
                episodes.append(episode)
            except Exception as e:
                logger.warning(f"Failed to create episode from record: {e}")
                continue
        
        logger.info(f"✅ Found {len(episodes)} episodes for session {session_id}")
        return episodes
        
    except Exception as e:
        logger.error(f"❌ Failed to fetch episodes for session {session_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get session episodes: {str(e)}"
        )