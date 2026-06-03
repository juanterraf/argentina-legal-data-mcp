"""BCRA — Estadísticas Monetarias (v4.0) and Cotizaciones (Estadísticas Cambiarias v1.0).

Live-verified contract (2026-06-03): no auth. NOTE: v2.0/v3.0 are deprecated and return
HTTP 410 Gone — only **v4.0** works for Monetarias. BCRA's TLS chain is occasionally
incomplete; we never disable verification — a handshake failure surfaces as a structured
error. The catalog's ``metadata.resultset.count`` is the count REMAINING from the offset,
so the grand total is taken from the first page only.
"""

from __future__ import annotations

from .base import SourceError, get_json

BASE = "https://api.bcra.gob.ar"
_FUENTE_MON = "BCRA — Estadísticas Monetarias v4.0"
_TLS_NOTE = "Si falla por TLS, el certificado de BCRA puede tener cadena incompleta; no se desactiva la verificacion."
_MAX_PAGES = 50  # safety bound for catalog pagination


def _norm_var(r: dict) -> dict:
    return {
        "id_variable": r.get("idVariable"),
        "descripcion": (r.get("descripcion") or "").strip(),
        "categoria": r.get("categoria"),
        "unidad": r.get("unidadExpresion"),
        "periodicidad": r.get("periodicidad"),
        "ultima_fecha": r.get("ultFechaInformada"),
        "ultimo_valor": r.get("ultValorInformado"),
    }


def variables(
    *,
    id_variable: int | None = None,
    desde: str | None = None,
    hasta: str | None = None,
    limit: int = 1000,
    user_agent: str,
    timeout: float = 20.0,
) -> dict:
    """Catalog of monetary variables (no id) or the time series of one variable."""
    if id_variable is not None:
        # Series of one variable. Contract is ?desde&hasta only (no `limit` param);
        # cap client-side instead.
        url = f"{BASE}/estadisticas/v4.0/Monetarias/{int(id_variable)}"
        params: dict = {}
        if desde:
            params["desde"] = desde
        if hasta:
            params["hasta"] = hasta
        try:
            data = get_json(url, user_agent=user_agent, timeout=timeout, params=params)
        except SourceError as exc:
            return {"error": f"BCRA no disponible: {exc}", "fuente": "BCRA", "nota": _TLS_NOTE}
        results = data.get("results", []) if isinstance(data, dict) else []
        detalle = results[0].get("detalle", []) if results else []
        serie = [{"fecha": d.get("fecha"), "valor": d.get("valor")} for d in detalle]
        if limit and len(serie) > limit:
            serie = serie[:limit]
        return {
            "id_variable": int(id_variable),
            "serie": serie,
            "total": len(serie),
            "fuente": _FUENTE_MON,
        }

    # Catalog of all variables — paginate so we never silently truncate at `limit`.
    url = f"{BASE}/estadisticas/v4.0/Monetarias"
    collected: dict[int, dict] = {}  # keyed by idVariable to dedupe if offset is ignored
    order: list = []
    offset = 0
    grand_total: int | None = None
    for _ in range(_MAX_PAGES):
        try:
            data = get_json(url, user_agent=user_agent, timeout=timeout,
                            params={"limit": limit, "offset": offset})
        except SourceError as exc:
            if collected:
                break  # keep the partial catalog already gathered
            return {"error": f"BCRA no disponible: {exc}", "fuente": "BCRA", "nota": _TLS_NOTE}
        page = data.get("results", []) if isinstance(data, dict) else []
        if grand_total is None:  # only the first page's count is the true total
            meta = data.get("metadata") if isinstance(data, dict) else None
            rs = meta.get("resultset") if isinstance(meta, dict) else None
            if isinstance(rs, dict) and rs.get("count") is not None:
                grand_total = rs.get("count")
        if not page:
            break
        for r in page:
            vid = r.get("idVariable")
            if vid not in collected:
                collected[vid] = r
                order.append(vid)
        offset += len(page)
        if grand_total is not None and len(collected) >= grand_total:
            break
        if len(page) < limit:
            break

    variables_norm = [_norm_var(collected[v]) for v in order]
    resp = {
        "variables": variables_norm,
        "total": len(variables_norm),
        "fuente": _FUENTE_MON,
    }
    if grand_total is not None and len(variables_norm) < grand_total:
        resp["truncado"] = True
        resp["count_reportado"] = grand_total
        resp["nota"] = (f"Catalogo parcial: la API reporta {grand_total} variables pero "
                        f"se obtuvieron {len(variables_norm)}.")
    return resp


def cotizaciones(
    *,
    moneda: str | None = None,
    fechadesde: str | None = None,
    fechahasta: str | None = None,
    user_agent: str,
    timeout: float = 20.0,
) -> dict:
    """FX table (today's snapshot) or per-currency history (ARS per unit)."""
    if moneda:
        url = f"{BASE}/estadisticascambiarias/v1.0/Cotizaciones/{moneda.upper()}"
        params: dict = {}
        if fechadesde:
            params["fechadesde"] = fechadesde
        if fechahasta:
            params["fechahasta"] = fechahasta
    else:
        url = f"{BASE}/estadisticascambiarias/v1.0/Cotizaciones"
        params = {}
    try:
        data = get_json(url, user_agent=user_agent, timeout=timeout, params=params)
    except SourceError as exc:
        return {"error": f"BCRA cotizaciones no disponible: {exc}", "fuente": "BCRA", "nota": _TLS_NOTE}

    results = data.get("results") if isinstance(data, dict) else None
    out = []

    def _emit(fecha, detalle):
        for d in detalle or []:
            out.append({
                "fecha": fecha,
                "codigo_moneda": d.get("codigoMoneda"),
                "moneda": d.get("descripcion"),
                "tipo_pase": d.get("tipoPase"),
                "cotizacion": d.get("tipoCotizacion"),
            })

    if isinstance(results, dict):  # snapshot: results is an object
        _emit(results.get("fecha"), results.get("detalle"))
    elif isinstance(results, list):  # history: array of {fecha, detalle:[...]}
        for row in results:
            _emit(row.get("fecha"), row.get("detalle"))
    return {
        "cotizaciones": out,
        "total": len(out),
        "fuente": "BCRA — Estadísticas Cambiarias v1.0",
    }
