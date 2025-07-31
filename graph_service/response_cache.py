"""
Intelligent response caching system for falkordb-service
"""
import time
import hashlib
import json
from typing import Any, Dict, Optional, Tuple
import logging
from .performance_config import CACHE_TTL_SECONDS, CACHE_MAX_SIZE

logger = logging.getLogger(__name__)

class ResponseCache:
    """LRU cache for HTTP responses and expensive computations"""
    
    def __init__(self, max_size: int = CACHE_MAX_SIZE, ttl: int = CACHE_TTL_SECONDS):
        self.max_size = max_size
        self.ttl = ttl
        self._cache: Dict[str, Tuple[Any, float]] = {}
        self._access_order: list[str] = []
        
    def _generate_key(self, method: str, url: str, params: Optional[Dict] = None, data: Optional[Dict] = None) -> str:
        """Generate a cache key from request parameters"""
        key_data = {
            'method': method,
            'url': url,
            'params': params or {},
            'data': data or {}
        }
        key_string = json.dumps(key_data, sort_keys=True)
        return hashlib.md5(key_string.encode()).hexdigest()
    
    def get(self, method: str, url: str, params: Optional[Dict] = None, data: Optional[Dict] = None) -> Optional[Any]:
        """Get cached response"""
        key = self._generate_key(method, url, params, data)
        
        if key in self._cache:
            value, timestamp = self._cache[key]
            if time.time() - timestamp < self.ttl:
                # Move to end (LRU)
                self._access_order.remove(key)
                self._access_order.append(key)
                logger.debug(f"💾 Cache hit: {method} {url}")
                return value
            else:
                # Expired
                del self._cache[key]
                self._access_order.remove(key)
                logger.debug(f"🗑️ Cache expired: {method} {url}")
        
        return None
    
    def set(self, method: str, url: str, params: Optional[Dict] = None, data: Optional[Dict] = None, value: Any = None):
        """Set cached response"""
        key = self._generate_key(method, url, params, data)
        
        # Remove oldest if cache is full
        if len(self._cache) >= self.max_size and self._access_order:
            oldest_key = self._access_order.pop(0)
            if oldest_key in self._cache:
                del self._cache[oldest_key]
        
        # Add new entry
        self._cache[key] = (value, time.time())
        if key in self._access_order:
            self._access_order.remove(key)
        self._access_order.append(key)
        
        logger.debug(f"💾 Cache set: {method} {url}")
    
    def invalidate(self, pattern: str = None):
        """Invalidate cache entries matching pattern"""
        if pattern:
            keys_to_remove = [k for k in self._cache.keys() if pattern in k]
            for key in keys_to_remove:
                del self._cache[key]
                if key in self._access_order:
                    self._access_order.remove(key)
            logger.info(f"🗑️ Cache invalidated {len(keys_to_remove)} entries matching: {pattern}")
        else:
            self._cache.clear()
            self._access_order.clear()
            logger.info("🗑️ Cache completely invalidated")
    
    def get_stats(self) -> Dict[str, Any]:
        """Get cache statistics"""
        return {
            'size': len(self._cache),
            'max_size': self.max_size,
            'ttl': self.ttl,
            'hit_rate': getattr(self, '_hit_rate', 0.0)
        }

# Global singleton instance
response_cache = ResponseCache()