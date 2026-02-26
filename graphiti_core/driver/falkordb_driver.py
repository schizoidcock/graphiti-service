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

import asyncio
import logging
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
from graphiti_core.driver.falkordb import STOPWORDS
from graphiti_core.driver.falkordb.operations.community_edge_ops import (
    FalkorCommunityEdgeOperations,
)
from graphiti_core.driver.falkordb.operations.community_node_ops import (
    FalkorCommunityNodeOperations,
)
from graphiti_core.driver.falkordb.operations.entity_edge_ops import FalkorEntityEdgeOperations
from graphiti_core.driver.falkordb.operations.entity_node_ops import FalkorEntityNodeOperations
from graphiti_core.driver.falkordb.operations.episode_node_ops import FalkorEpisodeNodeOperations
from graphiti_core.driver.falkordb.operations.episodic_edge_ops import FalkorEpisodicEdgeOperations
from graphiti_core.driver.falkordb.operations.graph_ops import FalkorGraphMaintenanceOperations
from graphiti_core.driver.falkordb.operations.has_episode_edge_ops import (
    FalkorHasEpisodeEdgeOperations,
)
from graphiti_core.driver.falkordb.operations.next_episode_edge_ops import (
    FalkorNextEpisodeEdgeOperations,
)
from graphiti_core.driver.falkordb.operations.saga_node_ops import FalkorSagaNodeOperations
from graphiti_core.driver.falkordb.operations.search_ops import FalkorSearchOperations
from graphiti_core.driver.operations.community_edge_ops import CommunityEdgeOperations
from graphiti_core.driver.operations.community_node_ops import CommunityNodeOperations
from graphiti_core.driver.operations.entity_edge_ops import EntityEdgeOperations
from graphiti_core.driver.operations.entity_node_ops import EntityNodeOperations
from graphiti_core.driver.operations.episode_node_ops import EpisodeNodeOperations
from graphiti_core.driver.operations.episodic_edge_ops import EpisodicEdgeOperations
from graphiti_core.driver.operations.graph_ops import GraphMaintenanceOperations
from graphiti_core.driver.operations.has_episode_edge_ops import HasEpisodeEdgeOperations
from graphiti_core.driver.operations.next_episode_edge_ops import NextEpisodeEdgeOperations
from graphiti_core.driver.operations.saga_node_ops import SagaNodeOperations
from graphiti_core.driver.operations.search_ops import SearchOperations
from graphiti_core.graph_queries import get_fulltext_indices, get_range_indices
from graphiti_core.utils.datetime_utils import convert_datetimes_to_strings

logger = logging.getLogger(__name__)


class FalkorDriverSession(GraphDriverSession):
    provider = GraphProvider.FALKORDB

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
    default_group_id: str = '\\_'
    fulltext_syntax: str = '@'  # FalkorDB uses a redisearch-like syntax for fulltext queries
    aoss_client: None = None

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

        Args:
            host: FalkorDB host address
            port: FalkorDB port number
            username: FalkorDB username (optional)
            password: FalkorDB password (optional)
            falkor_db: Existing FalkorDB instance (optional)
            database: Database name
        """
        super().__init__()

        self._database = database
        if falkor_db is not None:
            # If a FalkorDB instance is provided, use it directly
            self.client = falkor_db
        else:
            # Initialize FalkorDB client
            self.client = FalkorDB(host=host, port=port, username=username, password=password)

        # Configure Redis to avoid persistence issues on Railway
        self._configure_redis_for_railway()

        # Instantiate FalkorDB operations
        self._entity_node_ops = FalkorEntityNodeOperations()
        self._episode_node_ops = FalkorEpisodeNodeOperations()
        self._community_node_ops = FalkorCommunityNodeOperations()
        self._saga_node_ops = FalkorSagaNodeOperations()
        self._entity_edge_ops = FalkorEntityEdgeOperations()
        self._episodic_edge_ops = FalkorEpisodicEdgeOperations()
        self._community_edge_ops = FalkorCommunityEdgeOperations()
        self._has_episode_edge_ops = FalkorHasEpisodeEdgeOperations()
        self._next_episode_edge_ops = FalkorNextEpisodeEdgeOperations()
        self._search_ops = FalkorSearchOperations()
        self._graph_ops = FalkorGraphMaintenanceOperations()

        # Schedule the indices and constraints to be built
        try:
            # Try to get the current event loop
            loop = asyncio.get_running_loop()
            # Schedule the build_indices_and_constraints to run
            loop.create_task(self.build_indices_and_constraints())
        except RuntimeError:
            # No event loop running, this will be handled later
            pass

    def _configure_redis_for_railway(self):
        """Configure Redis settings for Railway with persistent volume"""
        try:
            async def _config_redis():
                """Async Redis configuration for Railway environment"""
                try:
                    logger.info('Configuring Redis for Railway environment with persistent volume...')

                    # Redis commands to ensure persistence works with Railway volume
                    # FalkorDB graph operations don't replay correctly from AOF
                    # Use RDB snapshots only for reliable persistence
                    redis_commands = [
                        ('CONFIG', 'SET', 'appendonly', 'no'),  # Disable AOF - causes replay errors
                        ('CONFIG', 'SET', 'save', '60 1 300 10 900 1'),  # Enable aggressive RDB snapshots
                        ('CONFIG', 'SET', 'stop-writes-on-bgsave-error', 'no'),  # Don't block writes
                    ]

                    success_count = 0
                    for cmd in redis_commands:
                        try:
                            # Try different FalkorDB client interfaces for Redis commands
                            if hasattr(self.client, 'redis') and hasattr(self.client.redis, 'execute_command'):
                                await self.client.redis.execute_command(*cmd)
                                success_count += 1
                            elif hasattr(self.client, 'execute_command'):
                                await self.client.execute_command(*cmd)
                                success_count += 1
                            elif hasattr(self.client, '_redis') and hasattr(
                                self.client._redis, 'execute_command'
                            ):
                                await self.client._redis.execute_command(*cmd)
                                success_count += 1
                        except Exception as cmd_err:
                            logger.debug(f'Redis config command {cmd[1]} failed: {cmd_err}')
                            continue

                    if success_count > 0:
                        logger.info(
                            f'Redis configured for Railway ({success_count}/3 settings applied)'
                        )
                    else:
                        logger.info('Using default Redis configuration')

                except Exception as config_err:
                    logger.warning(f'Redis configuration error: {config_err}')
                    logger.info('Proceeding with default Redis configuration')

            # Handle async execution properly without blocking
            # IMPORTANT: Never use asyncio.run() here - it creates a new event loop
            # and corrupts connections when running under Hypercorn/Gunicorn
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(_config_redis())
                logger.debug('Redis configuration scheduled as async task')
            except RuntimeError:
                # No event loop running yet - skip configuration
                # It will be handled when the service starts
                logger.debug('No event loop available, skipping Redis configuration')

        except Exception as e:
            logger.warning(f'Redis configuration setup failed: {e}')
            logger.info('Service will continue with default Redis configuration')

    # --- Operations properties ---

    @property
    def entity_node_ops(self) -> EntityNodeOperations:
        return self._entity_node_ops

    @property
    def episode_node_ops(self) -> EpisodeNodeOperations:
        return self._episode_node_ops

    @property
    def community_node_ops(self) -> CommunityNodeOperations:
        return self._community_node_ops

    @property
    def saga_node_ops(self) -> SagaNodeOperations:
        return self._saga_node_ops

    @property
    def entity_edge_ops(self) -> EntityEdgeOperations:
        return self._entity_edge_ops

    @property
    def episodic_edge_ops(self) -> EpisodicEdgeOperations:
        return self._episodic_edge_ops

    @property
    def community_edge_ops(self) -> CommunityEdgeOperations:
        return self._community_edge_ops

    @property
    def has_episode_edge_ops(self) -> HasEpisodeEdgeOperations:
        return self._has_episode_edge_ops

    @property
    def next_episode_edge_ops(self) -> NextEpisodeEdgeOperations:
        return self._next_episode_edge_ops

    @property
    def search_ops(self) -> SearchOperations:
        return self._search_ops

    @property
    def graph_ops(self) -> GraphMaintenanceOperations:
        return self._graph_ops

    def _get_graph(self, graph_name: str | None) -> FalkorGraph:
        # FalkorDB requires a non-None database name for multi-tenant graphs
        if graph_name is None:
            graph_name = self._database
        return self.client.select_graph(graph_name)

    async def execute_query(self, cypher_query_, **kwargs: Any):
        graph = self._get_graph(self._database)

        # Convert datetime objects to ISO strings (FalkorDB does not support datetime objects)
        params = convert_datetimes_to_strings(dict(kwargs))

        try:
            result = await graph.query(cypher_query_, params)  # type: ignore[reportUnknownArgumentType]
        except Exception as e:
            if 'already indexed' in str(e):
                logger.info(f'Index already exists: {e}')
                return None

            # Check if this is a Redis persistence error
            if 'MISCONF' in str(e) and 'stop-writes-on-bgsave-error' in str(e):
                logger.warning(f'Redis persistence error detected: {e}')
                logger.info('Attempting to fix Redis configuration...')

                try:
                    redis_client = self.client
                    if hasattr(redis_client, 'connection') and hasattr(
                        redis_client.connection, 'execute_command'
                    ):
                        await redis_client.connection.execute_command('CONFIG', 'SET', 'save', '')
                        await redis_client.connection.execute_command(
                            'CONFIG', 'SET', 'stop-writes-on-bgsave-error', 'no'
                        )
                        await redis_client.connection.execute_command(
                            'CONFIG', 'SET', 'appendonly', 'no'
                        )
                        logger.info('Redis configuration fixed dynamically')

                        # Retry the query after fixing configuration
                        result = await graph.query(cypher_query_, params)  # type: ignore
                    else:
                        raise e
                except Exception as fix_err:
                    logger.error(f'Failed to fix Redis configuration: {fix_err}')
                    logger.error(f'Original FalkorDB query error: {e}\n{cypher_query_}\n{params}')
                    raise e
            else:
                logger.error(f'Error executing FalkorDB query: {e}\n{cypher_query_}\n{params}')
                raise

        # Convert the result header to a list of strings
        header = [h[1] for h in result.header]

        # Convert FalkorDB's result format (list of lists) to list of dicts
        records = []
        for row in result.result_set:
            record = {}
            for i, field_name in enumerate(header):
                if i < len(row):
                    record[field_name] = row[i]
                else:
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
        result = await self.execute_query('CALL db.indexes()')
        if not result:
            return

        records, _, _ = result
        drop_tasks = []

        for record in records:
            label = record['label']
            entity_type = record['entitytype']

            for field_name, index_type in record['types'].items():
                if 'RANGE' in index_type:
                    drop_tasks.append(self.execute_query(f'DROP INDEX ON :{label}({field_name})'))
                elif 'FULLTEXT' in index_type:
                    if entity_type == 'NODE':
                        drop_tasks.append(
                            self.execute_query(
                                f'DROP FULLTEXT INDEX FOR (n:{label}) ON (n.{field_name})'
                            )
                        )
                    elif entity_type == 'RELATIONSHIP':
                        drop_tasks.append(
                            self.execute_query(
                                f'DROP FULLTEXT INDEX FOR ()-[e:{label}]-() ON (e.{field_name})'
                            )
                        )

        if drop_tasks:
            await asyncio.gather(*drop_tasks)

    async def build_indices_and_constraints(self, delete_existing=False):
        if delete_existing:
            await self.delete_all_indexes()
        index_queries = get_range_indices() + get_fulltext_indices()
        for query in index_queries:
            await self.execute_query(query)

    def clone(self, database: str) -> 'GraphDriver':
        """
        Returns a shallow copy of this driver with a different default database.
        Reuses the same connection (e.g. FalkorDB).
        """
        if database == self._database:
            cloned = self
        elif database == self.default_group_id:
            cloned = FalkorDriver(falkor_db=self.client)
        else:
            cloned = FalkorDriver(falkor_db=self.client, database=database)

        return cloned

    async def health_check(self) -> None:
        """Check FalkorDB connectivity by running a simple query."""
        try:
            await self.execute_query('MATCH (n) RETURN 1 LIMIT 1')
            return None
        except Exception as e:
            print(f'FalkorDB health check failed: {e}')
            raise

    def sanitize(self, query: str) -> str:
        """
        Replace FalkorDB special characters with whitespace.
        Based on FalkorDB tokenization rules: ,.<>{}[]"':;!@#$%^&*()-+=~
        """
        separator_map = str.maketrans(
            {
                ',': ' ',
                '.': ' ',
                '<': ' ',
                '>': ' ',
                '{': ' ',
                '}': ' ',
                '[': ' ',
                ']': ' ',
                '"': ' ',
                "'": ' ',
                ':': ' ',
                ';': ' ',
                '!': ' ',
                '@': ' ',
                '#': ' ',
                '$': ' ',
                '%': ' ',
                '^': ' ',
                '&': ' ',
                '*': ' ',
                '(': ' ',
                ')': ' ',
                '-': ' ',
                '+': ' ',
                '=': ' ',
                '~': ' ',
                '?': ' ',
                '|': ' ',
                '/': ' ',
                '\\': ' ',
            }
        )
        sanitized = query.translate(separator_map)
        sanitized = ' '.join(sanitized.split())
        return sanitized

    def build_fulltext_query(
        self, query: str, group_ids: list[str] | None = None, max_query_length: int = 128
    ) -> str:
        """
        Build a fulltext query string for FalkorDB using RedisSearch syntax.
        """
        if group_ids is None or len(group_ids) == 0:
            group_filter = ''
        else:
            escaped_group_ids = [f'"{gid}"' for gid in group_ids]
            group_values = '|'.join(escaped_group_ids)
            group_filter = f'(@group_id:{group_values})'

        sanitized_query = self.sanitize(query)

        query_words = sanitized_query.split()
        filtered_words = [word for word in query_words if word and word.lower() not in STOPWORDS]
        sanitized_query = ' | '.join(filtered_words)

        if len(sanitized_query.split(' ')) + len(group_ids or '') >= max_query_length:
            return ''

        full_query = group_filter + ' (' + sanitized_query + ')'

        return full_query
