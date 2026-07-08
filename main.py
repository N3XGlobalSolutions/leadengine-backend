"""Main FastAPI application."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from slowapi.middleware import SlowAPIMiddleware

from app.core.config import get_settings
from app.core.logging_config import get_logger, setup_logging
from app.middleware.exception_handlers import setup_exception_handlers
from app.middleware.rate_limiter import limiter
from app.middleware.request_context import RequestContextMiddleware
from app.middleware.security_headers import SecurityHeadersMiddleware
from app.routes.v1 import auth, tenants, users, chat, documents, leads, analytics, audit, outbound, drafts, outbound_webhook, outbound_ai

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
        logger.info("SQS Worker auto-started in background thread")

    # Auto-start Outbound SQS worker in background thread
    outbound_worker_thread = None
    if getattr(settings, "OUTBOUND_SQS_QUEUE_URL", None):
        import threading
        from app.workers.outbound_worker import run_outbound_worker
        
        def outbound_worker_runner():
            try:
                run_outbound_worker()
            except Exception as e:
                logger.error(f"Outbound SQS Worker error: {e}")
                
        outbound_worker_thread = threading.Thread(target=outbound_worker_runner, daemon=True, name="OutboundWorker")
        outbound_worker_thread.start()
        logger.info("Outbound SQS Worker auto-started in background thread")

    yield

    logger.info("Application shutting down")
    if worker_thread and worker_thread.is_alive():
        logger.info("SQS Worker thread will terminate with app")
    if outbound_worker_thread and outbound_worker_thread.is_alive():
        logger.info("Outbound SQS Worker thread will terminate with app")

    # Gracefully close shared outbound HTTP clients (pooled connections).
    try:
        from app.services.outbound.whatsapp_sender import _http_client as _wa_client
        await _wa_client.aclose()
    except Exception:
        logger.warning("Failed to close WhatsApp HTTP client", exc_info=True)
    try:
        from app.services.outbound.telegram_sender import _http_client as _tg_client
        await _tg_client.aclose()
    except Exception:
        logger.warning("Failed to close Telegram HTTP client", exc_info=True)


app = FastAPI(
    title=settings.APP_NAME,
    description="Multi-tenant SaaS chatbot backend with RAG capabilities and JWT authentication",
    version="2.0.0",
    lifespan=lifespan,
    debug=settings.DEBUG,  # Explicitly controlled; never leaks tracebacks unless enabled
)


# Setup custom exception handlers
setup_exception_handlers(app)

# Register the rate limiter (Redis-backed in prod). SlowAPIMiddleware enforces
# @limiter.limit(...) decorators; RateLimitExceeded is handled in exception_handlers.
app.state.limiter = limiter
app.add_middleware(SlowAPIMiddleware)

# Middleware order note: Starlette runs the LAST-added middleware first (outermost).
# We add CORS last so it is the outermost layer and is the single source of truth
# for CORS headers (manual per-response CORS logic has been removed elsewhere).
app.add_middleware(SecurityHeadersMiddleware)

# Add request context middleware (for request ID tracking)
app.add_middleware(RequestContextMiddleware)

# CORS middleware (outermost) — strict whitelist, the only place CORS headers are set.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(auth.router)
app.include_router(users.router)
app.include_router(tenants.router)
app.include_router(documents.router)
app.include_router(chat.router)
app.include_router(leads.router)
app.include_router(analytics.router)
app.include_router(audit.router)
app.include_router(outbound.router)
app.include_router(outbound_webhook.router)
app.include_router(outbound_ai.router)
app.include_router(drafts.router)



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


@app.get("/livez", tags=["Health"])
async def liveness():
    """Liveness probe: is the process up? Never touches dependencies.

    Wire container/orchestrator liveness checks here so a transient DB blip
    does not cause healthy instances to be killed.
    """
    return {"status": "alive", "version": "2.0.0"}


async def _check_database() -> dict:
    """Cheap readiness check against the app's own table (DescribeTable).

    Uses DescribeTable on the configured table (least-privilege) instead of the
    account-wide ListTables, and runs the blocking boto3 call off the event loop.
    """
    import asyncio

    from app.core.dynamodb import get_dynamodb_client

    try:
        db_client = get_dynamodb_client()
        await asyncio.to_thread(
            db_client.describe_table, TableName=settings.DYNAMODB_TABLE_NAME
        )
        return {"database": "connected"}
    except Exception as e:  # noqa: BLE001 - readiness must degrade, not crash
        return {"database": f"error: {type(e).__name__}"}


@app.get("/readyz", tags=["Health"])
async def readiness():
    """Readiness probe: can we actually serve traffic (dependencies reachable)?

    Returns 503 when a required dependency is unavailable so the load balancer
    stops routing to this instance.
    """
    import time

    from fastapi.responses import JSONResponse

    start_time = time.time()
    details = await _check_database()
    ready = details.get("database") == "connected"
    latency_ms = int((time.time() - start_time) * 1000)

    body = {
        "status": "ready" if ready else "not_ready",
        "latency_ms": latency_ms,
        "details": details,
        "version": "2.0.0",
    }
    return JSONResponse(status_code=200 if ready else 503, content=body)


# Backwards-compatible alias for existing health checks.
@app.get("/health", tags=["Health"])
async def health_check():
    """Deprecated alias for /readyz (kept for backwards compatibility)."""
    return await readiness()
