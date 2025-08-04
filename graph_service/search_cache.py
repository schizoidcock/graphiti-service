"""
Simple in-memory search result cache for performance optimization
"""
import time
import hashlib
import logging
from typing import Any, Optional, Dict, Tuple

logger = logging.getLogger(__name__)


class SearchCache:
    """Simple in-memory cache for search results with TTL and size limits"""
    
    def __init__(self, max_size: int = 1000, ttl_seconds: int = 300):
        self.cache: Dict[str, Tuple[Any, float]] = {}
        self.max_size = max_size
        self.ttl = ttl_seconds
        self.hits = 0
        self.misses = 0
        self.last_cleanup = time.time()
        self.cleanup_interval = 60  # Clean up every minute
    
    def _generate_cache_key(self, session_id: str, query: str, top_k: int, search_type: str) -> str:
        """Generate a consistent cache key for search parameters"""
        key_data = f"{session_id}:{query}:{top_k}:{search_type}"
        return hashlib.md5(key_data.encode()).hexdigest()
    
    def _cleanup_expired(self):
        """Remove expired entries from cache"""
        current_time = time.time()
        
        # Only cleanup if interval has passed
        if current_time - self.last_cleanup < self.cleanup_interval:
            return
        
        expired_keys = []
        for key, (_, timestamp) in self.cache.items():
            if current_time - timestamp > self.ttl:
                expired_keys.append(key)
        
        for key in expired_keys:
            del self.cache[key]
        
        # If still over size limit, remove oldest entries
        if len(self.cache) > self.max_size:
            # Sort by timestamp and remove oldest
            sorted_items = sorted(self.cache.items(), key=lambda x: x[1][1])
            excess_count = len(self.cache) - self.max_size
            
            for i in range(excess_count):
                key_to_remove = sorted_items[i][0]
                del self.cache[key_to_remove]
        
        self.last_cleanup = current_time
        
        if expired_keys:
            logger.info(f"🧹 Cache cleanup: removed {len(expired_keys)} expired entries, cache size: {len(self.cache)}")
    
    def get(self, session_id: str, query: str, top_k: int, search_type: str) -> Optional[Any]:
        """Get cached search result if available and fresh"""
        cache_key = self._generate_cache_key(session_id, query, top_k, search_type)
        
        self._cleanup_expired()
        
        if cache_key in self.cache:
            result, timestamp = self.cache[cache_key]
            
            # Check if result is still fresh
            if time.time() - timestamp < self.ttl:
                self.hits += 1
                logger.debug(f"🎯 Cache HIT for session {session_id[:8]}: {query[:50]}...")
                return result
            else:
                # Remove expired entry
                del self.cache[cache_key]
        
        self.misses += 1
        logger.debug(f"❌ Cache MISS for session {session_id[:8]}: {query[:50]}...")
        return None
    
    def put(self, session_id: str, query: str, top_k: int, search_type: str, result: Any):
        """Cache search result with current timestamp"""
        cache_key = self._generate_cache_key(session_id, query, top_k, search_type)
        current_time = time.time()
        
        self.cache[cache_key] = (result, current_time)
        logger.debug(f"💾 Cached result for session {session_id[:8]}: {query[:50]}...")
    
    def get_stats(self) -> Dict[str, Any]:
        """Get cache statistics"""
        total_requests = self.hits + self.misses
        hit_rate = (self.hits / total_requests * 100) if total_requests > 0 else 0
        
        return {
            "cache_size": len(self.cache),
            "max_size": self.max_size,
            "ttl_seconds": self.ttl,
            "hits": self.hits,
            "misses": self.misses,
            "hit_rate_percent": round(hit_rate, 2),
            "total_requests": total_requests
        }
    
    def clear(self):
        """Clear all cached entries"""
        self.cache.clear()
        self.hits = 0
        self.misses = 0
        logger.info("🗑️ Search cache cleared")


# Global cache instance
search_cache = SearchCache(max_size=1000, ttl_seconds=300)  # 5 minute TTL