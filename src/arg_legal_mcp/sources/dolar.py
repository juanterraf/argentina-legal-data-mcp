"""Dólar — cotizaciones from the public DolarAPI (https://dolarapi.com)."""

from __future__ import annotations

from .base import SourceError, get_json

DOLARAPI_URL = "https://dolarapi.com/v1/dolares"


def cotizaciones(*, user_agent: str, timeout: float = 15.0) -> dict:
    """Current USD quotes (oficial, blue, MEP, CCL, tarjeta, etc.)."""
    try:
        data = get_json(DOLARAPI_URL, user_agent=user_agent, timeout=timeout)
    except SourceError as exc:
        return {"error": f"No se pudieron obtener cotizaciones del dolar: {exc}",
                "fuente": "dolarapi.com"}
    cotizaciones_norm = [
        {
            "casa": d.get("casa"),
            "nombre": d.get("nombre"),
            "compra": d.get("compra"),
            "venta": d.get("venta"),
            "actualizado": d.get("fechaActualizacion"),
        }
        for d in (data if isinstance(data, list) else [])
    ]
    return {
        "cotizaciones": cotizaciones_norm,
        "total": len(cotizaciones_norm),
        "fuente": "DolarAPI (dolarapi.com)",
    }
