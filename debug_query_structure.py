#!/usr/bin/env python3
"""
Debug script to understand the query structure issue
"""

# Original query from bug report:
ORIGINAL_QUERY = """
CALL db.idx.fulltext.queryNodes('Entity', $query)
YIELD node AS n, score
WHERE n:Entity AND n.group_id IN $group_ids
WITH n, score
LIMIT $limit

RETURN

n.uuid AS uuid,
n.name AS name,
n.group_id AS group_id,
n.created_at AS created_at,
n.summary AS summary,
labels(n) AS labels,
properties(n) AS attributes

ORDER BY score DESC
"""

# What our current code generates (simplified):
def generate_current_query():
    from graphiti_core.graph_queries import get_nodes_query
    from graphiti_core.driver.driver import GraphProvider
    from graphiti_core.models.nodes.node_db_queries import ENTITY_NODE_RETURN
    
    provider = GraphProvider.FALKORDB
    
    query = (
        get_nodes_query(provider, 'node_name_and_summary', '$query')
        + """
        YIELD node AS n, score
        WHERE n:Entity AND n.group_id IN $group_ids
        WITH n, score
        LIMIT $limit
        """
        + """
        RETURN
        """
        + ENTITY_NODE_RETURN
        + """
        ORDER BY score DESC
        """
    )
    return query

if __name__ == "__main__":
    print("=== ORIGINAL QUERY FROM BUG REPORT ===")
    print(ORIGINAL_QUERY.strip())
    print()
    
    print("=== CURRENT GENERATED QUERY ===")
    try:
        current = generate_current_query()
        print(current)
    except Exception as e:
        print(f"Error generating query: {e}")
        
    print()
    print("=== ANALYSIS ===")
    print("Looking at the FalkorDB documentation examples:")
    print("1. YIELD node AS m is correct syntax")
    print("2. ORDER BY after RETURN is correct syntax")
    print("3. The structure seems correct based on FalkorDB docs")
    print()
    print("Possible issues to investigate:")
    print("1. Check if get_nodes_query() generates correct CALL statement")
    print("2. Check if ENTITY_NODE_RETURN has proper field mapping") 
    print("3. Check if there are syntax issues in field selection")