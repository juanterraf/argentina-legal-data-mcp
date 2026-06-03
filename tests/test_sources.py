"""Extra-source tools (dólar, feriados, BCRA, INDEC, AFIP, boletín, tributaria) — mocked HTTP."""

from __future__ import annotations

import httpx
import respx

from arg_legal_mcp.sources import afip, bcra, boletin, dolar, feriados, indec, tributaria

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


# ── BCRA ──────────────────────────────────────────────────────────────────────
def test_bcra_variables_catalog():
    payload = {"status": 200, "metadata": {"resultset": {"count": 2, "offset": 0, "limit": 1000}},
               "results": [
                   {"idVariable": 1, "descripcion": "Reservas internacionales ", "categoria": "Principales Variables",
                    "unidadExpresion": "En millones de USD", "periodicidad": "D",
                    "ultFechaInformada": "2026-05-29", "ultValorInformado": 48193.0},
                   {"idVariable": 4, "descripcion": "Tipo de cambio minorista", "categoria": "Principales Variables",
                    "unidadExpresion": "Pesos por USD", "periodicidad": "D",
                    "ultFechaInformada": "2026-06-02", "ultValorInformado": 1448.79}]}
    with respx.mock(assert_all_called=True) as rsx:
        rsx.get(url__regex=r"v4\.0/Monetarias\?").mock(
            return_value=httpx.Response(200, json=payload)
        )
        out = bcra.variables(user_agent=UA)
    assert out["total"] == 2
    assert out["variables"][0]["id_variable"] == 1
    assert out["variables"][0]["descripcion"] == "Reservas internacionales"  # trimmed


def test_bcra_variables_catalog_paginates():
    """Catalog spanning two pages (count=3, limit=2) is fully collected (fix #3)."""
    def responder(request):
        from urllib.parse import parse_qs, urlparse
        off = int(parse_qs(urlparse(str(request.url)).query).get("offset", ["0"])[0])
        if off == 0:
            return httpx.Response(200, json={
                "metadata": {"resultset": {"count": 3, "offset": 0, "limit": 2}},
                "results": [{"idVariable": 1, "descripcion": "A"}, {"idVariable": 2, "descripcion": "B"}]})
        return httpx.Response(200, json={
            "metadata": {"resultset": {"count": 1, "offset": 2, "limit": 2}},
            "results": [{"idVariable": 3, "descripcion": "C"}]})

    with respx.mock(assert_all_called=True) as rsx:
        rsx.get(url__regex=r"v4\.0/Monetarias\?").mock(side_effect=responder)
        out = bcra.variables(limit=2, user_agent=UA)
    assert out["total"] == 3
    assert [v["id_variable"] for v in out["variables"]] == [1, 2, 3]
    assert "truncado" not in out


def test_bcra_variables_series():
    payload = {"status": 200, "metadata": {"resultset": {"count": 2}},
               "results": [{"idVariable": 1, "detalle": [
                   {"fecha": "2024-01-10", "valor": 23411.0},
                   {"fecha": "2024-01-09", "valor": 23286.0}]}]}
    with respx.mock(assert_all_called=True) as rsx:
        rsx.get(url__regex=r"v4\.0/Monetarias/1").mock(return_value=httpx.Response(200, json=payload))
        out = bcra.variables(id_variable=1, desde="2024-01-01", hasta="2024-01-10", user_agent=UA)
    assert out["id_variable"] == 1
    assert out["total"] == 2
    assert out["serie"][0] == {"fecha": "2024-01-10", "valor": 23411.0}


def test_bcra_cotizaciones_snapshot():
    payload = {"status": 200, "results": {"fecha": "2026-06-02", "detalle": [
        {"codigoMoneda": "USD", "descripcion": "DOLAR E.E.U.U.", "tipoPase": 1.0, "tipoCotizacion": 1448.79},
        {"codigoMoneda": "EUR", "descripcion": "EURO", "tipoPase": 1.08, "tipoCotizacion": 1560.0}]}}
    with respx.mock(assert_all_called=True) as rsx:
        rsx.get(url__regex=r"estadisticascambiarias/v1\.0/Cotizaciones$").mock(
            return_value=httpx.Response(200, json=payload)
        )
        out = bcra.cotizaciones(user_agent=UA)
    assert out["total"] == 2
    usd = next(c for c in out["cotizaciones"] if c["codigo_moneda"] == "USD")
    assert usd["cotizacion"] == 1448.79 and usd["fecha"] == "2026-06-02"


# ── INDEC ─────────────────────────────────────────────────────────────────────
def test_indec_serie():
    payload = {"data": [["2017-01-01", 101.58], ["2017-02-01", 103.2]],
               "meta": [{"frequency": "month"}]}
    with respx.mock(assert_all_called=True) as rsx:
        rsx.get(url__regex=r"apis\.datos\.gob\.ar/series/api/series").mock(
            return_value=httpx.Response(200, json=payload)
        )
        out = indec.serie("148.3_INIVELNAL_DICI_M_26", user_agent=UA)
    assert out["total"] == 2
    assert out["observaciones"][0] == {"fecha": "2017-01-01", "valor": 101.58}


def test_indec_buscar():
    payload = {"count": 1, "data": [
        {"field": {"id": "148.3_INIVELNAL_DICI_M_26", "description": "IPC Nivel general", "frequency": "month"},
         "dataset": {"title": "Índice de precios al consumidor"}}]}
    with respx.mock(assert_all_called=True) as rsx:
        rsx.get(url__regex=r"apis\.datos\.gob\.ar/series/api/search").mock(
            return_value=httpx.Response(200, json=payload)
        )
        out = indec.buscar("ipc", user_agent=UA)
    assert out["total"] == 1
    assert out["series"][0]["id"] == "148.3_INIVELNAL_DICI_M_26"


# ── AFIP ──────────────────────────────────────────────────────────────────────
def test_afip_cuit_validation_local():
    # 33-69345023-9 is AFIP's own CUIT (valid check digit).
    assert afip.cuit_valido("33693450239")
    assert not afip.cuit_valido("33693450230")
    assert not afip.cuit_valido("123")


def test_afip_bad_length_no_network():
    out = afip.padron("123", user_agent=UA)
    assert out["available"] is False
    assert "11 digitos" in out["error"]


def test_afip_padron_service_down_is_structured():
    with respx.mock(assert_all_called=False) as rsx:
        rsx.get(url__regex=r"soa\.afip\.gob\.ar").mock(return_value=httpx.Response(404, text="Not Found"))
        out = afip.padron("33693450239", user_agent=UA, timeout=0.5)
    assert out["available"] is False
    assert out["cuit_valido"] is True
    assert out["nota"]


# ── Boletín ─────────────────────────────────────────────────────────────────--
def test_boletin_caba():
    payload = [{"superseccion_id": 1, "nombre": "Normas", "descripcion": "Normas del boletín",
                "numero_boletin": 7378, "url_documento": "http://x/download/5120392"}]
    with respx.mock(assert_all_called=True) as rsx:
        rsx.get(url__regex=r"buenosaires\.gob\.ar/obtenerSeccionesBoletin/02-06-2026").mock(
            return_value=httpx.Response(200, json=payload)
        )
        out = boletin.buscar(fecha="2026-06-02", jurisdiccion="caba", user_agent=UA)
    assert out["jurisdiccion"] == "caba"
    assert out["total"] == 1
    assert out["secciones"][0]["numero_boletin"] == 7378


def test_boletin_nacional_is_honest():
    out = boletin.buscar(fecha="2026-06-02", jurisdiccion="nacional", user_agent=UA)
    assert "error" in out
    assert any("argentina.gob.ar" in a for a in out["alternativas"])


def test_boletin_caba_rejects_bad_date():
    # Invalid date is rejected locally (no network call) — fix #7.
    out = boletin.buscar(fecha="2026-13-99", jurisdiccion="caba", user_agent=UA)
    assert "inválido" in out["error"]


def test_boletin_caba_zero_pads_date():
    from arg_legal_mcp.sources.boletin import _to_ddmmyyyy
    assert _to_ddmmyyyy("2026-6-3") == "03-06-2026"
    assert _to_ddmmyyyy("2026-ab-cd") is None


# ── Tributaria (uses the InfoLEG dataset) ──────────────────────────────────---
def test_tributaria_filters_tax_norms(tmp_path):
    from arg_legal_mcp.infoleg.dataset import DatasetStore, build_dataset_from_csv
    from tests import fixtures

    csv_path = fixtures.write_synthetic_csv(tmp_path / "infoleg.csv")
    db = tmp_path / "infoleg.sqlite"
    build_dataset_from_csv(csv_path, db)
    store = DatasetStore(db)
    # "emergencia economica" (Decreto 99999) mentions 'economia' -> tax keyword.
    out = tributaria.buscar(store, query="emergencia economica")
    ids = {i["id"] for i in out["items"]}
    assert 99999 in ids


def test_tributaria_no_dataset(tmp_path):
    from arg_legal_mcp.infoleg.dataset import DatasetStore

    out = tributaria.buscar(DatasetStore(tmp_path / "missing.sqlite"), query="afip")
    assert out["total"] == 0
    assert "Dataset" in out["nota"]
