"""Register the extra-source MCP tools (dólar, feriados, BCRA, INDEC, AFIP, tributaria, boletín)."""

from __future__ import annotations

from . import afip as afip_src
from . import bcra as bcra_src
from . import boletin as boletin_src
from . import dolar as dolar_src
from . import feriados as feriados_src
from . import indec as indec_src
from . import tributaria as tributaria_src

try:
    from mcp.types import ToolAnnotations

    _RO = ToolAnnotations(readOnlyHint=True, openWorldHint=True)
except Exception:  # pragma: no cover
    _RO = None


def register_sources(mcp, settings, *, dataset=None) -> None:
    ro = {"annotations": _RO} if _RO else {}
    ua = settings.user_agent
    timeout = settings.http_timeout

    # ── Dólar ──────────────────────────────────────────────────────────────--
    @mcp.tool(name="dolar_cotizaciones", **ro)
    def dolar_cotizaciones() -> dict:
        """Cotizaciones del dolar en Argentina (oficial, blue, MEP, CCL, tarjeta, etc.).

        CUANDO USARLA: para el valor actual del dolar por tipo. Datos en vivo de DolarAPI.
        """
        return dolar_src.cotizaciones(user_agent=ua, timeout=timeout)

    # ── Feriados ─────────────────────────────────────────────────────────────
    @mcp.tool(name="feriados_nacionales", **ro)
    def feriados_nacionales(anio: int) -> dict:
        """Feriados nacionales de Argentina para un anio (ej: 2026).

        CUANDO USARLA: feriados (inamovibles, trasladables, no laborables) de un anio.
        Datos de ArgentinaDatos.
        """
        return feriados_src.feriados(anio, user_agent=ua, timeout=timeout)

    # ── BCRA ───────────────────────────────────────────────────────────────--
    @mcp.tool(name="bcra_variables", **ro)
    def bcra_variables(
        id_variable: int | None = None,
        desde: str | None = None,
        hasta: str | None = None,
        limit: int = 1000,
    ) -> dict:
        """Variables monetarias del BCRA (reservas, tipo de cambio, tasas, base monetaria...).

        CUANDO USARLA: sin id_variable devuelve el CATALOGO completo (id + descripcion +
        ultimo valor). Con id_variable devuelve la SERIE temporal (usa desde/hasta ISO
        'YYYY-MM-DD'). Datos en vivo de la API v4.0 del BCRA.
        """
        return bcra_src.variables(
            id_variable=id_variable, desde=desde, hasta=hasta, limit=limit,
            user_agent=ua, timeout=timeout,
        )

    @mcp.tool(name="bcra_cotizaciones", **ro)
    def bcra_cotizaciones(
        moneda: str | None = None,
        fechadesde: str | None = None,
        fechahasta: str | None = None,
    ) -> dict:
        """Cotizaciones de divisas del BCRA (pesos por unidad de moneda extranjera).

        CUANDO USARLA: sin moneda devuelve la tabla del dia (todas las divisas). Con
        moneda (ej 'USD','EUR','BRL') y fechadesde/fechahasta devuelve el historico.
        """
        return bcra_src.cotizaciones(
            moneda=moneda, fechadesde=fechadesde, fechahasta=fechahasta,
            user_agent=ua, timeout=timeout,
        )

    # ── INDEC / datos.gob.ar ─────────────────────────────────────────────────
    @mcp.tool(name="indec_serie", **ro)
    def indec_serie(
        serie_id: str,
        start_date: str | None = None,
        end_date: str | None = None,
        limit: int = 1000,
        sort: str = "asc",
    ) -> dict:
        """Serie de tiempo de INDEC/datos.gob.ar por su id (IPC, EMAE, tipo de cambio...).

        CUANDO USARLA: cuando ya conoces el serie_id (usa indec_buscar_series si no).
        sort='desc' + limit=1 para el ultimo valor. Fechas ISO 'YYYY-MM-DD'.
        """
        return indec_src.serie(
            serie_id, start_date=start_date, end_date=end_date, limit=limit, sort=sort,
            user_agent=ua, timeout=timeout,
        )

    @mcp.tool(name="indec_buscar_series", **ro)
    def indec_buscar_series(q: str, limit: int = 10) -> dict:
        """Busca ids de series de tiempo por texto (ej: 'ipc', 'emae', 'tipo de cambio').

        CUANDO USARLA: para hallar el serie_id que despues pasas a indec_serie.
        """
        return indec_src.buscar(q, limit=limit, user_agent=ua, timeout=timeout)

    # ── AFIP (best effort) ───────────────────────────────────────────────────
    @mcp.tool(name="afip_padron", **ro)
    def afip_padron(cuit: str) -> dict:
        """Datos de padron AFIP/ARCA por CUIT (BEST EFFORT, endpoint NO oficial).

        CUANDO USARLA: para validar un CUIT (siempre, localmente) e intentar traer
        nombre/estado. NO hay API gratuita oficial: el servicio puede estar caido y no
        existe busqueda por nombre. Revisa el campo `available` y la `nota`.
        """
        return afip_src.padron(cuit, user_agent=ua, timeout=min(timeout, 12.0))

    # ── Boletín Oficial ──────────────────────────────────────────────────────
    @mcp.tool(name="boletin_oficial_buscar", **ro)
    def boletin_oficial_buscar(fecha: str | None = None, jurisdiccion: str = "caba") -> dict:
        """Secciones/sumario del Boletin Oficial por fecha (ISO 'YYYY-MM-DD').

        CUANDO USARLA: jurisdiccion='caba' devuelve JSON real (Ciudad de Bs As).
        jurisdiccion='nacional' NO tiene API JSON estable: devuelve punteros oficiales
        (argentina.gob.ar/normativa) y sugiere usar las tools de InfoLEG.
        """
        return boletin_src.buscar(
            fecha=fecha, jurisdiccion=jurisdiccion, user_agent=ua, timeout=timeout
        )

    # ── Legislación tributaria (InfoLEG dataset filtrado) ──────────────────---
    if dataset is not None:
        @mcp.tool(name="tributaria_buscar", **ro)
        def tributaria_buscar(
            query: str | None = None,
            tipo: str | None = None,
            desde: str | None = None,
            hasta: str | None = None,
            limit: int = 20,
            nro_pag: int = 1,
        ) -> dict:
            """Legislacion TRIBUTARIA: normas de InfoLEG filtradas por materia impositiva.

            CUANDO USARLA: para normas tributarias (AFIP/ARCA/DGI/impuestos/aduana).
            Usa el dataset offline de InfoLEG filtrado por heuristica. Requiere dataset
            construido. NO vinculante.
            """
            offset = max(0, (nro_pag - 1)) * limit
            return tributaria_src.buscar(
                dataset, query=query, tipo=tipo, desde=desde, hasta=hasta,
                limit=limit, offset=offset,
            )
