"""calcular_actualizacion — índices mockeados."""

from __future__ import annotations

from datetime import date

import httpx
import pytest
import respx

from arg_legal_mcp.sources import actualizacion, base

UA = "test-agent/1.0"
HOY = date(2026, 10, 15)


@pytest.fixture(autouse=True)
def _clean_cache():
    base.clear_cache()
    yield
    base.clear_cache()


def _bcra_handler(valores):
    """valores: {'YYYY-MM-DD': valor}; devuelve los puntos dentro de desde/hasta."""
    def responder(request):
        q = dict(request.url.params)
        det = [{"fecha": f, "valor": v} for f, v in sorted(valores.items(), reverse=True)
               if q["desde"] <= f <= q["hasta"]]
        return httpx.Response(200, json={"results": [{"idVariable": 31, "detalle": det}]})
    return responder


def test_uva_coeficiente_y_monto():
    vals = {"2026-01-10": 1000.0, "2026-10-15": 1500.0}
    with respx.mock() as rsx:
        rsx.get(url__regex=r"Monetarias/31").mock(side_effect=_bcra_handler(vals))
        out = actualizacion.calcular(
            200000, "2026-01-10", "2026-10-15", "uva", user_agent=UA, hoy=HOY)
    assert out["coeficiente"] == 1.5
    assert out["monto_actualizado"] == 300000.0
    assert out["variacion_porcentual"] == 50.0
    assert out["valor_indice_desde"]["fecha_efectiva"] == "2026-01-10"
    assert "orientativo" in out["aviso"].lower()
    assert out["fuente"]["url"].startswith("https://")


def test_cer_usa_ultimo_dia_anterior():
    vals = {"2026-03-13": 100.0, "2026-10-15": 125.0}  # sin dato el sabado 14
    with respx.mock() as rsx:
        rsx.get(url__regex=r"Monetarias/30").mock(side_effect=_bcra_handler(vals))
        out = actualizacion.calcular(
            1000, "2026-03-14", "2026-10-15", "CER", user_agent=UA, hoy=HOY)
    assert out["valor_indice_desde"]["fecha_efectiva"] == "2026-03-13"
    assert "nota" in out["valor_indice_desde"]
    assert out["monto_actualizado"] == 1250.0


def test_ipc_mensual_y_mes_no_publicado():
    def responder(request):
        end = request.url.params["end_date"]
        data = {"2026-01-01": 100.0, "2026-07-01": 120.0}
        rows = [[f, v] for f, v in sorted(data.items(), reverse=True) if f <= end][:1]
        return httpx.Response(200, json={"data": rows})
    with respx.mock() as rsx:
        rsx.get(url__regex=r"series/api/series").mock(side_effect=responder)
        out = actualizacion.calcular(
            1000, "2026-01-20", "2026-10-01", "IPC", user_agent=UA, hoy=HOY)
    assert out["coeficiente"] == 1.2
    assert out["monto_actualizado"] == 1200.0
    assert out["valor_indice_desde"]["periodo"] == "2026-01"
    assert out["valor_indice_hasta"]["periodo"] == "2026-07"
    assert "no esta publicado" in out["valor_indice_hasta"]["nota"]


@pytest.mark.parametrize("args", [
    (0, "2026-01-01", "2026-02-01", "CER"),
    (-5, "2026-01-01", "2026-02-01", "CER"),
    ("abc", "2026-01-01", "2026-02-01", "CER"),
    (100, "2026-02-01", "2026-01-01", "CER"),
    (100, "2026-01-01", "2026-01-01", "CER"),
    (100, "2026-01-01", "2026-12-31", "CER"),  # futura
    (100, "01/01/2026", "2026-02-01", "CER"),
    (100, "2026-01-01", "2026-02-01", "DOLAR"),
])
def test_validaciones_sin_red(args):
    out = actualizacion.calcular(*args, user_agent=UA, hoy=HOY)
    assert "error" in out and out["fuente"]


def test_fuente_caida_es_error_estructurado():
    with respx.mock() as rsx:
        rsx.get(url__regex=r"api\.bcra\.gob\.ar").mock(return_value=httpx.Response(500))
        out = actualizacion.calcular(
            100, "2026-01-01", "2026-02-01", "ICL", user_agent=UA, hoy=HOY)
    assert "error" in out
