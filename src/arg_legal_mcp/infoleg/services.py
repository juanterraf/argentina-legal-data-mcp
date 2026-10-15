"""Service layer: hybrid dataset+live orchestration, pagination, chunking, caching.

Default behaviour is dataset-first (robust, offline). Live mode adds depth/freshness
(native operators, real full text, structured vinculos). Whenever live fails -network,
timeout, or an HTML we cannot trust (``ParseAnomalyError``)- we degrade to the dataset
and surface an ``aviso`` in the response. Anomalous live answers are never cached.
Every public method runs under a total time budget (``live_budget_seconds``).
"""

from __future__ import annotations

import difflib
import functools
import math

import httpx
from pydantic import ValidationError

from .anexos import resolve_anexo_url
from .cache import InfoLegCache
from .catalogs import CatalogService
from .client import InfoLegClient, LiveBudgetExceeded, live_budget
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
from .parsers import NormaNotFoundError, ParseAnomalyError
from .session import SessionManager
from .texto import html_a_markdown, limpiar

# Anything that means "the live answer is not usable": network, HTTP, timeouts/budget,
# layout changes (anomaly), and malformed data that fails validation while parsing.
_LIVE_ERRORS = (
    httpx.HTTPError,
    OSError,
    ParseAnomalyError,
    ValidationError,
    ValueError,
    AttributeError,
    IndexError,
    KeyError,
    TypeError,
)

MAX_FRAGMENTO = 40_000  # max characters per text request


def _motivo(exc: BaseException) -> str:
    """Short, user-safe reason (no stack traces, no internals)."""
    if isinstance(exc, LiveBudgetExceeded):
        return "se agoto el tiempo maximo de consulta"
    if isinstance(exc, httpx.TimeoutException):
        return "tiempo de espera agotado"
    if isinstance(exc, httpx.HTTPStatusError):
        return f"InfoLEG respondio HTTP {exc.response.status_code}"
    if isinstance(exc, httpx.HTTPError | OSError):
        return "error de conexion con InfoLEG"
    if isinstance(exc, ParseAnomalyError):
        return str(exc)
    return "respuesta de InfoLEG con formato inesperado"


def _budgeted(fn):
    @functools.wraps(fn)
    def wrapper(self, *args, **kwargs):
        with live_budget(self.s.live_budget_seconds):
            return fn(self, *args, **kwargs)

    return wrapper


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


def _cantidad(raw) -> int | None:
    raw = str(raw or "").strip()
    return int(raw) if raw.isdigit() else (0 if not raw else None)


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
        identidad = f"{tipo} {numero}".strip() or f"id {row.get('id_norma')}"
        out = {
            "id": row.get("id_norma"),
            "identidad_norma": identidad,
            "organismo_emisor": row.get("organismo_origen"),
            "fecha_publicacion": row.get("fecha_boletin"),
            "fecha_sancion": row.get("fecha_sancion"),
            "tema": limpiar(row.get("titulo_resumido")),
            "sumario": limpiar(row.get("titulo_sumario")) or limpiar(row.get("texto_resumido")),
            "cantidad_normas_que_la_modifican": _cantidad(row.get("modificada_por")),
        }
        if (row.get("clase_norma") or "").strip().upper() == "DNU":
            out["clase"] = "DNU"
        return out

    def _row_to_vernorma(self, row: dict) -> dict:
        def _url(col: str) -> str | None:
            raw = (row.get(col) or "").strip()
            return raw if raw.lower().startswith(("http://", "https://")) and " " not in raw else None

        return {
            "summary": self._row_to_summary(row),
            "fecha_emision": row.get("fecha_sancion"),
            "pagina_boletin": row.get("pagina_boletin"),
            "resumen": limpiar(row.get("texto_resumido")),
            "observaciones": limpiar(row.get("observaciones")),
            "url_texto_completo": _url("texto_original"),
            "url_texto_actualizado": _url("texto_actualizado"),
            "normas_que_modifica": _cantidad(row.get("modifica_a")),
            "normas_que_modifican_esta": _cantidad(row.get("modificada_por")),
            "texto_disponible_en_dataset": {
                "original": bool(row.get("texto_original")),
                "actualizado": bool(row.get("texto_actualizado")),
            },
        }

    # ── búsqueda ────────────────────────────────────────────────────────────
    @_budgeted
    def buscar_normas(
        self,
        *,
        tipo_norma: str | int | None = None,
        numero: int | str | None = None,
        anio_sancion: int | None = None,
        texto: str | None = None,
        organismo: str | None = None,
        publicado_desde: str | None = None,
        publicado_hasta: str | None = None,
        orden: str = "relevancia",
        en_vivo: bool = False,
        nro_pag: int = 1,
    ) -> dict:
        tipo_nombre = self.catalogs.tipo_canonico(tipo_norma)
        if tipo_norma not in (None, "") and tipo_nombre is None:
            raise ValueError(
                f"tipo_norma desconocido: {tipo_norma!r}. Consulta el recurso "
                "infoleg://tipos-norma (ej: 'Ley', 'Decreto', 'Resolución', 'Comunicación')."
            )
        organismo = self.catalogs.expandir_organismo(organismo)
        provided = sum(
            p not in (None, "")
            for p in (tipo_nombre, numero, anio_sancion, organismo, publicado_desde,
                      publicado_hasta)
        )
        if not texto and provided < 1:
            raise ValueError("Indica 'texto' o al menos un filtro (tipo_norma, numero, ...).")
        if orden not in ("relevancia", "recientes"):
            raise ValueError("orden debe ser 'relevancia' o 'recientes'.")

        mcp_page = nro_pag if nro_pag and nro_pag > 0 else 1
        args = (tipo_nombre, numero, anio_sancion, texto, organismo,
                publicado_desde, publicado_hasta, mcp_page)

        if en_vivo:
            motivo = self._vivo_no_aplicable(tipo_nombre, numero, anio_sancion, texto,
                                             organismo, provided)
            if motivo is None:
                try:
                    return self._buscar_vivo(*args)
                except _LIVE_ERRORS as exc:
                    motivo = f"busqueda en vivo no disponible ({_motivo(exc)})"
            result = self._buscar_dataset(*args, orden=orden)
            result["fuente"] = "dataset (degradado)"
            result["aviso"] = f"{motivo[0].upper()}{motivo[1:]}; se uso el dataset offline."
            return result

        if not self.dataset.available():
            motivo = self._vivo_no_aplicable(tipo_nombre, numero, anio_sancion, texto,
                                             organismo, provided)
            if motivo is None:
                try:
                    result = self._buscar_vivo(*args)
                    result["aviso"] = "Dataset offline no disponible; se uso la busqueda en vivo."
                    return result
                except _LIVE_ERRORS as exc:
                    motivo = _motivo(exc)
            return {
                "resultados": [], "pagina_actual": mcp_page, "total_pags": 1,
                "total_resultados": 0, "fuente": "ninguna",
                "aviso": f"Sin dataset offline y sin busqueda en vivo ({motivo}). "
                         "Reintenta mas tarde.",
            }
        return self._buscar_dataset(*args, orden=orden)

    def _vivo_no_aplicable(self, tipo_nombre, numero, anio, texto, organismo, provided):
        """Reasons the live form cannot run this query (its own constraints)."""
        if tipo_nombre and self.catalogs.tipo_id_infoleg(tipo_nombre) is None:
            return f"el buscador en vivo no admite el tipo {tipo_nombre!r}"
        if organismo and self.catalogs.dependencia_id_infoleg(organismo) is None:
            return "el filtro por organismo no se puede traducir al buscador en vivo"
        if not texto and provided < 2:
            return "el buscador en vivo exige al menos 2 filtros si no hay texto"
        if tipo_nombre == "Ley" and anio is not None:
            return "el buscador en vivo no admite anio_sancion para Leyes"
        if numero is not None and not str(numero).strip().isdigit():
            return "el buscador en vivo exige un numero entero"
        return None

    def _buscar_dataset(self, tipo_nombre, numero, anio, texto, organismo,
                        desde, hasta, mcp_page, *, orden="relevancia") -> dict:
        page_size = self.s.mcp_page_size
        offset = (mcp_page - 1) * page_size
        rows, total = self.dataset.search(
            texto=texto, tipo_norma=tipo_nombre, numero=numero, anio=anio,
            organismo=organismo, desde=desde, hasta=hasta, limit=page_size, offset=offset,
            orden=orden,
        )
        return {
            "resultados": [self._row_to_summary(r) for r in rows],
            "pagina_actual": mcp_page,
            "total_pags": max(math.ceil(total / page_size) if total else 1, 1),
            "total_resultados": total,
            "orden": orden if texto else "recientes",
            "fuente": "dataset",
        }

    def _buscar_vivo(self, tipo_nombre, numero, anio, texto, organismo,
                     desde, hasta, mcp_page) -> dict:
        request = BusquedaNormaRequest(
            tipoNorma=self.catalogs.tipo_id_infoleg(tipo_nombre) if tipo_nombre else None,
            numero=int(numero) if numero not in (None, "") else None,
            anio_sancion=anio, texto=texto,
            dependencia=self.catalogs.dependencia_id_infoleg(organismo) if organismo else None,
            diaPubDesde=_d(desde, 2), mesPubDesde=_d(desde, 1), anioPubDesde=_d(desde, 0),
            diaPubHasta=_d(hasta, 2), mesPubHasta=_d(hasta, 1), anioPubHasta=_d(hasta, 0),
        )
        payload = request.model_dump(exclude_none=True)

        def fetch_real_page(real_page: int):
            s = self.sm.get_search_session(payload)
            with s.lock:  # one request at a time per stateful InfoLEG session
                if s.first_request:
                    res = self.client.buscar_normas(s.client, request)
                    self.sm.set_search_pages(payload, res.total_pags)
                    if real_page <= 1:
                        return res.resultados, res.total
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
        """Live ficha (cached) as a dict. Raises NormaNotFound / live errors."""
        if self.cache:
            cached = self.cache.get_norma(id_norma)
            if cached is not None:
                return cached
        client = self.sm.get_client()
        resp = self.client.ver_norma(client, ParamsVerNorma(id=id_norma))
        data = resp.model_dump(mode="json")
        if self.cache:  # only reached for a ficha that passed the anomaly checks
            self.cache.set_norma(id_norma, data)
        return data

    @_budgeted
    def ver_norma(self, id_norma: int) -> dict:
        row = self.dataset.get_norma(id_norma)
        try:
            data = dict(self._ficha_dict(id_norma) or {})
            if row:  # the dataset adds what the ficha lacks (observaciones, resumen)
                extra = self._row_to_vernorma(row)
                data.setdefault("observaciones", extra["observaciones"])
                data.setdefault("resumen", extra["resumen"])
            return {**data, "fuente": "vivo"}
        except NormaNotFoundError:
            if row:
                return {**self._row_to_vernorma(row), "fuente": "dataset",
                        "aviso": "La ficha en vivo no encontro la norma; datos del dataset."}
            return {"error": f"No existe una norma con id {id_norma} en InfoLEG.", "id": id_norma}
        except _LIVE_ERRORS as exc:
            if row:
                return {
                    **self._row_to_vernorma(row),
                    "fuente": "dataset (degradado)",
                    "aviso": f"Ficha en vivo no disponible ({_motivo(exc)}); "
                             "datos del dataset offline.",
                }
            return {"error": f"InfoLEG no disponible ({_motivo(exc)}) y la norma {id_norma} "
                             "no esta en el dataset offline.", "id": id_norma}

    # ── texto ────────────────────────────────────────────────────────────────
    def _dataset_text_or_url(self, id_norma: int, tipo: TipoTexto) -> tuple[str | None, str | None]:
        """Read the dataset's text column. Returns (texto, url): the official dataset
        stores the **anexo URL** there for most norms (and occasionally embedded HTML/text).
        """
        row = self.dataset.get_norma(id_norma)
        if not row:
            return None, None
        col = "texto_actualizado" if tipo == TipoTexto.ACTUALIZADO else "texto_original"
        raw = (row.get(col) or "").strip()
        if not raw:
            return None, None
        if raw.lower().startswith(("http://", "https://")) and " " not in raw and "<" not in raw:
            return None, raw  # it's a URL pointer, not the body
        if "<" in raw and ">" in raw:  # embedded HTML
            return html_a_markdown(raw), None
        return limpiar(raw), None

    def _resolve_text(self, id_norma: int, tipo: TipoTexto) -> tuple[str | None, str | None, str | None]:
        """Resolve a norma's text.

        Returns ``(texto, fuente, url_oficial)``. ``texto`` is None when the body can't
        be downloaded right now (in which case ``url_oficial`` points at the real anexo).
        Order: cache -> dataset (embedded text or anexo URL) -> live ficha URL -> fetch anexo.
        Raises ``NormaNotFoundError`` if the live ficha says the norma does not exist.
        """
        if self.cache:
            cached = self.cache.get_texto(id_norma, tipo.value)
            if cached is not None:
                return cached, "cache", None

        texto_ds, url = self._dataset_text_or_url(id_norma, tipo)
        if texto_ds:
            return texto_ds, "dataset", None

        if url is None:  # ask the live ficha for the anexo URL (when the search app is up)
            try:
                ficha = self._ficha_dict(id_norma)
                key = ("url_texto_actualizado" if tipo == TipoTexto.ACTUALIZADO
                       else "url_texto_completo")
                url = (ficha or {}).get(key)
            except _LIVE_ERRORS:
                url = None

        if url:
            abs_url = resolve_anexo_url(self.s.infoleg_base_url, url)
            try:
                texto = self.client.consultar_anexo(self.sm.get_client(), abs_url)
                if self.cache and texto:
                    self.cache.set_texto(id_norma, tipo.value, texto)
                return texto, "anexo", abs_url
            except _LIVE_ERRORS:
                return None, None, abs_url  # have the official URL, can't download now
        return None, None, None

    @_budgeted
    def obtener_texto(
        self, id_norma: int, tipo: TipoTexto, inicio: int = 0, fin: int | None = None
    ) -> dict:
        try:
            texto, fuente, url = self._resolve_text(id_norma, tipo)
        except NormaNotFoundError:
            return {"error": f"No existe una norma con id {id_norma}.", "id": id_norma}

        tipo_efectivo = tipo
        aviso = None
        # Asked for actualizado but it's unavailable -> fall back to original.
        if texto is None and tipo == TipoTexto.ACTUALIZADO:
            try:
                t2, f2, u2 = self._resolve_text(id_norma, TipoTexto.ORIGINAL)
            except NormaNotFoundError:
                t2 = f2 = u2 = None
            if t2 is not None:
                texto, fuente, tipo_efectivo, url = t2, f2, TipoTexto.ORIGINAL, u2 or url
                aviso = ("No hay texto actualizado (consolidado) disponible; se devuelve el "
                         "ORIGINAL, que puede no reflejar modificaciones posteriores.")
            elif u2 and not url:
                url = u2

        if texto is None:
            return {
                "id": id_norma,
                "tipo_texto": tipo.value,
                "error": f"No se pudo obtener el texto {tipo.value} de la norma {id_norma} ahora.",
                "url_oficial": url,
                "aviso": "El texto no esta disponible en este momento (InfoLEG no responde o "
                         "la norma no tiene texto digitalizado). Abri la url_oficial o "
                         "reintenta mas tarde." if url else
                         "La norma no tiene texto digitalizado en el dataset ni en InfoLEG.",
            }

        chunk = self._chunk(texto, inicio, fin)
        return {
            "id": id_norma,
            "tipo_texto": tipo_efectivo.value,
            "fuente": fuente,
            "url_oficial": url,
            "aviso": aviso,
            **chunk,
        }

    def _chunk(self, texto: str, inicio: int = 0, fin: int | None = None) -> dict:
        total = len(texto)
        size = self.s.text_chunk_size
        inicio = max(0, int(inicio or 0))
        if fin is None or fin <= inicio:
            fin = inicio + size
        fin = min(fin, inicio + MAX_FRAGMENTO, total)
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
    @_budgeted
    def ver_vinculos(self, id_norma: int, modo: ModoVinculo, nro_pag: int = 1) -> dict:
        mcp_page = nro_pag if nro_pag and nro_pag > 0 else 1
        fuente = "vivo"
        aviso = None
        vinculos: list[dict] = []
        esperado = self.dataset.vinculos_count(id_norma, int(modo))
        try:
            cached = self.cache.get_vinculos(id_norma, int(modo)) if self.cache else None
            if cached is not None:
                vinculos = cached
                fuente = "vivo (cache)"
            else:
                resp = self.client.ver_vinculos(
                    self.sm.get_client(), ParamsVerVinculos(id=id_norma, modo=modo)
                )
                vinculos = [v.model_dump(mode="json") for v in resp.vinculos]
                if not vinculos and esperado:
                    raise ParseAnomalyError(
                        f"InfoLEG devolvio 0 vinculos pero el dataset registra {esperado}"
                    )
                if self.cache:
                    self.cache.set_vinculos(id_norma, int(modo), vinculos)
        except _LIVE_ERRORS as exc:
            fuente = "dataset (degradado)"
            vinculos = []
            if esperado is None:
                aviso = (f"Vinculos en vivo no disponibles ({_motivo(exc)}) y la norma no "
                         "esta en el dataset offline: NO se sabe si tiene vinculos.")
            else:
                aviso = (f"Vinculos en vivo no disponibles ({_motivo(exc)}). El dataset "
                         f"offline solo registra la CANTIDAD ({esperado}), no el detalle. "
                         "La lista vacia NO significa que no existan: reintenta mas tarde.")
            return {
                "id": id_norma,
                "modo": int(modo),
                "direccion": _direccion(modo),
                "vinculos": [],
                "cantidad_segun_dataset": esperado,
                "pagina_actual": mcp_page,
                "total_pags": 1,
                "total_resultados": None,
                "fuente": fuente,
                "aviso": aviso,
            }

        page_size = self.s.mcp_page_size
        total = len(vinculos)
        start = (mcp_page - 1) * page_size
        return {
            "id": id_norma,
            "modo": int(modo),
            "direccion": _direccion(modo),
            "vinculos": vinculos[start : start + page_size],
            "pagina_actual": mcp_page,
            "total_pags": max(math.ceil(total / page_size) if total else 1, 1),
            "total_resultados": total,
            "fuente": fuente,
            "aviso": aviso,
        }

    # ── comparación original vs actualizado ───────────────────────────────---
    @_budgeted
    def comparar_textos(self, id_norma: int, max_lineas: int = 80) -> dict:
        original = self._get_any_text(id_norma, TipoTexto.ORIGINAL)
        actualizado = self._get_any_text(id_norma, TipoTexto.ACTUALIZADO)
        if original is None and actualizado is None:
            return {"error": f"No se obtuvieron textos para la norma {id_norma}."}
        if actualizado is None:
            return {
                "id": id_norma,
                "nota": "No hay texto actualizado distinto del original (norma sin modificaciones "
                        "consolidadas, o no disponible ahora).",
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
            texto, _fuente, _url = self._resolve_text(id_norma, tipo)
            return texto
        except NormaNotFoundError:
            return None


def _direccion(modo: ModoVinculo) -> str:
    return ("normas que esta modifica" if modo == ModoVinculo.MODIFICA_A
            else "normas que la modifican")


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
