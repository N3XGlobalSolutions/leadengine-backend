"""Authentication routes."""

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from app.core.config import get_settings
from app.core.logging_config import get_logger
from app.middleware.rate_limiter import limiter
from app.models.user import Token, UserCreate, UserResponse, UserLogin
from app.services.auth.auth_service import auth_service
from app.utils.dependencies import get_current_user
from app.utils.exceptions import AuthenticationError

logger = get_logger(__name__)
settings = get_settings()

router = APIRouter(prefix="/api/auth", tags=["Authentication"])


class RefreshRequest(BaseModel):
    """Refresh token supplied in the request body (never the query string)."""

    refresh_token: str = Field(..., min_length=1)


@router.post(
    "/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED
)
async def register(user_data: UserCreate):
    """
    Register a new user.
    [DEPRECATED] Public registration is disabled. Use Admin Create User API.
    """
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Public registration is disabled. Please contact an administrator."
    )


@router.post("/login", response_model=Token)
@limiter.limit(settings.RATE_LIMIT_AUTH)
async def login(request: Request, login_data: UserLogin):
    """Login and receive JWT tokens."""
    try:
        return auth_service.login(login_data)
    except AuthenticationError as e:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(e))
    except Exception as e:
        logger.error("Login failed unexpectedly", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Login failed. Please try again later.",
        )


@router.post("/refresh", response_model=dict)
@limiter.limit(settings.RATE_LIMIT_AUTH)
async def refresh_token(request: Request, body: RefreshRequest):
    """Refresh access token using a refresh token supplied in the JSON body."""
    try:
        new_access_token = auth_service.refresh_access_token(body.refresh_token)
        return {"access_token": new_access_token, "token_type": "bearer"}
    except AuthenticationError as e:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(e))
    except Exception:
        logger.error("Token refresh failed unexpectedly", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Token refresh failed. Please try again later.",
        )


@router.get("/me", response_model=UserResponse)
async def get_current_user_info(current_user: UserResponse = Depends(get_current_user)):
    """Get current authenticated user information."""
    return current_user
