"""Global exception handlers for consistent error responses."""

import traceback
from typing import Any, Optional

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from slowapi.errors import RateLimitExceeded

from app.core.logging_config import get_logger, request_id_var
from app.utils.exceptions import (
    AuthenticationError,
    AuthorizationError,
    ResourceNotFoundError,
    TenantAccessError,
)

logger = get_logger(__name__)


def create_error_response(
    status_code: int,
    error_type: str,
    message: str,
    details: Any = None,
    request: Optional[Request] = None,
) -> JSONResponse:
    """Create a consistent error response with CORS headers."""
    from app.core.config import get_settings
    
    request_id = request_id_var.get()

    response_body = {
        "error": {
            "type": error_type,
            "message": message,
            "request_id": request_id,
        }
    }

    if details:
        response_body["error"]["details"] = details

    response = JSONResponse(
        status_code=status_code,
        content=response_body,
    )
    
    # Add CORS headers to error responses (critical for Lambda/API Gateway)
    settings = get_settings()
    origin = None
    if request:
        origin = request.headers.get("origin")
    
    # Determine allowed origin
    allowed_origin = "*"
    if origin and settings.CORS_ORIGINS:
        origins_list = [o.strip() for o in settings.CORS_ORIGINS]
        if origin in origins_list:
            allowed_origin = origin
    
    # Set CORS headers
    response.headers["Access-Control-Allow-Origin"] = allowed_origin
    response.headers["Access-Control-Allow-Credentials"] = "true"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization, X-Tenant-ID, Accept, X-Api-Key"
    response.headers["Access-Control-Max-Age"] = "3600"
    
    return response


async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """Handle Pydantic validation errors."""
    logger.warning(
        f"Validation error: {exc.errors()}",
        extra={"extra_data": {"errors": exc.errors()}},
    )

    # Format validation errors for client
    details = []
    for error in exc.errors():
        details.append(
            {
                "field": ".".join(str(loc) for loc in error["loc"]),
                "message": error["msg"],
                "type": error["type"],
            }
        )

    return create_error_response(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        error_type="validation_error",
        message="Request validation failed",
        details=details,
        request=request,
    )


async def authentication_exception_handler(
    request: Request, exc: AuthenticationError
) -> JSONResponse:
    """Handle authentication errors."""
    logger.warning(f"Authentication failed: {exc}")

    return create_error_response(
        status_code=status.HTTP_401_UNAUTHORIZED,
        error_type="authentication_error",
        message=str(exc),
        request=request,
    )


async def authorization_exception_handler(
    request: Request, exc: AuthorizationError
) -> JSONResponse:
    """Handle authorization errors."""
    logger.warning(f"Authorization failed: {exc}")

    return create_error_response(
        status_code=status.HTTP_403_FORBIDDEN,
        error_type="authorization_error",
        message=str(exc),
        request=request,
    )


async def tenant_access_exception_handler(
    request: Request, exc: TenantAccessError
) -> JSONResponse:
    """Handle tenant access errors."""
    logger.warning(f"Tenant access denied: {exc}")

    return create_error_response(
        status_code=status.HTTP_403_FORBIDDEN,
        error_type="tenant_access_error",
        message=str(exc),
        request=request,
    )


async def not_found_exception_handler(
    request: Request, exc: ResourceNotFoundError
) -> JSONResponse:
    """Handle resource not found errors."""
    logger.info(f"Resource not found: {exc}")

    return create_error_response(
        status_code=status.HTTP_404_NOT_FOUND,
        error_type="not_found",
        message=str(exc),
        request=request,
    )


async def rate_limit_exception_handler(
    request: Request, exc: RateLimitExceeded
) -> JSONResponse:
    """Handle rate limit exceeded errors."""
    logger.warning(
        f"Rate limit exceeded: {exc.detail}",
        extra={"extra_data": {"path": request.url.path}},
    )

    return create_error_response(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        error_type="rate_limit_exceeded",
        message="Too many requests. Please slow down.",
        details={"retry_after": "60 seconds"},
        request=request,
    )


async def generic_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Handle unexpected exceptions."""
    # Log full traceback for debugging
    logger.error(
        f"Unhandled exception: {exc}",
        extra={
            "extra_data": {
                "path": request.url.path,
                "method": request.method,
                "traceback": traceback.format_exc(),
            }
        },
    )

    # Don't expose internal error details to client
    return create_error_response(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        error_type="internal_error",
        message="An unexpected error occurred. Please try again later.",
        request=request,
    )


def setup_exception_handlers(app: FastAPI) -> None:
    """Register all exception handlers with the FastAPI app."""
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(AuthenticationError, authentication_exception_handler)
    app.add_exception_handler(AuthorizationError, authorization_exception_handler)
    app.add_exception_handler(TenantAccessError, tenant_access_exception_handler)
    app.add_exception_handler(ResourceNotFoundError, not_found_exception_handler)
    app.add_exception_handler(RateLimitExceeded, rate_limit_exception_handler)
    app.add_exception_handler(Exception, generic_exception_handler)

    logger.info("Exception handlers registered")
