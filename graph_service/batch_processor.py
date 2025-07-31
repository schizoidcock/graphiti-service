"""
Batch processor for optimizing falkordb-service operations
"""
import asyncio
import logging
from typing import List, Dict, Any, Optional, Callable
from dataclasses import dataclass
import time
from .performance_config import BATCH_SIZE, BATCH_TIMEOUT_SECONDS
from .response_cache import response_cache

logger = logging.getLogger(__name__)

@dataclass
class BatchItem:
    """Individual item in a batch"""
    data: Any
    future: asyncio.Future
    timestamp: float

class BatchProcessor:
    """Process multiple operations in batches for efficiency"""
    
    def __init__(self, batch_size: int = BATCH_SIZE, timeout: float = BATCH_TIMEOUT_SECONDS):
        self.batch_size = batch_size
        self.timeout = timeout
        self.current_batch: List[BatchItem] = []
        self.batch_lock = asyncio.Lock()
        self.processing_task: Optional[asyncio.Task] = None
        
    async def add_item(self, data: Any, processor_func: Callable) -> asyncio.Future:
        """Add item to batch for processing"""
        future = asyncio.Future()
        item = BatchItem(data=data, future=future, timestamp=time.time())
        
        async with self.batch_lock:
            self.current_batch.append(item)
            
            # Start processing if batch is full
            if len(self.current_batch) >= self.batch_size:
                await self._process_batch(processor_func)
            # Or start timeout task for partial batch
            elif not self.processing_task or self.processing_task.done():
                self.processing_task = asyncio.create_task(
                    self._timeout_processor(processor_func)
                )
        
        return future
    
    async def _timeout_processor(self, processor_func: Callable):
        """Process batch after timeout even if not full"""
        await asyncio.sleep(self.timeout)
        
        async with self.batch_lock:
            if self.current_batch:
                await self._process_batch(processor_func)
    
    async def _process_batch(self, processor_func: Callable):
        """Process the current batch"""
        if not self.current_batch:
            return
            
        batch_to_process = self.current_batch.copy()
        self.current_batch.clear()
        
        try:
            results = await processor_func([item.data for item in batch_to_process])
            
            # Complete futures with results
            for item, result in zip(batch_to_process, results):
                if not item.future.cancelled():
                    item.future.set_result(result)
                    
        except Exception as e:
            logger.error(f"Batch processing failed: {e}")
            # Fail all items in batch
            for item in batch_to_process:
                if not item.future.cancelled():
                    item.future.set_exception(e)

class MemoryBatchProcessor(BatchProcessor):
    """Specialized batch processor for memory operations"""
    
    def __init__(self):
        super().__init__(batch_size=5, timeout=0.5)  # Small batches, quick processing
    
    async def process_messages_batch(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Process multiple messages in a single graph operation"""
        logger.info(f"📦 Processing batch of {len(messages)} messages")
        
        # Group messages by user for efficient processing
        user_groups: Dict[str, List[Dict[str, Any]]] = {}
        for msg in messages:
            user_id = msg.get('group_id', 'default')
            if user_id not in user_groups:
                user_groups[user_id] = []
            user_groups[user_id].append(msg)
        
        results = []
        for user_id, user_messages in user_groups.items():
            try:
                # Cache check
                cache_key = f"messages_{user_id}_{hash(str(user_messages))}"
                cached = response_cache.get('POST', '/add-messages', data={'group_id': user_id, 'messages': user_messages})
                
                if cached:
                    results.extend(cached)
                    continue
                
                # Process messages for this user
                result = await self._process_user_messages(user_id, user_messages)
                results.extend(result)
                
                # Cache result
                response_cache.set('POST', '/add-messages', data={'group_id': user_id, 'messages': user_messages}, value=result)
                
            except Exception as e:
                logger.error(f"Failed to process messages for user {user_id}: {e}")
                # Return partial results
                continue
        
        return results
    
    async def _process_user_messages(self, user_id: str, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Process messages for a single user"""
        # This would integrate with the actual graph processing
        from .zep_graphiti import get_or_create_pooled_client
        
        client = await get_or_create_pooled_client(user_id)
        results = []
        
        # Process messages concurrently
        tasks = []
        for message in messages:
            task = asyncio.create_task(self._process_single_message(client, message))
            tasks.append(task)
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        return [r for r in results if not isinstance(r, Exception)]
    
    async def _process_single_message(self, client, message: Dict[str, Any]) -> Dict[str, Any]:
        """Process a single message"""
        try:
            # Use the existing client processing
            result = await client.add_episode(**message)
            return {
                'message_id': message.get('uuid'),
                'episode_uuid': result.episode.uuid if result.episode else None,
                'status': 'success'
            }
        except Exception as e:
            logger.error(f"Failed to process message {message.get('uuid')}: {e}")
            raise

# Global instances
memory_batch_processor = MemoryBatchProcessor()

async def process_messages_concurrently(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """High-level function to process messages concurrently"""
    return await memory_batch_processor.process_messages_batch(messages)

class GraphQueryOptimizer:
    """Optimize graph queries with caching and batching"""
    
    def __init__(self):
        self.query_cache = {}
        
    async def get_contextual_summary(self, user_id: str, max_episodes: int = 3) -> Dict[str, Any]:
        """Get contextual summary with caching"""
        cache_key = f"summary_{user_id}_{max_episodes}"
        
        # Check cache
        cached = response_cache.get('GET', f'/context/{user_id}', params={'max_episodes': max_episodes})
        if cached:
            return cached
        
        # Generate summary
        try:
            from .zep_graphiti import get_or_create_pooled_client
            client = await get_or_create_pooled_client(user_id)
            
            # Use efficient query patterns
            episodes = await client.get_episodes(limit=max_episodes)
            summary = await self._generate_summary_from_episodes(episodes)
            
            # Cache result
            response_cache.set('GET', f'/context/{user_id}', params={'max_episodes': max_episodes}, value=summary)
            
            return summary
            
        except Exception as e:
            logger.error(f"Failed to get contextual summary for {user_id}: {e}")
            return {'summary': '', 'key_entities': []}
    
    async def _generate_summary_from_episodes(self, episodes: List[Any]) -> Dict[str, Any]:
        """Generate summary from episodes"""
        if not episodes:
            return {'summary': '', 'key_entities': []}
        
        # Extract key information
        key_entities = set()
        summary_parts = []
        
        for episode in episodes[-3:]:  # Last 3 episodes
            if hasattr(episode, 'content') and episode.content:
                summary_parts.append(episode.content[:100])  # First 100 chars
                
                # Simple entity extraction (could be enhanced)
                content = episode.content.lower()
                for word in content.split():
                    if len(word) > 3 and word[0].isupper():
                        key_entities.add(word)
        
        return {
            'summary': ' '.join(summary_parts),
            'key_entities': list(key_entities)[:10]
        }

# Global optimizer instance
query_optimizer = GraphQueryOptimizer()