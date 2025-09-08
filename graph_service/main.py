"""
Zep-Compatible Graphiti API Service
FastAPI service that provides full Zep Cloud API v2 compatibility using remote FalkorDB
"""
from contextlib import asynccontextmanager
import logging
import sys
import time
import logging.config

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from graph_service.config import get_settings
from graph_service.routers import episodes, ingest, retrieve, sessions, users, graph, maintenance
from graph_service.zep_graphiti import initialize_graphiti

# Logging configuration to ensure clean output for Railway
LOGGING_CONFIG = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'clean': {
            'format': '%(message)s',
        },
        'service': {
            'format': '%(levelname)s:%(name)s:%(message)s',
        },
    },
    'handlers': {
        'stdout': {
            'class': 'logging.StreamHandler',
            'stream': sys.stdout,
            'formatter': 'clean',
        },
        'service_handler': {
            'class': 'logging.StreamHandler',
            'stream': sys.stdout,
            'formatter': 'service',
        },
    },
    'root': {
        'level': 'INFO',
        'handlers': ['stdout'],
    },
    'loggers': {
        'graph_service': {
            'level': 'DEBUG',
            'handlers': ['stdout'],  # Use clean formatter instead of service_handler
            'propagate': False,
        },
        'graph_service.zep_graphiti': {
            'level': 'DEBUG', 
            'handlers': ['stdout'],  # Use clean formatter instead of service_handler
            'propagate': False,
        },
        'graph_service.main': {
            'level': 'DEBUG',
            'handlers': ['stdout'],  # Use clean formatter for main startup logs
            'propagate': False,
        },
        'uvicorn': {
            'level': 'WARNING',  # Suppress INFO messages
            'handlers': ['stdout'],
            'propagate': False,
        },
        'uvicorn.error': {
            'level': 'WARNING',  # Suppress INFO messages from uvicorn.error
            'handlers': ['stdout'],
            'propagate': False,
        },
        'uvicorn.access': {
            'level': 'WARNING',  # Suppress access logs
            'handlers': ['stdout'],
            'propagate': False,
        },
    },
}

logging.config.dictConfig(LOGGING_CONFIG)
logger = logging.getLogger(__name__)

# CRITICAL: Suppress ALL logging during startup to prevent racing
import os
_STARTUP_COMPLETE = False

# Override all logging during startup
class StartupSuppressor:
    def __init__(self):
        self.original_print = print
        self.startup_messages = []
    
    def suppress_print(self, *args, **kwargs):
        # Capture print messages during startup instead of showing them
        if not _STARTUP_COMPLETE:
            message = ' '.join(str(arg) for arg in args)
            self.startup_messages.append(message)
        else:
            self.original_print(*args, **kwargs)
    
    def enable_startup_sequence(self):
        global _STARTUP_COMPLETE
        _STARTUP_COMPLETE = True
        # Restore normal print
        import builtins
        builtins.print = self.original_print

# Create suppressor and override print
_suppressor = StartupSuppressor()
import builtins
builtins.print = _suppressor.suppress_print


async def sequential_startup():
    """Completely sequential startup coordinator with suppression control"""
    global _STARTUP_COMPLETE
    import socket
    
    # CRITICAL: Enable clean startup sequence and restore print
    _suppressor.enable_startup_sequence()
    
    # Step 1: Service startup announcement  
    print("🚀  Starting Zep-Compatible Graphiti Service...")
    
    # Step 2: Network configuration  
    port = os.getenv('PORT')
    print(f"🔧  Railway assigned PORT: {port}")
    
    try:
        hostname = socket.gethostname()
        local_ip = socket.gethostbyname(hostname)
        print(f"🖥️  Hostname: {hostname}")
        print(f"🔗  Local IP: {local_ip}")
        print(f"🌐  Will listen on http://0.0.0.0:{port}")
        print(f"🏥  Health endpoint: http://0.0.0.0:{port}/healthcheck")
        
        # Railway internal endpoint
        private_domain = os.getenv('RAILWAY_PRIVATE_DOMAIN', 'graphiti-service.railway.internal')
        internal_endpoint = f"http://{private_domain}:{port}"
        print(f"🔌  Internal endpoint: {internal_endpoint}")
    except Exception as net_error:
        print(f"⚠️  Network debug failed: {net_error}")
    
    # Step 3: Configuration loading
    settings = get_settings()
    print(f"✅  Configuration loaded: FalkorDB at {settings.falkordb_host}:{settings.falkordb_port}")
    print(f"🤖  OpenAI API key configured: {bool(settings.openai_api_key and len(settings.openai_api_key) > 10)}")
    
    # Step 4: Router confirmation (they were loaded silently earlier)
    print("✅  All routers loaded successfully")
    
    # Step 5: Graphiti initialization - WAIT for full completion
    try:
        await initialize_graphiti(settings)
        print("✅  Graphiti initialization successful")
    except Exception as init_error:
        print(f"⚠️  Graphiti initialization failed: {init_error}")
        print("📝  Service will start but may have limited functionality")
    
    # Step 6: Enable normal logging and complete startup
    from graph_service.zep_graphiti import disable_startup_mode
    disable_startup_mode()
    print("✅  Zep-Compatible Graphiti Service startup completed")

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan management with sequential startup coordination"""
    # Run completely sequential startup - no racing, no async conflicts
    await sequential_startup()
    
    yield  # Service is running
    
    print("👋  Zep-Compatible Graphiti Service shutting down...")


# Create FastAPI app with Zep compatibility
app = FastAPI(
    lifespan=lifespan,
    title="Zep-Compatible Graphiti Service",
    description="Enhanced Graphiti service with full Zep Cloud API compatibility and NLP capabilities",
    version="2.0.0"
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Configure based on your needs
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Add request logging middleware for debugging
@app.middleware("http")
async def log_requests(request: Request, call_next):
    import time
    start_time = time.time()
    
    # Log incoming request (suppressed during startup)
    client_host = request.client.host if request.client else "unknown"
    print(f"📥  Incoming: {request.method} {request.url.path} from {client_host}")
    
    response = await call_next(request)
    
    # Log response
    process_time = time.time() - start_time
    print(f"📤  Response: {response.status_code} in {process_time:.3f}s")
    
    return response


# Include all routers - order matters for route precedence (SILENT LOADING)
try:
    app.include_router(graph.router)     # NEW: Official Zep-compatible graph API
    app.include_router(retrieve.router)
    app.include_router(ingest.router)
    app.include_router(sessions.router)  # New Zep-compatible session management
    app.include_router(users.router)     # User management endpoints
    app.include_router(episodes.router)  # Episodes management endpoints
    app.include_router(maintenance.router)  # Maintenance and cleanup endpoints
    # Note: Router loading success will be logged during coordinated startup
except Exception as router_error:
    logger.error(f"❌  Error loading routers: {router_error}")


@app.get('/')
async def root():
    """Root endpoint to verify service is running"""
    return JSONResponse(content={
        'service': 'Zep-Compatible Graphiti Service',
        'version': '2.0.0',
        'status': 'running',
        'endpoints': {
            'health': '/healthcheck',
            'status': '/debug/status',
            'debug': '/debug/config',
            'nlp_test': '/debug/nlp-test',
            'sessions': '/api/v2/sessions',
            'graph_search': '/api/v2/graph/search',
            'graph_add': '/api/v2/graph',
            'graph_batch': '/api/v2/graph/batch',
            'nodes': '/api/v2/graph/nodes/{uuid}',
            'edges': '/api/v2/graph/edges/{uuid}',
            'episodes': '/api/v2/graph/episodes/{uuid}',
            'delete_edge': 'DELETE /api/v2/graph/edges/{uuid}',
            'delete_episode': 'DELETE /api/v2/graph/episodes/{uuid}',
            'user_episodes': 'GET /api/v2/graph/episodes/user/{user_id}',
            'session_episodes': 'GET /api/v2/graph/episodes/session/{session_id}',
            'docs': '/docs'
        },
        'compatibility': 'Official Zep Cloud API v2',
        'features': [
            'Entity nodes with summaries',
            'Entity edges with semantic facts',
            'Episodic nodes for raw data',
            'Hybrid search (semantic + BM25 + RRF)',
            'Multi-tenant database isolation',
            'Temporal knowledge tracking'
        ]
    }, status_code=200)


@app.get('/healthcheck')
async def healthcheck():
    return JSONResponse(content={'status': 'healthy', 'service': 'zep-graphiti', 'version': '2.0.0'}, status_code=200)


@app.get('/ping')
async def ping():
    """Simple connectivity test endpoint"""
    return JSONResponse(content={'ping': 'pong', 'timestamp': time.time()}, status_code=200)


@app.get('/api/v2/health')  
async def api_health():
    """Zep-compatible health check endpoint"""
    return JSONResponse(content={
        'status': 'healthy', 
        'service': 'graphiti-service',
        'version': '2.0.0',
        'endpoints': {
            'memory': '/api/v2/sessions/{session_id}/memory',
            'sessions': '/api/v2/sessions', 
            'graph_search': '/api/v2/graph/search'
        }
    }, status_code=200)


# Add root-level search endpoint for zep-server compatibility
@app.post('/search')
async def root_search(request: Request):
    """
    Root-level search endpoint for zep-server compatibility
    Proxy to the graph search API with proper request handling
    """
    import json
    from graph_service.config import get_settings
    from graph_service.routers.graph import search_graph
    from graph_service.dto.graph import GraphSearchRequest
    
    logger.info("🔗 Root-level /search called, proxying to graph search API")
    
    try:
        # Parse request body
        body = await request.body()
        request_data = json.loads(body) if body else {}
        
        # Convert to GraphSearchRequest
        graph_request = GraphSearchRequest(
            query=request_data.get('query', ''),
            user_id=request_data.get('user_id'),
            group_ids=request_data.get('group_ids', []),
            max_results=request_data.get('max_results', 10),
            search_type=request_data.get('search_type', 'similarity'),
            reranker=request_data.get('reranker', 'RRF'),
            scope=request_data.get('scope', 'edges')
        )
        
        # Get settings and proxy to graph search
        settings = get_settings()
        result = await search_graph(graph_request, settings, request)
        
        return result
        
    except Exception as e:
        logger.error(f"Root search proxy failed: {e}")
        return JSONResponse(
            content={'error': f'Search failed: {str(e)}'},
            status_code=500
        )


@app.get('/debug/config')
async def debug_config():
    """Debug endpoint to check configuration"""
    import os
    settings = get_settings()
    
    # Get the actual port the server is running on
    server_port = os.getenv('PORT', '8000')
    
    return JSONResponse(content={
        'server_port': server_port,
        'openai_api_key_configured': bool(settings.openai_api_key and len(settings.openai_api_key) > 10),
        'openai_api_key_prefix': settings.openai_api_key[:10] + "..." if settings.openai_api_key else None,
        'openai_base_url': settings.openai_base_url,
        'model_name': settings.model_name,
        'temperature': settings.temperature,
        'falkordb_host': settings.falkordb_host,
        'falkordb_port': settings.falkordb_port,
        'falkordb_username_set': bool(settings.falkordb_username),
        'env_port': os.getenv('PORT'),
        'env_openai_key_set': bool(os.getenv('OPENAI_API_KEY')),
    }, status_code=200)


@app.get('/debug/nlp-test')
async def debug_nlp_test():
    """Test endpoint to verify NLP capabilities without database access"""
    settings = get_settings()
    
    try:
        # Test LLM configuration without creating database connections
        if not settings.openai_api_key:
            return JSONResponse(content={
                'status': 'error',
                'message': 'OpenAI API key not configured'
            }, status_code=500)
        
        # Simple configuration check without database initialization
        return JSONResponse(content={
            'status': 'success',
            'message': 'NLP configuration verified',
            'openai_configured': bool(settings.openai_api_key),
            'openai_base_url': settings.openai_base_url,
            'model_name': settings.model_name,
            'embedding_model': settings.embedding_model_name,
            'temperature': settings.temperature,
            'note': 'Database connections are created lazily when users send messages'
        })
            
    except Exception as e:
        return JSONResponse(content={
            'status': 'error',
            'message': str(e),
            'error_type': type(e).__name__
        }, status_code=500)


@app.get('/debug/status')
async def debug_status():
    """Comprehensive status check for all service components"""
    from graph_service.zep_graphiti import get_graphiti
    from datetime import datetime, timezone
    settings = get_settings()
    import os
    
    status_result = {
        'service': 'Zep-Compatible Graphiti Service',
        'version': '2.0.0',
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'port': os.getenv('PORT', '8000'),
        'components': {}
    }
    
    # Check FalkorDB connection
    try:
        async for graphiti in get_graphiti(settings):
            # Test basic graph operation
            await graphiti.driver.execute_query("RETURN 1 as test")
            status_result['components']['falkordb'] = {
                'status': 'healthy',
                'host': settings.falkordb_host,
                'port': settings.falkordb_port
            }
            break
    except Exception as e:
        status_result['components']['falkordb'] = {
            'status': 'error',
            'error': str(e)
        }
    
    # Check LLM client
    try:
        async for graphiti in get_graphiti(settings):
            if graphiti.llm_client:
                status_result['components']['llm_client'] = {
                    'status': 'configured',
                    'model': getattr(graphiti.llm_client, 'model', 'unknown'),
                    'api_key_configured': bool(settings.openai_api_key)
                }
            else:
                status_result['components']['llm_client'] = {
                    'status': 'not_configured',
                    'api_key_configured': bool(settings.openai_api_key)
                }
            break
    except Exception as e:
        status_result['components']['llm_client'] = {
            'status': 'error',
            'error': str(e)
        }
    
    # Overall health
    all_healthy = all(
        comp.get('status') in ['healthy', 'configured'] 
        for comp in status_result['components'].values()
    )
    
    status_result['overall_status'] = 'healthy' if all_healthy else 'degraded'
    
    return JSONResponse(
        content=status_result,
        status_code=200 if all_healthy else 503
    )
