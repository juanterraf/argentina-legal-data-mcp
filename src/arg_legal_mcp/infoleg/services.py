"""Service layer: hybrid dataset+live orchestration, pagination, chunking, caching.

Default behaviour is dataset-first (robust, offline). Live mode adds depth/freshness
(native operators, real full text, structured vinculos). Whenever live fails we
degrade to the dataset and surface an ``aviso`` in the response.
"""

from __future__ import annotations

import difflib
import math

import httpx

from .anexos import computed_anexo_url, resolve_anexo_url
from .cache import InfoLegCache
from .catalogs import CatalogService
from .client import InfoLegClient
from .dataset import DatasetStore
from .models import (
    BusquedaNormaRequest,
    ModoDesplazamiento,
    ModoVinculo,
    PaginacionRequest,
    ParamsVerNorma,
    ParamsVerVinculos,
    TipoTexto,
)
from .parsers import NormaNotFoundError
from .session import SessionManager

_LIVE_ERRORS = (httpx.HTTPError, OSError)


class Paginator:
    """Two-level pagination: real pages (InfoLEG, 50) -> virtual pages (MCP, small)."""

    def __init__(self, virtual_page_size: int, real_page_size: int):
        self.v = max(1, virtual_page_size)
        self.r = max(1, real_page_size)

    def page(self, virtual_page: int, fetch_real_page) -> dict:
        virtual_page = max(1, virtual_page)
        real_page = ((virtual_page - 1) * self.v) // self.r + 1
        offset = ((virtual_page - 1) * self.v) % self.r
        items, total = fetch_real_page(real_page)
        total_virtual = math.ceil(total / self.v) if total else (1 if items else 0)
        return {
            "items": items[offset : offset + self.v],
            "pagina_actual": virtual_page,
            "total_pags": max(total_virtual, 1),
            "total_resultados": total,
        }


class InfoLegService:
    def __init__(
        self,
        *,
        settings,
        client: InfoLegClient,
        session_manager: SessionManager,
        dataset: DatasetStore,
        cache: InfoLegCache | None,
        catalogs: CatalogService,
    ):
        self.s = settings
        self.client = client
        self.sm = session_manager
        self.dataset = dataset
        self.cache = cache
        self.catalogs = catalogs
        self.paginator = Paginator(settings.mcp_page_size, settings.infoleg_page_size)

    # ── shaping helpers ───────────────────────────────────────────────────---
    @staticmethod
    def _row_to_summary(row: dict) -> dict:
        tipo = (row.get("tipo_norma") or "").strip()
        numero = (row.get("numero_norma") or "").strip()
        identidad = f"{tipo} {numero}".strip() or (tipo or f"id {row.get('id_norma')}")
        return {
            "id": row.get("id_norma"),
            "identidad_norma": identidad,
            "organismo_emisor": row.get("organismo_origen"),
            "fecha_publicacion": row.get("fecha_boletin"),
            "tema": row.get("titulo_resumido"),
            "sumario": row.get("titulo_sumario") or row.get("texto_resumido"),
        }

    def _row_to_vernorma(self, row: dict) -> dict:
        return {
            "summary": self._row_to_summary(row),
            "fecha_emision": row.get("fecha_sancion"),
            "pagina_boletin": row.get("pagina_boletin"),
            "url_texto_completo": None,
            "url_texto_actualizado": None,
            "texto_disponible_en_dataset": {
                "original": bool(row.get("texto_original")),
                "actualizado": bool(row.get("texto_actualizado")),
            },
        }

    # ── búsqueda ────────────────────────────────────────────────────────────
    def buscar_normas(
        self,
        *,
        tipo_norma: int | None = None,
        numero: int | None = None,
        anio_sancion: int | None = None,
        texto: str | None = None,
        dependencia: int | None = None,
        publicado_desde: str | None = None,
        publicado_hasta: str | None = None,
        en_vivo: bool = False,
        nro_pag: int = 1,
    ) -> dict:
        # Validation (matches the live form's real constraints).
        provided = sum(
            p is not None
            for p in (tipo_norma, numero, anio_sancion, dependencia, publicado_desde, publicado_hasta)
        )
        if not texto and provided < 2:
            raise ValueError(
                "Se requieren al menos 2 parametros de busqueda (salvo que uses 'texto')."
            )
        if tipo_norma == 1 and anio_sancion is not None:
            raise ValueError("Para Leyes (tipo_norma=1) no se debe indicar anio_sancion.")

        mcp_page = nro_pag if nro_pag and nro_pag > 0 else 1

        if en_vivo:
            try:
                return self._buscar_vivo(
                    tipo_norma, numero, anio_sancion, texto, dependencia,
                    publicado_desde, publicado_hasta, mcp_page,
                )
            except _LIVE_ERRORS as exc:
                result = self._buscar_dataset(
                    tipo_norma, numero, anio_sancion, texto, dependencia,
                    publicado_desde, publicado_hasta, mcp_page,
                )
                result["fuente"] = "dataset (degradado)"
                result["aviso"] = f"Busqueda en vivo no disponible ({exc}); se uso el dataset offline."
                return result

        # dataset-first (default)
        if not self.dataset.available():
            try:
                result = self._buscar_vivo(
                    tipo_norma, numero, anio_sancion, texto, dependencia,
                    publicado_desde, publicado_hasta, mcp_page,
                )
                result["aviso"] = "Dataset offline no disponible; se uso la busqueda en vivo."
                return result
            except _LIVE_ERRORS as exc:
                return {
                    "resultados": [], "pagina_actual": mcp_page, "total_pags": 1,
                    "total_resultados": 0, "fuente": "ninguna",
                    "aviso": f"Sin dataset offline y la busqueda en vivo fallo ({exc}). "
                             "Ejecuta la construccion del dataset (infoleg_actualizar_dataset).",
                }
        return self._buscar_dataset(
            tipo_norma, numero, anio_sancion, texto, dependencia,
            publicado_desde, publicado_hasta, mcp_page,
        )

    def _buscar_dataset(self, tipo_norma, numero, anio, texto, dependencia,
                        desde, hasta, mcp_page) -> dict:
        tipo_nombre = self.catalogs.tipo_nombre(tipo_norma) if tipo_norma else None
        organismo = self.catalogs.dependencia_nombre(dependencia) if dependencia else None
        page_size = self.s.mcp_page_size
        offset = (mcp_page - 1) * page_size
        rows, total = self.dataset.search(
            texto=texto, tipo_norma=tipo_nombre, numero=numero, anio=anio,
            organismo=organismo, desde=desde, hasta=hasta, limit=page_size, offset=offset,
        )
        return {
            "resultados": [self._row_to_summary(r) for r in rows],
            "pagina_actual": mcp_page,
            "total_pags": max(math.ceil(total / page_size) if total else 1, 1),
            "total_resultados": total,
            "fuente": "dataset",
        }

    def _buscar_vivo(self, tipo_norma, numero, anio, texto, dependencia,
                     desde, hasta, mcp_page) -> dict:
        request = BusquedaNormaRequest(
            tipoNorma=tipo_norma, numero=numero, anio_sancion=anio, texto=texto,
            dependencia=dependencia,
            diaPubDesde=_d(desde, 2), mesPubDesde=_d(desde, 1), anioPubDesde=_d(desde, 0),
            diaPubHasta=_d(hasta, 2), mesPubHasta=_d(hasta, 1), anioPubHasta=_d(hasta, 0),
        )
        payload = request.model_dump(exclude_none=True)

        def fetch_real_page(real_page: int):
            s = self.sm.get_search_session(payload)
            if s.first_request:
                res = self.client.buscar_normas(s.client, request)
                self.sm.set_search_pages(payload, res.total_pags)
                if real_page <= 1:
                    return res.resultados, res.total
                s = self.sm.get_search_session(payload)
            if real_page > 1:
                target = min(real_page, s.total_pags or real_page)
                preq = PaginacionRequest(
                    irAPagina=target, desplazamiento=ModoDesplazamiento.AVANZAR
                )
                res = self.client.navegar_normas(s.client, preq)
            else:
                res = self.client.buscar_normas(s.client, request)
            return res.resultados, res.total

        page = self.paginator.page(mcp_page, fetch_real_page)
        return {
            "resultados": [r.model_dump(mode="json") for r in page["items"]],
            "pagina_actual": page["pagina_actual"],
            "total_pags": page["total_pags"],
            "total_resultados": page["total_resultados"],
            "fuente": "vivo",
        }

    # ── ficha ────────────────────────────────────────────────────────────────
    def _ficha_dict(self, id_norma: int) -> dict | None:
        """Live ficha (cached) as a dict, or None if unavailable. Raises NormaNotFound."""
        if self.cache:
            cached = self.cache.get_norma(id_norma)
            if cached is not None:
                return cached
        client = self.sm.get_client()
        resp = self.client.ver_norma(client, ParamsVerNorma(id=id_norma))
        data = resp.model_dump(mode="json")
        if self.cache:
            self.cache.set_norma(id_norma, data)
        return data

    def ver_norma(self, id_norma: int) -> dict:
        try:
            data = self._ficha_dict(id_norma)
            return {**data, "fuente": "vivo"}
        except NormaNotFoundError:
            row = self.dataset.get_norma(id_norma)
            if row:
                return {**self._row_to_vernorma(row), "fuente": "dataset"}
            return {"error": f"No existe una norma con id {id_norma} en InfoLEG.", "id": id_norma}
        except _LIVE_ERRORS as exc:
            row = self.dataset.get_norma(id_norma)
            if row:
                return {
                    **self._row_to_vernorma(row),
                    "fuente": "dataset (degradado)",
                    "aviso": f"Ficha en vivo no disponible ({exc}); datos del dataset offline.",
                }
            return {"error": f"InfoLEG no disponible ({exc}) y la norma {id_norma} "
                             "no esta en el dataset offline.", "id": id_norma}

    # ── texto ────────────────────────────────────────────────────────────────
    def _full_text_live(self, id_norma: int, tipo: TipoTexto) -> str | None:
        if self.cache:
            cached = self.cache.get_texto(id_norma, tipo.value)
            if cached is not None:
                return cached
        ficha = self._ficha_dict(id_norma)
        if not ficha:
            return None
        key = "url_texto_actualizado" if tipo == TipoTexto.ACTUALIZADO else "url_texto_completo"
        rel = ficha.get(key)
        if not rel:
            return None
        abs_url = resolve_anexo_url(self.s.infoleg_base_url, rel)
        texto = self.client.consultar_anexo(self.sm.get_client(), abs_url)
        if self.cache and texto:
            self.cache.set_texto(id_norma, tipo.value, texto)
        return texto

    def _full_text_dataset(self, id_norma: int, tipo: TipoTexto) -> str | None:
        row = self.dataset.get_norma(id_norma)
        if not row:
            return None
        col = "texto_actualizado" if tipo == TipoTexto.ACTUALIZADO else "texto_original"
        raw = row.get(col)
        if not raw:
            return None
        if "<" in raw and ">" in raw:  # looks like HTML
            from bs4 import BeautifulSoup
            from markdownify import markdownify as md
            soup = BeautifulSoup(raw, "lxml")
            for tag in soup(["script", "style"]):
                tag.decompose()
            return md(str(soup), heading_style="ATX").strip()
        return raw.strip()

    def obtener_texto(
        self, id_norma: int, tipo: TipoTexto, inicio: int = 0, fin: int | None = None
    ) -> dict:
        fuente = None
        aviso = None
        tipo_efectivo = tipo
        texto: str | None = None

        try:
            texto = self._full_text_live(id_norma, tipo)
            if texto is not None:
                fuente = "vivo"
        except NormaNotFoundError:
            return {"error": f"No existe una norma con id {id_norma}.", "id": id_norma}
        except _LIVE_ERRORS as exc:
            aviso = f"Texto en vivo no disponible ({exc}); se intenta el dataset offline."

        if texto is None:
            texto = self._full_text_dataset(id_norma, tipo)
            if texto is not None:
                fuente = "dataset" if fuente is None else fuente

        # If asked for actualizado but only original exists, degrade with notice.
        if texto is None and tipo == TipoTexto.ACTUALIZADO:
            texto = self._full_text_dataset(id_norma, TipoTexto.ORIGINAL)
            if texto is not None:
                tipo_efectivo = TipoTexto.ORIGINAL
                fuente = "dataset"
                aviso = "No hay texto actualizado; se devuelve el texto ORIGINAL del dataset."

        if texto is None:
            unverified = computed_anexo_url(self.s.infoleg_base_url, id_norma, tipo)
            return {
                "id": id_norma,
                "error": f"No se pudo obtener el texto {tipo.value} de la norma {id_norma}.",
                "url_calculada_no_verificada": unverified,
                "aviso": "URL calculada con bloques de 5000 (NO verificada). "
                         "Preferi la URL real de la ficha; esta puede no existir.",
            }

        chunk = self._chunk(texto, inicio, fin)
        return {
            "id": id_norma,
            "tipo_texto": tipo_efectivo.value,
            "fuente": fuente,
            "aviso": aviso,
            **chunk,
        }

    def _chunk(self, texto: str, inicio: int = 0, fin: int | None = None) -> dict:
        total = len(texto)
        size = self.s.text_chunk_size
        inicio = max(0, inicio)
        if fin is None:
            fin = inicio + size
        fin = min(fin, total)
        if inicio >= total:
            return {
                "texto": "", "total_caracteres": total, "inicio": inicio, "fin": total,
                "hay_mas": False,
                "nota": f"inicio ({inicio}) >= total ({total}); no hay mas texto.",
            }
        return {
            "texto": texto[inicio:fin],
            "total_caracteres": total,
            "inicio": inicio,
            "fin": fin,
            "hay_mas": fin < total,
            "siguiente_inicio": fin if fin < total else None,
        }

    # ── vínculos ───────────────────────────────────────────────────────────--
    def ver_vinculos(self, id_norma: int, modo: ModoVinculo, nro_pag: int = 1) -> dict:
        mcp_page = nro_pag if nro_pag and nro_pag > 0 else 1
        fuente = "vivo"
        aviso = None
        vinculos: list[dict] = []
        try:
            if self.cache:
                cached = self.cache.get_vinculos(id_norma, int(modo))
            else:
                cached = None
            if cached is not None:
                vinculos = cached
                fuente = "vivo (cache)"
            else:
                resp = self.client.ver_vinculos(
                    self.sm.get_client(), ParamsVerVinculos(id=id_norma, modo=modo)
                )
                vinculos = [v.model_dump(mode="json") for v in resp.vinculos]
                if self.cache:
                    self.cache.set_vinculos(id_norma, int(modo), vinculos)
        except _LIVE_ERRORS as exc:
            vinculos = self.dataset.vinculos_fallback(id_norma, int(modo))
            fuente = "dataset (degradado)"
            aviso = f"verVinculos en vivo no disponible ({exc}); datos parciales del dataset."

        page_size = self.s.mcp_page_size
        total = len(vinculos)
        start = (mcp_page - 1) * page_size
        return {
            "id": id_norma,
            "modo": int(modo),
            "direccion": "normas que esta modifica" if modo == ModoVinculo.MODIFICA_A
            else "normas que la modifican",
            "vinculos": vinculos[start : start + page_size],
            "pagina_actual": mcp_page,
            "total_pags": max(math.ceil(total / page_size) if total else 1, 1),
            "total_resultados": total,
            "fuente": fuente,
            "aviso": aviso,
        }

    # ── comparación original vs actualizado ───────────────────────────────---
    def comparar_textos(self, id_norma: int, max_lineas: int = 80) -> dict:
        original = self._get_any_text(id_norma, TipoTexto.ORIGINAL)
        actualizado = self._get_any_text(id_norma, TipoTexto.ACTUALIZADO)
        if original is None and actualizado is None:
            return {"error": f"No se obtuvieron textos para la norma {id_norma}."}
        if actualizado is None:
            return {
                "id": id_norma,
                "nota": "No hay texto actualizado distinto del original (norma sin modificaciones "
                        "consolidadas, o no disponible).",
            }
        o_lines = (original or "").splitlines()
        a_lines = actualizado.splitlines()
        diff = list(difflib.unified_diff(o_lines, a_lines, "original", "actualizado", lineterm=""))
        added = [ln[1:] for ln in diff if ln.startswith("+") and not ln.startswith("+++")]
        removed = [ln[1:] for ln in diff if ln.startswith("-") and not ln.startswith("---")]
        return {
            "id": id_norma,
            "lineas_original": len(o_lines),
            "lineas_actualizado": len(a_lines),
            "agregadas": len(added),
            "eliminadas": len(removed),
            "diff_unificado": "\n".join(diff[:max_lineas]),
            "diff_truncado": len(diff) > max_lineas,
            "nota": "Comparacion MECANICA y NO VINCULANTE. Es un insumo: para concluir "
                    "modificaciones/derogaciones leer los textos completos con criterio juridico.",
        }

    def _get_any_text(self, id_norma: int, tipo: TipoTexto) -> str | None:
        try:
            t = self._full_text_live(id_norma, tipo)
            if t is not None:
                return t
        except (NormaNotFoundError, *_LIVE_ERRORS):
            pass
        return self._full_text_dataset(id_norma, tipo)


def _d(iso_date: str | None, part: int) -> int | None:
    """Split an ISO 'YYYY-MM-DD' into [year, month, day] by index; None if absent."""
    if not iso_date:
        return None
    parts = iso_date.split("-")
    if len(parts) != 3:
        return None
    try:
        return int(parts[part])
    except (ValueError, IndexError):
        return None
