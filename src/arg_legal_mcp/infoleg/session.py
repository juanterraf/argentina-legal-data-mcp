"""Stateful InfoLEG session management.

InfoLEG's live search pagination is session-bound: page N+1 is fetched by POSTing
to the same endpoint over the same cookie jar that produced page 1. We therefore
keep one ``httpx.Client`` per distinct search (keyed by the request payload hash),
plus a single global client for stateless GETs (ficha, vinculos, anexo, config).

All clients verify TLS (httpx default ``verify=True``) — never disabled.
For a remote deployment, run a single instance (this state is in-process).
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import httpx

from .cache import request_hash


@dataclass
class SearchSession:
    client: httpx.Client
    created_at: float
    total_pags: int | None = None
    first_request: bool = True


class SessionManager:
    def __init__(
        self,
        *,
        user_agent: str,
        timeout: float = 20.0,
        ttl: int = 300,
        verify: bool = True,
    ):
        self._user_agent = user_agent
        self._timeout = timeout
        self._ttl = ttl
        self._verify = verify
        self._global: httpx.Client | None = None
        self._global_ts: float = 0.0
        self._searches: dict[str, SearchSession] = {}

    def _new_client(self) -> httpx.Client:
        return httpx.Client(
            headers={"User-Agent": self._user_agent},
            timeout=self._timeout,
            verify=self._verify,  # TLS verification ALWAYS on
            follow_redirects=True,
        )

    # ── global client for stateless GETs ─────────────────────────────────────
    def get_client(self) -> httpx.Client:
        now = time.time()
        if self._global is None or (now - self._global_ts) > self._ttl:
            if self._global is not None:
                self._global.close()
            self._global = self._new_client()
            self._global_ts = now
        return self._global

    # ── per-search stateful sessions ─────────────────────────────────────────
    def _key(self, payload: dict) -> str:
        return request_hash(payload)

    def _evict_expired(self) -> None:
        now = time.time()
        for k in [k for k, s in self._searches.items() if (now - s.created_at) > self._ttl]:
            self._searches[k].client.close()
            del self._searches[k]

    def get_search_session(self, payload: dict) -> SearchSession:
        self._evict_expired()
        key = self._key(payload)
        s = self._searches.get(key)
        if s is None:
            s = SearchSession(client=self._new_client(), created_at=time.time())
            self._searches[key] = s
        return s

    def set_search_pages(self, payload: dict, total_pags: int) -> None:
        s = self._searches.get(self._key(payload))
        if s is not None:
            s.total_pags = total_pags
            s.first_request = False

    def close(self) -> None:
        if self._global is not None:
            self._global.close()
            self._global = None
        for s in self._searches.values():
            s.client.close()
        self._searches.clear()
