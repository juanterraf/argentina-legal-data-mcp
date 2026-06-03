"""Annex URL resolution + the 5,000-block rule."""

from __future__ import annotations

from arg_legal_mcp.infoleg.anexos import (
    ANEXO_BLOCK_SIZE,
    computed_anexo_range,
    computed_anexo_url,
    resolve_anexo_url,
)
from arg_legal_mcp.infoleg.models import TipoTexto

BASE = "https://servicios.infoleg.gob.ar/infolegInternet"


def test_block_size_is_5000_not_50000():
    assert ANEXO_BLOCK_SIZE == 5000


def test_computed_range_ley_27275():
    # Ley 27.275 = id 265949 lives in the 265000-269999 (5,000) folder.
    assert computed_anexo_range(265949) == "265000-269999"
    assert computed_anexo_range(0) == "0-4999"
    assert computed_anexo_range(5000) == "5000-9999"


def test_resolve_anexo_url_strips_dotdot():
    rel = "../anexos/265000-269999/265949/norma.htm"
    assert resolve_anexo_url(BASE, rel) == f"{BASE}/anexos/265000-269999/265949/norma.htm"


def test_resolve_anexo_url_absolute_passthrough():
    abs_url = "https://servicios.infoleg.gob.ar/infolegInternet/anexos/x/y/norma.htm"
    assert resolve_anexo_url(BASE, abs_url) == abs_url


def test_computed_url_uses_correct_block():
    url = computed_anexo_url(BASE, 265949, TipoTexto.ACTUALIZADO)
    assert "265000-269999/265949/texact.htm" in url
