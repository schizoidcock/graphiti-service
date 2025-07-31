"""
Optimized HTTP client with connection pooling and retry logic for falkordb-service
"""
import asyncio
import aiohttp
import json
from typing import Any, Dict, Optional
from contextlib import asynccontextmanager
import logging
from .performance_config import (
    HTTP_TIMEOUT, HTTP_CONNECT_TIMEOUT, HTTP_READ_TIMEOUT,
    MAX_RETRIES, RETRY_BACKOFF_FACTOR, CONNECTION_POOL_SIZE,
    CONNECTION_POOL_MAX_OVERFLOW, CONNECTION_POOL_TIMEOUT
)

logger = logging.getLogger(__name__)

class OptimizedHTTPClient:
    """HTTP client with connection pooling and retry logic"""
    
    def __init__(self):
        self._session: Optional[aiohttp.ClientSession] = None
        self._connector: Optional[aiohttp.TCPConnector] = None
    
    async def __aenter__(self):
        await self.start()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()
    
    async def start(self):
        """Initialize the HTTP client with connection pooling"""
        if self._session is None:
            self._connector = aiohttp.TCPConnector(
                limit=CONNECTION_POOL_SIZE,
                limit_per_host=CONNECTION_POOL_SIZE // 2,
                ttl_dns_cache=300,
                use_dns_cache=True,
                keepalive_timeout=30,
                force_close=False,
                enable_cleanup_closed=True
            )
            
            timeout = aiohttp.ClientTimeout(
                total=HTTP_TIMEOUT,
                connect=HTTP_CONNECT_TIMEOUT,
                sock_read=HTTP_READ_TIMEOUT
            )
            
            self._session = aiohttp.ClientSession(
                connector=self._connector,
                timeout=timeout,
                json_serialize=lambda x: json.dumps(x, ensure_ascii=False),
                headers={
                    'User-Agent': 'zep-falkordb-service/1.0',
                    'Accept': 'application/json',
                    'Content-Type': 'application/json'
                }
            )
            logger.info("🔧 HTTP client initialized with connection pooling")
    
    async def close(self):
        """Close the HTTP client"""
        if self._session:
            await self._session.close()
            self._session = None
        if self._connector:
            await self._connector.close()
            self._connector = None
    
    async def request(
        self,
        method: str,
        url: str,
        json_data: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        retry_count: int = MAX_RETRIES
    ) -> aiohttp.ClientResponse:
        """Make an HTTP request with retry logic"""
        if not self._session:
            await self.start()
        
        attempt = 0
        last_exception = None
        
        while attempt <= retry_count:
            try:
                async with self._session.request(
                    method=method,
                    url=url,
                    json=json_data,
                    headers=headers
                ) as response:
                    # Read response content to avoid connection leaks
                    await response.read()
                    return response
                    
            except asyncio.TimeoutError as e:
                last_exception = e
                logger.warning(f"⏱️ Request timeout to {url} (attempt {attempt + 1}/{retry_count + 1})")
            except aiohttp.ClientError as e:
                last_exception = e
                logger.warning(f"🌐 Network error to {url} (attempt {attempt + 1}/{retry_count + 1}): {e}")
            
            attempt += 1
            if attempt <= retry_count:
                backoff_time = RETRY_BACKOFF_FACTOR * (2 ** (attempt - 1))
                await asyncio.sleep(backoff_time)
        
        logger.error(f"❌ All retry attempts failed for {url}: {last_exception}")
        raise last_exception

# Global singleton instance
_http_client = OptimizedHTTPClient()

async def get_http_client() -> OptimizedHTTPClient:
    """Get the global HTTP client instance"""
    if not _http_client._session:
        await _http_client.start()
    return _http_client