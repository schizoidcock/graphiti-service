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

EPISODIC_EDGE_SAVE = """
    MATCH (episode:Episodic {uuid: $episode_uuid})
    MATCH (node:Entity {uuid: $entity_uuid})
    MERGE (episode)-[e:MENTIONS {uuid: $uuid}]->(node)
    SET
        e.group_id = $group_id,
        e.created_at = $created_at
    RETURN e.uuid AS uuid
"""


def get_episodic_edge_save_bulk_query() -> str:
    return """
        UNWIND $episodic_edges AS edge
        MATCH (episode:Episodic {uuid: edge.source_node_uuid})
        MATCH (node:Entity {uuid: edge.target_node_uuid})
        MERGE (episode)-[e:MENTIONS {uuid: edge.uuid}]->(node)
        SET
            e.group_id = edge.group_id,
            e.created_at = edge.created_at
        RETURN e.uuid AS uuid
    """


EPISODIC_EDGE_RETURN = """
    e.uuid AS uuid,
    e.group_id AS group_id,
    n.uuid AS source_node_uuid,
    m.uuid AS target_node_uuid,
    e.created_at AS created_at
"""


def get_entity_edge_save_query() -> str:
    return """
        MATCH (source:Entity {uuid: $edge_data.source_uuid})
        MATCH (target:Entity {uuid: $edge_data.target_uuid})
        MERGE (source)-[e:RELATES_TO {uuid: $edge_data.uuid}]->(target)
        SET e = $edge_data
        SET e.fact_embedding = vecf32($edge_data.fact_embedding)
        RETURN e.uuid AS uuid
    """


def get_entity_edge_save_bulk_query() -> str:
    return """
        UNWIND $entity_edges AS edge
        MATCH (source:Entity {uuid: edge.source_node_uuid})
        MATCH (target:Entity {uuid: edge.target_node_uuid})
        MERGE (source)-[r:RELATES_TO {uuid: edge.uuid}]->(target)
        SET r = edge
        SET r.fact_embedding = vecf32(edge.fact_embedding)
        WITH r, edge
        RETURN edge.uuid AS uuid
    """


def get_entity_edge_return_query() -> str:
    # `fact_embedding` is not returned by default and must be manually loaded using `load_fact_embedding()`.
    return """
        e.uuid AS uuid,
        n.uuid AS source_node_uuid,
        m.uuid AS target_node_uuid,
        e.group_id AS group_id,
        e.created_at AS created_at,
        e.name AS name,
        e.fact AS fact,
        e.episodes AS episodes,
        e.expired_at AS expired_at,
        e.valid_at AS valid_at,
        e.invalid_at AS invalid_at,
        properties(e) AS attributes
    """


def get_community_edge_save_query() -> str:
    return """
        MATCH (community:Community {uuid: $community_uuid})
        MATCH (node {uuid: $entity_uuid})
        MERGE (community)-[e:HAS_MEMBER {uuid: $uuid}]->(node)
        SET e = {uuid: $uuid, group_id: $group_id, created_at: $created_at}
        RETURN e.uuid AS uuid
    """


COMMUNITY_EDGE_RETURN = """
    e.uuid AS uuid,
    e.group_id AS group_id,
    n.uuid AS source_node_uuid,
    m.uuid AS target_node_uuid,
    e.created_at AS created_at
"""


HAS_EPISODE_EDGE_SAVE = """
    MATCH (saga:Saga {uuid: $saga_uuid})
    MATCH (episode:Episodic {uuid: $episode_uuid})
    MERGE (saga)-[e:HAS_EPISODE {uuid: $uuid}]->(episode)
    SET
        e.group_id = $group_id,
        e.created_at = $created_at
    RETURN e.uuid AS uuid
"""

HAS_EPISODE_EDGE_RETURN = """
    e.uuid AS uuid,
    e.group_id AS group_id,
    n.uuid AS source_node_uuid,
    m.uuid AS target_node_uuid,
    e.created_at AS created_at
"""


NEXT_EPISODE_EDGE_SAVE = """
    MATCH (source_episode:Episodic {uuid: $source_episode_uuid})
    MATCH (target_episode:Episodic {uuid: $target_episode_uuid})
    MERGE (source_episode)-[e:NEXT_EPISODE {uuid: $uuid}]->(target_episode)
    SET
        e.group_id = $group_id,
        e.created_at = $created_at
    RETURN e.uuid AS uuid
"""

NEXT_EPISODE_EDGE_RETURN = """
    e.uuid AS uuid,
    e.group_id AS group_id,
    n.uuid AS source_node_uuid,
    m.uuid AS target_node_uuid,
    e.created_at AS created_at
"""
