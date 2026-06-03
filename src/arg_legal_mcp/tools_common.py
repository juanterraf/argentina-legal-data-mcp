"""Cross-cutting tools (health, diagnostics) not tied to a single data source."""

from __future__ import annotations

from .health import HealthStore, data_health

try:
    from mcp.types import ToolAnnotations

    _RO = ToolAnnotations(readOnlyHint=True, openWorldHint=False)
except Exception:  # pragma: no cover
    _RO = None


def register_common(mcp, *, health: HealthStore, dataset) -> None:
    ro = {"annotations": _RO} if _RO else {}

    @mcp.tool(name="data_health", **ro)
    def data_health_tool() -> dict:
        """Estado de salud de las fuentes de datos (disponibilidad, frescura, smoke test).

        CUANDO USARLA: para diagnosticar si el dataset offline esta cargado y si las
        fuentes estan al dia. Util antes de confiar en resultados o al reportar fallas.
        """
        return data_health(health, dataset)

    @mcp.tool(name="requests_recientes", **ro)
    def requests_recientes(limit: int = 20) -> dict:
        """Ultimas llamadas a tools registradas (solo transporte HTTP). Diagnostico."""
        return {"requests": health.recent_requests(limit)}
