"""Caché vencida como respaldo: si la fuente cae, se sirve el último dato con aviso."""

from __future__ import annotations

import httpx
import pytest

from arg_legal_mcp.sources import base, indec

UA = "test-agent"
SERIE = {"data": [["2026-08-01", 12276.766]], "meta": [{"frequency": "month"}]}


@pytest.fixture(autouse=True)
def _limpiar_cache():
    base.clear_cache()
    yield
    base.clear_cache()


def _transporte(respuestas):
    """MockTransport que devuelve, en orden, un JSON o una excepción de red."""
    def handler(request):
        r = respuestas.pop(0)
        if isinstance(r, Exception):
            raise r
        return httpx.Response(200, json=r)
    return httpx.MockTransport(handler)


def _con_transporte(monkeypatch, respuestas):
    transporte = _transporte(respuestas)
    original = httpx.Client

    def cliente(*a, **kw):
        kw["transport"] = transporte
        return original(*a, **kw)

    monkeypatch.setattr(base.httpx, "Client", cliente)
    monkeypatch.setattr(base.time, "sleep", lambda s: None)


def _vencer_todo():
    with base._cache_lock:
        for k, (vence, guardado, valor) in list(base._cache.items()):
            base._cache[k] = (vence - 10_000, guardado - 10_000, valor)


def test_serie_cae_y_devuelve_el_ultimo_dato_con_aviso(monkeypatch):
    caida = httpx.ReadTimeout("timeout")
    _con_transporte(monkeypatch, [SERIE, caida, caida])
    ok = indec.serie("148.3_INIVELNAL_DICI_M_26", user_agent=UA)
    assert ok["observaciones"][0]["valor"] == 12276.766 and "aviso" not in ok

    _vencer_todo()
    respaldo = indec.serie("148.3_INIVELNAL_DICI_M_26", user_agent=UA)
    assert respaldo["observaciones"][0]["valor"] == 12276.766
    assert "ultimo dato guardado" in respaldo["aviso"]
    assert base.STALE_KEY not in str(respaldo)


def test_sin_respaldo_la_caida_es_un_error_estructurado(monkeypatch):
    caida = httpx.ReadTimeout("timeout")
    _con_transporte(monkeypatch, [caida, caida])
    out = indec.serie("148.3_INIVELNAL_DICI_M_26", user_agent=UA)
    assert "error" in out and out["fuente"] == "datos.gob.ar"


def test_sin_stale_max_no_se_sirve_vencido(monkeypatch):
    caida = httpx.ReadTimeout("timeout")
    _con_transporte(monkeypatch, [{"x": 1}, caida, caida])
    url = "https://ejemplo.invalid/api"
    assert base.get_json(url, user_agent=UA, ttl=60) == {"x": 1}
    _vencer_todo()
    with pytest.raises(base.SourceError):
        base.get_json(url, user_agent=UA, ttl=60)
