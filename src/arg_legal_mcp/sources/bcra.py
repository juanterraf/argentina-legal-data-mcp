"""BCRA — Estadísticas Monetarias (v4.0) and Cotizaciones (Estadísticas Cambiarias v1.0).

Live-verified contract (2026-10-15): no auth. NOTE: v2.0/v3.0 are deprecated and return
HTTP 410 Gone — only **v4.0** works for Monetarias. BCRA's TLS chain is occasionally
incomplete; we never disable verification — a handshake failure surfaces as a structured
error. The catalog (1610 variables on 2026-10-15) is paginated with a fixed page size and
the grand total is taken from the first page's ``metadata.resultset.count`` (which is the
count REMAINING from the offset). The catalog is cached for 6 h, series for 15 min.
"""

from __future__ import annotations

import re
import unicodedata

from .base import (
    TTL_BCRA_CATALOGO,
    TTL_BCRA_SERIE,
    SourceError,
    error_result,
    get_json,
    parse_iso_date,
)

BASE = "https://api.bcra.gob.ar"
URL_MONETARIAS = f"{BASE}/estadisticas/v4.0/Monetarias"
_FUENTE_MON = "BCRA — Estadísticas Monetarias v4.0"
_FUENTE_CAMB = "BCRA — Estadísticas Cambiarias v1.0"
_TLS_NOTE = (
    "Si falla por TLS, el certificado de BCRA puede tener cadena incompleta; "
    "no se desactiva la verificacion."
)
_PAGE = 1000  # catalog page size (the API accepts at least 3000)
_MAX_PAGES = 20  # safety bound for catalog pagination
LIMIT_MAX = 5000


# Siglas de uso corriente que el catálogo del BCRA no menciona en la descripción
# (ids verificados contra /estadisticas/v4.0/monetarias el 15/10/2026).
_SIGLAS = {
    30: "cer",
    31: "uva",
    32: "uvi",
    40: "icl",
    43: "tasa pasiva uso justicia 14290",
}


def _strip_accents(s: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", s or "") if unicodedata.category(c) != "Mn"
    ).lower()


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


def _clamp_limit(limit, default: int = 100) -> int:
    try:
        n = int(limit)
    except (TypeError, ValueError):
        return default
    return max(1, min(n, LIMIT_MAX))


def serie_puntos(
    id_variable: int, desde: str | None, hasta: str | None, *, user_agent: str,
    timeout: float = 20.0,
) -> list[dict]:
    """Raw points [{'fecha','valor'}] of one variable (API order: newest first).

    Raises SourceError.
    """
    params: dict = {}
    if desde:
        params["desde"] = desde
    if hasta:
        params["hasta"] = hasta
    data = get_json(
        f"{URL_MONETARIAS}/{int(id_variable)}", user_agent=user_agent, timeout=timeout,
        params=params, ttl=TTL_BCRA_SERIE,
    )
    results = data.get("results", []) if isinstance(data, dict) else []
    detalle = results[0].get("detalle", []) if results and isinstance(results[0], dict) else []
    return [
        {"fecha": d.get("fecha"), "valor": d.get("valor")}
        for d in detalle
        if isinstance(d, dict)
    ]


def catalogo_completo(*, user_agent: str, timeout: float = 20.0) -> tuple[list[dict], int | None]:
    """Whole variables catalog (raw rows) and the total reported by the API."""
    collected: dict = {}
    grand_total: int | None = None
    offset = 0
    for _ in range(_MAX_PAGES):
        try:
            data = get_json(
                URL_MONETARIAS, user_agent=user_agent, timeout=timeout,
                params={"limit": _PAGE, "offset": offset}, ttl=TTL_BCRA_CATALOGO,
            )
        except SourceError:
            if collected:
                break  # keep the partial catalog already gathered
            raise
        page = data.get("results", []) if isinstance(data, dict) else []
        if grand_total is None:  # only the first page's count is the true total
            meta = data.get("metadata") if isinstance(data, dict) else None
            rs = meta.get("resultset") if isinstance(meta, dict) else None
            if isinstance(rs, dict) and isinstance(rs.get("count"), int):
                grand_total = rs["count"]
        if not page:
            break
        new = 0
        for r in page:
            vid = r.get("idVariable")
            if vid not in collected:
                collected[vid] = r
                new += 1
        if new == 0:  # API ignored the offset: stop instead of looping
            break
        offset += len(page)
        if grand_total is not None and len(collected) >= grand_total:
            break
        if len(page) < _PAGE and grand_total is None:
            break
    return list(collected.values()), grand_total


def variables(
    *,
    id_variable: int | None = None,
    desde: str | None = None,
    hasta: str | None = None,
    limit: int = 100,
    buscar: str | None = None,
    user_agent: str,
    timeout: float = 20.0,
) -> dict:
    """Catalog of monetary variables (no id) or the time series of one variable."""
    if id_variable is not None:
        try:
            vid = int(id_variable)
        except (TypeError, ValueError):
            return error_result("id_variable debe ser un entero.", "BCRA")
        if vid <= 0:
            return error_result("id_variable debe ser un entero positivo.", "BCRA")
        for nombre, valor in (("desde", desde), ("hasta", hasta)):
            if valor and parse_iso_date(valor) is None:
                return error_result(f"{nombre} debe ser una fecha ISO 'YYYY-MM-DD'.", "BCRA")
        d0, d1 = parse_iso_date(desde), parse_iso_date(hasta)
        if d0 and d1 and d0 > d1:
            return error_result("desde no puede ser posterior a hasta.", "BCRA")
        try:
            serie = serie_puntos(vid, desde, hasta, user_agent=user_agent, timeout=timeout)
        except SourceError as exc:
            return error_result(f"BCRA no disponible: {exc}", "BCRA", nota=_TLS_NOTE)
        total = len(serie)
        n = _clamp_limit(limit, default=1000)
        out = {
            "id_variable": vid,
            "serie": serie[:n],
            "total": min(total, n),
            "total_disponible": total,
            "orden": "fecha descendente (mas reciente primero)",
            "fuente": _FUENTE_MON,
        }
        if total > n:
            out["truncado"] = True
        return out

    try:
        rows, grand_total = catalogo_completo(user_agent=user_agent, timeout=timeout)
    except SourceError as exc:
        return error_result(f"BCRA no disponible: {exc}", "BCRA", nota=_TLS_NOTE)
    norm = [_norm_var(r) for r in rows]
    total_catalogo = len(norm)
    if buscar:
        tokens = [t for t in re.split(r"\s+", _strip_accents(buscar).strip()) if t]

        def _texto(v: dict) -> str:
            sigla = _SIGLAS.get(v["id_variable"], "")
            return _strip_accents(f"{sigla} {v['descripcion']} {v['categoria'] or ''}")

        norm = [v for v in norm if all(t in _texto(v) for t in tokens)]

        # Primero la variable cuya sigla se pidió (UVA, CER, ICL...), después las
        # "Principales Variables", las que nombran el término como palabra suelta y,
        # a igualdad, la descripción más corta.
        def _orden(v: dict) -> tuple:
            sigla = _SIGLAS.get(v["id_variable"], "")
            pidio_sigla = any(t in sigla.split() for t in tokens)
            principal = (v["categoria"] or "") == "Principales Variables"
            palabras = set(re.findall(r"\w+", _texto(v)))
            enteras = sum(t in palabras for t in tokens)
            return (not pidio_sigla, not principal, -enteras, len(v["descripcion"]))

        norm.sort(key=_orden)
    n = _clamp_limit(limit)
    resp = {
        "variables": norm[:n],
        "total": min(len(norm), n),
        "coincidencias": len(norm),
        "total_catalogo": total_catalogo,
        "fuente": _FUENTE_MON,
    }
    if len(norm) > n:
        resp["truncado"] = True
        resp["nota"] = (
            f"Se muestran {n} de {len(norm)} variables. Usa `buscar` (texto) para filtrar "
            "o subi `limit`."
        )
    if grand_total is not None and total_catalogo < grand_total:
        resp["catalogo_parcial"] = True
        resp["count_reportado"] = grand_total
        resp["aviso"] = (
            f"Catalogo parcial: la API reporta {grand_total} variables pero se obtuvieron "
            f"{total_catalogo}."
        )
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
    for nombre, valor in (("fechadesde", fechadesde), ("fechahasta", fechahasta)):
        if valor and parse_iso_date(valor) is None:
            return error_result(f"{nombre} debe ser una fecha ISO 'YYYY-MM-DD'.", "BCRA")
    d0, d1 = parse_iso_date(fechadesde), parse_iso_date(fechahasta)
    if d0 and d1 and d0 > d1:
        return error_result("fechadesde no puede ser posterior a fechahasta.", "BCRA")
    params: dict = {}
    if moneda:
        code = moneda.strip().upper()
        if not re.fullmatch(r"[A-Z]{3}", code):
            return error_result("moneda debe ser un codigo de 3 letras (ej: USD, EUR, BRL).",
                                "BCRA")
        url = f"{BASE}/estadisticascambiarias/v1.0/Cotizaciones/{code}"
        if fechadesde:
            params["fechadesde"] = fechadesde
        if fechahasta:
            params["fechahasta"] = fechahasta
    else:
        url = f"{BASE}/estadisticascambiarias/v1.0/Cotizaciones"
        if fechadesde:  # the snapshot endpoint accepts a single day via `fecha`
            params["fecha"] = fechadesde
    try:
        data = get_json(url, user_agent=user_agent, timeout=timeout, params=params,
                        ttl=TTL_BCRA_SERIE)
    except SourceError as exc:
        return error_result(f"BCRA cotizaciones no disponible: {exc}", "BCRA", nota=_TLS_NOTE)

    results = data.get("results") if isinstance(data, dict) else None
    out = []

    def _emit(fecha, detalle):
        for d in detalle or []:
            if not isinstance(d, dict):
                continue
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
            if isinstance(row, dict):
                _emit(row.get("fecha"), row.get("detalle"))
    return {
        "cotizaciones": out,
        "total": len(out),
        "fuente": _FUENTE_CAMB,
    }
