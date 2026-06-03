"""Parser tests against faithful HTML fixtures (no network)."""

from __future__ import annotations

from datetime import date

import pytest

from arg_legal_mcp.infoleg.models import ModoVinculo
from arg_legal_mcp.infoleg.parsers import (
    InfoLegBusquedasParser,
    InfoLegConfigParser,
    InfoLegNormaParser,
    NormaNotFoundError,
    VerVinculosParser,
    extract_id,
    parse_fecha_es,
)
from tests import fixtures


def test_parse_fecha_es():
    assert parse_fecha_es("16-nov-2016") == date(2016, 11, 16)
    assert parse_fecha_es("14-sep-2016") == date(2016, 9, 14)
    assert parse_fecha_es("02-feb-2018") == date(2018, 2, 2)
    assert parse_fecha_es("basura") is None
    assert parse_fecha_es("") is None


def test_extract_id():
    assert extract_id("verNorma.do?id=265949") == 265949
    assert extract_id("https://x/verNorma.do?id=265949&resaltar=1") == 265949
    assert extract_id("../anexos/265000-269999/265949/norma.htm") == 265949
    assert extract_id("nada") is None


def test_busquedas_parser():
    res = InfoLegBusquedasParser().parse(fixtures.BUSQUEDA_HTML)
    assert res.total == 1
    assert res.total_pags == 1
    assert len(res.resultados) == 1
    n = res.resultados[0]
    assert n.id == 265949
    assert n.identidad_norma == "Ley 27275"
    assert n.organismo_emisor == "HONORABLE CONGRESO DE LA NACION ARGENTINA"
    assert n.id_boletin == 12345
    assert n.fecha_publicacion == date(2016, 11, 16)
    assert n.tema == "DERECHO A LA INFORMACION"
    assert "informacion" in (n.sumario or "").lower()


def test_norma_parser():
    res = InfoLegNormaParser(265949).parse(fixtures.FICHA_HTML)
    assert res.summary.id == 265949
    assert res.summary.identidad_norma == "Ley 27275"
    assert res.fecha_emision == date(2016, 9, 14)
    assert res.summary.fecha_publicacion == date(2016, 11, 16)
    assert res.pagina_boletin == 3
    assert res.summary.tema == "ACCESO A LA INFORMACION PUBLICA"
    assert res.url_texto_completo.endswith("norma.htm")
    assert res.url_texto_actualizado.endswith("texact.htm")
    assert res.normas_que_modifica == 5
    assert res.normas_que_modifican_esta == 3


def test_norma_parser_not_found():
    with pytest.raises(NormaNotFoundError):
        InfoLegNormaParser(1).parse(fixtures.FICHA_NOT_FOUND_HTML)


def test_vinculos_parser():
    res = VerVinculosParser(fixtures.VINCULOS_HTML, 265949, ModoVinculo.MODIFICADA_POR).parse()
    assert res.id == 265949
    assert len(res.vinculos) == 2
    v0 = res.vinculos[0]
    assert v0.id == 111
    assert v0.identidad_norma == "Decreto 222"
    assert v0.organismo_emisor == "PODER EJECUTIVO NACIONAL"
    assert v0.fecha_publicacion == date(2017, 1, 10)
    assert v0.organismo_padre == "REGLAMENTACION"
    assert "Reglamenta" in (v0.tema or "")


def test_config_parser():
    cfg = InfoLegConfigParser().parse(fixtures.CONFIG_HTML)
    tipos = {t.id: t.nombre for t in cfg.tipos_norma}
    assert tipos == {1: "Ley", 2: "Decreto"}
    deps = {d.id for d in cfg.dependencias}
    assert deps == {5, 310}
