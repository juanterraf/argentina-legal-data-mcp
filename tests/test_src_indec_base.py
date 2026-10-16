"""indec_buscar_series (re-ranking), caché y errores de base, BCRA catálogo completo, register."""

from __future__ import annotations

import asyncio
from datetime import date, timedelta
from types import SimpleNamespace

import httpx
import pytest
import respx

from arg_legal_mcp.sources import base, bcra, feriados, indec

UA = "test-agent/1.0"


@pytest.fixture(autouse=True)
def _clean_cache():
    base.clear_cache()
    yield
    base.clear_cache()


def _item(id_, desc, end, dataset, freq="R/P1M", hits="0"):
    return {"field": {"id": id_, "description": desc, "frequency": freq,
                      "time_index_end": end, "hits_90_days": hits, "units": "Indice"},
            "dataset": {"title": dataset, "source": "INDEC"}}


def test_buscar_series_vigentes_nacionales_primero():
    reciente = (date.today() - timedelta(days=40)).replace(day=1).isoformat()
    payload = {"count": 3, "data": [
        _item("99.3_GBA", "IPC nivel general GBA", "2013-12-01",
              "IPC GBA base 2008. SERIE DISCONTINUADA"),
        _item("148.3_REGION", "IPC. Nivel General Región noroeste. Mensual.", reciente,
              "IPC Nacional. Base dic 2016."),
        _item("148.3_NAL", "IPC. Nivel General Nacional. Base dic 2016. Mensual.", reciente,
              "IPC Nacional. Base dic 2016."),
    ]}
    with respx.mock() as rsx:
        rsx.get(url__regex=r"series/api/search").mock(
            return_value=httpx.Response(200, json=payload))
        out = indec.buscar("ipc nivel general nacional", limit=3, user_agent=UA)
    ids = [s["id"] for s in out["series"]]
    assert ids[0] == "148.3_NAL"
    assert ids[-1] == "99.3_GBA"
    assert out["series"][0]["vigente"] is True and out["series"][0]["cobertura"] == "nacional"
    assert out["series"][-1]["vigente"] is False


def test_buscar_series_validaciones_y_error():
    assert "error" in indec.buscar("  ", user_agent=UA)
    with respx.mock() as rsx:
        rsx.get(url__regex=r"series/api/search").mock(return_value=httpx.Response(500))
        out = indec.buscar("ipc", user_agent=UA)
    assert "error" in out and out["fuente"] == "datos.gob.ar"


def test_indec_serie_valida_entradas_sin_red():
    assert "error" in indec.serie("../etc/passwd", user_agent=UA)
    assert "error" in indec.serie("148.3_X_M_26", start_date="ayer", user_agent=UA)
    assert "error" in indec.serie("148.3_X_M_26", sort="up", user_agent=UA)


def test_cache_ttl_evita_segunda_llamada():
    with respx.mock() as rsx:
        ruta = rsx.get("https://x.test/a").mock(return_value=httpx.Response(200, json={"v": 1}))
        assert base.get_json("https://x.test/a", user_agent=UA, ttl=60, params={"p": 1}) == {"v": 1}
        assert base.get_json("https://x.test/a", user_agent=UA, ttl=60, params={"p": 1}) == {"v": 1}
        assert ruta.call_count == 1
        base.get_json("https://x.test/a", user_agent=UA, ttl=60, params={"p": 2})
        assert ruta.call_count == 2
        base.get_json("https://x.test/a", user_agent=UA, params={"p": 2})  # sin ttl: no cachea
        assert ruta.call_count == 3


def test_cache_tope_de_tamano(monkeypatch):
    monkeypatch.setattr(base, "_CACHE_MAX", 3)
    with respx.mock() as rsx:
        rsx.get(url__regex=r"x\.test").mock(return_value=httpx.Response(200, json={}))
        for i in range(10):
            base.get_json(f"https://x.test/{i}", user_agent=UA, ttl=60)
    assert len(base._cache) == 3


def test_errores_no_se_cachean_y_son_source_error():
    with respx.mock() as rsx:
        rsx.get("https://x.test/e").mock(return_value=httpx.Response(404))
        with pytest.raises(base.SourceError):
            base.get_json("https://x.test/e", user_agent=UA, ttl=60)
        assert not base._cache
        rsx.get("https://x.test/j").mock(return_value=httpx.Response(200, text="no es json"))
        with pytest.raises(base.SourceError):
            base.get_json("https://x.test/j", user_agent=UA, retries=1)


def test_bcra_catalogo_1610_completo_y_buscar():
    total = 1610

    def responder(request):
        off = int(request.url.params["offset"])
        lim = int(request.url.params["limit"])
        ids = range(off + 1, min(off + lim, total) + 1)
        return httpx.Response(200, json={
            "metadata": {"resultset": {"count": total - off, "offset": off, "limit": lim}},
            "results": [{"idVariable": i,
                         "descripcion": "Reservas internacionales" if i == 7 else f"Variable {i}"}
                        for i in ids]})

    with respx.mock() as rsx:
        rsx.get(url__regex=r"v4\.0/Monetarias\?").mock(side_effect=responder)
        out = bcra.variables(limit=5000, user_agent=UA)
        assert out["total_catalogo"] == 1610 and out["total"] == 1610
        assert "catalogo_parcial" not in out
        out = bcra.variables(buscar="RESERVAS", user_agent=UA)
        assert out["coincidencias"] == 1 and out["variables"][0]["id_variable"] == 7
        out = bcra.variables(limit=10, user_agent=UA)
        assert out["truncado"] is True and out["total"] == 10


def test_bcra_validaciones():
    assert "error" in bcra.variables(id_variable=1, desde="2026-02-01", hasta="2026-01-01",
                                     user_agent=UA)
    assert "error" in bcra.variables(id_variable=-3, user_agent=UA)
    assert "error" in bcra.cotizaciones(moneda="DOLAR1", user_agent=UA)
    assert "error" in bcra.cotizaciones(fechadesde="x", user_agent=UA)


def test_feriados_anio_invalido():
    assert "error" in feriados.feriados(1500, user_agent=UA)


def test_register_expone_tools_esperadas():
    from mcp.server.fastmcp import FastMCP

    from arg_legal_mcp.sources.register import register_sources

    mcp = FastMCP("t")
    register_sources(mcp, SimpleNamespace(user_agent=UA, http_timeout=5.0), dataset=object())
    nombres = {t.name for t in asyncio.run(mcp.list_tools())}
    esperadas = {"dolar_cotizaciones", "feriados_nacionales", "bcra_variables",
                 "bcra_cotizaciones", "indec_serie", "indec_buscar_series",
                 "calcular_actualizacion", "calcular_plazo", "tributaria_buscar"}
    assert esperadas <= nombres
    assert "afip_padron" not in nombres
    assert "cuit_validar" not in nombres
