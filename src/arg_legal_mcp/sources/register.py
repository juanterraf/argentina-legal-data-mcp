"""Register the extra-source MCP tools."""

from __future__ import annotations

from . import dolar as dolar_src
from . import feriados as feriados_src

try:
    from mcp.types import ToolAnnotations

    _RO = ToolAnnotations(readOnlyHint=True, openWorldHint=True)
except Exception:  # pragma: no cover
    _RO = None


def register_sources(mcp, settings) -> None:
    ro = {"annotations": _RO} if _RO else {}
    ua = settings.user_agent
    timeout = settings.http_timeout

    @mcp.tool(name="dolar_cotizaciones", **ro)
    def dolar_cotizaciones() -> dict:
        """Cotizaciones del dolar en Argentina (oficial, blue, MEP, CCL, tarjeta, etc.).

        CUANDO USARLA: para el valor actual del dolar por tipo. Datos en vivo de DolarAPI.
        """
        return dolar_src.cotizaciones(user_agent=ua, timeout=timeout)

    @mcp.tool(name="feriados_nacionales", **ro)
    def feriados_nacionales(anio: int) -> dict:
        """Feriados nacionales de Argentina para un anio (ej: 2026).

        CUANDO USARLA: para saber los feriados (inamovibles, trasladables, no laborables)
        de un anio. Datos de ArgentinaDatos.
        """
        return feriados_src.feriados(anio, user_agent=ua, timeout=timeout)
