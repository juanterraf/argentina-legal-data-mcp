"""Feriados nacionales from the public ArgentinaDatos API (api.argentinadatos.com)."""

from __future__ import annotations

from .base import TTL_FERIADOS, SourceError, error_result, get_json

BASE = "https://api.argentinadatos.com/v1/feriados"
_FUENTE = "api.argentinadatos.com"
ANIO_MIN, ANIO_MAX = 2000, 2100


def feriados(anio: int, *, user_agent: str, timeout: float = 15.0) -> dict:
    """National holidays for a given year (inamovible, trasladable, puente...)."""
    try:
        anio = int(anio)
    except (TypeError, ValueError):
        return error_result("El anio debe ser un entero (ej: 2026).", _FUENTE)
    if not ANIO_MIN <= anio <= ANIO_MAX:
        return error_result(f"Anio fuera de rango ({ANIO_MIN}-{ANIO_MAX}).", _FUENTE)
    try:
        data = get_json(f"{BASE}/{anio}", user_agent=user_agent, timeout=timeout, ttl=TTL_FERIADOS)
    except SourceError as exc:
        return error_result(f"No se pudieron obtener feriados de {anio}: {exc}", _FUENTE)
    if not isinstance(data, list):
        return error_result(f"Respuesta inesperada de ArgentinaDatos para {anio}.", _FUENTE)
    items = [
        {"fecha": d.get("fecha"), "tipo": d.get("tipo"), "nombre": d.get("nombre")}
        for d in data
        if isinstance(d, dict)
    ]
    out = {
        "anio": anio,
        "feriados": items,
        "total": len(items),
        "fuente": "ArgentinaDatos (api.argentinadatos.com)",
        "nota": "Incluye feriados nacionales y dias no laborables con fines turisticos "
        "('puente'). No incluye feriados provinciales ni municipales.",
    }
    if not items:
        out["aviso"] = "La fuente no devolvio feriados para ese anio (puede no estar publicado aun)."
    return out


def feriados_por_fecha(anio: int, *, user_agent: str, timeout: float = 15.0) -> dict:
    """{date: (tipo, nombre)} for a year. Raises SourceError (used by calcular_plazo)."""
    from .base import parse_iso_date

    anio = int(anio)
    if not ANIO_MIN <= anio <= ANIO_MAX:
        raise SourceError(f"anio fuera de rango ({ANIO_MIN}-{ANIO_MAX})")
    try:
        data = get_json(f"{BASE}/{anio}", user_agent=user_agent, timeout=timeout,
                        ttl=TTL_FERIADOS)
    except SourceError as exc:
        if "HTTP 404" in str(exc):  # year not published yet
            return {}
        raise
    if not isinstance(data, list):
        raise SourceError(f"respuesta inesperada de ArgentinaDatos para {anio}")
    out: dict = {}
    for d in data:
        if not isinstance(d, dict):
            continue
        f = parse_iso_date(d.get("fecha"))
        if f is not None:
            out[f] = (d.get("tipo"), d.get("nombre"))
    return out
