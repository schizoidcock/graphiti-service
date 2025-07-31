"""
Performance configuration for falkordb-service optimizations
"""
import os
from typing import Optional

# Connection pooling settings
CONNECTION_POOL_SIZE = int(os.getenv('FALKORDB_CONNECTION_POOL_SIZE', '50'))
CONNECTION_POOL_MAX_OVERFLOW = int(os.getenv('FALKORDB_CONNECTION_POOL_MAX_OVERFLOW', '10'))
CONNECTION_POOL_TIMEOUT = float(os.getenv('FALKORDB_CONNECTION_POOL_TIMEOUT', '30.0'))

# HTTP client settings
HTTP_TIMEOUT = float(os.getenv('FALKORDB_HTTP_TIMEOUT', '10.0'))
HTTP_CONNECT_TIMEOUT = float(os.getenv('FALKORDB_HTTP_CONNECT_TIMEOUT', '5.0'))
HTTP_READ_TIMEOUT = float(os.getenv('FALKORDB_HTTP_READ_TIMEOUT', '15.0'))

# Retry settings
MAX_RETRIES = int(os.getenv('FALKORDB_MAX_RETRIES', '3'))
RETRY_BACKOFF_FACTOR = float(os.getenv('FALKORDB_RETRY_BACKOFF_FACTOR', '0.3'))

# Caching settings
CACHE_TTL_SECONDS = int(os.getenv('FALKORDB_CACHE_TTL_SECONDS', '300'))
CACHE_MAX_SIZE = int(os.getenv('FALKORDB_CACHE_MAX_SIZE', '1000'))

# Batch processing
BATCH_SIZE = int(os.getenv('FALKORDB_BATCH_SIZE', '10'))
BATCH_TIMEOUT_SECONDS = float(os.getenv('FALKORDB_BATCH_TIMEOUT_SECONDS', '2.0'))

# Async processing
WORKER_POOL_SIZE = int(os.getenv('FALKORDB_WORKER_POOL_SIZE', '4'))
QUEUE_MAX_SIZE = int(os.getenv('FALKORDB_QUEUE_MAX_SIZE', '100'))