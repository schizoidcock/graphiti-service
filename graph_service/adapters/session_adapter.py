"""
Response format adapters to convert between graph and session API formats
"""
import logging
from datetime import datetime, timezone
from typing import Dict, List, Any, Optional

logger = logging.getLogger(__name__)


class SessionMemoryAdapter:
    """Adapter to convert graph search results to session memory format"""
    
    @staticmethod
    def graph_to_session_memory(graph_response, session_id: str) -> Dict[str, Any]:
        """Convert graph search results to session memory format expected by zep-server"""
        facts = []
        
        # Convert edges to facts (primary source of structured information)
        for edge in getattr(graph_response, 'edges', []):
            fact = {
                "uuid": edge.uuid,
                "fact": edge.fact,
                "score": getattr(edge, 'fact_rating', 1.0),
                "created_at": edge.created_at.isoformat() if edge.created_at else None,
                "updated_at": edge.updated_at.isoformat() if edge.updated_at else None,
                "valid_at": getattr(edge, 'valid_at', None),
                "expires_at": getattr(edge, 'expires_at', None),
                "metadata": {
                    "source_node_uuid": edge.source_node_uuid,
                    "target_node_uuid": edge.target_node_uuid,
                    "predicate": getattr(edge, 'predicate', 'relates_to'),
                    "edge_type": getattr(edge, 'edge_type', 'relates_to'),
                    "type": "relationship",
                    **getattr(edge, 'metadata', {})
                },
                "search_rank": len(facts) + 1,
                "relevance_score": getattr(edge, 'fact_rating', 1.0)
            }
            facts.append(fact)
        
        # Convert episodes to facts (raw conversation data)
        for episode in getattr(graph_response, 'episodes', []):
            fact = {
                "uuid": episode.uuid,
                "fact": getattr(episode, 'content', ''),
                "score": 1.0,  # Episodes get neutral score
                "created_at": episode.created_at.isoformat() if episode.created_at else None,
                "updated_at": episode.updated_at.isoformat() if episode.updated_at else None,
                "metadata": {
                    "episode_type": getattr(episode, 'episode_type', 'text'),
                    "source": getattr(episode, 'source', 'unknown'),
                    "user_id": getattr(episode, 'user_id', None),
                    "session_id": getattr(episode, 'session_id', None),
                    "type": "episode",
                    **getattr(episode, 'metadata', {})
                },
                "search_rank": len(facts) + 1,
                "relevance_score": 1.0
            }
            facts.append(fact)
        
        # Convert nodes to facts (entity information)
        for node in getattr(graph_response, 'nodes', []):
            fact = {
                "uuid": node.uuid,
                "fact": f"{node.name}: {getattr(node, 'summary', '')}" if getattr(node, 'summary', '') else node.name,
                "score": 0.8,  # Nodes get slightly lower score than relationships
                "created_at": node.created_at.isoformat() if node.created_at else None,
                "updated_at": node.updated_at.isoformat() if node.updated_at else None,
                "metadata": {
                    "entity_type": getattr(node, 'entity_type', 'generic'),
                    "labels": getattr(node, 'labels', []),
                    "attributes": getattr(node, 'attributes', {}),
                    "type": "entity",
                    **getattr(node, 'metadata', {})
                },
                "search_rank": len(facts) + 1,
                "relevance_score": 0.8
            }
            facts.append(fact)
        
        return {
            "session_id": session_id,
            "memory": facts,
            "total_facts": len(facts),
            "search_metadata": {
                **getattr(graph_response, 'search_metadata', {}),
                "conversion_timestamp": datetime.now(timezone.utc).isoformat(),
                "adapter_version": "1.0"
            }
        }


class ZepCompatibilityAdapter:
    """Adapter to ensure responses match official Zep API format"""
    
    @staticmethod
    def format_session_response(session_data: Dict[str, Any]) -> Dict[str, Any]:
        """Format session data to match Zep API response structure"""
        return {
            "uuid": session_data.get("uuid"),
            "id": session_data.get("id"),
            "session_id": session_data.get("session_id"),
            "user_id": session_data.get("user_id"),
            "created_at": session_data.get("created_at").isoformat() if session_data.get("created_at") else None,
            "updated_at": session_data.get("updated_at").isoformat() if session_data.get("updated_at") else None,
            "metadata": session_data.get("metadata", {}),
            "summary": session_data.get("summary"),
            "fact_count": len(session_data.get("messages", [])),
            "token_count": sum(msg.get("token_count", 0) for msg in session_data.get("messages", []) if isinstance(msg, dict))
        }


# Convenience functions
def adapt_graph_to_session_memory(graph_response, session_id: str) -> Dict[str, Any]:
    """Convenience function to convert graph response to session memory format"""
    return SessionMemoryAdapter.graph_to_session_memory(graph_response, session_id)


def format_for_zep_api(data: Dict[str, Any], response_type: str = "session") -> Dict[str, Any]:
    """Convenience function to format any response for Zep API compatibility"""
    if response_type == "session":
        return ZepCompatibilityAdapter.format_session_response(data)
    else:
        return data