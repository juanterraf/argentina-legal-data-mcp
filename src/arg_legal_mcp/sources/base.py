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
# Series mensuales: si la fuente no responde, se acepta un dato vencido de hasta 7 días.
STALE_INDEC = 7 * 24 * 3600

# Clave que get_json agrega a un dict servido desde caché vencida (edad en segundos).
STALE_KEY = "_cache_vencido_seg"

_CACHE_MAX = 256
_STALE_KEEP = 7 * 24 * 3600  # una entrada vencida se descarta pasado este tiempo
_cache: OrderedDict[str, tuple[float, float, Any]] = OrderedDict()  # (vence, guardado, valor)
_cache_lock = threading.Lock()


class SourceError(Exception):
    """A data source could not be reached or returned an unexpected payload."""


def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()


def _cache_key(url: str, params: dict | None) -> str:
    return url + "?" + json.dumps(params or {}, sort_keys=True, default=str)


def _cache_get(key: str) -> tuple[str, Any, float]:
    """('vigente' | 'vencido' | 'ausente', valor, segundos desde que se guardó)."""
    now = time.monotonic()
    with _cache_lock:
        item = _cache.get(key)
        if item is None:
            return "ausente", None, 0.0
        vence, guardado, valor = item
        if vence > now:
            _cache.move_to_end(key)
            return "vigente", valor, now - guardado
        if now - vence > _STALE_KEEP:
            del _cache[key]
            return "ausente", None, 0.0
        return "vencido", valor, now - guardado


def _cache_put(key: str, value: Any, ttl: float) -> None:
    now = time.monotonic()
    with _cache_lock:
        _cache[key] = (now + ttl, now, value)
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
    stale_max: float = 0,
) -> Any:
    """GET ``url`` and return decoded JSON. ``ttl`` (seconds) enables the in-memory cache.

    With ``stale_max`` > 0, if the source fails and there is an expired cached response
    saved less than ``stale_max`` seconds ago, that response is returned instead of the
    error; a dict response carries ``STALE_KEY`` with its age so the tool can say so.
    """
    key = _cache_key(url, params) if ttl else ""
    estado, cacheado, edad = _cache_get(key) if ttl else ("ausente", None, 0.0)
    if estado == "vigente":
        return cacheado
    try:
        return _fetch_json(url, key, user_agent=user_agent, timeout=timeout, params=params,
                           retries=retries, backoff=backoff, ttl=ttl)
    except SourceError:
        if estado == "vencido" and stale_max and edad <= stale_max:
            if isinstance(cacheado, dict):
                return {**cacheado, STALE_KEY: int(edad)}
            return cacheado
        raise


def aviso_cache_vencido(data: Any) -> str | None:
    """Aviso para el usuario si ``data`` vino de caché vencida (ver ``get_json``)."""
    edad = data.get(STALE_KEY) if isinstance(data, dict) else None
    if edad is None:
        return None
    horas = edad / 3600
    hace = f"{horas:.0f} h" if horas >= 1 else f"{edad // 60} min"
    return (f"La fuente no respondio; se devuelve el ultimo dato guardado (de hace {hace}). "
            "Puede no incluir publicaciones recientes.")


def _fetch_json(
    url: str,
    key: str,
    *,
    user_agent: str,
    timeout: float,
    params: dict | None,
    retries: int,
    backoff: float,
    ttl: float | None,
) -> Any:
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
