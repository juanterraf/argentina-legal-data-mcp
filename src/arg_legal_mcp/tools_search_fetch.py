"""OpenAI-convention `search` and `fetch` tools (required by ChatGPT connectors / deep research).

These two read-only tools follow OpenAI's expected shape:
  * search(query) -> { "results": [ { "id", "title", "url" } ] }
  * fetch(id)     -> { "id", "title", "text", "url", "metadata" }

They are backed by the InfoLEG corpus: ``search`` uses the offline dataset ranking;
``fetch`` returns the ficha header plus the norm's TEXT (consolidated if it exists,
otherwise the original), clean of HTML, first fragment + how to read the rest.
Returning a TypedDict makes FastMCP emit both structuredContent and a JSON text block.
"""

from __future__ import annotations

from typing import TypedDict

from .infoleg.models import TipoTexto
from .infoleg.services import InfoLegService
from .infoleg.texto import limpiar

try:
    from mcp.types import ToolAnnotations

    _RO = ToolAnnotations(readOnlyHint=True, idempotentHint=True, openWorldHint=True)
except Exception:  # pragma: no cover
    _RO = None

MAX_QUERY = 300


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


def _title(row: dict) -> str:
    tipo = (row.get("tipo_norma") or "").strip()
    numero = (row.get("numero_norma") or "").strip()
    ident = f"{tipo} {numero}".strip()
    tema = limpiar(row.get("titulo_sumario")) or limpiar(row.get("titulo_resumido")) or ""
    return " — ".join([p for p in (ident, tema) if p]) or f"Norma {row.get('id_norma')}"


def do_search(service: InfoLegService, query: str, limit: int = 10) -> SearchResults:
    query = " ".join(str(query or "").split())[:MAX_QUERY]
    if not query:
        return {"results": []}
    base_results: list[SearchHit] = []
    if service.dataset.available():
        rows, _total = service.dataset.search(texto=query, limit=limit)
        for r in rows:
            rid = r.get("id_norma")
            base_results.append({"id": str(rid), "title": _title(r),
                                 "url": _norma_url(service, rid)})
    else:
        out = service.buscar_normas(texto=query, en_vivo=False, nro_pag=1)
        for r in out.get("resultados", [])[:limit]:
            rid = r.get("id")
            title = " — ".join([p for p in (r.get("identidad_norma"), r.get("tema")) if p])
            base_results.append({"id": str(rid), "title": title or f"Norma {rid}",
                                 "url": _norma_url(service, rid)})
    return {"results": base_results}


def do_fetch(service: InfoLegService, doc_id: str) -> FetchResult:
    raw_id = str(doc_id or "").strip()
    if not raw_id.isdigit() or len(raw_id) > 9:
        return {"id": raw_id[:20], "title": "id invalido",
                "text": "El id debe ser numerico (id_norma de InfoLEG, devuelto por search).",
                "url": "", "metadata": {}}
    nid = int(raw_id)
    url = _norma_url(service, nid)
    row = service.dataset.get_norma(nid)
    if not row:
        return {"id": str(nid), "title": f"Norma {nid} no encontrada en el dataset",
                "text": "", "url": url, "metadata": {}}

    cabecera = [
        p for p in (
            _title(row),
            f"Organismo: {row['organismo_origen']}" if row.get("organismo_origen") else None,
            f"Sancion: {row['fecha_sancion']}" if row.get("fecha_sancion") else None,
            f"Publicacion (Boletin Oficial): {row['fecha_boletin']}"
            if row.get("fecha_boletin") else None,
            limpiar(row.get("texto_resumido")),
            ("Observaciones: " + limpiar(row.get("observaciones")))
            if limpiar(row.get("observaciones")) else None,
        ) if p
    ]

    texto = service.obtener_texto(nid, TipoTexto.ACTUALIZADO)
    metadata = {
        "tipo": (row.get("tipo_norma") or "").strip(),
        "numero": (row.get("numero_norma") or "").strip(),
        "fecha_boletin": row.get("fecha_boletin"),
        "fecha_sancion": row.get("fecha_sancion"),
        "organismo": row.get("organismo_origen"),
        "cantidad_normas_que_la_modifican": (
            int(row["modificada_por"]) if str(row.get("modificada_por") or "").isdigit()
            else None
        ),
    }
    if texto.get("texto"):
        version = "ACTUALIZADO (consolidado)" if texto["tipo_texto"] == "actualizado" \
            else "ORIGINAL"
        cuerpo = f"--- Texto {version} ---\n\n{texto['texto']}"
        if texto.get("aviso"):
            cuerpo = f"[Aviso: {texto['aviso']}]\n\n{cuerpo}"
        if texto.get("hay_mas"):
            tool = ("infoleg_obtener_texto_actualizado" if texto["tipo_texto"] == "actualizado"
                    else "infoleg_obtener_texto_original")
            cuerpo += (
                f"\n\n[Fragmento: caracteres {texto['inicio']}-{texto['fin']} de "
                f"{texto['total_caracteres']}. Para seguir leyendo: "
                f"{tool}(id={nid}, inicio={texto['siguiente_inicio']}).]"
            )
        metadata.update({
            "tipo_texto": texto["tipo_texto"],
            "total_caracteres": texto["total_caracteres"],
            "hay_mas": texto["hay_mas"],
            "siguiente_inicio": texto.get("siguiente_inicio"),
            "url_texto_oficial": texto.get("url_oficial"),
        })
    else:
        cuerpo = ("[El texto completo no esta disponible en este momento"
                  + (f"; texto oficial: {texto['url_oficial']}" if texto.get("url_oficial") else "")
                  + ".]")
        metadata["url_texto_oficial"] = texto.get("url_oficial")

    return {
        "id": str(nid),
        "title": _title(row),
        "text": "\n".join(cabecera) + "\n\n" + cuerpo,
        "url": url,
        "metadata": metadata,
    }


def register_search_fetch(mcp, service: InfoLegService) -> None:
    ro = {"annotations": _RO} if _RO else {}

    @mcp.tool(name="search", **ro)
    def search(query: str) -> SearchResults:
        """Busca legislacion nacional argentina (InfoLEG) por palabras clave o referencia
        ("Ley 20628", "DNU 70/2023", "defensa del consumidor").

        CUANDO USARLA: busqueda rapida estilo ChatGPT/deep research; devuelve
        {"results": [{"id", "title", "url"}]} ordenado por relevancia. Pasa un `id` a
        `fetch` para leer la norma. Para filtros (tipo, organismo, fechas) usa
        infoleg_buscar_normas.
        """
        return do_search(service, query)

    @mcp.tool(name="fetch", **ro)
    def fetch(id: str) -> FetchResult:
        """Trae una norma por su id (de `search`): ficha + TEXTO de la norma (actualizado
        si existe, si no el original), limpio, primer fragmento (~8.000 caracteres).

        CUANDO USARLA: despues de `search`, para leer la norma. Si el texto sigue, el final
        de `text` indica como pedir el resto (infoleg_obtener_texto_*, inicio=N).
        """
        return do_fetch(service, id)
