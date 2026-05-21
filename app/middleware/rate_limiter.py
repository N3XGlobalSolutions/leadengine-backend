from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(
    key_func=get_remote_address,  # Limit by IP address
    storage_uri="memory://",       # In-memory storage
    default_limits=["200/minute"]  # Default: 200 req/min
)
