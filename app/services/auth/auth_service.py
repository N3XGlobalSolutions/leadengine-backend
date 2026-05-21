"""Authentication service."""

from typing import Optional

from app.models.user import Token, UserCreate, UserLogin, UserResponse
from app.services import user_service, audit_service
from app.models.audit import AuditLogCreate
from app.utils.exceptions import AuthenticationError
from app.utils.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    verify_password,
)


class AuthService:
    """Service for authentication operations."""

    def register(self, user_data: UserCreate) -> UserResponse:
        """Register a new user."""
        try:
            return user_service.create_user(user_data)
        except ValueError as e:
            raise AuthenticationError(str(e))

    def login(self, login_data: UserLogin) -> Token:
        """Authenticate a user and return JWT tokens."""
        # Get user by email
        user = user_service.get_user_by_email(login_data.email)
        if not user:
            raise AuthenticationError("Invalid email or password")

        # Verify password
        if not verify_password(login_data.password, user.hashed_password):
            raise AuthenticationError("Invalid email or password")

        # Check if user is active AND not soft-deleted
        if not user.is_active or user.is_deleted:
            raise AuthenticationError("User account is inactive")

        # Update last login
        user_service.update_last_login(user.user_id)

        # Audit log
        try:
            audit_service.log_activity(
                AuditLogCreate(
                    action="USER_LOGIN",
                    user_id=user.user_id,
                    user_name=user.full_name,
                    user_role=user.role,
                    tenant_id=user.tenant_id,
                    description=f"User logged in successfully"
                )
            )
        except Exception as e:
            print(f"Warning: Failed to create audit log for login: {e}")

        # Create tokens
        token_data = {
            "sub": user.user_id,
            "email": user.email,
            "role": user.role,
            "tenant_id": user.tenant_id,
        }

        access_token = create_access_token(token_data)
        refresh_token = create_refresh_token(token_data)

        return Token(access_token=access_token, refresh_token=refresh_token)

    def refresh_access_token(self, refresh_token: str) -> str:
        """Create a new access token from a refresh token."""
        payload = decode_token(refresh_token)
        if payload is None or payload.get("type") != "refresh":
            raise AuthenticationError("Invalid refresh token")

        # Create new access token
        token_data = {
            "sub": payload.get("sub"),
            "email": payload.get("email"),
            "role": payload.get("role"),
            "tenant_id": payload.get("tenant_id"),
        }

        return create_access_token(token_data)

    def validate_token(self, token: str) -> Optional[dict]:
        """Validate a token and return its payload."""
        return decode_token(token)


auth_service = AuthService()
