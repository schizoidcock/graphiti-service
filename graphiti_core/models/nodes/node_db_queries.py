"""
Copyright 2024, Zep Software, Inc.

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
"""

from typing import Any

from graphiti_core.driver.driver import GraphProvider 

def get_episode_node_save_query(provider: GraphProvider) -> str:
    match provider:
        case GraphProvider.FALKORDB:
            return """
                MERGE (n:Episodic {uuid: $uuid})
                SET n = {uuid: $uuid, name: $name, group_id: $group_id, source_description: $source_description, source: $source, content: $content,
                entity_edges: $entity_edges, created_at: $created_at, valid_at: $valid_at}
                RETURN n.uuid AS uuid
            """

def get_episode_node_save_bulk_query(provider: GraphProvider) -> str:
    match provider:
        case GraphProvider.FALKORDB:
            return """
                UNWIND $episodes AS episode
                MERGE (n:Episodic {uuid: episode.uuid})
                SET n = {uuid: episode.uuid, name: episode.name, group_id: episode.group_id, source_description: episode.source_description,
                    source: episode.source, content: episode.content,
                entity_edges: episode.entity_edges, created_at: episode.created_at, valid_at: episode.valid_at}
                RETURN n.uuid AS uuid
            """

EPISODIC_NODE_RETURN = """
    e.content AS content,
    e.created_at AS created_at,
    e.valid_at AS valid_at,
    e.uuid AS uuid,
    e.name AS name,
    e.group_id AS group_id,
    e.created_at AS created_at,
    e.source AS source,
    e.source_description AS source_description,
    e.content AS content,
    e.valid_at AS valid_at,
    e.entity_edges AS entity_edges
"""

def get_entity_node_save_query(provider: GraphProvider, labels: str) -> str:
    # FalkorDB-only implementation - handle vector embedding separately to avoid type conflicts
    # Don't use bulk assignment for entity_data since it includes name_embedding as raw list
    match provider:
        case GraphProvider.FALKORDB:
            return f"""
                MERGE (n:Entity {{uuid: $entity_data.uuid}})
                SET n:{labels}
                SET n.uuid = $entity_data.uuid,
                    n.name = $entity_data.name,
                    n.group_id = $entity_data.group_id,
                    n.labels = $entity_data.labels,
                    n.created_at = $entity_data.created_at,
                    n.valid_at = $entity_data.valid_at,
                    n.invalid_at = $entity_data.invalid_at,
                    n.summary = $entity_data.summary,
                    n.attributes = $entity_data.attributes
                WITH n
                WHERE $entity_data.name_embedding IS NOT NULL
                SET n.name_embedding = vecf32($entity_data.name_embedding)
                RETURN n.uuid AS uuid
            """


def get_entity_node_save_bulk_query(provider: GraphProvider, nodes: list[dict]) -> str | Any:
    # FalkorDB-only implementation - handle vector embedding separately to avoid type conflicts
    queries = []
    for node in nodes:
        for label in node['labels']:
            queries.append(
                (
                    f"""
                    UNWIND $nodes AS node
                    MERGE (n:Entity {{uuid: node.uuid}})
                    SET n:{label}
                    SET n.uuid = node.uuid,
                        n.name = node.name,
                        n.group_id = node.group_id,
                        n.labels = node.labels,
                        n.created_at = node.created_at,
                        n.valid_at = node.valid_at,
                        n.invalid_at = node.invalid_at,
                        n.summary = node.summary,
                        n.attributes = node.attributes
                    WITH n, node
                    WHERE node.name_embedding IS NOT NULL
                    SET n.name_embedding = vecf32(node.name_embedding)
                    RETURN n.uuid AS uuid
                    """,
                    {'nodes': [node]},
                )
            )
    return queries

def get_entity_node_return_query(provider: GraphProvider) -> str:
    # `name_embedding` is not returned by default and must be loaded manually using `load_name_embedding()`.
    if provider == GraphProvider.FALKORDB:
        return """
            n.uuid AS uuid,
            n.name AS name,
            n.group_id AS group_id,
            n.created_at AS created_at,
            n.summary AS summary,
            labels(n) AS labels,
            properties(n) AS attributes
        """

def get_community_node_save_query(provider: GraphProvider) -> str:
    # FalkorDB-only implementation - handle vector embedding separately to avoid type conflicts
    return """
        MERGE (n:Community {uuid: $uuid})
        SET n.uuid = $uuid,
            n.name = $name,
            n.group_id = $group_id,
            n.summary = $summary,
            n.created_at = $created_at
        WITH n
        WHERE $name_embedding IS NOT NULL
        SET n.name_embedding = vecf32($name_embedding)
        RETURN n.uuid AS uuid
    """


COMMUNITY_NODE_RETURN = """
    c.uuid AS uuid,
    c.name AS name,
    c.group_id AS group_id,
    c.created_at AS created_at,
    c.name_embedding AS name_embedding,
    c.summary AS summary
"""
