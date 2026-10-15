"""Registration of the InfoLEG MCP tools and resource.

Docstrings follow a deliberate style (CUANDO USARLA / NO CONFUNDIR / restricciones /
operadores documentados) so the agent picks the right tool and arguments. Every input
is validated/clamped here (lengths, ids, pages, ISO dates) before reaching the service.

All tools are read-only. Dataset maintenance (download + rebuild) is NOT exposed over
MCP: it runs only from the CLI (``python -m arg_legal_mcp build-dataset``) and the
systemd timer.
"""

from __future__ import annotations

import json
from datetime import date

from .disclaimer import with_disclaimer
from .infoleg.models import ModoVinculo, TipoTexto
from .infoleg.services import InfoLegService

try:  # tool annotations are best-effort; older SDKs may lack the type
    from mcp.types import ToolAnnotations

    _RO = ToolAnnotations(readOnlyHint=True, idempotentHint=True, openWorldHint=True)
except Exception:  # pragma: no cover
    _RO = None

MAX_TEXTO = 300
MAX_PAGINA = 1000
MAX_ID = 99_999_999


class ParametroInvalido(ValueError):
    pass


def _err(exc: Exception) -> dict:
    return {"error": str(exc)}


def validar_texto(valor: str | None, campo: str, max_len: int = MAX_TEXTO) -> str | None:
    if valor is None:
        return None
    valor = " ".join(str(valor).split())
    if len(valor) > max_len:
        raise ParametroInvalido(f"'{campo}' es demasiado largo (maximo {max_len} caracteres).")
    return valor or None


def validar_fecha(valor: str | None, campo: str) -> str | None:
    if valor is None or not str(valor).strip():
        return None
    raw = str(valor).strip()
    try:
        if len(raw) != 10:
            raise ValueError
        return date.fromisoformat(raw).isoformat()
    except ValueError:
        raise ParametroInvalido(
            f"'{campo}' debe ser una fecha ISO 'YYYY-MM-DD' (ej: 2024-01-31); recibido {raw[:20]!r}."
        ) from None


def validar_id(valor: int) -> int:
    if not isinstance(valor, int) or not (1 <= valor <= MAX_ID):
        raise ParametroInvalido("'id' debe ser el id numerico de InfoLEG (entero positivo).")
    return valor


def clamp_pagina(nro_pag: int | None) -> int:
    try:
        n = int(nro_pag or 1)
    except (TypeError, ValueError):
        n = 1
    return max(1, min(n, MAX_PAGINA))


def _numero(valor: int | str | None) -> str | None:
    if valor is None or str(valor).strip() == "":
        return None
    raw = str(valor).strip().replace(".", "")
    if not raw.isdigit() or len(raw) > 9:
        raise ParametroInvalido("'numero' debe ser el numero de la norma, sin puntos (ej: 27275).")
    return str(int(raw))


def register_infoleg(mcp, service: InfoLegService, catalogs) -> None:
    ro = {"annotations": _RO} if _RO else {}

    # ── Resource: catalogo de tipos de norma ─────────────────────────────────
    @mcp.resource("infoleg://tipos-norma")
    def tipos_norma_resource() -> str:
        """Catalogo de TODOS los tipos de norma del dataset (nombre + cantidad).

        Usa el `nombre` en el parametro `tipo_norma` de infoleg_buscar_normas (ej:
        'Ley', 'Decreto', 'Resolución', 'Comunicación', 'Decreto/Ley', 'Acordada').
        `id_infoleg` (cuando existe) es el id del buscador en vivo. Devuelve JSON."""
        return json.dumps(catalogs.tipos, ensure_ascii=False, indent=2)

    # ── Búsqueda ──────────────────────────────────────────────────────────---
    @mcp.tool(**ro)
    def infoleg_buscar_normas(
        texto: str | None = None,
        tipo_norma: str | None = None,
        numero: str | None = None,
        anio_sancion: int | None = None,
        organismo: str | None = None,
        publicado_desde: str | None = None,
        publicado_hasta: str | None = None,
        orden: str = "relevancia",
        en_vivo: bool = False,
        nro_pag: int = 1,
    ) -> dict:
        """Busca normas nacionales argentinas (leyes, decretos, DNU, resoluciones,
        disposiciones, comunicaciones, etc.) en InfoLEG.

        CUANDO USARLA: cuando NO conoces el id de la norma, o para ubicar una norma por
        su referencia ("Ley 20628", "Decreto 70/2023", "DNU 70/2023": van primero).
        Si ya tenes el id, usa infoleg_ver_norma.

        ORDEN: por defecto 'relevancia' (prioriza coincidencia en el titulo, normas base
        muy modificadas -p. ej. la Ley 20.628 para 'impuesto a las ganancias'- y leyes
        sobre resoluciones). orden='recientes' ordena por fecha de publicacion.

        MODO: por defecto consulta el DATASET offline (rapido, robusto). en_vivo=true usa
        el buscador real de InfoLEG (mas fresco; exige >= 2 filtros si no hay texto y no
        admite anio_sancion para Leyes); si falla, degrada al dataset y lo dice en `aviso`.

        PARAMETROS:
        - texto: palabras clave (max 300). Operadores: Y/AND, O/OR, NO/NOT, -excluir,
          "frase exacta", prefijo* (ej: 'residu*'). Singular/plural se toleran.
        - tipo_norma: nombre del tipo ('Ley', 'Decreto', 'Resolución', 'Disposición',
          'Decisión Administrativa', 'Comunicación', 'Decreto/Ley', 'Acordada'...; ver
          recurso infoleg://tipos-norma). 'DNU' se busca como 'Decreto'.
        - numero: numero de la norma SIN puntos (27275, no 27.275).
        - anio_sancion: anio de sancion (4 digitos).
        - organismo: organismo emisor (nombre o sigla: 'AFIP', 'BCRA', 'Ministerio de
          Salud'). Para el nombre exacto usa infoleg_buscar_organismos.
        - publicado_desde / publicado_hasta: fechas ISO 'YYYY-MM-DD' de PUBLICACION en el
          Boletin Oficial. Las ~7.000 normas antiguas sin fecha de publicacion se
          filtran por su fecha de sancion (no quedan excluidas).
        - nro_pag: pagina (5 resultados por pagina). Repeti con nro_pag=2, 3...

        DEVUELVE: resultados (id, identidad_norma, organismo, fechas, tema, sumario,
        cantidad_normas_que_la_modifican) + pagina_actual + total_pags +
        total_resultados + fuente (+ aviso si hubo degradacion).
        """
        try:
            texto = validar_texto(texto, "texto")
            tipo = validar_texto(tipo_norma, "tipo_norma", 60)
            org = validar_texto(organismo, "organismo", 150)
            desde = validar_fecha(publicado_desde, "publicado_desde")
            hasta = validar_fecha(publicado_hasta, "publicado_hasta")
            if desde and hasta and desde > hasta:
                raise ParametroInvalido("publicado_desde es posterior a publicado_hasta.")
            if anio_sancion is not None and not (1800 <= anio_sancion <= 2100):
                raise ParametroInvalido("anio_sancion debe tener 4 digitos (ej: 2016).")
            data = service.buscar_normas(
                texto=texto, tipo_norma=tipo, numero=_numero(numero),
                anio_sancion=anio_sancion, organismo=org,
                publicado_desde=desde, publicado_hasta=hasta,
                orden=(orden or "relevancia").strip().lower(),
                en_vivo=bool(en_vivo), nro_pag=clamp_pagina(nro_pag),
            )
        except ValueError as exc:
            return _err(exc)
        return with_disclaimer(data)

    # ── Ficha ────────────────────────────────────────────────────────────────
    @mcp.tool(**ro)
    def infoleg_ver_norma(id: int) -> dict:
        """Ficha completa de una norma por su id de InfoLEG: identidad, organismo,
        fechas de sancion y publicacion, resumen, observaciones, URLs oficiales del texto
        original y actualizado, y cantidad de normas que modifica / que la modifican.

        CUANDO USARLA: cuando ya tenes el id (de infoleg_buscar_normas o search). Para
        leer el articulado usa infoleg_obtener_texto_actualizado.
        """
        try:
            validar_id(id)
        except ValueError as exc:
            return _err(exc)
        return with_disclaimer(service.ver_norma(id))

    # ── Texto ────────────────────────────────────────────────────────────────
    @mcp.tool(**ro)
    def infoleg_obtener_texto_actualizado(
        id: int, inicio: int = 0, fin: int | None = None
    ) -> dict:
        """Texto VIGENTE (consolidado, con las modificaciones aplicadas) de una norma.

        CUANDO USARLA: para leer la norma tal como rige segun InfoLEG. Si InfoLEG no
        tiene version consolidada, devuelve el ORIGINAL y lo avisa en `aviso` (en ese caso
        revisa infoleg_ver_normas_que_la_modifican: puede haber cambios no consolidados).

        PAGINACION: fragmentos de ~8.000 caracteres. Si `hay_mas` es true, volve a llamar
        con inicio=`siguiente_inicio`. `fin` opcional (maximo 40.000 caracteres por pedido).
        """
        try:
            validar_id(id)
        except ValueError as exc:
            return _err(exc)
        return with_disclaimer(
            service.obtener_texto(id, TipoTexto.ACTUALIZADO, max(0, inicio or 0), fin)
        )

    @mcp.tool(**ro)
    def infoleg_obtener_texto_original(
        id: int, inicio: int = 0, fin: int | None = None
    ) -> dict:
        """Texto ORIGINAL de una norma, tal como fue publicada (sin modificaciones).

        CUANDO USARLA: investigacion historica o para ver la redaccion inicial. NO refleja
        lo vigente (para eso, infoleg_obtener_texto_actualizado). Misma paginacion:
        inicio=`siguiente_inicio` mientras `hay_mas` sea true.
        """
        try:
            validar_id(id)
        except ValueError as exc:
            return _err(exc)
        return with_disclaimer(
            service.obtener_texto(id, TipoTexto.ORIGINAL, max(0, inicio or 0), fin)
        )

    # ── Vínculos ─────────────────────────────────────────────────────────────
    @mcp.tool(**ro)
    def infoleg_ver_normas_que_modifica(id: int, nro_pag: int = 1) -> dict:
        """Normas que ESTA norma modifica, deroga o complementa (direccion ACTIVA).

        CUANDO USARLA: para rastrear el impacto de una norma sobre normas anteriores.
        NO CONFUNDIR con infoleg_ver_normas_que_la_modifican (la inversa). Consulta
        InfoLEG en vivo; si no responde, la lista viene VACIA con un `aviso` y la
        cantidad segun el dataset: en ese caso la lista vacia NO significa "ninguna".
        """
        try:
            validar_id(id)
        except ValueError as exc:
            return _err(exc)
        return with_disclaimer(
            service.ver_vinculos(id, ModoVinculo.MODIFICA_A, clamp_pagina(nro_pag))
        )

    @mcp.tool(**ro)
    def infoleg_ver_normas_que_la_modifican(id: int, nro_pag: int = 1) -> dict:
        """Normas que modificaron, derogaron o complementaron a ESTA (direccion PASIVA).

        CUANDO USARLA: para saber si una norma fue alterada o derogada (vigencia).
        NO CONFUNDIR con infoleg_ver_normas_que_modifica (la inversa). Consulta InfoLEG en
        vivo; si no responde, la lista viene VACIA con un `aviso`: NO concluyas que nadie
        la modifico. Paginado (nro_pag).
        """
        try:
            validar_id(id)
        except ValueError as exc:
            return _err(exc)
        return with_disclaimer(
            service.ver_vinculos(id, ModoVinculo.MODIFICADA_POR, clamp_pagina(nro_pag))
        )

    # ── Diff ─────────────────────────────────────────────────────────────────
    @mcp.tool(**ro)
    def infoleg_comparar_original_actualizado(id: int) -> dict:
        """Comparacion MECANICA (diff de lineas, no vinculante) entre el texto original y
        el actualizado de una norma.

        CUANDO USARLA: para ver a grandes rasgos que cambio entre la publicacion y la
        version consolidada. Es un insumo: la conclusion juridica requiere leer los textos.
        """
        try:
            validar_id(id)
        except ValueError as exc:
            return _err(exc)
        return with_disclaimer(service.comparar_textos(id))

    # ── Organismos ───────────────────────────────────────────────────────────
    @mcp.tool(**ro)
    def infoleg_buscar_organismos(query: str, limit: int = 10) -> dict:
        """Busca organismos emisores por nombre o sigla (tolerante a tildes y typos) y
        devuelve el nombre EXACTO con que InfoLEG los registra + cantidad de normas.

        CUANDO USARLA: antes de filtrar infoleg_buscar_normas por `organismo` si no sabes
        como lo nombra InfoLEG. Ej: 'AFIP', 'ministerio de salud', 'ANSES', 'BCRA'.
        """
        try:
            q = validar_texto(query, "query", 150)
        except ValueError as exc:
            return _err(exc)
        if not q:
            return {"error": "Indica 'query' (nombre o sigla del organismo)."}
        limit = max(1, min(int(limit or 10), 50))
        return {"resultados": catalogs.buscar_organismos(q, limit)}
