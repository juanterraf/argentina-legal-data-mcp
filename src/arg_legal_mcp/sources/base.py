"""Shared helpers for the extra data sources: a small, resilient JSON GET with a TTL cache.

TLS verification is always on. Transient failures get a short bounded retry; the tool
layer turns errors into a structured ``{"error": ..., "fuente": ...}`` rather than raising.
Successful responses can be cached in memory (per URL + params) with a TTL and a size cap.
"""

from __future__ import annotations

import json
import threading
import time
from collections import OrderedDict
from typing import Any

import httpx

# Cache TTLs (seconds) used by the source modules.
TTL_DOLAR = 300  # 5 min
TTL_BCRA_CATALOGO = 6 * 3600  # 6 h
TTL_BCRA_SERIE = 900  # 15 min
TTL_FERIADOS = 24 * 3600  # 24 h
TTL_INDEC = 1800  # 30 min

_CACHE_MAX = 256
_cache: OrderedDict[str, tuple[float, Any]] = OrderedDict()
_cache_lock = threading.Lock()


class SourceError(Exception):
    """A data source could not be reached or returned an unexpected payload."""


def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()


def _cache_key(url: str, params: dict | None) -> str:
    return url + "?" + json.dumps(params or {}, sort_keys=True, default=str)


def _cache_get(key: str) -> tuple[bool, Any]:
    now = time.monotonic()
    with _cache_lock:
        item = _cache.get(key)
        if item is None:
            return False, None
        if item[0] <= now:
            del _cache[key]
            return False, None
        _cache.move_to_end(key)
        return True, item[1]


def _cache_put(key: str, value: Any, ttl: float) -> None:
    with _cache_lock:
        _cache[key] = (time.monotonic() + ttl, value)
        _cache.move_to_end(key)
        while len(_cache) > _CACHE_MAX:
            _cache.popitem(last=False)


def error_result(mensaje: str, fuente: str, **extra: Any) -> dict:
    """Uniform structured error payload for tools."""
    out = {"error": mensaje, "fuente": fuente}
    out.update(extra)
    return out


def get_json(
    url: str,
    *,
    user_agent: str,
    timeout: float = 15.0,
    params: dict | None = None,
    retries: int = 2,
    backoff: float = 0.5,
    ttl: float | None = None,
) -> Any:
    """GET ``url`` and return decoded JSON. ``ttl`` (seconds) enables the in-memory cache."""
    key = _cache_key(url, params) if ttl else ""
    if ttl:
        hit, value = _cache_get(key)
        if hit:
            return value
    last: Exception | None = None
    headers = {"User-Agent": user_agent, "Accept": "application/json"}
    tmo = httpx.Timeout(timeout, connect=min(timeout, 8.0))
    for attempt in range(max(1, retries)):
        try:
            with httpx.Client(timeout=tmo, headers=headers, follow_redirects=True) as client:
                resp = client.get(url, params=params)
            if resp.status_code >= 500:
                raise SourceError(f"server error {resp.status_code} from {url}")
            resp.raise_for_status()
            data = resp.json()
            if ttl:
                _cache_put(key, data, ttl)
            return data
        except httpx.HTTPStatusError as exc:
            # 4xx is a permanent client error (404/410/...) — do not retry.
            raise SourceError(f"HTTP {exc.response.status_code} from {url}") from exc
        except (httpx.HTTPError, ValueError, SourceError) as exc:
            last = exc
            if attempt == retries - 1:
                break
            time.sleep(backoff * (2**attempt))
        except Exception as exc:  # never leak a raw exception to the tool layer
            raise SourceError(f"{type(exc).__name__}: {exc}") from exc
    raise SourceError(str(last) or type(last).__name__)


def parse_iso_date(value: str | None):
    """Parse 'YYYY-MM-DD' into a ``date`` or return None if it is not a valid ISO date."""
    from datetime import date

    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        return None
