"""Dataset layer: build, count, id-resolution, FTS search + recency boost, filters."""

from __future__ import annotations

import pytest

from arg_legal_mcp.infoleg.dataset import DatasetStore, build_dataset_from_csv, to_fts5_query
from tests import fixtures


@pytest.fixture()
def store(tmp_path):
    csv_path = fixtures.write_synthetic_csv(tmp_path / "infoleg.csv")
    db_path = tmp_path / "infoleg.sqlite"
    count = build_dataset_from_csv(csv_path, db_path)
    assert count == 3
    return DatasetStore(db_path)


def test_available_and_count(store):
    assert store.available() is True
    assert store.count() == 3


def test_resolve_id(store):
    assert store.resolve_id("Ley", 27275) == 265949
    assert store.resolve_id("ley", "27275") == 265949  # case-insensitive
    assert store.resolve_id("Ley", 999999) is None


def test_get_norma(store):
    n = store.get_norma(265949)
    assert n["tipo_norma"] == "Ley"
    assert n["numero_norma"] == "27275"
    assert "consolidado" in n["texto_actualizado"]


def test_fts_search_recency_boost(store):
    # Both Ley 27275 (2016) and Ley 16986 (1966) match "informacion".
    rows, total = store.search(texto="informacion", limit=5, offset=0)
    assert total >= 2
    # Recency boost should rank the 2016 norma above the 1966 one.
    ids = [r["id_norma"] for r in rows]
    assert ids[0] == 265949
    assert 16986 in ids


def test_fts_diacritic_insensitive(store):
    rows, total = store.search(texto="informacIÓN", limit=5)
    assert total >= 1


def test_search_by_filters(store):
    rows, total = store.search(tipo_norma="Decreto", numero=1000, limit=5)
    assert total == 1
    assert rows[0]["id_norma"] == 99999


def test_vinculos_fallback(store):
    activos = store.vinculos_fallback(265949, modo=1)  # modifica_a = "111 222"
    ids = {v["id"] for v in activos}
    assert {111, 222} <= ids
    pasivos = store.vinculos_fallback(265949, modo=2)  # modificada_por = "300100"
    assert any(v["id"] == 300100 for v in pasivos)


def test_to_fts5_query():
    assert to_fts5_query("") is None
    assert to_fts5_query("   ") is None
    # plain words quoted
    assert to_fts5_query("informacion publica") == '"informacion" "publica"'
    # operators mapped
    assert to_fts5_query("aranceles Y aduaneros") == '"aranceles" AND "aduaneros"'
    assert to_fts5_query("energia NO eolica") == '"energia" NOT "eolica"'
    # prefix wildcard preserved
    assert to_fts5_query("residu*") == "residu*"
    # exclusion
    assert to_fts5_query("-eolica") == 'NOT "eolica"'
    # phrase preserved
    assert to_fts5_query('"transporte de carga"') == '"transporte de carga"'
