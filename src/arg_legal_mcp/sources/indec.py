"""INDEC / datos.gob.ar — Series de Tiempo API (live-verified 2026-10-15, no auth).

  * Series:  GET /series?ids=<id>&start_date=&end_date=&limit=&sort=&format=json
             -> {"data": [[date, value], ...], "meta": [...]}
  * Search:  GET /search?q=<query>&limit=
             -> {"data": [{"field": {...}, "dataset": {...}}], "count": N}

The search endpoint ranks by loose text relevance (an "ipc nivel general nacional" query
returns discontinued GBA series first), so ``buscar`` over-fetches and re-ranks locally with
the metadata the API provides: ``field.time_index_end`` (last observation), the dataset
title ("SERIE DISCONTINUADA"), territorial coverage in the descriptions and
``field.hits_90_days``.
"""

from __future__ import annotations

import math
import re
import unicodedata
from datetime import date, timedelta

from .base import TTL_INDEC, SourceError, error_result, get_json, parse_iso_date

BASE = "https://apis.datos.gob.ar/series/api"
_FUENTE = "datos.gob.ar — Series de Tiempo (INDEC y otros)"
SERIE_LIMIT_MAX = 5000
BUSCAR_LIMIT_MAX = 50
_FETCH = 1000  # candidates pulled from the API before re-ranking (it ranks loosely)
_FETCH_FALLBACK = 200  # smaller page if the big one times out
_VIGENTE_DIAS = 450  # last observation no older than this (monthly/quarterly/annual lag)

_SERIE_ID_RE = re.compile(r"^[A-Za-z0-9_.\-]{3,80}$")
_STOP = {"de", "del", "la", "el", "los", "las", "y", "en", "por", "para", "a"}
# Territorial markers: a series carrying one of these is NOT national coverage.
_REGIONES = (
    "gba", "caba", "ciudad de buenos aires", "provincia", "patagonia", "cuyo", "noa", "nea",
    "pampeana", "gran resistencia", "gran buenos aires", "regional", "region", "noroeste",
    "noreste",
)


def _norm(s: str | None) -> str:
    s = unicodedata.normalize("NFD", s or "")
    return "".join(c for c in s if unicodedata.category(c) != "Mn").lower()


def _tokens(s: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9]+", _norm(s)) if t not in _STOP]


def serie(
    serie_id: str,
    *,
    start_date: str | None = None,
    end_date: str | None = None,
    limit: int = 1000,
    sort: str = "asc",
    user_agent: str,
    timeout: float = 20.0,
) -> dict:
    """Observations for a single time-series id.

    (The API accepts comma-separated ids returning [date, v1, v2, ...] rows, but this
    tool is single-id: only the first value per row is reported. Call once per id.)
    """
    serie_id = (serie_id or "").strip()
    if not _SERIE_ID_RE.match(serie_id):
        return error_result("serie_id invalido (ej: '148.3_INIVELNAL_DICI_M_26').", "datos.gob.ar")
    for nombre, valor in (("start_date", start_date), ("end_date", end_date)):
        if valor and parse_iso_date(valor) is None:
            return error_result(f"{nombre} debe ser una fecha ISO 'YYYY-MM-DD'.", "datos.gob.ar")
    d0, d1 = parse_iso_date(start_date), parse_iso_date(end_date)
    if d0 and d1 and d0 > d1:
        return error_result("start_date no puede ser posterior a end_date.", "datos.gob.ar")
    sort = (sort or "asc").lower()
    if sort not in ("asc", "desc"):
        return error_result("sort debe ser 'asc' o 'desc'.", "datos.gob.ar")
    try:
        limit = max(1, min(int(limit), SERIE_LIMIT_MAX))
    except (TypeError, ValueError):
        limit = 1000
    params: dict = {"ids": serie_id, "format": "json", "limit": limit, "sort": sort}
    if start_date:
        params["start_date"] = start_date
    if end_date:
        params["end_date"] = end_date
    try:
        data = get_json(f"{BASE}/series", user_agent=user_agent, timeout=timeout,
                        params=params, ttl=TTL_INDEC)
    except SourceError as exc:
        return error_result(f"INDEC/datos.gob.ar no disponible: {exc}", "datos.gob.ar")

    rows = data.get("data", []) if isinstance(data, dict) else []
    observaciones = [
        {"fecha": r[0], "valor": r[1]}
        for r in rows
        if isinstance(r, list) and len(r) >= 2
    ]
    meta = data.get("meta") if isinstance(data, dict) else None
    return {
        "serie_id": serie_id,
        "observaciones": observaciones,
        "total": len(observaciones),
        "meta": meta,
        "fuente": _FUENTE,
    }


def _estado(field: dict, dataset: dict, hoy: date) -> tuple[bool, str | None]:
    """(vigente, fecha_ultimo_dato) from the series metadata."""
    fin = field.get("time_index_end")
    texto = _norm(f"{dataset.get('title') or ''} {field.get('description') or ''}")
    discontinuada = "discontinu" in texto
    fecha_fin = parse_iso_date(fin) if isinstance(fin, str) else None
    reciente = bool(fecha_fin and fecha_fin >= hoy - timedelta(days=_VIGENTE_DIAS))
    return (reciente and not discontinuada), fin


def _cobertura(field: dict, dataset: dict, query_norm: str) -> str:
    texto = _norm(f"{field.get('description') or ''} {field.get('title') or ''} "
                  f"{dataset.get('title') or ''}")
    if any(r in texto and r not in query_norm for r in _REGIONES):
        return "regional/subnacional"
    if "nacional" in texto or "nacion" in texto:
        return "nacional"
    return "no especificada"


def _rank(item: dict, q_tokens: list[str], query_norm: str, hoy: date) -> tuple:
    field = item.get("field") or {}
    dataset = item.get("dataset") or {}
    vigente, fin = _estado(field, dataset, hoy)
    cobertura = _cobertura(field, dataset, query_norm)
    blob = _norm(f"{field.get('description') or ''} {field.get('title') or ''} "
                 f"{dataset.get('title') or ''}")
    blob_tokens = set(re.findall(r"[a-z0-9]+", blob))
    hit = sum(1 for t in q_tokens if t in blob_tokens)
    relev = round(hit / len(q_tokens), 1) if q_tokens else 0.0
    try:
        hits = int(field.get("hits_90_days") or 0)
    except (TypeError, ValueError):
        hits = 0
    return (
        1 if vigente else 0,
        relev,
        {"nacional": 2, "no especificada": 1, "regional/subnacional": 0}[cobertura],
        fin or "",
        math.log1p(hits),
    )


def buscar(q: str, *, limit: int = 10, user_agent: str, timeout: float = 25.0) -> dict:
    """Search for series ids; results re-ranked: vigentes, relevantes, nacionales, recientes."""
    q = (q or "").strip()
    if not q:
        return error_result("q no puede estar vacio.", "datos.gob.ar")
    if len(q) > 200:
        q = q[:200]
    try:
        limit = max(1, min(int(limit), BUSCAR_LIMIT_MAX))
    except (TypeError, ValueError):
        limit = 10
    data = None
    last_exc: SourceError | None = None
    for fetch in (_FETCH, _FETCH_FALLBACK):
        try:
            data = get_json(
                f"{BASE}/search", user_agent=user_agent, timeout=timeout,
                params={"q": q, "limit": max(fetch, limit)}, ttl=TTL_INDEC, retries=1,
            )
            break
        except SourceError as exc:
            last_exc = exc
    if data is None:
        return error_result(f"Busqueda de series no disponible: {last_exc}", "datos.gob.ar")
    items = [x for x in (data.get("data", []) if isinstance(data, dict) else [])
             if isinstance(x, dict)]
    hoy = date.today()
    q_tokens = _tokens(q)
    query_norm = _norm(q)
    ranked = sorted(items, key=lambda x: _rank(x, q_tokens, query_norm, hoy), reverse=True)
    series = []
    for x in ranked[:limit]:
        field = x.get("field") or {}
        dataset = x.get("dataset") or {}
        vigente, fin = _estado(field, dataset, hoy)
        series.append({
            "id": field.get("id"),
            "descripcion": field.get("description") or field.get("title"),
            "frecuencia": field.get("frequency"),
            "unidades": field.get("units"),
            "dataset": dataset.get("title"),
            "fuente_dato": (dataset.get("source") or None),
            "vigente": vigente,
            "ultimo_dato": fin,
            "cobertura": _cobertura(field, dataset, query_norm),
        })
    return {
        "series": series,
        "total": len(series),
        "total_api": data.get("count") if isinstance(data, dict) else None,
        "criterio_orden": "vigentes primero, luego coincidencia con la consulta, cobertura "
        "nacional y dato mas reciente; las discontinuadas quedan al final (vigente=false)",
        "fuente": "datos.gob.ar — Series de Tiempo (búsqueda)",
    }
