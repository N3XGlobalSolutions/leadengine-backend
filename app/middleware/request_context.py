"""Request context middleware for request ID tracking."""

import uuid
from typing import Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.core.logging_config import get_logger, request_id_var, tenant_id_var

logger = get_logger(__name__)


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Middleware to add request context for logging and tracking."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        """Process request and add context."""
        # Generate or extract request ID
        request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))

        # Set context variables for logging
        request_id_token = request_id_var.set(request_id)

        # Extract tenant ID from header if present
        tenant_id = request.headers.get("X-Tenant-Id")
        tenant_id_token = None
        if tenant_id:
            tenant_id_token = tenant_id_var.set(tenant_id)

        try:
            # Log request start
            logger.info(
                f"{request.method} {request.url.path}",
                extra={
                    "extra_data": {
                        "method": request.method,
                        "path": request.url.path,
                        "client_ip": self._get_client_ip(request),
                    }
                },
            )

            # Process request. NOTE: CORS is handled exclusively by CORSMiddleware
            # (the outermost middleware, configured with a strict origin whitelist).
            # This middleware must NOT set Access-Control-* headers — doing so
            # previously reflected any Origin with credentials and defeated the
            # whitelist.
            response = await call_next(request)

            # Log request completion
            logger.info(
                f"Completed {request.method} {request.url.path} - {response.status_code}",
                extra={
                    "extra_data": {
                        "status_code": response.status_code,
                    }
                },
            )

            # Add request ID to response headers (safe, non-CORS)
            response.headers["X-Request-ID"] = request_id

            return response

        except Exception as e:
            logger.exception(f"Request failed: {e}")
            raise

        finally:
            # Reset context variables
            request_id_var.reset(request_id_token)
            if tenant_id_token:
                tenant_id_var.reset(tenant_id_token)

    def _get_client_ip(self, request: Request) -> str:
        """Extract client IP from request, handling proxies."""
        # Check X-Forwarded-For header (for proxies/load balancers)
        forwarded_for = request.headers.get("X-Forwarded-For")
        if forwarded_for:
            # Get the first IP (original client)
            return forwarded_for.split(",")[0].strip()

        # Check X-Real-IP header
        real_ip = request.headers.get("X-Real-IP")
        if real_ip:
            return real_ip

        # Fall back to direct client
        if request.client:
            return request.client.host

        return "unknown"
