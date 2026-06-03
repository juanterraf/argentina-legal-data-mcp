"""Boletín Oficial — search by date.

Honest reality (verified 2026-06-03): the NATIONAL Boletín Oficial
(boletinoficial.gob.ar) exposes JSON only to its own browser frontend; an F5 WAF blocks
non-browser clients, so there is no stable national JSON API. The reliable, clean,
no-auth JSON option is the **Buenos Aires CITY** bulletin (a different institution):
``api-restboletinoficial.buenosaires.gob.ar``.

So ``jurisdiccion="caba"`` returns real JSON; ``jurisdiccion="nacional"`` returns an
honest pointer to the official HTML search / InfoLEG instead of a flaky scrape.
"""

from __future__ import annotations

from datetime import datetime

from .base import SourceError, get_json

BA_BASE = "https://api-restboletinoficial.buenosaires.gob.ar"


def _to_ddmmyyyy(fecha_iso: str) -> str | None:
    """Validate an ISO 'YYYY-MM-DD' date and render it zero-padded as 'DD-MM-YYYY'."""
    try:
        dt = datetime.strptime((fecha_iso or "").strip(), "%Y-%m-%d")
    except ValueError:
        return None
    return dt.strftime("%d-%m-%Y")


def buscar(
    *,
    fecha: str | None = None,
    jurisdiccion: str = "caba",
    user_agent: str,
    timeout: float = 20.0,
) -> dict:
    if jurisdiccion == "nacional":
        return {
            "jurisdiccion": "nacional",
            "error": "El Boletín Oficial NACIONAL no expone una API JSON pública estable "
                     "(bloqueo WAF para clientes no-navegador).",
            "alternativas": [
                "Usá jurisdiccion='caba' (API JSON de la Ciudad de Buenos Aires).",
                "Búsqueda oficial (HTML): https://www.argentina.gob.ar/normativa/buscar-boletin",
                "Para normas nacionales publicadas, usá las tools de InfoLEG.",
            ],
            "fuente": "boletinoficial.gob.ar",
        }

    # CABA — real JSON API.
    if not fecha:
        return {"error": "Indicá 'fecha' (YYYY-MM-DD) para consultar el boletín de CABA.",
                "fuente": "Boletín Oficial CABA"}
    dd = _to_ddmmyyyy(fecha)
    if not dd:
        return {"error": "Formato de fecha inválido; usá YYYY-MM-DD.",
                "fuente": "Boletín Oficial CABA"}
    try:
        data = get_json(
            f"{BA_BASE}/obtenerSeccionesBoletin/{dd}", user_agent=user_agent, timeout=timeout
        )
    except SourceError as exc:
        return {"error": f"Boletín CABA no disponible: {exc}", "fuente": "Boletín Oficial CABA"}

    secciones = [
        {
            "superseccion_id": s.get("superseccion_id"),
            "nombre": s.get("nombre"),
            "descripcion": s.get("descripcion"),
            "numero_boletin": s.get("numero_boletin"),
            "documento": s.get("url_documento"),
        }
        for s in (data if isinstance(data, list) else [])
    ]
    return {
        "jurisdiccion": "caba",
        "fecha": fecha,
        "secciones": secciones,
        "total": len(secciones),
        "fuente": "Boletín Oficial de la Ciudad de Buenos Aires",
    }
