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

try:
    import boto3
    from opensearchpy import OpenSearch
    from opensearchpy.connection.http_urllib3 import Urllib3HttpConnection
    from opensearchpy_aws import Urllib3AWSV4SignerAuth
    _HAS_OPENSEARCH = True
except ImportError:
    boto3 = None
    OpenSearch = None
    Urllib3AWSV4SignerAuth = None
    Urllib3HttpConnection = None
    _HAS_OPENSEARCH = False

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
from graphiti_core.utils.datetime_utils import convert_datetimes_to_strings

logger = logging.getLogger(__name__)

STOPWORDS = [
    'a',
    'is',
    'the',
    'an',
    'and',
    'are',
    'as',
    'at',
    'be',
    'but',
    'by',
    'for',
    'if',
    'in',
    'into',
    'it',
    'no',
    'not',
    'of',
    'on',
    'or',
    'such',
    'that',
    'their',
    'then',
    'there',
    'these',
    'they',
    'this',
    'to',
    'was',
    'will',
    'with',
]


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
    aoss_client: None = None

    def __init__(
        self,
        host: str = 'localhost',
        port: int = 6379,
        username: str | None = None,
        password: str | None = None,
        falkor_db: FalkorDB | None = None,
        database: str = 'default_db',
        aoss_host: str | None = None,
        aoss_port: int | None = None,
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
            aoss_host: AWS OpenSearch Service host (optional)
            aoss_port: AWS OpenSearch Service port (optional)
        """
        super().__init__()

        self._database = database
        if falkor_db is not None:
            # If a FalkorDB instance is provided, use it directly
            self.client = falkor_db
        else:
            # Initialize FalkorDB client with Railway-compatible settings
            self.client = FalkorDB(host=host, port=port, username=username, password=password)
            
        # Configure Redis to avoid persistence issues on Railway
        self._configure_redis_for_railway()

        # Initialize OpenSearch client if configured
        self.aoss_client = None
        if aoss_host and aoss_port and boto3 is not None:
            try:
                session = boto3.Session()
                self.aoss_client = OpenSearch(  # type: ignore
                    hosts=[{'host': aoss_host, 'port': aoss_port}],
                    http_auth=Urllib3AWSV4SignerAuth(  # type: ignore
                        session.get_credentials(), session.region_name, 'aoss'
                    ),
                    use_ssl=True,
                    verify_certs=True,
                    connection_class=Urllib3HttpConnection,
                    pool_maxsize=20,
                )  # type: ignore
            except Exception as e:
                logger.warning(f'Failed to initialize OpenSearch client: {e}')
                self.aoss_client = None

        self.fulltext_syntax = '@'  # FalkorDB uses a redisearch-like syntax for fulltext queries see https://redis.io/docs/latest/develop/ai/search-and-query/query/full-text/

    def _configure_redis_for_railway(self):
        """Configure Redis settings for Railway with persistent volume"""
        try:
            import asyncio

            async def _config_redis():
                """Async Redis configuration for Railway environment"""
                try:
                    logger.info("🔧 Configuring Redis for Railway environment with persistent volume...")

                    # Redis commands to ensure persistence works with Railway volume
                    # FalkorDB graph operations don't replay correctly from AOF
                    # Use RDB snapshots only for reliable persistence
                    redis_commands = [
                        ('CONFIG', 'SET', 'appendonly', 'no'),  # Disable AOF - causes replay errors with graph operations
                        ('CONFIG', 'SET', 'save', '60 1 300 10 900 1'),  # Enable aggressive RDB snapshots
                        ('CONFIG', 'SET', 'stop-writes-on-bgsave-error', 'no'),  # Don't block writes on save errors
                    ]
                    
                    success_count = 0
                    for cmd in redis_commands:
                        try:
                            # Try different FalkorDB client interfaces for Redis commands
                            if hasattr(self.client, 'redis') and hasattr(self.client.redis, 'execute_command'):
                                # Use redis client directly if available
                                await self.client.redis.execute_command(*cmd)
                                success_count += 1
                            elif hasattr(self.client, 'execute_command'):
                                # Try FalkorDB's execute_command method
                                await self.client.execute_command(*cmd)
                                success_count += 1
                            elif hasattr(self.client, '_redis') and hasattr(self.client._redis, 'execute_command'):
                                # Try internal redis connection
                                await self.client._redis.execute_command(*cmd)
                                success_count += 1
                        except Exception as cmd_err:
                            logger.debug(f"Redis config command {cmd[1]} failed: {cmd_err}")
                            continue
                    
                    if success_count > 0:
                        logger.info(f"✅ Redis configured for Railway with persistent volume ({success_count}/3 settings applied)")
                        logger.info("💾 RDB persistence enabled - AOF disabled due to graph operation incompatibility")
                        logger.info("💾 Snapshots: 60s/1key, 300s/10keys, 900s/1key")
                    else:
                        logger.info("ℹ️ Using default Redis configuration")
                        logger.info("⚠️ Persistence configuration may not be optimal for FalkorDB")
                        
                except Exception as config_err:
                    logger.warning(f"Redis configuration error: {config_err}")
                    logger.info("Proceeding with default Redis configuration")
            
            # Handle async execution properly without blocking
            try:
                # Check if we're already in an async context
                loop = asyncio.get_running_loop()
                # If we're in an async context, schedule the task
                asyncio.create_task(_config_redis())
                logger.debug("Redis configuration scheduled as async task")
                
            except RuntimeError:
                # No event loop running, we can run it directly
                try:
                    asyncio.run(_config_redis())
                    logger.debug("Redis configuration completed synchronously")
                except Exception as run_err:
                    logger.warning(f"Could not run Redis configuration: {run_err}")
                    logger.info("Redis configuration will be attempted during first query")
                    
        except Exception as e:
            logger.warning(f"Redis configuration setup failed: {e}")
            logger.info("Service will continue with default Redis configuration")

    def _get_graph(self, graph_name: str | None) -> FalkorGraph:
        # FalkorDB requires a non-None database name for multi-tenant graphs; the default is "default_db"
        if graph_name is None:
            graph_name = self._database
        return self.client.select_graph(graph_name)

    async def execute_query(self, cypher_query_, **kwargs: Any):
        graph = self._get_graph(self._database)

        # Convert datetime objects to ISO strings (FalkorDB does not support datetime objects directly)
        params = convert_datetimes_to_strings(dict(kwargs))

        # DEBUG: Log parameter types to identify unary + string issues
        for key, value in params.items():
            if isinstance(value, str) and key in ['reference_time', 'valid_at', 'created_at']:
                logger.debug(f"FalkorDB parameter {key}: '{value}' (type: {type(value)})")

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
        result = await self.execute_query("CALL db.indexes()")
        if not result:
            return

        records, _, _ = result
        drop_tasks = []

        for record in records:
            label = record["label"]
            entity_type = record["entitytype"]

            for field_name, index_type in record["types"].items():
                if "RANGE" in index_type:
                    drop_tasks.append(
                        self.execute_query(f"DROP INDEX ON :{label}({field_name})")
                    )
                elif "FULLTEXT" in index_type:
                    if entity_type == "NODE":
                        drop_tasks.append(
                            self.execute_query(
                                f"DROP FULLTEXT INDEX FOR (n:{label}) ON (n.{field_name})"
                            )
                        )
                    elif entity_type == "RELATIONSHIP":
                        drop_tasks.append(
                            self.execute_query(
                                f"DROP FULLTEXT INDEX FOR ()-[e:{label}]-() ON (e.{field_name})"
                            )
                        )

        if drop_tasks:
            await asyncio.gather(*drop_tasks)

    def clone(self, database: str) -> 'GraphDriver':
        """
        Returns a shallow copy of this driver with a different default database.
        Reuses the same connection (e.g. FalkorDB).
        """
        cloned = FalkorDriver(falkor_db=self.client, database=database)

        return cloned

    def sanitize(self, query: str) -> str:
        """
        Replace FalkorDB special characters with whitespace.
        Based on FalkorDB tokenization rules: ,.<>{}[]"':;!@#$%^&*()-+=~
        """
        # FalkorDB separator characters that break text into tokens
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
            }
        )
        sanitized = query.translate(separator_map)
        # Clean up multiple spaces
        sanitized = ' '.join(sanitized.split())
        return sanitized

    def build_fulltext_query(
        self, query: str, group_ids: list[str] | None = None, max_query_length: int = 128
    ) -> str:
        """
        Build a fulltext query string for FalkorDB using RedisSearch syntax.
        FalkorDB uses RedisSearch-like syntax where:
        - Field queries use @ prefix: @field:value
        - Multiple values for same field: (@field:value1|value2)
        - Text search doesn't need @ prefix for content fields
        - AND is implicit with space: (@group_id:value) (text)
        - OR uses pipe within parentheses: (@group_id:value1|value2)
        """
        if group_ids is None or len(group_ids) == 0:
            group_filter = ''
        else:
            group_values = '|'.join(group_ids)
            group_filter = f'(@group_id:{group_values})'

        sanitized_query = self.sanitize(query)

        # Remove stopwords from the sanitized query
        query_words = sanitized_query.split()
        filtered_words = [word for word in query_words if word.lower() not in STOPWORDS]
        sanitized_query = ' | '.join(filtered_words)

        # If the query is too long return no query
        if len(sanitized_query.split(' ')) + len(group_ids or '') >= max_query_length:
            return ''

        full_query = group_filter + ' (' + sanitized_query + ')'

        return full_query