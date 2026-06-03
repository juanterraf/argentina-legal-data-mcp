"""INDEC / datos.gob.ar — Series de Tiempo API (live-verified, no auth).

  * Series:  GET /series?ids=<id>&start_date=&end_date=&limit=&sort=&format=json
             -> {"data": [[date, value], ...], "meta": [...]}
  * Search:  GET /series/search?q=<query>&limit=
             -> {"data": [{"field": {...}, "dataset": {...}}], "count": N}
"""

from __future__ import annotations

from .base import SourceError, get_json

BASE = "https://apis.datos.gob.ar/series/api"


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
    params: dict = {"ids": serie_id, "format": "json", "limit": limit, "sort": sort}
    if start_date:
        params["start_date"] = start_date
    if end_date:
        params["end_date"] = end_date
    try:
        data = get_json(f"{BASE}/series", user_agent=user_agent, timeout=timeout, params=params)
    except SourceError as exc:
        return {"error": f"INDEC/datos.gob.ar no disponible: {exc}", "fuente": "datos.gob.ar"}

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
        "fuente": "datos.gob.ar — Series de Tiempo (INDEC y otros)",
    }


def buscar(q: str, *, limit: int = 10, user_agent: str, timeout: float = 20.0) -> dict:
    """Full-text search for series ids."""
    try:
        data = get_json(
            f"{BASE}/search", user_agent=user_agent, timeout=timeout,
            params={"q": q, "limit": limit},
        )
    except SourceError as exc:
        return {"error": f"Busqueda de series no disponible: {exc}", "fuente": "datos.gob.ar"}
    items = data.get("data", []) if isinstance(data, dict) else []
    series = []
    for x in items:
        field = x.get("field", {}) if isinstance(x, dict) else {}
        dataset = x.get("dataset", {}) if isinstance(x, dict) else {}
        series.append({
            "id": field.get("id"),
            "descripcion": field.get("description") or field.get("title"),
            "frecuencia": field.get("frequency"),
            "unidades": field.get("units"),
            "dataset": dataset.get("title"),
        })
    return {
        "series": series,
        "total": data.get("count", len(series)) if isinstance(data, dict) else len(series),
        "fuente": "datos.gob.ar — Series de Tiempo (búsqueda)",
    }
