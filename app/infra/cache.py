"""
fastapi-cache2 Redis backend bootstrap.

This module is the *only* place in the codebase that wires
:class:`fastapi_cache.FastAPICache` to a concrete backend. It lives in
``app/infra/`` and obeys the same two rules as the rest of this folder:

  1. **No business logic.** No routers, no ``@cache`` decorators, no
     invalidation calls. Those belong in the service layer
     (``app/services/``) and the API layer (``app/api/``).
  2. **No SQLAlchemy.** Infra speaks to external systems only.

Why a separate module from ``app/infra/queue.py``?
--------------------------------------------------
RQ and fastapi-cache2 both use Redis, but they are wired through
different client surfaces (sync ``redis`` for RQ, ``redis.asyncio`` for
fastapi-cache2) and they have different lifecycle expectations
(``RQClient`` is constructed eagerly; ``FastAPICache`` is a process-wide
singleton initialised inside an async startup handler). Mixing them
would couple two unrelated lifecycles in one file.

Logical-DB layout (project convention — see CLAUDE.md §4):

    db 0 — RQ job queue        (used by app/infra/queue.py)
    db 1 — fastapi-cache2      (used here)

The caller picks the DB by choosing the URL suffix, e.g.
``redis://redis:6379/1``. Wrong suffix = cache writes land in the queue
keyspace = a great way to ruin everyone's afternoon. ``app/core``
should hold a single source of truth for these URLs.
"""

from __future__ import annotations

import logging

from fastapi_cache import FastAPICache
from fastapi_cache.backends.redis import RedisBackend
from redis.asyncio import Redis as AsyncRedis
from redis.exceptions import ConnectionError as RedisConnectionError

from app.infra.exceptions import CacheUnavailableError


logger = logging.getLogger(__name__)


# Cache-key prefix shared by every cached endpoint in the project. We
# centralise it here (not buried inside a router) so that:
#   • an ops-time ``FLUSHDB`` is unnecessary — we can scope wipes to
#     ``KEYS "dc-cache:*"`` if we ever need surgical invalidation;
#   • a future second tenant on the same Redis instance won't collide.
# Treat it as a constant — if you change it, every existing cached
# entry becomes a cache miss, which is fine but worth being deliberate
# about.
CACHE_KEY_PREFIX: str = "dc-cache"


async def init_redis_cache(redis_url: str) -> None:
    """Bootstrap :class:`fastapi_cache.FastAPICache` against Redis.

    Intended call site: the FastAPI ``startup`` event handler in
    ``app/api/main.py`` (owned by Person B). Once this returns, the
    service layer is free to use ``@cache(...)`` decorators on
    repository-call helpers.

    Parameters
    ----------
    redis_url
        Strict Redis connection URL — for example
        ``"redis://redis:6379/1"`` inside compose. The DB suffix
        **must** match the project's logical-DB layout (see module
        docstring); the adapter does not enforce this because doing so
        would couple the adapter to that convention. The caller — by
        convention ``app/core/settings.py`` — is the source of truth.

    Returns
    -------
    None
        ``FastAPICache`` is a process-wide singleton; this function has
        side-effects on module-level state inside ``fastapi_cache``.
        Callers do not receive a handle back. To use the cache,
        decorators import ``FastAPICache`` directly.

    Notes
    -----
    Why ``async``?
        ``FastAPICache.init`` is itself sync, but we issue an
        ``await client.ping()`` here so that a missing/unreachable
        Redis fails the FastAPI startup probe loudly rather than
        manifesting as a flood of cache-decorator errors on the first
        request. That ping is async.

    Why ``decode_responses=False``?
        ``RedisBackend`` stores already-pickled bytes; if we let the
        client auto-decode to ``str`` we get UnicodeDecodeError on
        every read of a pickled payload. Leaving the default
        (``False``) is correct; we state it explicitly here as a
        warning to anyone tempted to "tidy up" the client config.
    """
    # ``AsyncRedis.from_url`` parses the URL (host, port, db, auth, TLS)
    # in one shot. The returned object owns a connection pool internally
    # — *no* socket is opened until the first command.
    client: AsyncRedis = AsyncRedis.from_url(
        redis_url,
        encoding="utf-8",
        # Must stay False: the backend stores pickled bytes.
        decode_responses=False,
    )

    # Liveness probe. Any failure here surfaces as an exception that
    # propagates out of the FastAPI startup event, which is exactly
    # what we want — better a refusing-to-start API than one that 200s
    # on /healthz but errors on every cached route.
    try:
        pong = await client.ping()
        logger.info("cache: redis ping returned %r", pong)
    except RedisConnectionError as exc:
        # We deliberately do NOT log redis_url verbatim — it may carry
        # credentials. The caller knows what URL it passed.
        # logger.exception() + typed error per CLAUDE.md §10.2 / §10.4.
        logger.exception("cache: redis ping failed during startup")
        raise CacheUnavailableError(
            f"Redis cache is unreachable at startup: {exc}"
        ) from exc

    # Construct the backend wrapper and install it on the FastAPICache
    # singleton. ``prefix`` is shared by every decorator in the app, so
    # invalidations / debugging KEY scans are scoped predictably.
    backend = RedisBackend(client)
    FastAPICache.init(backend, prefix=CACHE_KEY_PREFIX)

    logger.info(
        "cache: FastAPICache initialised (backend=RedisBackend, prefix=%r)",
        CACHE_KEY_PREFIX,
    )
