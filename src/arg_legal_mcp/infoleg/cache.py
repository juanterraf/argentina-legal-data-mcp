"""Persistent cache (diskcache) for InfoLEG fichas, texts, vinculos and search pages.

Caching lives in the service layer (not the HTTP client) so the client stays pure
and trivially testable. Pass ``cache=None`` to disable (used in unit tests).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import diskcache

TTL_SHORT = 3000  # search pages (session-bound, change often)
TTL_LONG = 86400  # fichas, texts, vinculos (stable for a day)


class InfoLegCache:
    def __init__(self, cache_dir: str | Path, ttl_short: int = TTL_SHORT, ttl_long: int = TTL_LONG):
        self._c = diskcache.Cache(str(Path(cache_dir) / "infoleg"))
        self.ttl_short = ttl_short
        self.ttl_long = ttl_long

    # ── generic ────────────────────────────────────────────────────────────--
    def get(self, key: str) -> Any:
        return self._c.get(key)

    def set(self, key: str, value: Any, ttl: int) -> None:
        self._c.set(key, value, expire=ttl)

    def close(self) -> None:
        self._c.close()

    # ── typed helpers ────────────────────────────────────────────────────────
    def get_norma(self, norma_id: int) -> Any:
        return self.get(f"norma:{norma_id}")

    def set_norma(self, norma_id: int, value: Any) -> None:
        self.set(f"norma:{norma_id}", value, self.ttl_long)

    def get_texto(self, norma_id: int, tipo: str) -> str | None:
        return self.get(f"texto:{tipo}:{norma_id}")

    def set_texto(self, norma_id: int, tipo: str, value: str) -> None:
        self.set(f"texto:{tipo}:{norma_id}", value, self.ttl_long)

    def get_vinculos(self, norma_id: int, modo: int) -> Any:
        return self.get(f"vinculos:{modo}:{norma_id}")

    def set_vinculos(self, norma_id: int, modo: int, value: Any) -> None:
        self.set(f"vinculos:{modo}:{norma_id}", value, self.ttl_long)

    def get_busqueda(self, req_hash: str, page: int) -> Any:
        return self.get(f"busqueda:{req_hash}:{page}")

    def set_busqueda(self, req_hash: str, page: int, value: Any) -> None:
        self.set(f"busqueda:{req_hash}:{page}", value, self.ttl_short)


def request_hash(payload: dict) -> str:
    """Stable hash of a search request payload (for cache + session keying)."""
    return hashlib.md5(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()
