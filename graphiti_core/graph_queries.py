"""
Database query utilities for FalkorDB graph database backend.

This module provides query generation for FalkorDB,
supporting index creation, fulltext search, and bulk operations.
"""

from typing import cast

from typing_extensions import LiteralString

from graphiti_core.driver.falkordb import STOPWORDS

# Mapping from fulltext index names to FalkorDB node labels
INDEX_TO_LABEL_MAPPING = {
    'node_name_and_summary': 'Entity',
    'community_name': 'Community',
    'episode_content': 'Episodic',
    'edge_name_and_fact': 'RELATES_TO',
}


def get_range_indices() -> list[LiteralString]:
    return [
        # Entity node
        'CREATE INDEX FOR (n:Entity) ON (n.uuid, n.group_id, n.name, n.created_at)',
        # Episodic node
        'CREATE INDEX FOR (n:Episodic) ON (n.uuid, n.group_id, n.created_at, n.valid_at)',
        # Community node
        'CREATE INDEX FOR (n:Community) ON (n.uuid)',
        # Saga node
        'CREATE INDEX FOR (n:Saga) ON (n.uuid, n.group_id, n.name)',
        # RELATES_TO edge
        'CREATE INDEX FOR ()-[e:RELATES_TO]-() ON (e.uuid, e.group_id, e.name, e.created_at, e.expired_at, e.valid_at, e.invalid_at)',
        # MENTIONS edge
        'CREATE INDEX FOR ()-[e:MENTIONS]-() ON (e.uuid, e.group_id)',
        # HAS_MEMBER edge
        'CREATE INDEX FOR ()-[e:HAS_MEMBER]-() ON (e.uuid)',
        # HAS_EPISODE edge
        'CREATE INDEX FOR ()-[e:HAS_EPISODE]-() ON (e.uuid, e.group_id)',
        # NEXT_EPISODE edge
        'CREATE INDEX FOR ()-[e:NEXT_EPISODE]-() ON (e.uuid, e.group_id)',
    ]


def get_fulltext_indices() -> list[LiteralString]:
    # Convert to string representation for embedding in queries
    stopwords_str = str(STOPWORDS)

    # Use type: ignore to satisfy LiteralString requirement while maintaining single source of truth
    return cast(
        list[LiteralString],
        [
            f"""CALL db.idx.fulltext.createNodeIndex(
                                            {{
                                                label: 'Episodic',
                                                stopwords: {stopwords_str}
                                            }},
                                            'content', 'source', 'source_description', 'group_id'
                                            )""",
            f"""CALL db.idx.fulltext.createNodeIndex(
                                            {{
                                                label: 'Entity',
                                                stopwords: {stopwords_str}
                                            }},
                                            'name', 'summary', 'group_id'
                                            )""",
            f"""CALL db.idx.fulltext.createNodeIndex(
                                            {{
                                                label: 'Community',
                                                stopwords: {stopwords_str}
                                            }},
                                            'name', 'group_id'
                                            )""",
            """CREATE FULLTEXT INDEX FOR ()-[e:RELATES_TO]-() ON (e.name, e.fact, e.group_id)""",
        ],
    )


def get_nodes_query(name: str, query: str, limit: int) -> str:
    label = INDEX_TO_LABEL_MAPPING[name]
    return f"CALL db.idx.fulltext.queryNodes('{label}', {query})"


def get_vector_cosine_func_query(vec1: str, vec2: str) -> str:
    # FalkorDB cosine similarity using vec.cosineDistance
    return f'(2 - vec.cosineDistance({vec1}, vecf32({vec2})))/2'


def get_relationships_query(name: str, limit: int) -> str:
    label = INDEX_TO_LABEL_MAPPING[name]
    return f"CALL db.idx.fulltext.queryRelationships('{label}', $query)"
