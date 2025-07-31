"""
Optimized async worker pool with concurrent processing for falkordb-service
"""
import asyncio
import logging
from typing import Callable, Any, Optional
from concurrent.futures import ThreadPoolExecutor
import time
from .performance_config import WORKER_POOL_SIZE, QUEUE_MAX_SIZE

logger = logging.getLogger(__name__)

class AsyncWorkerPool:
    """Enhanced async worker pool with concurrent processing"""
    
    def __init__(self, pool_size: int = WORKER_POOL_SIZE, max_queue_size: int = QUEUE_MAX_SIZE):
        self.pool_size = pool_size
        self.max_queue_size = max_queue_size
        self.work_queues = [asyncio.Queue(maxsize=max_queue_size // pool_size) for _ in range(pool_size)]
        self.workers = []
        self.executor = ThreadPoolExecutor(max_workers=pool_size * 2)
        self._running = False
        
    async def start(self):
        """Start the worker pool"""
        if self._running:
            return
            
        self._running = True
        for i in range(self.pool_size):
            worker = asyncio.create_task(self._worker(i))
            self.workers.append(worker)
        
        logger.info(f"🚀 Async worker pool started with {self.pool_size} workers")
    
    async def stop(self):
        """Stop the worker pool gracefully"""
        if not self._running:
            return
            
        self._running = False
        
        # Signal all workers to stop
        for queue in self.work_queues:
            for _ in range(self.pool_size):
                await queue.put(None)
        
        # Wait for all workers to complete
        if self.workers:
            await asyncio.gather(*self.workers, return_exceptions=True)
            self.workers.clear()
        
        self.executor.shutdown(wait=True)
        logger.info("🛑 Async worker pool stopped")
    
    async def _worker(self, worker_id: int):
        """Individual worker coroutine"""
        queue = self.work_queues[worker_id]
        
        while self._running:
            try:
                job = await queue.get()
                if job is None:  # Shutdown signal
                    break
                
                func, args, kwargs, future = job
                
                try:
                    if asyncio.iscoroutinefunction(func):
                        result = await func(*args, **kwargs)
                    else:
                        # Run sync function in thread pool
                        loop = asyncio.get_event_loop()
                        result = await loop.run_in_executor(self.executor, func, *args, **kwargs)
                    
                    if future and not future.cancelled():
                        future.set_result(result)
                        
                except Exception as e:
                    logger.error(f"Worker {worker_id} error: {e}")
                    if future and not future.cancelled():
                        future.set_exception(e)
                
                finally:
                    queue.task_done()
                    
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Worker {worker_id} unexpected error: {e}")
    
    async def submit(self, func: Callable, *args, **kwargs) -> asyncio.Future:
        """Submit work to the pool"""
        if not self._running:
            raise RuntimeError("Worker pool not started")
        
        future = asyncio.Future()
        job = (func, args, kwargs, future)
        
        # Use round-robin to distribute work
        queue_index = hash(str(args) + str(kwargs)) % self.pool_size
        queue = self.work_queues[queue_index]
        
        try:
            await queue.put(job)
            return future
        except asyncio.QueueFull:
            # Try next queue
            for i in range(self.pool_size):
                next_queue = self.work_queues[(queue_index + i + 1) % self.pool_size]
                try:
                    await next_queue.put(job)
                    return future
                except asyncio.QueueFull:
                    continue
            
            # All queues full, wait for first available
            await queue.put(job)
            return future
    
    def get_queue_stats(self) -> dict:
        """Get queue statistics"""
        return {
            'pool_size': self.pool_size,
            'queues': [
                {
                    'queue_id': i,
                    'size': q.qsize(),
                    'max_size': q.maxsize
                }
                for i, q in enumerate(self.work_queues)
            ]
        }

# Global singleton instance
worker_pool = AsyncWorkerPool()

async def get_worker_pool() -> AsyncWorkerPool:
    """Get the global worker pool instance"""
    return worker_pool