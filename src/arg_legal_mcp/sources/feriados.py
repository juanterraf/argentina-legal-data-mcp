"""Feriados nacionales from the public ArgentinaDatos API (api.argentinadatos.com)."""

from __future__ import annotations

from .base import SourceError, get_json

BASE = "https://api.argentinadatos.com/v1/feriados"


def feriados(anio: int, *, user_agent: str, timeout: float = 15.0) -> dict:
    """National holidays for a given year."""
    try:
        data = get_json(f"{BASE}/{int(anio)}", user_agent=user_agent, timeout=timeout)
    except SourceError as exc:
        return {"error": f"No se pudieron obtener feriados de {anio}: {exc}",
                "fuente": "api.argentinadatos.com"}
    items = [
        {"fecha": d.get("fecha"), "tipo": d.get("tipo"), "nombre": d.get("nombre")}
        for d in (data if isinstance(data, list) else [])
    ]
    return {
        "anio": int(anio),
        "feriados": items,
        "total": len(items),
        "fuente": "ArgentinaDatos (api.argentinadatos.com)",
    }
