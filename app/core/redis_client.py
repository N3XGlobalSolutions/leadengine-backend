import redis
from app.core.config import get_settings

settings = get_settings()

print("REDIS_URL from redis config =", settings.REDIS_URL)

redis_client = redis.Redis.from_url(
    settings.REDIS_URL,
    decode_responses=True
)