"""Fixed-window rate limits for uploads (threat model 4.7: structuring, bursts, abuse).

Uses Redis when reachable so limits hold across API replicas; falls back to process memory in
dev/test. Production should always run Redis (it is also the Celery broker).
"""

import logging
import threading
import time
from functools import lru_cache

from app.core.config import get_settings

log = logging.getLogger(__name__)


class RateLimited(Exception):
    def __init__(self, scope: str, retry_after: int):
        super().__init__(scope)
        self.scope = scope
        self.retry_after = retry_after


class _Memory:
    def __init__(self) -> None:
        self.counts: dict[str, tuple[int, float]] = {}
        self.lock = threading.Lock()

    def hit(self, key: str, window: int) -> int:
        now = time.time()
        with self.lock:
            count, expires = self.counts.get(key, (0, now + window))
            if now >= expires:
                count, expires = 0, now + window
            self.counts[key] = (count + 1, expires)
            return count + 1

    def reset(self) -> None:
        with self.lock:
            self.counts.clear()


class _Redis:
    def __init__(self, url: str) -> None:
        import redis

        self.r = redis.Redis.from_url(url, socket_timeout=1, socket_connect_timeout=1)
        self.r.ping()

    def hit(self, key: str, window: int) -> int:
        pipe = self.r.pipeline()
        pipe.incr(key)
        pipe.expire(key, window, nx=True)
        return int(pipe.execute()[0])


@lru_cache
def backend() -> _Memory | _Redis:
    s = get_settings()
    if s.env != "test":
        try:
            return _Redis(s.redis_url)
        except Exception as exc:  # noqa: BLE001
            log.warning("rate limiter falling back to memory: %s", exc)
    return _Memory()


def check(scope: str, key: str, limit: int, window: int) -> None:
    if limit <= 0:
        return
    bucket = int(time.time() // window)
    count = backend().hit(f"pops:rl:{scope}:{key}:{bucket}", window)
    if count > limit:
        raise RateLimited(scope, window - int(time.time() % window))
