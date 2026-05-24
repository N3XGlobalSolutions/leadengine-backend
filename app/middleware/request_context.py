"""Request context middleware for request ID tracking."""

import uuid
from typing import Callable

from app.services.tenant.tenant_service_dynamodb import tenant_service_dynamodb
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
        # Extract request origin
        origin = request.headers.get("origin")

        if origin:
            origin = origin.lower().rstrip("/")

        # Extract tenant ID from header if present
        tenant_id = request.headers.get("X-Tenant-ID")
        # Validate request origin
        if tenant_id and origin:

            tenant = tenant_service_dynamodb.get_tenant(tenant_id)

            allowed_origins = (
                tenant.config.allowed_origins
                if tenant and tenant.config
                else []
            )

            if tenant and origin not in allowed_origins:
                return Response(
                    content='{"detail":"Origin not allowed"}',
                    status_code=403,
                    media_type="application/json"
                )

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

            # Handle preflight requests
            if request.method == "OPTIONS":

                response = Response(status_code=200)

                if origin:
                    response.headers["Access-Control-Allow-Origin"] = origin

                response.headers["Access-Control-Allow-Headers"] = (
                    "Content-Type, Authorization, X-Tenant-ID, X-API-Key"
                )

                response.headers["Access-Control-Allow-Methods"] = (
                    "GET, POST, PUT, DELETE, OPTIONS"
                )

                response.headers["Access-Control-Allow-Credentials"] = "true"
                response.headers["Vary"] = "Origin"

                return response

            # Process request
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

            # Add request ID to response headers
            response.headers["X-Request-ID"] = request_id

            # Add CORS headers
            if origin:
                response.headers["Access-Control-Allow-Origin"] = origin

            response.headers["Access-Control-Allow-Headers"] = (
                "Content-Type, Authorization, X-Tenant-ID, X-API-Key"
            )

            response.headers["Access-Control-Allow-Methods"] = (
                "GET, POST, PUT, DELETE, OPTIONS"
            )

            response.headers["Access-Control-Allow-Credentials"] = "true"
            response.headers["Vary"] = "Origin"

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
