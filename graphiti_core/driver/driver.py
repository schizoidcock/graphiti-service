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
import copy
import logging
import os
from abc import ABC, abstractmethod
from collections.abc import Coroutine
from datetime import datetime
from enum import Enum
from typing import Any

from dotenv import load_dotenv

from graphiti_core.embedder.client import EMBEDDING_DIM

try:
    from opensearchpy import AsyncOpenSearch, helpers
    _HAS_OPENSEARCH = True
except ImportError:
    AsyncOpenSearch = None
    helpers = None
    _HAS_OPENSEARCH = False

logger = logging.getLogger(__name__)

DEFAULT_SIZE = 10

load_dotenv()

ENTITY_INDEX_NAME = os.environ.get('ENTITY_INDEX_NAME', 'entities')
EPISODE_INDEX_NAME = os.environ.get('EPISODE_INDEX_NAME', 'episodes')
COMMUNITY_INDEX_NAME = os.environ.get('COMMUNITY_INDEX_NAME', 'communities')
ENTITY_EDGE_INDEX_NAME = os.environ.get('ENTITY_EDGE_INDEX_NAME', 'entity_edges')


class GraphProvider(Enum):
    FALKORDB = 'falkordb'


aoss_indices = [
    {
        'index_name': ENTITY_INDEX_NAME,
        'body': {
            'settings': {'index': {'knn': True}},
            'mappings': {
                'properties': {
                    'uuid': {'type': 'keyword'},
                    'name': {'type': 'text'},
                    'summary': {'type': 'text'},
                    'group_id': {'type': 'keyword'},
                    'created_at': {'type': 'date', 'format': 'strict_date_optional_time_nanos'},
                    'name_embedding': {
                        'type': 'knn_vector',
                        'dimension': EMBEDDING_DIM,
                        'method': {
                            'name': 'hnsw',
                            'space_type': 'cosinesimil',
                            'engine': 'lucene',
                        },
                    },
                    'summary_embedding': {
                        'type': 'knn_vector',
                        'dimension': EMBEDDING_DIM,
                        'method': {
                            'name': 'hnsw',
                            'space_type': 'cosinesimil',
                            'engine': 'lucene',
                        },
                    },
                }
            },
        },
    },
    {
        'index_name': EPISODE_INDEX_NAME,
        'body': {
            'settings': {'index': {'knn': True}},
            'mappings': {
                'properties': {
                    'uuid': {'type': 'keyword'},
                    'name': {'type': 'text'},
                    'summary': {'type': 'text'},
                    'group_id': {'type': 'keyword'},
                    'created_at': {'type': 'date', 'format': 'strict_date_optional_time_nanos'},
                    'valid_at': {'type': 'date', 'format': 'strict_date_optional_time_nanos'},
                    'invalid_at': {'type': 'date', 'format': 'strict_date_optional_time_nanos'},
                    'content': {'type': 'text'},
                    'source_description': {'type': 'text'},
                    'reference_time': {'type': 'date', 'format': 'strict_date_optional_time_nanos'},
                    'content_embedding': {
                        'type': 'knn_vector',
                        'dimension': EMBEDDING_DIM,
                        'method': {
                            'name': 'hnsw',
                            'space_type': 'cosinesimil',
                            'engine': 'lucene',
                        },
                    },
                }
            },
        },
    },
    {
        'index_name': COMMUNITY_INDEX_NAME,
        'body': {
            'settings': {'index': {'knn': True}},
            'mappings': {
                'properties': {
                    'uuid': {'type': 'keyword'},
                    'name': {'type': 'text'},
                    'summary': {'type': 'text'},
                    'group_id': {'type': 'keyword'},
                    'created_at': {'type': 'date', 'format': 'strict_date_optional_time_nanos'},
                    'summary_embedding': {
                        'type': 'knn_vector',
                        'dimension': EMBEDDING_DIM,
                        'method': {
                            'name': 'hnsw',
                            'space_type': 'cosinesimil',
                            'engine': 'lucene',
                        },
                    },
                }
            },
        },
    },
    {
        'index_name': ENTITY_EDGE_INDEX_NAME,
        'body': {
            'mappings': {
                'properties': {
                    'uuid': {'type': 'keyword'},
                    'group_id': {'type': 'keyword'},
                    'name': {'type': 'keyword'},
                    'fact': {'type': 'text'},
                    'source_node_uuid': {'type': 'keyword'},
                    'target_node_uuid': {'type': 'keyword'},
                    'created_at': {'type': 'date', 'format': 'strict_date_optional_time_nanos'},
                    'expired_at': {'type': 'date', 'format': 'strict_date_optional_time_nanos'},
                    'valid_at': {'type': 'date', 'format': 'strict_date_optional_time_nanos'},
                    'invalid_at': {'type': 'date', 'format': 'strict_date_optional_time_nanos'},
                }
            }
        },
    },
]


class GraphDriverSession(ABC):
    async def __aenter__(self):
        return self

    @abstractmethod
    async def __aexit__(self, exc_type, exc, tb):
        # No cleanup needed for Falkor, but method must exist
        pass

    @abstractmethod
    async def run(self, query: str, **kwargs: Any) -> Any:
        raise NotImplementedError()

    @abstractmethod
    async def close(self):
        raise NotImplementedError()

    @abstractmethod
    async def execute_write(self, func, *args, **kwargs):
        raise NotImplementedError()


class GraphDriver(ABC):
    provider: GraphProvider
    fulltext_syntax: str = '@'  # FalkorDB uses '@' prefix for fulltext queries
    _database: str
    aoss_client: AsyncOpenSearch | None = None

    @abstractmethod
    def execute_query(self, cypher_query_: str, **kwargs: Any) -> Coroutine:
        raise NotImplementedError()

    @abstractmethod
    def session(self, database: str | None = None) -> GraphDriverSession:
        raise NotImplementedError()

    @abstractmethod
    def close(self):
        raise NotImplementedError()

    @abstractmethod
    def delete_all_indexes(self) -> Coroutine:
        raise NotImplementedError()

    def with_database(self, database: str) -> 'GraphDriver':
        """
        Returns a shallow copy of this driver with a different default database.
        Reuses the same connection (e.g. FalkorDB).
        """
        cloned = copy.copy(self)
        cloned._database = database

        return cloned

    async def setup_aoss_indices(self) -> None:
        """Setup OpenSearch indices with proper mappings."""
        if not self.aoss_client:
            return

        for index_config in aoss_indices:
            index_name = index_config['index_name']
            try:
                # Check if index exists
                exists = await self.aoss_client.indices.exists(index=index_name)
                if not exists:
                    # Create the index with mappings
                    await self.aoss_client.indices.create(
                        index=index_name,
                        body=index_config['body']
                    )
                    logger.info(f"Created OpenSearch index: {index_name}")
                else:
                    logger.info(f"OpenSearch index already exists: {index_name}")
            except Exception as e:
                logger.error(f"Error setting up OpenSearch index {index_name}: {e}")

    async def delete_aoss_indices(self) -> None:
        """Delete all OpenSearch indices."""
        if not self.aoss_client:
            return

        for index_config in aoss_indices:
            index_name = index_config['index_name']
            try:
                exists = await self.aoss_client.indices.exists(index=index_name)
                if exists:
                    await self.aoss_client.indices.delete(index=index_name)
                    logger.info(f"Deleted OpenSearch index: {index_name}")
            except Exception as e:
                logger.error(f"Error deleting OpenSearch index {index_name}: {e}")
