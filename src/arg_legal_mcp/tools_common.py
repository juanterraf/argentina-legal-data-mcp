"""Cross-cutting tools (health) and tool instrumentation (request log, non-blocking)."""

from __future__ import annotations

import functools
import inspect
import logging
import time

import anyio.to_thread

from .health import HealthStore, data_health

try:
    from mcp.types import ToolAnnotations

    _RO = ToolAnnotations(readOnlyHint=True, openWorldHint=False)
except Exception:  # pragma: no cover
    _RO = None

log = logging.getLogger("arg_legal_mcp.tools")


def register_common(mcp, *, health: HealthStore, dataset) -> None:
    ro = {"annotations": _RO} if _RO else {}

    @mcp.tool(name="data_health", **ro)
    def data_health_tool() -> dict:
        """Estado de las fuentes de datos: dataset offline de InfoLEG (cantidad de normas y
        fecha de construccion) y salud/frescura de cada fuente externa.

        CUANDO USARLA: antes de confiar en resultados si sospechas datos viejos o faltantes,
        o para explicar al usuario por que una fuente no respondio. No recibe parametros.
        """
        return data_health(health, dataset)


def _outcome(result) -> tuple[bool, str | None]:
    """ok/error from a tool's raw return value (our tools return {'error': ...})."""
    if isinstance(result, dict) and result.get("error"):
        return False, "error_controlado"
    return True, None


def instrument_tools(mcp, health: HealthStore | None) -> int:
    """Wrap every registered tool: run sync tools in a worker thread (so a slow upstream
    never blocks the event loop) and log name + duration + ok/error to stderr and to the
    HealthStore. Arguments are NEVER logged (they can contain personal data).

    Returns the number of instrumented tools.
    """
    manager = getattr(mcp, "_tool_manager", None)
    tools = manager.list_tools() if manager is not None else []
    for tool in tools:
        if getattr(tool.fn, "__argmcp_instrumented__", False):
            continue
        original = tool.fn
        name = tool.name
        is_async = tool.is_async

        @functools.wraps(original)
        async def wrapper(*args, __fn=original, __name=name, __async=is_async, **kwargs):
            start = time.perf_counter()
            ok, err = False, "excepcion"
            try:
                if __async:
                    result = await __fn(*args, **kwargs)
                else:
                    result = await anyio.to_thread.run_sync(
                        functools.partial(__fn, *args, **kwargs)
                    )
                ok, err = _outcome(result)
                return result
            except Exception as exc:
                err = type(exc).__name__
                raise
            finally:
                ms = int((time.perf_counter() - start) * 1000)
                log.info("tool=%s ok=%s ms=%d%s", __name, ok, ms, f" error={err}" if err else "")
                if health is not None:
                    try:
                        health.log_request(__name, ok, ms, err)
                    except Exception:  # noqa: BLE001 — logging must never break a call
                        log.debug("request_log write failed", exc_info=True)

        wrapper.__signature__ = inspect.signature(original)  # type: ignore[attr-defined]
        wrapper.__argmcp_instrumented__ = True  # type: ignore[attr-defined]
        tool.fn = wrapper
        tool.is_async = True
    return len(tools)
