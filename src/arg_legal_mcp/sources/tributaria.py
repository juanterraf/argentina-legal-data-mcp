"""Legislación tributaria — a filtered view of the InfoLEG offline dataset.

There is no separate reliable tax-legislation API; the honest path is the InfoLEG
dataset filtered to tax matters IN SQL (so totals and pagination are exact):

* issued by a tax/customs authority (AFIP, ARCA, DGI, Aduana, Secretaría/Subsecretaría
  de Ingresos Públicos, Tribunal Fiscal, recaudación), or
* whose title clearly refers to taxes (impuesto, tributo, IVA, ganancias, aduanero,
  derechos de exportación/importación, procedimiento fiscal...).

Ministerio de Economía / Hacienda norms only qualify through the title rule. See
``infoleg.dataset.TRIBUTARIA_*`` for the exact lists. Requires the built dataset.
"""

from __future__ import annotations

FUENTE = "InfoLEG dataset filtrado por materia tributaria"
MAX_LIMIT = 50


def _summary(row: dict) -> dict:
    from ..infoleg.texto import limpiar

    tipo = (row.get("tipo_norma") or "").strip()
    numero = (row.get("numero_norma") or "").strip()
    return {
        "id": row.get("id_norma"),
        "identidad": f"{tipo} {numero}".strip(),
        "organismo": row.get("organismo_origen"),
        "fecha_boletin": row.get("fecha_boletin"),
        "fecha_sancion": row.get("fecha_sancion"),
        "titulo": limpiar(row.get("titulo_resumido")),
        "sumario": limpiar(row.get("titulo_sumario")) or limpiar(row.get("texto_resumido")),
    }


def buscar(
    dataset,
    *,
    query: str | None = None,
    tipo: str | None = None,
    desde: str | None = None,
    hasta: str | None = None,
    limit: int = 20,
    offset: int = 0,
) -> dict:
    if not dataset.available():
        return {
            "items": [],
            "total": 0,
            "fuente": FUENTE,
            "nota": "Dataset offline no disponible (se reconstruye periodicamente en el "
                    "servidor). Reintenta mas tarde.",
        }
    try:
        limit = max(1, min(int(limit or 20), MAX_LIMIT))
        offset = max(0, int(offset or 0))
    except (TypeError, ValueError):
        limit, offset = 20, 0
    query = " ".join(str(query or "").split())[:300] or None
    try:
        rows, total = dataset.search(
            texto=query, tipo_norma=tipo or None, desde=desde or None, hasta=hasta or None,
            limit=limit, offset=offset, materia="tributaria",
        )
    except Exception:  # noqa: BLE001 — never crash the tool; return a structured error
        return {
            "items": [],
            "total": 0,
            "error": "Error consultando el dataset de InfoLEG.",
            "fuente": FUENTE,
        }
    return {
        "items": [_summary(r) for r in rows],
        "total": total,
        "offset": offset,
        "limit": limit,
        "hay_mas": offset + len(rows) < total,
        "orden": "relevancia" if query else "recientes",
        "fuente": FUENTE,
        "nota": "Criterio: emitidas por AFIP/ARCA/DGI/Aduana/Ingresos Publicos o con titulo "
                "impositivo (impuestos, tributos, IVA, ganancias, aduana). No exhaustivo ni "
                "vinculante.",
    }
