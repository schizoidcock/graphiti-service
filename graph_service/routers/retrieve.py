from datetime import datetime, timezone

from fastapi import APIRouter, status, Request

from graph_service.dto import (
    GetMemoryRequest,
    GetMemoryResponse,
    Message,
    SearchQuery,
    SearchResults,
    EntityExtractionRequest,
    EntityExtractionResponse,
    ContextSummaryResponse,
)
from graph_service.config import ZepEnvDep
from graph_service.zep_graphiti import ZepGraphitiDep, get_fact_result_from_edge, extract_user_id_from_request, get_or_create_pooled_client, update_user_context_from_group_id

router = APIRouter()


@router.post('/search', status_code=status.HTTP_200_OK)
async def search(query: SearchQuery, settings: ZepEnvDep, request: Request):
    # Extract user context for proper database isolation
    user_id = None
    if query.group_ids:
        # Use first group_id to extract user context
        user_id = update_user_context_from_group_id(query.group_ids[0])
    else:
        # Fallback to request-based extraction
        user_id = extract_user_id_from_request(request)
        if not user_id:
            user_id = "default_user"
    
    graphiti = get_or_create_pooled_client(user_id, settings)
    # Enhanced search with contextual information using Graphiti's NLP capabilities
    processed_query = preprocess_search_query(query.query)
    
    # Use enhanced search with context
    enhanced_results = await graphiti.search_with_context(
        group_ids=query.group_ids or [],
        query=processed_query,
        num_results=query.max_facts,
        include_summary=True
    )
    
    # Process search results with enhanced metadata
    facts = []
    for i, edge in enumerate(enhanced_results["search_results"]):
        fact = get_fact_result_from_edge(edge)
        fact.search_rank = i + 1
        fact.relevance_score = calculate_relevance_score(edge, processed_query)
        
        # Add contextual metadata for n8n workflows
        fact.metadata = {
            "query_processed": enhanced_results["query_processed"],
            "total_results": enhanced_results["total_results"],
            "has_context": enhanced_results["context"] is not None
        }
        facts.append(fact)
    
    # Sort by relevance score
    facts.sort(key=lambda f: getattr(f, 'relevance_score', 0), reverse=True)
    
    # Enhanced response with context information
    response_data = {"facts": facts}
    
    # Add summary context if available (for n8n workflow context)
    if enhanced_results["context"]:
        response_data["context_summary"] = enhanced_results["context"]
    
    return SearchResults(**response_data)


def preprocess_search_query(query: str) -> str:
    """Preprocess search query for better results"""
    if not query or len(query.strip()) < 3:
        return query
    
    # Remove common stop words that don't add semantic value
    stop_words = {'the', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for', 'of'}
    words = query.lower().split()
    meaningful_words = [w for w in words if w not in stop_words and len(w) > 2]
    
    # If too many words removed, use original
    if len(meaningful_words) < len(words) * 0.3:
        return query
    
    return ' '.join(meaningful_words)


def calculate_relevance_score(edge, query: str) -> float:
    """Calculate relevance score based on multiple factors"""
    base_score = 1.0
    
    # Boost score if query terms appear in fact content
    if hasattr(edge, 'fact') and edge.fact:
        query_words = set(query.lower().split())
        fact_words = set(edge.fact.lower().split())
        overlap = len(query_words.intersection(fact_words))
        base_score += overlap * 0.1
    
    # Time decay: newer facts get higher score
    if hasattr(edge, 'created_at') and edge.created_at:
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        age_days = (now - edge.created_at).days
        time_factor = max(0.1, 1.0 - (age_days * 0.01))  # Gradual decay
        base_score *= time_factor
    
    return min(base_score, 2.0)  # Cap at 2.0


@router.get('/entity-edge/{uuid}', status_code=status.HTTP_200_OK)
async def get_entity_edge(uuid: str, settings: ZepEnvDep, request: Request):
    # Extract user context from request
    user_id = extract_user_id_from_request(request)
    if not user_id:
        user_id = "default_user"
    graphiti = get_or_create_pooled_client(user_id, settings)
    entity_edge = await graphiti.get_entity_edge(uuid)
    return get_fact_result_from_edge(entity_edge)


@router.get('/episodes/{group_id}', status_code=status.HTTP_200_OK)
async def get_episodes(group_id: str, last_n: int, settings: ZepEnvDep):
    # Extract user context from group_id
    user_id = update_user_context_from_group_id(group_id)
    graphiti = get_or_create_pooled_client(user_id, settings)
    episodes = await graphiti.retrieve_episodes(
        group_ids=[group_id], last_n=last_n, reference_time=datetime.now(timezone.utc)
    )
    return episodes


@router.post('/get-memory', status_code=status.HTTP_200_OK)
async def get_memory(
    request: GetMemoryRequest,
    settings: ZepEnvDep,
):
    # Extract user context from group_id
    user_id = update_user_context_from_group_id(request.group_id)
    graphiti = get_or_create_pooled_client(user_id, settings)
    # Enhanced query composition with better context preservation
    combined_query = compose_enhanced_query_from_messages(request.messages)
    
    # Use center node for better contextual results if provided
    center_node_uuid = getattr(request, 'center_node_uuid', None)
    
    # Get contextual summary for richer n8n integration
    context_summary = await graphiti.get_contextual_summary(
        group_id=request.group_id,
        max_episodes=10
    )
    
    result = await graphiti.search(
        group_ids=[request.group_id],
        query=combined_query,
        num_results=request.max_facts,
        center_node_uuid=center_node_uuid  # Enhanced with center node support
    )
    
    # Enhanced fact processing with relevance scoring and context
    facts = []
    for i, edge in enumerate(result):
        fact = get_fact_result_from_edge(edge)
        fact.score = 1.0 - (i * 0.1)  # Simple scoring system
        fact.search_rank = i + 1
        fact.relevance_score = calculate_relevance_score(edge, combined_query)
        
        # Add contextual metadata for n8n workflows
        fact.metadata = {
            "group_summary": context_summary["summary"],
            "key_entities": context_summary["key_entities"],
            "topics": context_summary["topics"],
            "conversation_context": len(request.messages)
        }
        facts.append(fact)
    
    return GetMemoryResponse(facts=facts)


@router.get('/context-summary/{group_id}', status_code=status.HTTP_200_OK, response_model=ContextSummaryResponse)
async def get_context_summary(group_id: str, settings: ZepEnvDep, max_episodes: int = 10):
    # Extract user context from group_id
    user_id = update_user_context_from_group_id(group_id)
    graphiti = get_or_create_pooled_client(user_id, settings)
    
    """New endpoint for getting conversation context summary using Graphiti's NLP"""
    summary = await graphiti.get_contextual_summary(group_id, max_episodes)
    return ContextSummaryResponse(
        group_id=group_id,
        summary=summary["summary"],
        key_entities=summary["key_entities"],
        topics=summary["topics"],
        episodes_analyzed=max_episodes
    )
    

@router.post('/extract-entities', status_code=status.HTTP_200_OK, response_model=EntityExtractionResponse)
async def extract_entities(
    request: EntityExtractionRequest,
    settings: ZepEnvDep
):
    # Extract user context from group_id
    user_id = update_user_context_from_group_id(request.group_id)
    graphiti = get_or_create_pooled_client(user_id, settings)
    
    """New endpoint for extracting entities from text using Graphiti's NLP"""
    entities = await graphiti.extract_entities_from_text(request.text, request.group_id)
    return EntityExtractionResponse(
        text_analyzed=request.text[:100] + "..." if len(request.text) > 100 else request.text,
        entities_extracted=len(entities),
        entities=entities,
        group_id=request.group_id
    )


def compose_enhanced_query_from_messages(messages: list[Message]):
    """Enhanced query composition with better context understanding"""
    if not messages:
        return ""
    
    # Prioritize recent messages and user intent
    recent_messages = messages[-3:] if len(messages) > 3 else messages
    user_messages = [msg for msg in recent_messages if msg.role == 'user']
    
    # Build contextual query
    query_parts = []
    
    # Add user intent (most recent user message)
    if user_messages:
        latest_user_msg = user_messages[-1]
        query_parts.append(f"Intent: {latest_user_msg.content}")
    
    # Add conversation context
    for message in recent_messages:
        role_prefix = {
            'user': 'User asked',
            'assistant': 'Assistant responded',
            'system': 'System noted'
        }.get(message.role, message.role)
        
        query_parts.append(f"{role_prefix}: {message.content}")
    
    return " | ".join(query_parts)
