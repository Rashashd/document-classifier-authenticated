"""fastapi-cache2 Redis backend bootstrap.

Project convention: db 0 = RQ queue, db 1 = fastapi-cache2. The caller
picks the DB by choosing the URL suffix; this module does not enforce
it.
"""

from __future__ import annotations

import logging

from fastapi_cache import FastAPICache
from fastapi_cache.backends.redis import RedisBackend
from redis.asyncio import Redis as AsyncRedis
from redis.exceptions import ConnectionError as RedisConnectionError

from app.infra.exceptions import CacheUnavailableError


logger = logging.getLogger(__name__)

CACHE_KEY_PREFIX: str = "dc-cache"


async def init_redis_cache(redis_url: str) -> None:
    """Initialise FastAPICache against Redis. Call from FastAPI startup.

    Pings Redis so an unreachable backend fails startup loudly rather
    than producing a flood of 500s on first cached request.
    """
    # decode_responses=False: RedisBackend stores pickled bytes.
    client: AsyncRedis = AsyncRedis.from_url(
        redis_url,
        encoding="utf-8",
        decode_responses=False,
    )

    try:
        pong = await client.ping()
        logger.info("cache: redis ping returned %r", pong)
    except RedisConnectionError as exc:
        # redis_url may carry credentials — do not log it verbatim.
        logger.exception("cache: redis ping failed during startup")
        raise CacheUnavailableError(
            f"Redis cache is unreachable at startup: {exc}"
        ) from exc

    FastAPICache.init(RedisBackend(client), prefix=CACHE_KEY_PREFIX)
    logger.info("cache: FastAPICache initialised (prefix=%r)", CACHE_KEY_PREFIX)
