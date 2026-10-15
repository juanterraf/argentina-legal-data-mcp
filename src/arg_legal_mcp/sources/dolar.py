"""Dólar — cotizaciones from the public DolarAPI (https://dolarapi.com)."""

from __future__ import annotations

from .base import TTL_DOLAR, SourceError, error_result, get_json

DOLARAPI_URL = "https://dolarapi.com/v1/dolares"
_FUENTE = "dolarapi.com"


def cotizaciones(*, user_agent: str, timeout: float = 15.0) -> dict:
    """Current USD quotes (oficial, blue, MEP, CCL, tarjeta, etc.)."""
    try:
        data = get_json(DOLARAPI_URL, user_agent=user_agent, timeout=timeout, ttl=TTL_DOLAR)
    except SourceError as exc:
        return error_result(f"No se pudieron obtener cotizaciones del dolar: {exc}", _FUENTE)
    if not isinstance(data, list):
        return error_result("Respuesta inesperada de DolarAPI.", _FUENTE)
    cotizaciones_norm = [
        {
            "casa": d.get("casa"),
            "nombre": d.get("nombre"),
            "compra": d.get("compra"),
            "venta": d.get("venta"),
            "actualizado": d.get("fechaActualizacion"),
        }
        for d in data
        if isinstance(d, dict)
    ]
    return {
        "cotizaciones": cotizaciones_norm,
        "total": len(cotizaciones_norm),
        "fuente": "DolarAPI (dolarapi.com)",
        "nota": "Cotizaciones de referencia de un agregador privado; no son valores oficiales "
        "para fines legales o tributarios (para eso, usar bcra_cotizaciones). Cache de 5 min.",
    }
