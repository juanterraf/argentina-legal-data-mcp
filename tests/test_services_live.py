"""Service-layer tests with mocked HTTP (respx): live fetch + graceful degradation.

The key assertion is that text is fetched from the REAL URL read off the ficha
(``texact.htm`` / ``norma.htm``), never from a computed annex range.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from arg_legal_mcp.config import Settings
from arg_legal_mcp.infoleg.dataset import build_dataset_from_csv
from arg_legal_mcp.infoleg.models import ModoVinculo, TipoTexto
from arg_legal_mcp.server import build_service
from tests import fixtures

BASE = "https://servicios.infoleg.gob.ar/infolegInternet"

# respx `url__regex` uses search semantics, so substrings are enough.
_VERNORMA = r"verNorma\.do"
_BUSCAR = r"buscarNormas\.do"
_VERVINCULOS = r"verVinculos\.do"
_TEXACT = r"anexos/265000-269999/265949/texact\.htm"


def make_service(tmp_path, with_dataset=False):
    settings = Settings(
        data_dir=tmp_path / "data",
        cache_dir=tmp_path / "cache",
        dataset_path=tmp_path / "data" / "infoleg.sqlite",
        infoleg_base_url=BASE,
        auth_enabled=False,
    )
    settings.ensure_dirs()
    if with_dataset:
        csv_path = fixtures.write_synthetic_csv(tmp_path / "infoleg.csv")
        build_dataset_from_csv(csv_path, settings.dataset_path)
    return build_service(settings, use_cache=False)


# ── dataset-only (no network) ─────────────────────────────────────────────────
def test_buscar_dataset_mode(tmp_path):
    svc = make_service(tmp_path, with_dataset=True).infoleg
    out = svc.buscar_normas(texto="informacion", en_vivo=False)
    assert out["fuente"] == "dataset"
    ids = [r["id"] for r in out["resultados"]]
    assert 265949 in ids


def test_buscar_validation(tmp_path):
    svc = make_service(tmp_path, with_dataset=True).infoleg
    with pytest.raises(ValueError):
        svc.buscar_normas(numero=27275)  # 1 param, no texto
    with pytest.raises(ValueError):
        svc.buscar_normas(tipo_norma=1, anio_sancion=2016)  # Ley + anio


# ── live (respx) ──────────────────────────────────────────────────────────────
def test_buscar_en_vivo(tmp_path):
    with respx.mock(assert_all_called=False) as rsx:
        rsx.post(url__regex=_BUSCAR).mock(
            return_value=httpx.Response(200, text=fixtures.BUSQUEDA_HTML)
        )
        svc = make_service(tmp_path).infoleg
        out = svc.buscar_normas(texto="informacion publica", en_vivo=True)
        assert out["fuente"] == "vivo"
        assert out["resultados"][0]["id"] == 265949
        svc.sm.close()


def test_obtener_texto_lee_url_real(tmp_path):
    """Text comes from the real texact.htm URL on the ficha; latin-1 decoded."""
    with respx.mock(assert_all_called=False) as rsx:
        rsx.get(url__regex=_VERNORMA).mock(
            return_value=httpx.Response(200, text=fixtures.FICHA_HTML)
        )
        texact_route = rsx.get(url__regex=_TEXACT).mock(
            return_value=httpx.Response(
                200,
                content=fixtures.TEXACT_HTML.encode("latin-1"),
                headers={"content-type": "text/html; charset=ISO-8859-1"},
            )
        )
        svc = make_service(tmp_path).infoleg
        out = svc.obtener_texto(265949, TipoTexto.ACTUALIZADO)
        assert texact_route.called, "must fetch the real texact.htm URL from the ficha"
        assert out["fuente"] == "anexo"  # text fetched from the real anexo URL
        assert out["tipo_texto"] == "actualizado"
        assert "informaci" in out["texto"].lower()  # accented char decoded from latin-1
        svc.sm.close()


def test_obtener_texto_uses_dataset_embedded_text(tmp_path):
    """When the dataset has embedded text, it is used directly (dataset-first), no live call."""
    with respx.mock(assert_all_called=False) as rsx:
        # Live verNorma would error, but the dataset short-circuits before any live call.
        rsx.get(url__regex=_VERNORMA).mock(side_effect=httpx.ConnectError)
        container = make_service(tmp_path, with_dataset=True)
        container.infoleg.client.MAX_RETRIES = 1
        container.infoleg.client.BACKOFF = 0
        out = container.infoleg.obtener_texto(265949, TipoTexto.ACTUALIZADO)
        assert out["fuente"] == "dataset"
        assert "consolidado" in out["texto"]
        container.close()


def test_ver_vinculos_en_vivo(tmp_path):
    with respx.mock(assert_all_called=False) as rsx:
        rsx.get(url__regex=_VERVINCULOS).mock(
            return_value=httpx.Response(200, text=fixtures.VINCULOS_HTML)
        )
        svc = make_service(tmp_path).infoleg
        out = svc.ver_vinculos(265949, ModoVinculo.MODIFICADA_POR)
        assert out["fuente"] == "vivo"
        assert out["total_resultados"] == 2
        assert out["vinculos"][0]["id"] == 111
        svc.sm.close()


def _dataset_with_url(tmp_path, texact_url):
    """Build a 1-row dataset whose texto_* columns hold anexo URLs (like the real data)."""
    import csv

    from arg_legal_mcp.infoleg.dataset import COLUMNS, build_dataset_from_csv

    norma_url = texact_url.replace("texact", "norma")
    row = {c: "" for c in COLUMNS}
    row.update({
        "id_norma": "265949", "tipo_norma": "Ley", "numero_norma": "27275",
        "fecha_boletin": "2016-11-16", "titulo_sumario": "Acceso a la informacion",
        "texto_actualizado": texact_url, "texto_original": norma_url,
    })
    csv_path = tmp_path / "u.csv"
    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        w.writerow(row)
    db = tmp_path / "u.sqlite"
    build_dataset_from_csv(csv_path, db)
    return db


def _svc_for_db(tmp_path, db, use_cache=False):
    settings = Settings(
        data_dir=tmp_path / "d", cache_dir=tmp_path / "c",
        dataset_path=db, infoleg_base_url=BASE,
    )
    settings.ensure_dirs()
    return build_service(settings, use_cache=use_cache)


def test_obtener_texto_dataset_url_fetches_anexo(tmp_path):
    """The dataset stores the anexo URL; we fetch the REAL anexo from it (fuente=anexo)."""
    url = "http://servicios.infoleg.gob.ar/infolegInternet/anexos/265000-269999/265949/texact.htm"
    db = _dataset_with_url(tmp_path, url)
    svc = _svc_for_db(tmp_path, db).infoleg
    with respx.mock(assert_all_called=True) as rsx:
        rsx.get(url__regex=_TEXACT).mock(return_value=httpx.Response(
            200, content=fixtures.TEXACT_HTML.encode("latin-1"),
            headers={"content-type": "text/html; charset=ISO-8859-1"}))
        out = svc.obtener_texto(265949, TipoTexto.ACTUALIZADO)
    assert out["fuente"] == "anexo"
    assert "informaci" in out["texto"].lower()
    assert out["url_oficial"].endswith("texact.htm")
    svc.sm.close()


def test_obtener_texto_url_download_fails_returns_pointer_not_url_as_text(tmp_path):
    """If the anexo can't be downloaded, return the official URL as a pointer — never a
    URL masquerading as the text body."""
    url = "http://servicios.infoleg.gob.ar/infolegInternet/anexos/265000-269999/265949/texact.htm"
    db = _dataset_with_url(tmp_path, url)
    container = _svc_for_db(tmp_path, db)
    container.infoleg.client.MAX_RETRIES = 1
    container.infoleg.client.BACKOFF = 0
    with respx.mock(assert_all_called=False) as rsx:
        rsx.get(url__regex=r"anexos/265000-269999/265949/(texact|norma)\.htm").mock(
            side_effect=httpx.ConnectError)
        out = container.infoleg.obtener_texto(265949, TipoTexto.ACTUALIZADO)
    assert "error" in out
    assert out["url_oficial"].endswith("texact.htm")
    assert "texto" not in out  # the URL is never returned as the body
    container.close()


def test_ver_norma_not_found_falls_back_to_dataset(tmp_path):
    with respx.mock(assert_all_called=False) as rsx:
        rsx.get(url__regex=_VERNORMA).mock(
            return_value=httpx.Response(200, text=fixtures.FICHA_NOT_FOUND_HTML)
        )
        container = make_service(tmp_path, with_dataset=True)
        out = container.infoleg.ver_norma(265949)
        assert out["fuente"] == "dataset"
        assert out["summary"]["id"] == 265949
        container.close()
