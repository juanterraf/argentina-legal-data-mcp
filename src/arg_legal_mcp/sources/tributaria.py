"""Legislación tributaria — a filtered view of the InfoLEG offline dataset.

There is no separate reliable tax-legislation API; the honest, production-grade path is
the InfoLEG dataset (already built locally) filtered to tax matters by issuing organism /
title keywords. Requires the dataset (``build-dataset``); otherwise returns a clear note.
"""

from __future__ import annotations

TAX_KEYWORDS = [
    "afip", "arca", "dgi", "aduan", "impositiv", "tribut", "fiscal",
    "impuesto", "ingresos publicos", "recaudacion", "econom", "hacienda",
]


def _looks_tax(row: dict) -> bool:
    blob = " ".join(
        str(row.get(k) or "")
        for k in ("organismo_origen", "titulo_resumido", "titulo_sumario", "texto_resumido")
    ).lower()
    return any(kw in blob for kw in TAX_KEYWORDS)


def _summary(row: dict) -> dict:
    tipo = (row.get("tipo_norma") or "").strip()
    numero = (row.get("numero_norma") or "").strip()
    return {
        "id": row.get("id_norma"),
        "identidad": f"{tipo} {numero}".strip(),
        "organismo": row.get("organismo_origen"),
        "fecha_boletin": row.get("fecha_boletin"),
        "titulo": row.get("titulo_resumido"),
        "sumario": row.get("titulo_sumario") or row.get("texto_resumido"),
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
            "fuente": "InfoLEG dataset (legislación tributaria)",
            "nota": "Dataset offline no disponible. Ejecutá la construcción del dataset "
                    "(infoleg_actualizar_dataset o `build-dataset`).",
        }
    # Over-fetch a little so the heuristic tax filter still fills the page.
    try:
        rows, total = dataset.search(
            texto=query, tipo_norma=tipo, desde=desde, hasta=hasta,
            limit=limit * 3, offset=offset,
        )
    except Exception as exc:  # noqa: BLE001 — never crash the tool; return structured error
        return {
            "items": [],
            "total_en_pagina": 0,
            "total_busqueda_sin_filtrar": 0,
            "error": f"Error consultando el dataset: {exc}",
            "fuente": "InfoLEG dataset (legislación tributaria)",
        }
    tax_rows = [r for r in rows if _looks_tax(r)][:limit]
    return {
        "items": [_summary(r) for r in tax_rows],
        "total_en_pagina": len(tax_rows),
        "total_busqueda_sin_filtrar": total,
        "fuente": "InfoLEG dataset filtrado por materia tributaria",
        "nota": "Heurística: normas cuyo organismo/título refieren a materia tributaria "
                "(AFIP/ARCA/DGI/impuestos/aduana). No exhaustivo ni vinculante.",
    }
