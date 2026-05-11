"""
Redis client initialization module.

This module initializes a Redis client for use throughout the application.
The client is configured with connection parameters from settings and
is set up to automatically decode responses to strings.

Attributes:
    redis_client (redis.Redis): A configured Redis client instance ready for use.
"""

from urllib.parse import quote

# core/redis.py
from redis.asyncio import Redis

from core.config import settings

redis_client: Redis | None = None


async def init_redis() -> None:
    global redis_client

    if settings.REDIS_USERNAME and settings.REDIS_PASSWORD:
        password = quote(settings.REDIS_PASSWORD, safe="")
        url = f"redis://{settings.REDIS_USERNAME}:{password}@{settings.REDIS_HOST}:{settings.REDIS_PORT}"
    else:
        url = f"redis://{settings.REDIS_HOST}:{settings.REDIS_PORT}"

    redis_client = Redis.from_url(
        url,
        decode_responses=True,
        socket_timeout=5,
        socket_connect_timeout=5,
    )


async def close_redis() -> None:
    if redis_client:
        await redis_client.close()


async def get_redis() -> Redis | None:
    return redis_client
