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

import logging
from datetime import datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from falkordb import Graph as FalkorGraph
    from falkordb.asyncio import FalkorDB
else:
    try:
        from falkordb import Graph as FalkorGraph
        from falkordb.asyncio import FalkorDB
    except ImportError:
        # If falkordb is not installed, raise an ImportError
        raise ImportError(
            'falkordb is required for FalkorDriver. '
            'Install it with: pip install graphiti-core[falkordb]'
        ) from None

from graphiti_core.driver.driver import GraphDriver, GraphDriverSession, GraphProvider

logger = logging.getLogger(__name__)


class FalkorDriverSession(GraphDriverSession):
    def __init__(self, graph: FalkorGraph):
        self.graph = graph

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        # No cleanup needed for Falkor, but method must exist
        pass

    async def close(self):
        # No explicit close needed for FalkorDB, but method must exist
        pass

    async def execute_write(self, func, *args, **kwargs):
        # Directly await the provided async function with `self` as the transaction/session
        return await func(self, *args, **kwargs)

    async def run(self, query: str | list, **kwargs: Any) -> Any:
        # FalkorDB does not support argument for Label Set, so it's converted into an array of queries
        if isinstance(query, list):
            for cypher, params in query:
                params = convert_datetimes_to_strings(params)
                await self.graph.query(str(cypher), params)  # type: ignore[reportUnknownArgumentType]
        else:
            params = dict(kwargs)
            params = convert_datetimes_to_strings(params)
            await self.graph.query(str(query), params)  # type: ignore[reportUnknownArgumentType]
        # Assuming `graph.query` is async (ideal); otherwise, wrap in executor
        return None


class FalkorDriver(GraphDriver):
    provider = GraphProvider.FALKORDB

    def __init__(
        self,
        host: str = 'localhost',
        port: int = 6379,
        username: str | None = None,
        password: str | None = None,
        falkor_db: FalkorDB | None = None,
        database: str = 'default_db',
    ):
        """
        Initialize the FalkorDB driver.

        FalkorDB is a multi-tenant graph database.
        To connect, provide the host and port.
        The default parameters assume a local (on-premises) FalkorDB instance.
        """
        super().__init__()

        self._database = database
        if falkor_db is not None:
            # If a FalkorDB instance is provided, use it directly
            self.client = falkor_db
        else:
            # Initialize FalkorDB client with Railway-compatible settings
            self.client = FalkorDB(host=host, port=port, username=username, password=password)
            
        # Try to configure Redis to avoid persistence issues
        self._configure_redis_for_railway()

        self.fulltext_syntax = '@'  # FalkorDB uses a redisearch-like syntax for fulltext queries see https://redis.io/docs/latest/develop/ai/search-and-query/query/full-text/

    def _configure_redis_for_railway(self):
        """Configure Redis settings to avoid persistence issues on Railway"""
        try:
            import asyncio
            # Try to configure Redis to disable persistence and avoid write blocking
            async def _config_redis():
                try:
                    # Get the underlying Redis connection
                    redis_client = self.client
                    config_success = False
                    
                    if hasattr(redis_client, 'connection') and hasattr(redis_client.connection, 'execute_command'):
                        # Try to disable RDB snapshots and write blocking
                        await redis_client.connection.execute_command('CONFIG', 'SET', 'save', '')
                        await redis_client.connection.execute_command('CONFIG', 'SET', 'stop-writes-on-bgsave-error', 'no')
                        await redis_client.connection.execute_command('CONFIG', 'SET', 'appendonly', 'no')
                        config_success = True
                    elif hasattr(redis_client, 'execute_command'):
                        # Alternative method if connection structure is different
                        await redis_client.execute_command('CONFIG', 'SET', 'save', '')
                        await redis_client.execute_command('CONFIG', 'SET', 'stop-writes-on-bgsave-error', 'no')
                        await redis_client.execute_command('CONFIG', 'SET', 'appendonly', 'no')
                        config_success = True
                    
                    if config_success:
                        logger.info("✅  Redis configured for Railway: persistence disabled, write blocking disabled")
                except Exception as config_err:
                    logger.warning(f"Could not configure Redis settings: {config_err}")
                    logger.info("Proceeding with default Redis configuration - may cause persistence errors")
            
            # Try to run the async config, but don't block if it fails
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    # If loop is already running, schedule it as a task
                    asyncio.create_task(_config_redis())
                else:
                    # If no loop is running, run it directly
                    loop.run_until_complete(_config_redis())
            except Exception:
                # If async doesn't work, just log and continue
                logger.info("Redis configuration will be applied during first query execution")
                
        except Exception as e:
            logger.warning(f"Failed to configure Redis for Railway: {e}")
            logger.info("Service will continue with default configuration")

    def _get_graph(self, graph_name: str | None) -> FalkorGraph:
        # FalkorDB requires a non-None database name for multi-tenant graphs; the default is "default_db"
        if graph_name is None:
            graph_name = self._database
        return self.client.select_graph(graph_name)

    async def execute_query(self, cypher_query_, **kwargs: Any):
        graph = self._get_graph(self._database)

        # Convert datetime objects to ISO strings (FalkorDB does not support datetime objects directly)
        params = convert_datetimes_to_strings(dict(kwargs))

        try:
            result = await graph.query(cypher_query_, params)  # type: ignore[reportUnknownArgumentType]
        except Exception as e:
            if 'already indexed' in str(e):
                # check if index already exists
                logger.info(f'Index already exists: {e}')
                return None
            
            # Check if this is a Redis persistence error
            if 'MISCONF' in str(e) and 'stop-writes-on-bgsave-error' in str(e):
                logger.warning(f"Redis persistence error detected: {e}")
                logger.info("Attempting to fix Redis configuration...")
                
                try:
                    # Try to fix Redis configuration dynamically
                    redis_client = self.client
                    if hasattr(redis_client, 'connection') and hasattr(redis_client.connection, 'execute_command'):
                        await redis_client.connection.execute_command('CONFIG', 'SET', 'save', '')
                        await redis_client.connection.execute_command('CONFIG', 'SET', 'stop-writes-on-bgsave-error', 'no')
                        await redis_client.connection.execute_command('CONFIG', 'SET', 'appendonly', 'no')
                        logger.info("✅ Redis configuration fixed dynamically")
                        
                        # Retry the query after fixing configuration
                        logger.info("Retrying query after Redis configuration fix...")
                        result = await graph.query(cypher_query_, params)  # type: ignore[reportUnknownArgumentType]
                    else:
                        raise e
                except Exception as fix_err:
                    logger.error(f"Failed to fix Redis configuration: {fix_err}")
                    logger.error(f'Original FalkorDB query error: {e}\n{cypher_query_}\n{params}')
                    raise e
            else:
                logger.error(f'Error executing FalkorDB query: {e}\n{cypher_query_}\n{params}')
                raise

        # Convert the result header to a list of strings
        header = [h[1] for h in result.header]

        # Convert FalkorDB's result format (list of lists) to the format expected by Graphiti (list of dicts)
        records = []
        for row in result.result_set:
            record = {}
            for i, field_name in enumerate(header):
                if i < len(row):
                    record[field_name] = row[i]
                else:
                    # If there are more fields in header than values in row, set to None
                    record[field_name] = None
            records.append(record)

        return records, header, None

    def session(self, database: str | None = None) -> GraphDriverSession:
        return FalkorDriverSession(self._get_graph(database))

    async def close(self) -> None:
        """Close the driver connection."""
        if hasattr(self.client, 'aclose'):
            await self.client.aclose()  # type: ignore[reportUnknownMemberType]
        elif hasattr(self.client.connection, 'aclose'):
            await self.client.connection.aclose()
        elif hasattr(self.client.connection, 'close'):
            await self.client.connection.close()

    async def delete_all_indexes(self) -> None:
        await self.execute_query(
            'CALL db.indexes() YIELD name DROP INDEX name',
        )

    def clone(self, database: str) -> 'GraphDriver':
        """
        Returns a shallow copy of this driver with a different default database.
        Reuses the same connection (e.g. FalkorDB, Neo4j).
        """
        cloned = FalkorDriver(falkor_db=self.client, database=database)

        return cloned


def convert_datetimes_to_strings(obj):
    if isinstance(obj, dict):
        return {k: convert_datetimes_to_strings(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [convert_datetimes_to_strings(item) for item in obj]
    elif isinstance(obj, tuple):
        return tuple(convert_datetimes_to_strings(item) for item in obj)
    elif isinstance(obj, datetime):
        return obj.isoformat()
    else:
        return obj
