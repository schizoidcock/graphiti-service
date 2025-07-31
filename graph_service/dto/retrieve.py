from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field

from graph_service.dto.common import Message


class SearchQuery(BaseModel):
    group_ids: list[str] | None = Field(
        None, description='The group ids for the memories to search'
    )
    query: str
    max_facts: int = Field(default=10, description='The maximum number of facts to retrieve')


class FactResult(BaseModel):
    uuid: str
    name: str
    fact: str
    valid_at: datetime | None
    invalid_at: datetime | None
    created_at: datetime
    expired_at: datetime | None
    
    # Enhanced fields for better n8n integration
    score: float = Field(default=1.0, description='Relevance score for this fact')
    search_rank: int = Field(default=0, description='Rank in search results')
    relevance_score: float = Field(default=1.0, description='Calculated relevance score')
    metadata: dict = Field(default_factory=dict, description='Additional metadata for n8n workflows')

    class Config:
        json_encoders = {datetime: lambda v: v.astimezone(timezone.utc).isoformat()}


class SearchResults(BaseModel):
    facts: list[FactResult]
    context_summary: dict = Field(default_factory=dict, description='Contextual summary for enhanced n8n integration')


class GetMemoryRequest(BaseModel):
    group_id: str = Field(..., description='The group id of the memory to get')
    max_facts: int = Field(default=10, description='The maximum number of facts to retrieve')
    center_node_uuid: str | None = Field(
        ..., description='The uuid of the node to center the retrieval on'
    )
    messages: list[Message] = Field(
        ..., description='The messages to build the retrieval query from '
    )


class GetMemoryResponse(BaseModel):
    facts: list[FactResult] = Field(..., description='The facts that were retrieved from the graph')


class EntityExtractionRequest(BaseModel):
    text: str = Field(..., description='Text to extract entities from')
    group_id: str = Field(..., description='Group ID to associate entities with')


class EntityExtractionResponse(BaseModel):
    text_analyzed: str = Field(..., description='Preview of analyzed text') 
    entities_extracted: int = Field(..., description='Number of entities extracted')
    entities: list[dict] = Field(..., description='List of extracted entities')
    group_id: str = Field(..., description='Group ID entities were associated with')


class ContextSummaryResponse(BaseModel):
    group_id: str = Field(..., description='Group ID for the summary')
    summary: str = Field(..., description='Contextual summary of conversations')
    key_entities: list = Field(..., description='Key entities mentioned in conversations')
    topics: list[str] = Field(..., description='Main topics discussed')
    episodes_analyzed: int = Field(..., description='Number of episodes analyzed')


# Zep-compatible DTOs to match official API contract
class ZepFact(BaseModel):
    """Zep-compatible Fact model matching official Graphiti service API"""
    uuid: str = Field(..., description='UUID of the fact')
    name: str = Field(..., description='Name of the fact')
    fact: str = Field(..., description='The fact content')
    created_at: datetime = Field(..., description='When the fact was created')
    expired_at: Optional[datetime] = Field(None, description='When the fact expires')
    valid_at: Optional[datetime] = Field(None, description='When the fact becomes valid')
    invalid_at: Optional[datetime] = Field(None, description='When the fact becomes invalid')

    def extract_created_at(self) -> datetime:
        """Extract effective creation time (matches Zep's API)"""
        if self.valid_at is not None:
            return self.valid_at
        return self.created_at


class ZepMessage(BaseModel):
    """Zep-compatible Message model matching official Graphiti service API"""
    uuid: str = Field(..., description='UUID of the message')
    role: str = Field(..., description='Role of the sender (e.g., "user", "assistant")')
    role_type: Optional[str] = Field(None, description='Type of the role (e.g., "user", "system")')
    content: str = Field(..., description='Content of the message')


class PutMemoryRequest(BaseModel):
    """Zep-compatible PutMemory request matching official API"""
    group_id: str = Field(..., description='Group ID for namespace isolation')
    messages: list[ZepMessage] = Field(..., description='Messages to add to memory')


class SearchRequest(BaseModel):
    """Zep-compatible Search request matching official API"""
    group_ids: list[str] = Field(..., description='Group IDs to search within')
    text: str = Field(..., alias='query', description='Search query text')
    max_facts: Optional[int] = Field(None, description='Maximum number of facts to return')


class SearchResponse(BaseModel):
    """Zep-compatible Search response matching official API"""
    facts: list[ZepFact] = Field(..., description='Retrieved facts from search')


class AddNodeRequest(BaseModel):
    """Zep-compatible AddNode request matching official API"""
    group_id: str = Field(..., description='Group ID for namespace isolation')
    uuid: str = Field(..., description='UUID for the new node')
    name: str = Field(..., description='Name of the node')
    summary: str = Field(..., description='Summary/description of the node')
