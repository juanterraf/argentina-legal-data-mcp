"""Stateful InfoLEG session management (thread-safe).

InfoLEG's live search pagination is session-bound: page N+1 is fetched by POSTing
to the same endpoint over the same cookie jar that produced page 1. We therefore
keep one ``httpx.Client`` per distinct search (keyed by the request payload hash),
plus a single global client for stateless GETs (ficha, vinculos, anexo, config).

Tools run in worker threads, so every structure here is guarded by a lock, and each
search session carries its own lock: the caller must hold ``session.lock`` while it
drives that session's pagination (two concurrent requests on the same cookie jar
would otherwise interleave pages). Rotated/evicted clients are closed later (not
while another thread may still be using them).

All clients verify TLS (httpx default ``verify=True``) — never disabled.
For a remote deployment, run a single instance (this state is in-process).
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

import httpx

from .cache import request_hash

# Upper bound on concurrent distinct live searches kept in memory.
MAX_SEARCH_SESSIONS = 64


@dataclass
class SearchSession:
    client: httpx.Client
    created_at: float
    total_pags: int | None = None
    first_request: bool = True
    lock: threading.Lock = field(default_factory=threading.Lock)


class SessionManager:
    def __init__(
        self,
        *,
        user_agent: str,
        timeout: float = 15.0,
        ttl: int = 300,
        verify: bool = True,
    ):
        self._user_agent = user_agent
        self._timeout = timeout
        self._ttl = ttl
        self._verify = verify
        self._lock = threading.RLock()
        self._global: httpx.Client | None = None
        self._global_ts: float = 0.0
        self._searches: dict[str, SearchSession] = {}
        # Clients retired by rotation/eviction; closed on the next rotation (> ttl later).
        self._retired: list[tuple[float, httpx.Client]] = []

    def _new_client(self) -> httpx.Client:
        return httpx.Client(
            headers={"User-Agent": self._user_agent},
            timeout=httpx.Timeout(self._timeout, connect=min(10.0, self._timeout)),
            verify=self._verify,  # TLS verification ALWAYS on
            follow_redirects=True,
            max_redirects=5,
        )

    def _retire(self, client: httpx.Client, now: float) -> None:
        self._retired.append((now, client))
        keep = []
        for ts, c in self._retired:
            if now - ts > self._ttl:
                c.close()
            else:
                keep.append((ts, c))
        self._retired = keep

    # ── global client for stateless GETs ─────────────────────────────────────
    def get_client(self) -> httpx.Client:
        with self._lock:
            now = time.time()
            if self._global is None or (now - self._global_ts) > self._ttl:
                if self._global is not None:
                    self._retire(self._global, now)
                self._global = self._new_client()
                self._global_ts = now
            return self._global

    # ── per-search stateful sessions ─────────────────────────────────────────
    @staticmethod
    def _key(payload: dict) -> str:
        return request_hash(payload)

    def _evict_expired(self, now: float) -> None:
        expired = [k for k, s in self._searches.items() if (now - s.created_at) > self._ttl]
        if len(self._searches) - len(expired) >= MAX_SEARCH_SESSIONS:
            oldest = sorted(self._searches.items(), key=lambda kv: kv[1].created_at)
            expired += [k for k, _ in oldest[: len(self._searches) - MAX_SEARCH_SESSIONS + 1]]
        for k in set(expired):
            s = self._searches.pop(k, None)
            if s is not None:
                self._retire(s.client, now)

    def get_search_session(self, payload: dict) -> SearchSession:
        with self._lock:
            now = time.time()
            self._evict_expired(now)
            key = self._key(payload)
            s = self._searches.get(key)
            if s is None:
                s = SearchSession(client=self._new_client(), created_at=now)
                self._searches[key] = s
            return s

    def set_search_pages(self, payload: dict, total_pags: int) -> None:
        with self._lock:
            s = self._searches.get(self._key(payload))
            if s is not None:
                s.total_pags = total_pags
                s.first_request = False

    def close(self) -> None:
        with self._lock:
            if self._global is not None:
                self._global.close()
                self._global = None
            for s in self._searches.values():
                s.client.close()
            self._searches.clear()
            for _, c in self._retired:
                c.close()
            self._retired.clear()
