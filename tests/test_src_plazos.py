"""calcular_plazo — feriados mockeados (ArgentinaDatos)."""

from __future__ import annotations

import httpx
import pytest
import respx

from arg_legal_mcp.sources import base, plazos

UA = "test-agent/1.0"

F2026 = [
    {"fecha": "2026-12-08", "tipo": "inamovible", "nombre": "Inmaculada Concepción"},
    {"fecha": "2026-12-07", "tipo": "puente", "nombre": "Puente turístico no laborable"},
    {"fecha": "2026-12-25", "tipo": "inamovible", "nombre": "Navidad"},
]
F2027 = [{"fecha": "2027-01-01", "tipo": "inamovible", "nombre": "Año nuevo"}]


@pytest.fixture(autouse=True)
def _clean_cache():
    base.clear_cache()
    yield
    base.clear_cache()


def _mock(rsx):
    rsx.get(url__regex=r"feriados/2026").mock(return_value=httpx.Response(200, json=F2026))
    rsx.get(url__regex=r"feriados/2027").mock(return_value=httpx.Response(200, json=F2027))


def test_habiles_cruza_fin_de_semana_y_feriado():
    # Inicio jueves 2026-12-03; 3 habiles: vie 4, (sab/dom, puente 7, feriado 8) mie 9, jue 10
    with respx.mock(assert_all_called=False) as rsx:
        _mock(rsx)
        out = plazos.calcular("2026-12-03", 3, "habiles", user_agent=UA)
    assert out["fecha_vencimiento"] == "2026-12-10"
    motivos = {d["fecha"]: d["motivo"] for d in out["dias_inhabiles"]}
    assert motivos["2026-12-05"] == "fin de semana"
    assert "Inmaculada" in motivos["2026-12-08"]
    assert "2026-12-07" in motivos
    assert out["no_contempla"]


def test_habiles_sin_puentes():
    with respx.mock(assert_all_called=False) as rsx:
        _mock(rsx)
        out = plazos.calcular("2026-12-03", 3, user_agent=UA, incluir_puentes=False)
    assert out["fecha_vencimiento"] == "2026-12-09"  # vie 4, lun 7 (cuenta), mie 9


def test_habiles_cruza_de_anio_pide_ambos_anios():
    with respx.mock(assert_all_called=False) as rsx:
        _mock(rsx)
        out = plazos.calcular("2026-12-29", 3, "habiles", user_agent=UA)
    # mie 30, jue 31, (vie 1/1 feriado, sab, dom) lun 4
    assert out["fecha_vencimiento"] == "2027-01-04"
    assert out["anios_consultados"] == [2026, 2027]


def test_corridos_prorroga_si_vence_inhabil():
    with respx.mock(assert_all_called=False) as rsx:
        _mock(rsx)
        out = plazos.calcular("2026-12-01", 24, "corridos", user_agent=UA)
    # 24 dias -> vie 2026-12-25 (Navidad) -> prorroga al lun 28
    assert out["vencimiento_sin_prorroga"] == "2026-12-25"
    assert out["fecha_vencimiento"] == "2026-12-28"
    assert out["prorrogado_por_dia_inhabil"] is True


def test_entradas_invalidas_sin_red():
    assert "error" in plazos.calcular("2026-13-01", 5, user_agent=UA)
    assert "error" in plazos.calcular("2026-12-01", 0, user_agent=UA)
    assert "error" in plazos.calcular("2026-12-01", -3, user_agent=UA)
    assert "error" in plazos.calcular("2026-12-01", 5, "semanas", user_agent=UA)
    assert "error" in plazos.calcular("2026-12-01", 366, "habiles", user_agent=UA)
    assert "error" in plazos.calcular("2026-12-01", 2.5, user_agent=UA)  # type: ignore[arg-type]


def test_fuente_caida_es_error_estructurado():
    with respx.mock(assert_all_called=False) as rsx:
        rsx.get(url__regex=r"argentinadatos").mock(return_value=httpx.Response(503))
        out = plazos.calcular("2026-12-01", 3, user_agent=UA)
    assert "error" in out and out["fuente"]


def test_anio_no_publicado_404_avisa():
    with respx.mock(assert_all_called=False) as rsx:
        rsx.get(url__regex=r"feriados/2030").mock(return_value=httpx.Response(404))
        out = plazos.calcular("2030-03-04", 2, user_agent=UA)
    assert out["fecha_vencimiento"] == "2030-03-06"
    assert out["anios_sin_feriados_publicados"] == [2030]
