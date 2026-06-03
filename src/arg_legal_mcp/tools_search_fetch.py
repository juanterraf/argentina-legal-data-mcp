"""OpenAI-convention `search` and `fetch` tools (required by ChatGPT connectors / deep research).

These two read-only tools follow OpenAI's expected shape:
  * search(query) -> { "results": [ { "id", "title", "url" } ] }
  * fetch(id)     -> { "id", "title", "text", "url", "metadata" }

They are backed by the offline InfoLEG corpus (the richest dataset here). claude.ai also
benefits, and it doubles as a generic "buscar + traer documento" pair. Returning a dict
makes FastMCP emit both structuredContent and a JSON text block, as OpenAI expects.
"""

from __future__ import annotations

from typing import TypedDict

from .infoleg.services import InfoLegService

try:
    from mcp.types import ToolAnnotations

    _RO = ToolAnnotations(readOnlyHint=True, openWorldHint=True)
except Exception:  # pragma: no cover
    _RO = None


# Typed returns so FastMCP emits `structuredContent` (OpenAI deep-research expects it).
class SearchHit(TypedDict):
    id: str
    title: str
    url: str


class SearchResults(TypedDict):
    results: list[SearchHit]


class FetchResult(TypedDict):
    id: str
    title: str
    text: str
    url: str
    metadata: dict


def _norma_url(service: InfoLegService, norma_id: int) -> str:
    return f"{service.s.infoleg_base_url.rstrip('/')}/verNorma.do?id={norma_id}"


def do_search(service: InfoLegService, query: str, limit: int = 10) -> SearchResults:
    base_results: list[dict] = []
    if service.dataset.available():
        rows, _total = service.dataset.search(texto=query, limit=limit)
        for r in rows:
            rid = r.get("id_norma")
            tipo = (r.get("tipo_norma") or "").strip()
            numero = (r.get("numero_norma") or "").strip()
            ident = f"{tipo} {numero}".strip()
            tema = r.get("titulo_resumido") or r.get("titulo_sumario") or ""
            title = " — ".join([p for p in (ident, tema) if p]) or f"Norma {rid}"
            base_results.append({"id": str(rid), "title": title, "url": _norma_url(service, rid)})
    else:
        out = service.buscar_normas(texto=query, en_vivo=False, nro_pag=1)
        for r in out.get("resultados", [])[:limit]:
            rid = r.get("id")
            title = " — ".join([p for p in (r.get("identidad_norma"), r.get("tema")) if p])
            base_results.append({"id": str(rid), "title": title or f"Norma {rid}",
                                 "url": _norma_url(service, rid)})
    return {"results": base_results}


def do_fetch(service: InfoLegService, doc_id: str) -> FetchResult:
    try:
        nid = int(str(doc_id).strip())
    except (ValueError, TypeError):
        return {"id": str(doc_id), "title": "id invalido",
                "text": "El id debe ser numerico (id_norma de InfoLEG).", "url": "", "metadata": {}}
    url = _norma_url(service, nid)
    row = service.dataset.get_norma(nid)
    if not row:
        return {"id": str(nid), "title": f"Norma {nid} no encontrada en el dataset",
                "text": "", "url": url, "metadata": {}}
    tipo = (row.get("tipo_norma") or "").strip()
    numero = (row.get("numero_norma") or "").strip()
    title = f"{tipo} {numero}".strip() or f"Norma {nid}"
    parts = [p for p in (row.get("titulo_resumido"), row.get("titulo_sumario"),
                         row.get("texto_resumido"), row.get("observaciones")) if p]
    text = "\n\n".join(parts) or "(El dataset no incluye resumen; ver el texto completo en la URL.)"
    return {
        "id": str(nid),
        "title": title,
        "text": text,
        "url": url,
        "metadata": {
            "tipo": tipo,
            "numero": numero,
            "fecha_boletin": row.get("fecha_boletin"),
            "fecha_sancion": row.get("fecha_sancion"),
            "organismo": row.get("organismo_origen"),
        },
    }


def register_search_fetch(mcp, service: InfoLegService) -> None:
    ro = {"annotations": _RO} if _RO else {}

    @mcp.tool(name="search", **ro)
    def search(query: str) -> SearchResults:
        """Search Argentine national legislation (InfoLEG) by keywords.

        Returns {"results": [{"id", "title", "url"}]}. Pass an `id` to `fetch` to get the
        document's content. (OpenAI deep-research / ChatGPT connector convention.)
        """
        return do_search(service, query)

    @mcp.tool(name="fetch", **ro)
    def fetch(id: str) -> FetchResult:
        """Fetch a legislation document by its id (id_norma from `search`).

        Returns {"id", "title", "text", "url", "metadata"} with a citable official URL.
        """
        return do_fetch(service, id)
