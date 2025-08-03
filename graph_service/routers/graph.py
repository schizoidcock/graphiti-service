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
    Compatible with official Zep graph search API
    """
    # Extract user context for proper database isolation (Zep v2 compatible)
    if request.user_id:
        # User-specific search: use user database with user-specific group_ids
        user_id = request.user_id
        graphiti = get_or_create_pooled_client(user_id, settings)
        # For user searches, use user-specific group_id pattern or provided group_ids
        search_group_ids = request.group_ids if request.group_ids else [f"{user_id}_session"]
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
        search_group_ids = [f"{user_id}_session"]
    
    try:
        # Use Graphiti's search with proper isolation
        search_results = await graphiti.search(
            group_ids=search_group_ids,
            query=request.query,
            num_results=request.max_results
        )
        
        # Initialize separate collections for Zep's architecture
        edges = []
        episodes = []
        nodes = []
        
        # Process search results based on their type
        for result in search_results:
            try:
                # Check if result is an EntityEdge (relationship)
                if hasattr(result, 'fact') and hasattr(result, 'source_node_uuid'):
                    edge = EntityEdge(
                        uuid=getattr(result, 'uuid', str(uuid_lib.uuid4())),
                        source_node_uuid=getattr(result, 'source_node_uuid', ''),
                        target_node_uuid=getattr(result, 'target_node_uuid', ''),
                        name=getattr(result, 'name', getattr(result, 'relation', 'relates_to')),
                        fact=getattr(result, 'fact', ''),
                        predicate=getattr(result, 'relation', 'relates_to'),
                        edge_type='relates_to',
                        attributes=getattr(result, 'attributes', {}),
                        episodes=getattr(result, 'episodes', []),
                        created_at=getattr(result, 'created_at', datetime.now(timezone.utc)),
                        updated_at=getattr(result, 'updated_at', datetime.now(timezone.utc)),
                        valid_at=getattr(result, 'valid_at', None),
                        expires_at=getattr(result, 'expires_at', None),
                        invalid_at=getattr(result, 'invalid_at', None),
                        metadata=getattr(result, 'metadata', {}),
                        group_ids=request.group_ids,
                        fact_rating=getattr(result, 'fact_rating', 1.0)
                    )
                    edges.append(edge)
                
                # Check if result is an EpisodicNode (episode)
                elif hasattr(result, 'episode_body') or hasattr(result, 'content'):
                    episode = EpisodicNode(
                        uuid=getattr(result, 'uuid', str(uuid_lib.uuid4())),
                        name=getattr(result, 'name', ''),
                        content=getattr(result, 'episode_body', getattr(result, 'content', '')),
                        episode_type=getattr(result, 'episode_type', 'text'),
                        source=getattr(result, 'source', 'unknown'),
                        created_at=getattr(result, 'created_at', datetime.now(timezone.utc)),
                        updated_at=getattr(result, 'updated_at', datetime.now(timezone.utc)),
                        metadata=getattr(result, 'metadata', {}),
                        group_ids=request.group_ids,
                        user_id=request.user_id,
                        session_id=request.session_id
                    )
                    episodes.append(episode)
                
                # Check if result is an EntityNode
                elif hasattr(result, 'summary'):
                    node = EntityNode(
                        uuid=getattr(result, 'uuid', str(uuid_lib.uuid4())),
                        name=getattr(result, 'name', ''),
                        summary=getattr(result, 'summary', ''),
                        entity_type=getattr(result, 'entity_type', 'generic'),
                        labels=getattr(result, 'labels', []),
                        attributes=getattr(result, 'attributes', {}),
                        created_at=getattr(result, 'created_at', datetime.now(timezone.utc)),
                        updated_at=getattr(result, 'updated_at', datetime.now(timezone.utc)),
                        metadata=getattr(result, 'metadata', {}),
                        group_ids=request.group_ids
                    )
                    nodes.append(node)
                
            except Exception as e:
                logger.warning(f"Failed to process search result: {e}")
                continue
        
        return GraphSearchResponse(
            edges=edges,
            episodes=episodes,
            nodes=nodes,
            search_metadata={
                "execution_time_ms": 0,  # TODO: Add timing
                "user_id": user_id,
                "group_ids": search_group_ids,
                "query_processed": request.query,
                "search_type": request.search_type,
                "reranker": request.reranker,
                "scope": request.scope,
                "total_edges": len(edges),
                "total_episodes": len(episodes),
                "total_nodes": len(nodes)
            }
        )
        
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
        episode_result = await graphiti.add_episode(
            name=f"Episode_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
            episode_body=request.data,
            source=request.data_type,
            source_description=request.source_description or f"Added via graph API - {request.data_type}",
            group_id=group_id,
            reference_time=datetime.now(timezone.utc)
        )
        
        processing_time = (datetime.now() - start_time).total_seconds() * 1000
        
        # Get statistics from the episode result
        entities_created = len(getattr(episode_result, 'extracted_entities', []))
        relationships_created = len(getattr(episode_result, 'extracted_edges', []))
        
        return GraphAddResponse(
            episode_uuid=getattr(episode_result, 'uuid', str(uuid_lib.uuid4())),
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
    Get graph triplets for a user using Zep's episode-based architecture.
    Episodes serve as the relationships connecting nodes.
    
    This endpoint matches the expected structure for zep-web-interface
    graph visualization where episodes are the connecting relationships.
    """
    
    try:
        # Extract user context for proper database isolation
        graphiti = get_or_create_pooled_client(user_id, settings)
        
        logger.info(f"🔍 Building graph triplets for user: {user_id}")
        
        # Step 1: Get all episodes for this user 
        # In Graphiti, episodes represent the temporal context of knowledge extraction
        from graphiti_core.nodes import EpisodicNode
        episodes = await EpisodicNode.get_by_group_ids(graphiti.driver, [f"{user_id}_*"])
        
        logger.info(f"📊 Found {len(episodes)} episodes for user {user_id}")
        
        triplets = []
        node_cache = {}  # Cache nodes to avoid duplicates
        
        # Step 2: For each episode, get the nodes and edges that were extracted from it
        for episode in episodes[:limit]:  # Limit episodes processed
            try:
                # Get entities and relationships extracted from this episode
                # In Graphiti, these are stored with the same group_id as the episode
                group_id = getattr(episode, 'group_id', f"{user_id}_session")
                
                # Get nodes that were extracted from this episode's group
                from graphiti_core.nodes import EntityNode
                nodes = await EntityNode.get_by_group_ids(graphiti.driver, [group_id])
                
                # Get edges that were extracted from this episode's group  
                from graphiti_core.edges import EntityEdge
                edges = await EntityEdge.get_by_group_ids(graphiti.driver, [group_id])
                
                # Cache nodes for reference
                for node in nodes:
                    node_cache[node.uuid] = {
                        "uuid": node.uuid,
                        "name": getattr(node, 'name', ''),
                        "summary": getattr(node, 'summary', ''),
                        "labels": getattr(node, 'labels', []),
                        "attributes": getattr(node, 'attributes', {}),
                        "created_at": getattr(node, 'created_at', '').isoformat() if hasattr(getattr(node, 'created_at', ''), 'isoformat') else str(getattr(node, 'created_at', '')),
                        "updated_at": getattr(node, 'updated_at', '').isoformat() if hasattr(getattr(node, 'updated_at', ''), 'isoformat') else str(getattr(node, 'updated_at', ''))
                    }
                
                # Step 3: Build triplets where the episode serves as the relationship
                # For each pair of nodes that appear in the same episode, create a triplet
                # with the episode as the connecting relationship
                for i, source_node in enumerate(nodes):
                    for target_node in nodes[i+1:]:  # Avoid duplicate pairs
                        
                        # Create triplet with episode as the relationship
                        triplet = {
                            "sourceNode": node_cache[source_node.uuid],
                            "episode": {
                                "uuid": episode.uuid,
                                "source_node_uuid": source_node.uuid,
                                "target_node_uuid": target_node.uuid, 
                                "type": "episode_relationship",
                                "name": getattr(episode, 'name', f"Episode_{episode.uuid[:8]}"),
                                "fact": f"Entities mentioned together in {getattr(episode, 'name', 'episode')}",
                                "content": getattr(episode, 'episode_body', getattr(episode, 'content', '')),
                                "summary": getattr(episode, 'summary', ''),
                                "created_at": getattr(episode, 'created_at', '').isoformat() if hasattr(getattr(episode, 'created_at', ''), 'isoformat') else str(getattr(episode, 'created_at', '')),
                                "updated_at": getattr(episode, 'updated_at', '').isoformat() if hasattr(getattr(episode, 'updated_at', ''), 'isoformat') else str(getattr(episode, 'updated_at', '')),
                                "valid_at": None,
                                "expired_at": None,  
                                "invalid_at": None
                            },
                            "targetNode": node_cache[target_node.uuid]
                        }
                        
                        triplets.append(triplet)
                        
                        # Limit triplets to avoid overwhelming the UI
                        if len(triplets) >= limit:
                            break
                    
                    if len(triplets) >= limit:
                        break
                        
            except Exception as e:
                logger.warning(f"Failed to process episode {episode.uuid}: {e}")
                continue
        
        logger.info(f"✅ Built {len(triplets)} graph triplets for user {user_id}")
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
        
        # Query for Episodic nodes where group_id starts with user_id
        query = """
        MATCH (e:Episodic) 
        WHERE e.group_id STARTS WITH $user_prefix
        RETURN e.uuid as uuid, e.name as name, e.content as content, 
               e.source as source, e.source_description as source_description,
               e.created_at as created_at, e.updated_at as updated_at,
               e.group_id as group_id
        ORDER BY e.created_at DESC
        LIMIT $limit
        """
        
        user_prefix = f"{user_id}:"
        result = await graphiti.driver.execute_query(
            query, 
            user_prefix=user_prefix, 
            limit=limit
        )
        
        episodes = []
        for record in result:
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
        for record in result:
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