"""Main FastAPI application."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import get_settings
from app.core.logging_config import get_logger, setup_logging
from app.middleware.exception_handlers import setup_exception_handlers
from app.middleware.request_context import RequestContextMiddleware
from app.routes.v1 import auth, tenants, users, chat, documents, leads, analytics, audit

# Initialize logging first
setup_logging()
logger = get_logger(__name__)

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan events."""
    logger.info("Application starting up", extra={"extra_data": {"version": "2.0.0"}})

    # Ensure async Qdrant collection exists
    try:
        from app.services.ai.async_rag_service import async_rag_service

        await async_rag_service.ensure_collection()
        logger.info("Async Qdrant collection ensured")
    except Exception as e:
        logger.warning(f"Failed to ensure async collection: {e}")

    # Auto-start SQS worker in background thread for local development
    worker_thread = None
    if settings.USE_ASYNC_INGESTION and settings.AWS_SQS_QUEUE_URL:
        import threading
        from app.workers.run_worker_service import run_worker
        
        def worker_runner():
            try:
                run_worker()
            except Exception as e:
                logger.error(f"SQS Worker error: {e}")
        
        worker_thread = threading.Thread(target=worker_runner, daemon=True, name="SQSWorker")
        worker_thread.start()
        logger.info("🚀 SQS Worker auto-started in background thread")

    yield
    
    logger.info("Application shutting down")
    if worker_thread and worker_thread.is_alive():
        logger.info("SQS Worker thread will terminate with app")


app = FastAPI(
    title=settings.APP_NAME,
    description="Multi-tenant SaaS chatbot backend with RAG capabilities and JWT authentication",
    version="2.0.0",
    lifespan=lifespan,
)


# Setup custom exception handlers
setup_exception_handlers(app)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Add request context middleware (for request ID tracking)
app.add_middleware(RequestContextMiddleware)

# Include routers
app.include_router(auth.router)
app.include_router(users.router)
app.include_router(tenants.router)
app.include_router(documents.router)
app.include_router(chat.router)
app.include_router(leads.router)
app.include_router(analytics.router)
app.include_router(audit.router)



# Health check endpoints
@app.get("/", tags=["Health"])
def read_root():
    """Root endpoint."""
    return {
        "message": f"{settings.APP_NAME} is running",
        "version": "2.0.0",
        "features": [
            "JWT Authentication",
            "Multi-tenant Support",
            "RAG-based Chat",
            "Lead Management",
            "API Key Auth for Widget",
            "Rate Limiting",
            "Request ID Tracking",
            "Structured Logging",
        ],
    }


@app.get("/health", tags=["Health"])
async def health_check():
    """Health check endpoint with DB connectivity verification."""
    from app.core.dynamodb import get_dynamodb_client
    from botocore.exceptions import ClientError
    import time
    
    start_time = time.time()
    status = "healthy"
    details = {}
    
    # Check DynamoDB
    try:
        db_client = get_dynamodb_client()
        # Perform a lightweight list_tables
        db_client.list_tables(Limit=1)
        details["database"] = "connected"
    except Exception as e:
        status = "degraded"
        details["database"] = f"error: {str(e)}"
        
    latency_ms = int((time.time() - start_time) * 1000)

    return {
        "status": status,
        "latency_ms": latency_ms,
        "timestamp": time.time(),
        "details": details,
        "version": "2.0.0"
    }
