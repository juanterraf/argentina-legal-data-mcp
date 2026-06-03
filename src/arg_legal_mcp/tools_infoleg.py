"""Registration of the InfoLEG MCP tools, resource and helpers.

Docstrings follow a deliberate style (CUANDO USARLA / NO CONFUNDIR / restrictions /
documented operators) so the agent picks the right tool and arguments.
"""

from __future__ import annotations

import json

from .disclaimer import with_disclaimer
from .infoleg.models import ModoVinculo, TipoTexto
from .infoleg.services import InfoLegService

try:  # tool annotations are best-effort; older SDKs may lack the type
    from mcp.types import ToolAnnotations

    _RO = ToolAnnotations(readOnlyHint=True, openWorldHint=True)
    _WRITE = ToolAnnotations(readOnlyHint=False, openWorldHint=True)
except Exception:  # pragma: no cover
    _RO = None
    _WRITE = None


def _err(value_error: Exception) -> dict:
    return {"error": str(value_error)}


def register_infoleg(mcp, service: InfoLegService, catalogs) -> None:
    ro = {"annotations": _RO} if _RO else {}
    write = {"annotations": _WRITE} if _WRITE else {}

    # ── Resource: catalogo de tipos de norma ─────────────────────────────────
    @mcp.resource("infoleg://tipos-norma")
    def tipos_norma_resource() -> str:
        """Catalogo de tipos de norma (id + nombre).

        Consultalo ANTES de usar `tipo_norma` en infoleg_buscar_normas (ej: 1=Ley,
        2=Decreto). Devuelve JSON."""
        return json.dumps(catalogs.tipos, ensure_ascii=False, indent=2)

    # ── Búsqueda ──────────────────────────────────────────────────────────---
    @mcp.tool(**ro)
    def infoleg_buscar_normas(
        texto: str | None = None,
        tipo_norma: int | None = None,
        numero: int | None = None,
        anio_sancion: int | None = None,
        dependencia: int | None = None,
        publicado_desde: str | None = None,
        publicado_hasta: str | None = None,
        en_vivo: bool = False,
        nro_pag: int = 1,
    ) -> dict:
        """Busca normas nacionales (Leyes, Decretos, Resoluciones, Disposiciones...).

        CUANDO USARLA: cuando NO conoces el id de la norma. Si ya tenes el id, usa
        infoleg_ver_norma. Resultados ordenados por recencia.

        MODO: por defecto consulta el DATASET offline (robusto). Con en_vivo=true usa
        el buscador real de InfoLEG (operadores nativos, datos frescos); si el vivo
        falla, degrada al dataset y lo avisa en el campo `aviso`.

        RESTRICCIONES:
        - Se requieren >= 2 parametros, EXCEPTO si usas `texto` (puede ir solo).
        - Para Leyes (tipo_norma=1) NO indiques anio_sancion.
        - Numeros SIN puntos: 27275, no 27.275.

        PARAMETROS:
        - texto: palabras clave. Operadores: Y/AND, O/OR, NO/NOT, +obligatoria,
          -excluir, "frase exacta", comodin * (prefijo). Ej: 'residu*',
          '"transporte de carga"', 'exporta* AND bienes'.
        - tipo_norma: id del tipo (ver recurso infoleg://tipos-norma).
        - numero / anio_sancion: numero de norma y anio (4 digitos).
        - dependencia: id del organismo (usa infoleg_buscar_dependencias para hallarlo).
        - publicado_desde / publicado_hasta: rango de PUBLICACION en Boletin, ISO
          'YYYY-MM-DD'. (Ojo: publicacion, no sancion.)
        - en_vivo: true para el buscador real de InfoLEG.
        - nro_pag: pagina virtual (paginacion del MCP). Repeti con nro_pag=2,3...

        DEVUELVE: resultados + pagina_actual + total_pags + total_resultados + fuente.
        """
        try:
            data = service.buscar_normas(
                texto=texto, tipo_norma=tipo_norma, numero=numero,
                anio_sancion=anio_sancion, dependencia=dependencia,
                publicado_desde=publicado_desde, publicado_hasta=publicado_hasta,
                en_vivo=en_vivo, nro_pag=nro_pag,
            )
        except ValueError as exc:
            return _err(exc)
        return with_disclaimer(data)

    # ── Ficha ────────────────────────────────────────────────────────────────
    @mcp.tool(**ro)
    def infoleg_ver_norma(id: int) -> dict:
        """Metadatos completos de una norma por su id de InfoLEG.

        CUANDO USARLA: cuando ya tenes el id (de infoleg_buscar_normas o
        infoleg_resolver_id). Incluye urls de texto, fechas y conteo de vinculos.
        """
        return with_disclaimer(service.ver_norma(id))

    @mcp.tool(**ro)
    def infoleg_resolver_id(tipo: str, numero: int) -> dict:
        """Resuelve "tipo + numero" (ej: 'Ley' 27275) a su id_norma usando el dataset.

        CUANDO USARLA: para convertir una referencia humana ('Ley 27275') en el id
        numerico que usan las demas tools. Devuelve {id_norma} o un error si no existe.
        """
        rid = service.dataset.resolve_id(tipo, numero)
        if rid is None:
            return {"error": f"No se encontro {tipo} {numero} en el dataset offline.",
                    "sugerencia": "Proba infoleg_buscar_normas con en_vivo=true."}
        return with_disclaimer({"id_norma": rid, "tipo": tipo, "numero": numero})

    # ── Texto ────────────────────────────────────────────────────────────────
    @mcp.tool(**ro)
    def infoleg_obtener_texto_actualizado(
        id: int, inicio: int = 0, fin: int | None = None
    ) -> dict:
        """Texto VIGENTE de una norma (con modificaciones aplicadas), paginado.

        CUANDO USARLA: para conocer la ley tal cual rige hoy. Si no hay version
        actualizada, devuelve la ORIGINAL avisando. Lee la URL REAL de la ficha o el
        dataset (nunca calcula la carpeta de anexos).

        PAGINACION: por defecto fragmentos grandes. Usa inicio/fin (o el campo
        `siguiente_inicio`) para seguir leyendo.
        """
        return with_disclaimer(
            service.obtener_texto(id, TipoTexto.ACTUALIZADO, inicio, fin)
        )

    @mcp.tool(**ro)
    def infoleg_obtener_texto_original(
        id: int, inicio: int = 0, fin: int | None = None
    ) -> dict:
        """Texto ORIGINAL de una norma, tal como fue sancionada (paginado).

        CUANDO USARLA: investigacion historica / redaccion inicial. NO refleja
        necesariamente lo vigente (para eso usa infoleg_obtener_texto_actualizado).
        """
        return with_disclaimer(
            service.obtener_texto(id, TipoTexto.ORIGINAL, inicio, fin)
        )

    # ── Vínculos ─────────────────────────────────────────────────────────────
    @mcp.tool(**ro)
    def infoleg_ver_normas_que_modifica(id: int, nro_pag: int = 1) -> dict:
        """Normas que ESTA norma modifica, deroga o complementa (direccion ACTIVA).

        CUANDO USARLA: para rastrear el impacto de una norma sobre normas anteriores.
        NO CONFUNDIR con infoleg_ver_normas_que_la_modifican (la inversa).
        """
        return with_disclaimer(service.ver_vinculos(id, ModoVinculo.MODIFICA_A, nro_pag))

    @mcp.tool(**ro)
    def infoleg_ver_normas_que_la_modifican(id: int, nro_pag: int = 1) -> dict:
        """Normas que modificaron/derogaron/complementaron a ESTA (direccion PASIVA).

        CUANDO USARLA: para saber si una norma sigue vigente o fue alterada.
        NO CONFUNDIR con infoleg_ver_normas_que_modifica (la inversa).
        """
        return with_disclaimer(service.ver_vinculos(id, ModoVinculo.MODIFICADA_POR, nro_pag))

    # ── Diff ─────────────────────────────────────────────────────────────────
    @mcp.tool(**ro)
    def infoleg_comparar_original_actualizado(id: int) -> dict:
        """Comparacion MECANICA (no vinculante) entre el texto original y el actualizado.

        CUANDO USARLA: para ver, a grandes rasgos, que cambio entre la sancion y la
        version vigente. Es un insumo: la conclusion juridica requiere leer los textos.
        """
        return with_disclaimer(service.comparar_textos(id))

    # ── Dependencias ─────────────────────────────────────────────────────────
    @mcp.tool(**ro)
    def infoleg_buscar_dependencias(query: str, limit: int = 10) -> dict:
        """Busca organismos/dependencias por nombre (fuzzy, tolerante a tildes/typos).

        CUANDO USARLA: ANTES de filtrar infoleg_buscar_normas por `dependencia`, para
        obtener el id del organismo. Ej: 'ministerio de salud', 'AFIP', 'ANSES'.
        """
        return {"resultados": catalogs.buscar_dependencias(query, limit)}

    @mcp.tool(**ro)
    def infoleg_get_dependencia_by_id(id: int) -> dict:
        """Datos de una dependencia por su id exacto. Si no sabes el id, usa
        infoleg_buscar_dependencias."""
        dep = catalogs.get_dependencia_by_id(id)
        return dep or {"error": f"No existe dependencia con id {id}."}

    # ── Dataset ──────────────────────────────────────────────────────────────
    @mcp.tool(**ro)
    def infoleg_estado_dataset() -> dict:
        """Estado del dataset offline de InfoLEG (disponibilidad y cantidad de normas)."""
        disponible = service.dataset.available()
        return {
            "disponible": disponible,
            "cantidad_normas": service.dataset.count() if disponible else 0,
            "ruta": service.dataset.db_path,
            "fecha_construccion": service.dataset.get_meta("built_at") if disponible else None,
        }

    @mcp.tool(**write)
    def infoleg_actualizar_dataset(confirmar: bool = False) -> dict:
        """Descarga el ZIP oficial (datos.jus.gob.ar) y reconstruye el dataset SQLite.

        OPERACION PESADA y de RED (cientos de MB, varios minutos). Requiere
        confirmar=true para ejecutarse. Bloquea hasta terminar.
        """
        if not confirmar:
            return {"aviso": "Operacion pesada. Volve a llamar con confirmar=true para descargar "
                             "y reconstruir el dataset desde datos.jus.gob.ar."}
        from .infoleg.dataset import download_and_build

        count = download_and_build(service.dataset.db_path, user_agent=service.s.user_agent)
        return {"ok": True, "cantidad_normas": count, "ruta": service.dataset.db_path}
