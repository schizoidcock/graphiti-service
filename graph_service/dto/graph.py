from datetime import datetime
from typing import Dict, List, Optional, Any, Literal
from pydantic import BaseModel, Field, validator, model_validator
from graphiti_core.utils.datetime_utils import utc_now


class EntityNode(BaseModel):
    """Entity node representing extracted entities with summaries"""
    uuid: str = Field(..., description="Unique identifier for the entity")
    name: str = Field(..., description="Name of the entity")
    summary: str = Field(..., description="Summary description of the entity")
    entity_type: str = Field(default="generic", description="Type of entity")
    labels: List[str] = Field(default_factory=list, description="Node labels")
    attributes: Dict[str, Any] = Field(default_factory=dict, description="Node attributes")
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    group_ids: Optional[List[str]] = Field(default_factory=list, description="Associated group IDs")


class EntityEdge(BaseModel):
    """Entity edge representing relationships with semantic facts"""
    uuid: str = Field(..., description="Unique identifier for the edge")
    source_node_uuid: str = Field(..., description="UUID of source entity node")
    target_node_uuid: str = Field(..., description="UUID of target entity node")
    name: str = Field(default="", description="Name/title of the relationship")
    fact: str = Field(..., description="The semantic fact describing the relationship")
    predicate: str = Field(..., description="The relationship predicate")
    edge_type: str = Field(default="relates_to", description="Type of relationship")
    attributes: Dict[str, Any] = Field(default_factory=dict, description="Edge attributes")
    episodes: List[str] = Field(default_factory=list, description="Associated episode UUIDs")
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    valid_at: Optional[datetime] = Field(None, description="When the fact becomes valid")
    expires_at: Optional[datetime] = Field(None, description="Expiration time for temporal facts")
    invalid_at: Optional[datetime] = Field(None, description="When the fact becomes invalid")
    metadata: Dict[str, Any] = Field(default_factory=dict)
    group_ids: Optional[List[str]] = Field(default_factory=list, description="Associated group IDs")
    fact_rating: float = Field(default=1.0, description="Rating of fact importance")


class EpisodicNode(BaseModel):
    """Episodic node representing raw data from chat/graph.add"""
    uuid: str = Field(..., description="Unique identifier for the episode")
    name: str = Field(..., description="Name/title of the episode")
    content: str = Field(..., description="Raw content of the episode")
    episode_type: Literal["text", "message", "json"] = Field(default="text", description="Type of episode")
    source: str = Field(default="chat", description="Source of the episode (chat, graph.add, etc.)")
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    group_ids: Optional[List[str]] = Field(default_factory=list, description="Associated group IDs")
    user_id: Optional[str] = Field(None, description="Associated user ID")
    session_id: Optional[str] = Field(None, description="Associated session ID")


class GraphSearchRequest(BaseModel):
    """Request for searching the graph with Zep-compatible parameters"""
    query: str = Field(..., max_length=256, description="Search query (max 256 chars)")
    group_ids: List[str] = Field(default_factory=list, description="Group IDs to search within")
    user_id: Optional[str] = Field(None, description="User ID to search within")
    session_id: Optional[str] = Field(None, description="Session ID to search within")
    search_type: Literal["similarity", "mmr"] = Field(default="similarity", description="Search algorithm")
    reranker: Literal["RRF", "MMR", "Cross-Encoder", "Episode Mentions", "Node Distance"] = Field(
        default="RRF", description="Reranking algorithm"
    )
    scope: Literal["Edges", "Nodes", "Episodes"] = Field(default="Edges", description="Search scope")
    max_results: int = Field(default=10, le=50, description="Maximum results to return")
    filters: Dict[str, Any] = Field(default_factory=dict, description="Additional filters")


class GraphSearchResult(BaseModel):
    """Result from graph search operations"""
    uuid: str = Field(..., description="UUID of the result item")
    content: str = Field(..., description="Content/fact of the result")
    result_type: Literal["entity_node", "entity_edge", "episodic_node"] = Field(
        ..., description="Type of search result"
    )
    score: float = Field(..., description="Relevance score")
    rank: int = Field(..., description="Ranking position")
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(..., description="Creation timestamp")
    group_ids: List[str] = Field(default_factory=list)


class GraphSearchResponse(BaseModel):
    """Response containing search results and metadata - matches Zep's GraphSearchResults"""
    # Core Zep structure - separate collections for different graph elements
    edges: List[EntityEdge] = Field(default_factory=list, description="Entity edges/relationships")
    episodes: List[EpisodicNode] = Field(default_factory=list, description="Episodes")
    nodes: List[EntityNode] = Field(default_factory=list, description="Entity nodes")
    
    # Additional metadata for compatibility
    search_metadata: Dict[str, Any] = Field(default_factory=dict, description="Search execution metadata")


class ZepFactResult(BaseModel):
    """Zep-compatible fact result structure"""
    uuid: str = Field(..., description="Unique identifier")
    fact: str = Field(..., description="The fact content")
    created_at: datetime = Field(..., description="Creation timestamp")
    updated_at: datetime = Field(..., description="Last update timestamp")
    metadata: Dict[str, Any] = Field(default_factory=dict)
    score: Optional[float] = Field(None, description="Relevance score")
    search_rank: Optional[int] = Field(None, description="Search ranking")
    relevance_score: Optional[float] = Field(None, description="Enhanced relevance score")


class GraphAddRequest(BaseModel):
    """Request for adding data to the graph - Zep v2 compatible"""
    data: str = Field(..., max_length=10000, description="Data to add (max 10,000 chars)")
    data_type: Literal["text", "message", "json"] = Field(default="text", description="Type of data")
    group_id: Optional[str] = Field(None, description="Group ID for shared data")
    user_id: Optional[str] = Field(None, description="User ID for user-specific data")
    session_id: Optional[str] = Field(None, description="Associated session ID")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Additional metadata")
    created_at: Optional[str] = Field(None, description="Timestamp for when the data was created")
    source_description: Optional[str] = Field(None, max_length=500, description="Source description (max 500 chars)")
    
    @model_validator(mode='after')
    def validate_isolation(self):
        """Ensure either user_id OR group_id is provided (Zep v2 requirement)"""
        user_id = self.user_id
        group_id = self.group_id
        
        if not user_id and not group_id:
            raise ValueError("Either user_id OR group_id must be provided")
        if user_id and group_id:
            raise ValueError("Cannot specify both user_id AND group_id - use either one")
        return self


class GraphAddResponse(BaseModel):
    """Response from adding data to the graph"""
    episode_uuid: str = Field(..., description="UUID of created episode")
    entities_created: int = Field(..., description="Number of entities created")
    relationships_created: int = Field(..., description="Number of relationships created")
    processing_time_ms: float = Field(..., description="Processing time in milliseconds")
    success: bool = Field(default=True)
    message: str = Field(default="Data added successfully")


class NodeRelationshipsRequest(BaseModel):
    """Request for getting node relationships"""
    node_uuid: str = Field(..., description="UUID of the node")
    relationship_types: Optional[List[str]] = Field(None, description="Filter by relationship types")
    max_depth: int = Field(default=1, le=3, description="Maximum traversal depth")
    include_episodes: bool = Field(default=True, description="Include related episodes")


class NodeRelationshipsResponse(BaseModel):
    """Response containing node relationships"""
    center_node: EntityNode = Field(..., description="The center node")
    connected_nodes: List[EntityNode] = Field(..., description="Connected entity nodes")
    relationships: List[EntityEdge] = Field(..., description="Connecting relationships")
    related_episodes: List[EpisodicNode] = Field(..., description="Related episodes")
    depth_analyzed: int = Field(..., description="Actual depth analyzed")


class EpisodeMentionsResponse(BaseModel):
    """Response containing nodes and edges mentioned in an episode (Zep-compatible)"""
    nodes: List[EntityNode] = Field(default_factory=list, description="Entity nodes mentioned in the episode")
    edges: List[EntityEdge] = Field(default_factory=list, description="Entity edges/relationships in the episode")


class RawTriplet(BaseModel):
    """Raw triplet structure for graph visualization (Zep-compatible)"""
    sourceNode: EntityNode = Field(..., description="Source entity node")
    edge: EntityEdge = Field(..., description="Connecting relationship edge")
    targetNode: EntityNode = Field(..., description="Target entity node")