import logging
from typing import Dict, Any, Optional, List, Union
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, status, Query
from pydantic import BaseModel, Field, validator

from graph_service.dto.graph import EpisodeMentionsResponse, EntityNode, EntityEdge
from graph_service.zep_graphiti import ZepGraphitiDep

# Logger for episodes management
logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v2", tags=["episodes"])


class EpisodeResponse(BaseModel):
    """Response model for episode data matching web interface expectations"""
    uuid: str = Field(..., description='Episode UUID')
    content: str = Field(..., description='Episode content/body')
    source: str = Field(..., description='Episode source type')
    source_description: Optional[str] = Field(None, description='Source description')
    status: str = Field(default="processed", description='Episode status')
    created_at: Optional[datetime] = Field(None, description='Episode creation timestamp')
    updated_at: Optional[datetime] = Field(None, description='Last update timestamp')
    role: Optional[str] = Field(None, description='Message role if applicable')
    processed: bool = Field(default=True, description='Whether episode is processed')
    
    @validator('created_at', 'updated_at', pre=True, allow_reuse=True)
    def parse_datetime(cls, v):
        """Parse datetime from various formats including string field names"""
        if v is None:
            return None
        if isinstance(v, datetime):
            return v
        if isinstance(v, str):
            # Skip field names that are not datetime values
            if v in ['created_at', 'updated_at']:
                return None
            # Try to parse ISO format datetime strings
            try:
                return datetime.fromisoformat(v.replace('Z', '+00:00'))
            except (ValueError, AttributeError):
                logger.warning(f"Could not parse datetime string: {v}")
                return None
        if isinstance(v, (int, float)):
            # Handle Unix timestamps
            try:
                return datetime.fromtimestamp(v, tz=timezone.utc)
            except (ValueError, OSError):
                logger.warning(f"Could not parse timestamp: {v}")
                return None
        return None


@router.get('/graph/episodes/user/{user_id}', status_code=status.HTTP_200_OK, response_model=List[EpisodeResponse])
async def get_user_episodes(
    user_id: str,
    graphiti: ZepGraphitiDep,
    limit: int = Query(100, description="Maximum number of episodes to return")
):
    """Get episodes for a specific user from the graph database"""
    
    try:
        # In Graphiti/FalkorDB, episodes are stored as EpisodicNode objects
        # We need to query for episodes associated with this user
        
        # Create group ID pattern for this user (matching sessions.py pattern)
        # Episodes are typically stored with group_id like "user_id:session_id"
        # We'll search for all episodes that start with this user_id
        
        logger.info(f"🔍 Fetching episodes for user: {user_id}")
        
        # Connected to FalkorDB database for this user
        logger.info(f"🔍 Connected to FalkorDB database for user: {user_id}")
        
        # Debug: Count all Episodic nodes in the current database
        try:
            count_query = "MATCH (e:Episodic) RETURN count(e) as total_episodes"
            count_result = await graphiti.driver.execute_query(count_query)
            if count_result and len(count_result) > 0:
                # Handle both dictionary and list formats
                first_result = count_result[0]
                if isinstance(first_result, dict):
                    total_episodes = first_result.get('total_episodes', 0)
                elif isinstance(first_result, list):
                    total_episodes = first_result[0] if first_result else 0
                else:
                    total_episodes = first_result
                logger.info(f"🔍 Total Episodic nodes in current database: {total_episodes}")
            else:
                logger.info(f"🔍 No count result returned from database")
            
            # Also check different group_id patterns
            pattern_query = """
            MATCH (e:Episodic) 
            RETURN DISTINCT e.group_id as group_id
            LIMIT 10
            """
            pattern_result = await graphiti.driver.execute_query(pattern_query)
            group_ids = []
            for r in pattern_result:
                if r is None:
                    continue
                if isinstance(r, dict):
                    group_ids.append(r.get('group_id'))
                elif isinstance(r, list):
                    group_ids.append(r[0] if r else None)
                else:
                    group_ids.append(str(r))
            group_ids = [gid for gid in group_ids if gid is not None and gid != 'group_id']
            logger.info(f"🔍 Sample group_id patterns in database: {group_ids}")
            
            # Also check what raw data looks like
            sample_query = """
            MATCH (e:Episodic) 
            RETURN e.uuid, e.name, e.content, e.group_id
            LIMIT 3
            """
            sample_result = await graphiti.driver.execute_query(sample_query)
            logger.info(f"🔍 Sample episode data (raw): {sample_result}")
            
        except Exception as debug_e:
            logger.warning(f"🔍 Could not perform debug queries: {debug_e}")
        
        # Use Graphiti's driver to query episodes directly
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
        logger.info(f"🔍 Searching for episodes with group_id starting with: '{user_prefix}'")
        
        result = await graphiti.driver.execute_query(
            query, 
            user_prefix=user_prefix, 
            limit=limit
        )
        
        episodes = []
        for i, record in enumerate(result):
            try:
                # Skip None records
                if record is None:
                    logger.debug(f"Skipping None record at index {i}")
                    continue
                
                # Handle both dictionary and list formats from FalkorDB
                if isinstance(record, dict):
                    # Dictionary format (expected)
                    record_data = record
                elif isinstance(record, list):
                    # Check if this is the header row with field names (common issue with FalkorDB)
                    if len(record) == 8 and all(isinstance(item, str) and item in ['uuid', 'name', 'content', 'source', 'source_description', 'created_at', 'updated_at', 'group_id'] for item in record if item is not None):
                        logger.debug(f"Skipping header row: {record}")
                        continue
                    
                    # List format - need to map to field names
                    # The query returns: uuid, name, content, source, source_description, created_at, updated_at, group_id
                    field_names = ['uuid', 'name', 'content', 'source', 'source_description', 'created_at', 'updated_at', 'group_id']
                    if len(record) != len(field_names):
                        logger.warning(f"Record length mismatch. Expected {len(field_names)}, got {len(record)}: {record}")
                        continue
                    record_data = dict(zip(field_names, record))
                else:
                    logger.warning(f"Unexpected record format: {type(record)} - {record}")
                    continue
                
                # Validate that we have meaningful data (not just field names)
                uuid_value = record_data.get('uuid', '')
                content_value = record_data.get('content', record_data.get('name', ''))
                
                # Skip records where uuid or content are just field names
                if uuid_value in ['uuid', 'name', 'content'] or content_value in ['uuid', 'name', 'content']:
                    logger.debug(f"Skipping record with field name values: uuid={uuid_value}, content={content_value}")
                    continue
                
                # Skip records with empty or meaningless data
                if not uuid_value or not content_value:
                    logger.debug(f"Skipping record with empty data: uuid={uuid_value}, content={content_value}")
                    continue
                
                # Convert the record to our response format
                episode = EpisodeResponse(
                    uuid=record_data.get('uuid', '') or '',
                    content=record_data.get('content', record_data.get('name', '')) or '',
                    source=record_data.get('source', 'message') or 'message',
                    source_description=record_data.get('source_description'),
                    status="processed",  # Assume processed if in DB
                    created_at=record_data.get('created_at'),
                    updated_at=record_data.get('updated_at'),
                    processed=True
                )
                episodes.append(episode)
                logger.debug(f"✅ Parsed episode: {episode.uuid} - {episode.content[:50]}...")
            except Exception as e:
                logger.warning(f"Failed to parse episode record: {e} - Record: {record}")
                continue
        
        logger.info(f"✅ Found {len(episodes)} episodes for user {user_id} in user-specific database")
        
        # If we found episodes but they seem to be just header data, return empty list
        if episodes and all(ep.uuid in ['uuid', 'name', 'content'] or ep.content in ['uuid', 'name', 'content'] for ep in episodes):
            logger.warning(f"🔍 All episodes appear to be header data, returning empty list")
            episodes = []
        
        # If no episodes found in user-specific database, try the default database
        # This handles cases where data was stored before database isolation was implemented
        if len(episodes) == 0:
            logger.info(f"🔍 No episodes found in user database, checking default database for user: {user_id}")
            try:
                # Get a client connected to the default database
                from graph_service.zep_graphiti import get_or_create_pooled_client
                from graph_service.config import get_settings
                default_settings = get_settings()
                default_graphiti = get_or_create_pooled_client("default_user", default_settings)
                
                # Search in default database with the same query
                default_result = await default_graphiti.driver.execute_query(
                    query, 
                    user_prefix=user_prefix, 
                    limit=limit
                )
                
                default_episodes = []
                for i, record in enumerate(default_result):
                    try:
                        # Skip None records
                        if record is None:
                            logger.debug(f"Skipping None record at index {i} in default database")
                            continue
                        
                        # Handle both dictionary and list formats from FalkorDB (same logic as above)
                        if isinstance(record, dict):
                            record_data = record
                        elif isinstance(record, list):
                            # Check if this is the header row with field names
                            if len(record) == 8 and all(isinstance(item, str) and item in ['uuid', 'name', 'content', 'source', 'source_description', 'created_at', 'updated_at', 'group_id'] for item in record if item is not None):
                                logger.debug(f"Skipping default database header row: {record}")
                                continue
                            
                            field_names = ['uuid', 'name', 'content', 'source', 'source_description', 'created_at', 'updated_at', 'group_id']
                            if len(record) != len(field_names):
                                logger.warning(f"Default database record length mismatch. Expected {len(field_names)}, got {len(record)}: {record}")
                                continue
                            record_data = dict(zip(field_names, record))
                        else:
                            logger.warning(f"Unexpected default database record format: {type(record)} - {record}")
                            continue
                        
                        # Validate that we have meaningful data (not just field names)
                        uuid_value = record_data.get('uuid', '')
                        content_value = record_data.get('content', record_data.get('name', ''))
                        
                        # Skip records where uuid or content are just field names
                        if uuid_value in ['uuid', 'name', 'content'] or content_value in ['uuid', 'name', 'content']:
                            logger.debug(f"Skipping default database record with field name values: uuid={uuid_value}, content={content_value}")
                            continue
                        
                        # Skip records with empty or meaningless data
                        if not uuid_value or not content_value:
                            logger.debug(f"Skipping default database record with empty data: uuid={uuid_value}, content={content_value}")
                            continue
                        
                        episode = EpisodeResponse(
                            uuid=record_data.get('uuid', '') or '',
                            content=record_data.get('content', record_data.get('name', '')) or '',
                            source=record_data.get('source', 'message') or 'message',
                            source_description=record_data.get('source_description'),
                            status="processed",
                            created_at=record_data.get('created_at'),
                            updated_at=record_data.get('updated_at'),
                            processed=True
                        )
                        default_episodes.append(episode)
                        logger.debug(f"✅ Parsed default database episode: {episode.uuid} - {episode.content[:50]}...")
                    except Exception as e:
                        logger.warning(f"Failed to parse episode record from default database: {e} - Record: {record}")
                        continue
                
                if default_episodes:
                    logger.info(f"✅ Found {len(default_episodes)} episodes for user {user_id} in DEFAULT database")
                    return default_episodes
                else:
                    logger.info(f"🔍 No episodes found in default database either for user: {user_id}")
                
            except Exception as default_e:
                logger.warning(f"Failed to check default database for user {user_id}: {default_e}")
        
        return episodes
        
    except Exception as e:
        logger.error(f"❌ Failed to fetch episodes for user {user_id}: {e}")
        # Return empty list instead of error to allow page to still load
        return []


@router.get('/graph/episodes/session/{session_id}', status_code=status.HTTP_200_OK, response_model=List[EpisodeResponse])
async def get_session_episodes(
    session_id: str,
    graphiti: ZepGraphitiDep,
    user_id: str = Query(..., description="User ID that owns the session"),
    limit: int = Query(100, description="Maximum number of episodes to return")
):
    """Get episodes for a specific session from the graph database"""
    
    try:
        logger.info(f"🔍 Fetching episodes for session: {session_id} (user: {user_id})")
        
        # Query for episodes with exact group_id match
        query = """
        MATCH (e:Episodic) 
        WHERE e.group_id = $group_id
        RETURN e.uuid as uuid, e.name as name, e.content as content, 
               e.source as source, e.source_description as source_description,
               e.created_at as created_at, e.updated_at as updated_at
        ORDER BY e.created_at DESC
        LIMIT $limit
        """
        
        group_id = f"{user_id}:{session_id}"
        result = await graphiti.driver.execute_query(
            query, 
            group_id=group_id, 
            limit=limit
        )
        
        episodes = []
        for record in result:
            try:
                episode = EpisodeResponse(
                    uuid=record.get('uuid', ''),
                    content=record.get('content', record.get('name', '')),
                    source=record.get('source', 'message'),
                    source_description=record.get('source_description'),
                    status="processed",
                    created_at=record.get('created_at'),
                    updated_at=record.get('updated_at'),
                    processed=True
                )
                episodes.append(episode)
            except Exception as e:
                logger.warning(f"Failed to parse episode record: {e}")
                continue
        
        logger.info(f"✅ Found {len(episodes)} episodes for session {session_id}")
        return episodes
        
    except Exception as e:
        logger.error(f"❌ Failed to fetch episodes for session {session_id}: {e}")
        return []


@router.get('/graph/episodes/{episode_uuid}/mentions', status_code=status.HTTP_200_OK, response_model=EpisodeMentionsResponse)
async def get_episode_mentions(
    episode_uuid: str,
    graphiti: ZepGraphitiDep
):
    """
    Get nodes and edges mentioned in a specific episode
    This endpoint matches Zep's official API: GET /api/v2/graph/episodes/{uuid}/mentions
    """
    
    try:
        logger.info(f"🔍 Fetching mentions for episode: {episode_uuid}")
        
        # Query for nodes and edges that reference this episode
        # In Graphiti, episodes are connected to nodes and edges through group_id relationships
        
        # First, get the episode to check its group_id
        episode_query = """
        MATCH (episode:Episodic {uuid: $episode_uuid})
        RETURN episode.group_id as group_id
        LIMIT 1
        """
        
        episode_result = await graphiti.driver.execute_query(episode_query, episode_uuid=episode_uuid)
        
        if not episode_result:
            logger.warning(f"Episode {episode_uuid} not found")
            return EpisodeMentionsResponse(nodes=[], edges=[])
        
        group_id = episode_result[0].get('group_id')
        if not group_id:
            logger.warning(f"Episode {episode_uuid} has no group_id")
            return EpisodeMentionsResponse(nodes=[], edges=[])
        
        # Get nodes that were extracted from this episode's group
        nodes_query = """
        MATCH (node:Entity)
        WHERE node.group_id = $group_id
        RETURN node.uuid as uuid, node.name as name, node.summary as summary,
               node.labels as labels, node.attributes as attributes,
               node.created_at as created_at, node.updated_at as updated_at
        LIMIT 100
        """
        
        # Get edges that were extracted from this episode's group
        edges_query = """
        MATCH (edge:EntityEdge)
        WHERE edge.group_id = $group_id
        RETURN edge.uuid as uuid, edge.source_node_uuid as source_node_uuid,
               edge.target_node_uuid as target_node_uuid, edge.fact as fact,
               edge.name as name, edge.episodes as episodes,
               edge.created_at as created_at, edge.updated_at as updated_at,
               edge.valid_at as valid_at, edge.expired_at as expired_at,
               edge.invalid_at as invalid_at, edge.attributes as attributes
        LIMIT 100
        """
        
        # Execute both queries
        nodes_result = await graphiti.driver.execute_query(nodes_query, group_id=group_id)
        edges_result = await graphiti.driver.execute_query(edges_query, group_id=group_id)
        
        # Process nodes
        nodes = []
        for record in nodes_result:
            try:
                node = EntityNode(
                    uuid=record.get('uuid', ''),
                    name=record.get('name', ''),
                    summary=record.get('summary', ''),
                    entity_type='Entity',  # Default type
                    labels=record.get('labels', []),
                    attributes=record.get('attributes', {}),
                    created_at=record.get('created_at'),
                    updated_at=record.get('updated_at'),
                    metadata=record.get('attributes', {}),
                    group_ids=[]
                )
                nodes.append(node)
            except Exception as e:
                logger.warning(f"Failed to parse node record: {e}")
                continue
        
        # Process edges
        edges = []
        for record in edges_result:
            try:
                edge = EntityEdge(
                    uuid=record.get('uuid', ''),
                    source_node_uuid=record.get('source_node_uuid', ''),
                    target_node_uuid=record.get('target_node_uuid', ''),
                    name=record.get('name', 'relates_to'),
                    fact=record.get('fact', ''),
                    predicate=record.get('name', 'relates_to'),
                    edge_type='relates_to',
                    attributes=record.get('attributes', {}),
                    episodes=record.get('episodes', []),
                    created_at=record.get('created_at'),
                    updated_at=record.get('updated_at'),
                    valid_at=record.get('valid_at'),
                    expires_at=record.get('expired_at'),
                    invalid_at=record.get('invalid_at'),
                    metadata=record.get('attributes', {}),
                    group_ids=[],
                    fact_rating=1.0
                )
                edges.append(edge)
            except Exception as e:
                logger.warning(f"Failed to parse edge record: {e}")
                continue
        
        logger.info(f"✅ Found {len(nodes)} nodes and {len(edges)} edges for episode {episode_uuid}")
        
        return EpisodeMentionsResponse(
            nodes=nodes,
            edges=edges
        )
        
    except Exception as e:
        logger.error(f"❌ Failed to fetch mentions for episode {episode_uuid}: {e}")
        # Return empty response instead of error to allow page to still load
        return EpisodeMentionsResponse(nodes=[], edges=[])