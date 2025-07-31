"""
Zep-Compatible Graphiti API Service
FastAPI service that provides full Zep Cloud API v2 compatibility using remote FalkorDB
"""
from contextlib import asynccontextmanager
import logging
import sys
import logging.config

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from graph_service.config import get_settings
from graph_service.routers import episodes, ingest, retrieve, sessions, users, graph, maintenance
from graph_service.zep_graphiti import initialize_graphiti

# Logging configuration to ensure all logs go to stdout for Railway
LOGGING_CONFIG = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'default': {
            'format': '%(levelname)s:%(name)s:%(message)s',
        },
    },
    'handlers': {
        'stdout': {
            'class': 'logging.StreamHandler',
            'stream': sys.stdout,
            'formatter': 'default',
        },
    },
    'root': {
        'level': 'INFO',
        'handlers': ['stdout'],
    },
    'loggers': {
        'graph_service': {
            'level': 'INFO',
            'handlers': ['stdout'],
            'propagate': False,
        },
        'uvicorn': {
            'level': 'INFO',
            'handlers': ['stdout'],
            'propagate': False,
        },
        'uvicorn.error': {
            'level': 'INFO',
            'handlers': ['stdout'],
            'propagate': False,
        },
        'uvicorn.access': {
            'level': 'INFO',
            'handlers': ['stdout'],
            'propagate': False,
        },
    },
}

logging.config.dictConfig(LOGGING_CONFIG)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan management with Zep compatibility"""
    logger.info("🚀 Starting Zep-Compatible Graphiti Service...")
    
    try:
        # Load and validate configuration
        settings = get_settings()
        logger.info(f"✅ Configuration loaded: FalkorDB at {settings.falkordb_host}:{settings.falkordb_port}")
        logger.info(f"🤖 OpenAI API key configured: {bool(settings.openai_api_key and len(settings.openai_api_key) > 10)}")
        
        # Initialize Graphiti with enhanced error handling
        try:
            await initialize_graphiti(settings)
            logger.info("✅ Graphiti initialization successful")
        except Exception as init_error:
            logger.error(f"⚠️ Graphiti initialization failed: {init_error}")
            logger.info("📝 Service will start but may have limited functionality")
        
        logger.info("✅ Zep-Compatible Graphiti Service startup completed")
        yield
        
    except Exception as e:
        logger.error(f"❌ Startup error: {e}", exc_info=True)
        # Still yield to allow the app to start even if there are initialization issues
        yield
    
    logger.info("👋 Zep-Compatible Graphiti Service shutting down...")


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

# Add startup logging
@app.on_event("startup")
async def startup_event():
    import os
    logger.info("FastAPI application startup complete")
    logger.info(f"Server will bind to port {os.getenv('PORT', '8000')}")
    logger.info(f"OpenAI API key configured: {bool(os.getenv('OPENAI_API_KEY'))}")
    logger.info("🔍 FastAPI startup complete - all endpoints available")

# Include all routers - order matters for route precedence
try:
    app.include_router(graph.router)     # NEW: Official Zep-compatible graph API
    app.include_router(retrieve.router)
    app.include_router(ingest.router)
    app.include_router(sessions.router)  # New Zep-compatible session management
    app.include_router(users.router)     # User management endpoints
    app.include_router(episodes.router)  # Episodes management endpoints
    app.include_router(maintenance.router)  # Maintenance and cleanup endpoints
    logger.info("✅ All routers loaded successfully")
except Exception as router_error:
    logger.error(f"❌ Error loading routers: {router_error}", exc_info=True)


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
        'falkordb_host': settings.falkordb_host,
        'falkordb_port': settings.falkordb_port,
        'falkordb_username_set': bool(settings.falkordb_username),
        'env_port': os.getenv('PORT'),
        'env_openai_key_set': bool(os.getenv('OPENAI_API_KEY')),
    }, status_code=200)


@app.get('/debug/nlp-test')
async def debug_nlp_test():
    """Test endpoint to verify NLP capabilities"""
    from graph_service.zep_graphiti import get_graphiti
    settings = get_settings()
    
    try:
        # Test LLM client directly
        async for graphiti in get_graphiti(settings):
            if not graphiti.llm_client:
                return JSONResponse(content={
                    'status': 'error',
                    'message': 'LLM client not initialized'
                }, status_code=500)
            
            # Test a simple completion to verify connectivity
            if hasattr(graphiti.llm_client, 'generate_response'):
                test_response = await graphiti.llm_client.generate_response([
                    {"role": "system", "content": "You are a helpful assistant. Respond with exactly: {'test': 'success'}"},
                    {"role": "user", "content": "Test connection"}
                ])
            elif hasattr(graphiti.llm_client, 'complete_chat'):
                test_response = await graphiti.llm_client.complete_chat([
                    {"role": "system", "content": "You are a helpful assistant. Respond with exactly: {'test': 'success'}"},
                    {"role": "user", "content": "Test connection"}
                ])
            elif hasattr(graphiti.llm_client, 'chat'):
                test_response = await graphiti.llm_client.chat([
                    {"role": "system", "content": "You are a helpful assistant. Respond with exactly: {'test': 'success'}"},
                    {"role": "user", "content": "Test connection"}
                ])
            elif hasattr(graphiti.llm_client, 'complete'):
                test_response = await graphiti.llm_client.complete([
                    {"role": "system", "content": "You are a helpful assistant. Respond with exactly: {'test': 'success'}"},
                    {"role": "user", "content": "Test connection"}
                ])
            else:
                return JSONResponse(content={
                    'status': 'error',
                    'message': 'Unknown LLM client methods',
                    'available_methods': [method for method in dir(graphiti.llm_client) if not method.startswith('_')]
                }, status_code=500)
            
            # Test entity extraction with token-optimized content
            test_entities = await graphiti.extract_entities_from_text(
                "John ordered coffee from the local cafe and talked to Sarah about the new project.",
                "test-group"
            )
            
            # Test contextual summary with small content
            test_summary = await graphiti.get_contextual_summary("test-group", max_episodes=3)
            
            return JSONResponse(content={
                'status': 'success',
                'llm_client_available': True,
                'test_response': test_response[:100] + "..." if len(test_response) > 100 else test_response,
                'model': graphiti.llm_client.model if hasattr(graphiti.llm_client, 'model') else 'unknown',
                'entity_extraction_test': {
                    'entities_found': len(test_entities),
                    'sample_entities': test_entities[:3] if test_entities else []
                },
                'summary_test': {
                    'summary_generated': bool(test_summary and test_summary.get('summary')),
                    'summary_preview': test_summary.get('summary', '')[:100] + "..." if test_summary and test_summary.get('summary') else 'No summary'
                }
            }, status_code=200)
            
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
