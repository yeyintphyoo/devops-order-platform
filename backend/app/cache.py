import json
import logging
import os

import redis
from prometheus_client import Counter
from redis.exceptions import RedisError

logger = logging.getLogger(__name__)

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
CACHE_TTL_SECONDS = int(os.getenv("CACHE_TTL_SECONDS", "60"))
PRODUCTS_CACHE_KEY = "products:all"

CACHE_HITS = Counter(
    "order_platform_cache_hits",
    "Number of successful Redis cache reads.",
)

CACHE_MISSES = Counter(
    "order_platform_cache_misses",
    "Number of Redis cache misses.",
)

CACHE_ERRORS = Counter(
    "order_platform_cache_errors",
    "Number of Redis cache errors.",
)

CACHE_INVALIDATIONS = Counter(
    "order_platform_cache_invalidations",
    "Number of product cache invalidation attempts.",
)

redis_client = redis.Redis.from_url(
    REDIS_URL,
    decode_responses=True,
    socket_connect_timeout=1,
    socket_timeout=1,
)


def get_cached_products():
    """Return cached products, or None when unavailable or empty."""
    try:
        cached_value = redis_client.get(PRODUCTS_CACHE_KEY)

        if cached_value is None:
            CACHE_MISSES.inc()
            return None

        products = json.loads(cached_value)
        CACHE_HITS.inc()
        return products

    except (RedisError, json.JSONDecodeError, TypeError):
        CACHE_ERRORS.inc()
        logger.warning("Redis cache read failed; falling back to the database")
        return None


def cache_products(products):
    """Cache the product list without affecting API availability."""
    try:
        redis_client.setex(
            PRODUCTS_CACHE_KEY,
            CACHE_TTL_SECONDS,
            json.dumps(products),
        )
    except (RedisError, TypeError, ValueError):
        CACHE_ERRORS.inc()
        logger.warning("Redis cache write failed", exc_info=True)


def invalidate_products_cache():
    """Remove the product-list cache after a successful database mutation."""
    CACHE_INVALIDATIONS.inc()

    try:
        redis_client.delete(PRODUCTS_CACHE_KEY)
    except RedisError:
        CACHE_ERRORS.inc()
        logger.warning("Redis cache invalidation failed", exc_info=True)
