"""Cached Azure auth helpers (Fabric REST + Lakehouse SQL)."""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass

from azure.identity import DefaultAzureCredential

_LOCK = threading.Lock()
_CRED: DefaultAzureCredential | None = None


def _credential() -> DefaultAzureCredential:
    global _CRED
    with _LOCK:
        if _CRED is None:
            _CRED = DefaultAzureCredential()
        return _CRED


@dataclass
class _CachedToken:
    token: str
    expires_on: float


_TOKEN_CACHE: dict[str, _CachedToken] = {}


def get_token(resource: str) -> str:
    """Return a bearer token for *resource*, cached until ~5 min before expiry."""
    now = time.time()
    cached = _TOKEN_CACHE.get(resource)
    if cached and cached.expires_on - 300 > now:
        return cached.token
    tok = _credential().get_token(resource)
    _TOKEN_CACHE[resource] = _CachedToken(token=tok.token, expires_on=tok.expires_on)
    return tok.token
