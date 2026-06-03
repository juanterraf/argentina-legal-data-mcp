"""Extra-source tools (dólar, feriados) with mocked HTTP."""

from __future__ import annotations

import httpx
import respx

from arg_legal_mcp.sources import dolar, feriados

UA = "test-agent/1.0"


def test_dolar_cotizaciones_ok():
    payload = [
        {"casa": "oficial", "nombre": "Oficial", "compra": 900.0, "venta": 950.0,
         "fechaActualizacion": "2026-06-03T10:00:00Z"},
        {"casa": "blue", "nombre": "Blue", "compra": 1200.0, "venta": 1250.0,
         "fechaActualizacion": "2026-06-03T10:00:00Z"},
    ]
    with respx.mock(assert_all_called=True) as rsx:
        rsx.get(url__regex=r"dolarapi\.com/v1/dolares").mock(
            return_value=httpx.Response(200, json=payload)
        )
        out = dolar.cotizaciones(user_agent=UA)
    assert out["total"] == 2
    casas = {c["casa"] for c in out["cotizaciones"]}
    assert {"oficial", "blue"} <= casas
    assert out["cotizaciones"][0]["venta"] == 950.0


def test_dolar_cotizaciones_error_is_structured():
    with respx.mock(assert_all_called=False) as rsx:
        rsx.get(url__regex=r"dolarapi\.com").mock(side_effect=httpx.ConnectError)
        out = dolar.cotizaciones(user_agent=UA, timeout=0.01)
    assert "error" in out
    assert out["fuente"] == "dolarapi.com"


def test_feriados_ok():
    payload = [
        {"fecha": "2026-01-01", "tipo": "inamovible", "nombre": "Año Nuevo"},
        {"fecha": "2026-05-01", "tipo": "inamovible", "nombre": "Día del Trabajador"},
    ]
    with respx.mock(assert_all_called=True) as rsx:
        rsx.get(url__regex=r"argentinadatos\.com/v1/feriados/2026").mock(
            return_value=httpx.Response(200, json=payload)
        )
        out = feriados.feriados(2026, user_agent=UA)
    assert out["anio"] == 2026
    assert out["total"] == 2
    assert out["feriados"][0]["nombre"] == "Año Nuevo"
