"""Custom exceptions for the application."""


class AuthenticationError(Exception):
    """Raised when authentication fails."""

    pass


class AuthorizationError(Exception):
    """Raised when user doesn't have permission."""

    pass


class TenantAccessError(Exception):
    """Raised when user tries to access a tenant they don't belong to."""

    pass


class ResourceNotFoundError(Exception):
    """Raised when a requested resource is not found."""

    pass
