from __future__ import annotations

import time
import threading
from collections import deque
from time import monotonic

from flask import current_app, request

_LOCK = threading.Lock()
_HITS: dict[str, deque[float]] = {}
_WINDOWS: dict[str, int] = {}
_LAST_SWEEP_AT = 0.0
_SWEEP_INTERVAL_SECONDS = 300.0
_REDIS_CLIENT = None
_REDIS_INIT_DONE = False


def _get_redis_client():
    global _REDIS_CLIENT, _REDIS_INIT_DONE
    if _REDIS_INIT_DONE:
        return _REDIS_CLIENT
    _REDIS_INIT_DONE = True
    try:
        redis_url = str(current_app.config.get("RATE_LIMIT_REDIS_URL") or "").strip()
    except Exception:
        redis_url = ""
    if not redis_url:
        _REDIS_CLIENT = None
        return None
    try:
        import redis  # type: ignore

        client = redis.from_url(
            redis_url,
            decode_responses=False,
            socket_connect_timeout=0.5,
            socket_timeout=0.5,
        )
        client.ping()
        _REDIS_CLIENT = client
        return client
    except Exception:
        _REDIS_CLIENT = None
        return None


def client_ip() -> str:
    trust_proxy = False
    try:
        trust_proxy = bool(current_app.config.get("TRUST_PROXY_X_FORWARDED_FOR"))
    except Exception:
        trust_proxy = False
    if trust_proxy:
        xff = (request.headers.get("X-Forwarded-For") or "").strip()
        if xff:
            return xff.split(",")[0].strip() or "unknown"
    return (request.remote_addr or "unknown").strip()


def _hit_limit_redis(*, bucket_key: str, lim: int, win: int) -> tuple[bool, int] | None:
    client = _get_redis_client()
    if client is None:
        return None
    now = int(time.time())
    slot = now // win
    redis_key = f"rl:{bucket_key}:{win}:{slot}"
    try:
        pipe = client.pipeline()
        pipe.incr(redis_key)
        pipe.ttl(redis_key)
        count, ttl = pipe.execute()
        if int(ttl or -1) < 0:
            client.expire(redis_key, win + 2)
        if int(count or 0) > lim:
            wait = int(ttl or 0)
            if wait <= 0:
                wait = max(1, win - (now % win))
            return True, wait
        return False, 0
    except Exception:
        return None


def hit_limit(*, key: str, limit: int, window_seconds: int) -> tuple[bool, int]:
    global _LAST_SWEEP_AT
    now = monotonic()
    lim = max(1, int(limit))
    win = max(1, int(window_seconds))
    bucket_key = str(key or "").strip() or "default"

    redis_result = _hit_limit_redis(bucket_key=bucket_key, lim=lim, win=win)
    if redis_result is not None:
        return redis_result

    with _LOCK:
        if now - _LAST_SWEEP_AT >= _SWEEP_INTERVAL_SECONDS:
            dead_keys: list[str] = []
            for k, q0 in _HITS.items():
                key_win = max(1, int(_WINDOWS.get(k) or win))
                cutoff0 = now - key_win
                while q0 and q0[0] <= cutoff0:
                    q0.popleft()
                if not q0:
                    dead_keys.append(k)
            for k in dead_keys:
                _HITS.pop(k, None)
                _WINDOWS.pop(k, None)
            _LAST_SWEEP_AT = now

        q = _HITS.get(bucket_key)
        if q is None:
            q = deque()
            _HITS[bucket_key] = q
        _WINDOWS[bucket_key] = win
        cutoff = now - win
        while q and q[0] <= cutoff:
            q.popleft()
        if len(q) >= lim:
            wait = int(max(1, win - (now - q[0])))
            return True, wait
        q.append(now)
    return False, 0
