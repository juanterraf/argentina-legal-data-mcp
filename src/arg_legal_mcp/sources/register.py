"""Register the extra-source MCP tools.

Dólar, feriados, BCRA, INDEC, actualización de montos, cómputo de plazos, tributaria
y (vía ``boletin.register``) Boletín Oficial.
"""

from __future__ import annotations

from . import actualizacion as actualizacion_src
from . import bcra as bcra_src
from . import boletin as boletin_src
from . import dolar as dolar_src
from . import feriados as feriados_src
from . import indec as indec_src
from . import plazos as plazos_src
from . import tributaria as tributaria_src

try:
    from mcp.types import ToolAnnotations

    _RO = ToolAnnotations(readOnlyHint=True, openWorldHint=True)
except Exception:  # pragma: no cover
    _RO = None


def _clamp(value, lo: int, hi: int, default: int) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, n))


def register_sources(mcp, settings, *, dataset=None) -> None:
    ro = {"annotations": _RO} if _RO else {}
    ua = settings.user_agent
    timeout = settings.http_timeout

    # ── Dólar ──────────────────────────────────────────────────────────────--
    @mcp.tool(name="dolar_cotizaciones", **ro)
    def dolar_cotizaciones() -> dict:
        """Cotizaciones actuales del dolar en Argentina (oficial, blue, MEP, CCL, tarjeta...).

        CUANDO USARLA: para el valor de HOY del dolar por tipo (compra/venta). Datos de
        DolarAPI, un agregador privado con cache de 5 minutos: sirve como referencia, NO
        como cotizacion oficial para liquidaciones o tributos (para eso, bcra_cotizaciones).
        No devuelve historicos.
        """
        return dolar_src.cotizaciones(user_agent=ua, timeout=timeout)

    # ── Feriados ─────────────────────────────────────────────────────────────
    @mcp.tool(name="feriados_nacionales", **ro)
    def feriados_nacionales(anio: int) -> dict:
        """Feriados nacionales de Argentina de un anio (ej: 2026), con su tipo.

        CUANDO USARLA: para ver feriados inamovibles, trasladables y los dias no laborables
        con fines turisticos ('puente') de un anio. Datos de ArgentinaDatos. NO incluye
        feriados provinciales ni municipales. Para calcular vencimientos usa calcular_plazo.
        """
        return feriados_src.feriados(_clamp(anio, 0, 9999, 0), user_agent=ua, timeout=timeout)

    # ── BCRA ───────────────────────────────────────────────────────────────--
    @mcp.tool(name="bcra_variables", **ro)
    def bcra_variables(
        id_variable: int | None = None,
        desde: str | None = None,
        hasta: str | None = None,
        limit: int = 100,
        buscar: str | None = None,
    ) -> dict:
        """Variables monetarias del BCRA (reservas, tasas, base monetaria, CER, UVA, ICL...).

        CUANDO USARLA: sin id_variable devuelve el CATALOGO (id + descripcion + ultimo
        valor; se leen todas las variables, ~1600); usa `buscar` (texto, ej 'reservas',
        'badlar') para filtrar y `limit` (1-5000, default 100) para acotar. Con id_variable
        devuelve la SERIE (mas reciente primero) entre desde/hasta ISO 'YYYY-MM-DD'.
        Ids utiles: 30=CER, 31=UVA, 40=ICL. Datos de la API v4.0 del BCRA. Para actualizar
        un monto usa calcular_actualizacion.
        """
        return bcra_src.variables(
            id_variable=id_variable, desde=desde, hasta=hasta,
            limit=_clamp(limit, 1, bcra_src.LIMIT_MAX, 100), buscar=buscar,
            user_agent=ua, timeout=timeout,
        )

    @mcp.tool(name="bcra_cotizaciones", **ro)
    def bcra_cotizaciones(
        moneda: str | None = None,
        fechadesde: str | None = None,
        fechahasta: str | None = None,
    ) -> dict:
        """Cotizaciones oficiales de divisas del BCRA (pesos por unidad de moneda extranjera).

        CUANDO USARLA: sin moneda devuelve la tabla del ultimo dia (todas las divisas).
        Con moneda (codigo de 3 letras: 'USD','EUR','BRL') y fechadesde/fechahasta
        ('YYYY-MM-DD') devuelve el historico. Es la fuente oficial para conversiones.
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
        """Serie de tiempo de INDEC/datos.gob.ar por su id (IPC, EMAE, salarios...).

        CUANDO USARLA: cuando ya conoces el serie_id (si no, usa indec_buscar_series).
        Ej: IPC Nivel General Nacional mensual = '148.3_INIVELNAL_DICI_M_26'. Para el ultimo
        valor: sort='desc' y limit=1. Fechas ISO 'YYYY-MM-DD'; limit 1-5000.
        """
        return indec_src.serie(
            serie_id, start_date=start_date, end_date=end_date,
            limit=_clamp(limit, 1, indec_src.SERIE_LIMIT_MAX, 1000), sort=sort,
            user_agent=ua, timeout=timeout,
        )

    @mcp.tool(name="indec_buscar_series", **ro)
    def indec_buscar_series(q: str, limit: int = 10) -> dict:
        """Busca ids de series de tiempo por texto (ej: 'ipc nivel general nacional', 'emae').

        CUANDO USARLA: para hallar el serie_id que despues pasas a indec_serie. Los
        resultados se reordenan: series vigentes primero, luego mejor coincidencia,
        cobertura nacional y dato mas reciente; las discontinuadas quedan al final
        (campo `vigente`). Revisa `cobertura`, `frecuencia` y `ultimo_dato` antes de elegir.
        """
        return indec_src.buscar(
            q, limit=_clamp(limit, 1, indec_src.BUSCAR_LIMIT_MAX, 10),
            user_agent=ua, timeout=timeout,
        )

    # ── Actualización de montos ──────────────────────────────────────────────
    @mcp.tool(name="calcular_actualizacion", **ro)
    def calcular_actualizacion(
        monto: float, fecha_desde: str, fecha_hasta: str, indice: str
    ) -> dict:
        """Actualiza un monto entre dos fechas con un indice oficial: CER, UVA, ICL o IPC.

        CUANDO USARLA: para actualizar un importe (alquileres, creditos, sumas de dinero)
        por CER, UVA o ICL (BCRA, diarios) o IPC nivel general nacional (INDEC, mensual).
        Fechas ISO 'YYYY-MM-DD', desde < hasta, sin fechas futuras. Devuelve coeficiente,
        monto actualizado, valor del indice en cada punta con la fecha efectiva usada y la
        fuente. Es orientativo: el indice aplicable (y como se aplica) depende del contrato,
        la sentencia o la norma; NO calcula intereses ni tasas.
        """
        return actualizacion_src.calcular(
            monto, fecha_desde, fecha_hasta, indice, user_agent=ua, timeout=timeout
        )

    # ── Plazos ───────────────────────────────────────────────────────────────
    @mcp.tool(name="calcular_plazo", **ro)
    def calcular_plazo(fecha_inicio: str, dias: int, tipo: str = "habiles") -> dict:
        """Calcula el vencimiento de un plazo en dias habiles o corridos.

        CUANDO USARLA: para obtener la fecha de vencimiento de un plazo contado desde
        fecha_inicio ('YYYY-MM-DD'; el computo arranca el dia siguiente), con tipo
        'habiles' (maximo 365 dias) o 'corridos' (maximo 1100). Descuenta fines de semana,
        feriados nacionales y puentes turisticos, y lista los dias salteados. NO contempla
        feriados provinciales, ferias judiciales, asuetos o dias inhabiles de cada tribunal
        ni el plazo de gracia: confirma siempre con la normativa procesal aplicable.
        """
        return plazos_src.calcular(
            fecha_inicio, dias, tipo, user_agent=ua, timeout=timeout
        )

    # ── Boletín Oficial (implementado en sources/boletin.py) ─────────────────
    if hasattr(boletin_src, "register"):
        boletin_src.register(mcp, ro=ro, user_agent=ua, timeout=timeout)

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

            CUANDO USARLA: para ubicar normas tributarias (ARCA/AFIP/DGI, impuestos,
            aduana) en el dataset offline de InfoLEG. Filtra por organismo emisor y por
            terminos impositivos del titulo (no exhaustivo: una norma tributaria de otro
            organismo con titulo generico puede quedar afuera). Filtros: query (texto),
            tipo (ej 'Ley', 'Decreto'), desde/hasta ISO. limit 1-50; nro_pag desde 1.
            Para el texto de una norma usa infoleg_ver_norma. NO vinculante.
            """
            limit = _clamp(limit, 1, 50, 20)
            offset = (_clamp(nro_pag, 1, 100000, 1) - 1) * limit
            return tributaria_src.buscar(
                dataset, query=query, tipo=tipo, desde=desde, hasta=hasta,
                limit=limit, offset=offset,
            )
