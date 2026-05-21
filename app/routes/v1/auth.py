"""Authentication routes."""

from fastapi import APIRouter, HTTPException, status

from app.models.user import Token, UserCreate, UserLogin, UserResponse
from app.services.auth.auth_service import auth_service
from app.utils.dependencies import get_current_user
from app.utils.exceptions import AuthenticationError
from fastapi import Depends

router = APIRouter(prefix="/api/auth", tags=["Authentication"])


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
    # try:
    #     return auth_service.register(user_data)
    # except AuthenticationError as e:
    #     raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    # except Exception as e:
    #     raise HTTPException(
    #         status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e)
    #     )


@router.post("/login", response_model=Token)
async def login(login_data: UserLogin):
    """Login and receive JWT tokens."""
    try:
        return auth_service.login(login_data)
    except AuthenticationError as e:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(e))
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e)
        )


@router.post("/refresh", response_model=dict)
async def refresh_token(refresh_token: str):
    """Refresh access token using refresh token."""
    try:
        new_access_token = auth_service.refresh_access_token(refresh_token)
        return {"access_token": new_access_token, "token_type": "bearer"}
    except AuthenticationError as e:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(e))
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e)
        )


@router.get("/me", response_model=UserResponse)
async def get_current_user_info(current_user: UserResponse = Depends(get_current_user)):
    """Get current authenticated user information."""
    return current_user
