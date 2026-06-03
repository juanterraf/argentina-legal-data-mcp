"""OpenAI-convention search/fetch tools, backed by the InfoLEG dataset."""

from __future__ import annotations

from arg_legal_mcp.config import Settings
from arg_legal_mcp.infoleg.dataset import build_dataset_from_csv
from arg_legal_mcp.server import build_service
from arg_legal_mcp.tools_search_fetch import do_fetch, do_search
from tests import fixtures


def _service(tmp_path):
    csv_path = fixtures.write_synthetic_csv(tmp_path / "infoleg.csv")
    settings = Settings(
        data_dir=tmp_path / "d", cache_dir=tmp_path / "c",
        dataset_path=tmp_path / "d" / "infoleg.sqlite",
    )
    settings.ensure_dirs()
    build_dataset_from_csv(csv_path, settings.dataset_path)
    return build_service(settings, use_cache=False).infoleg


def test_search_shape_and_results(tmp_path):
    svc = _service(tmp_path)
    out = do_search(svc, "informacion publica")
    assert "results" in out
    assert out["results"], "should find at least one norma"
    r = out["results"][0]
    assert set(r.keys()) == {"id", "title", "url"}
    assert r["url"].startswith("http") and "verNorma.do?id=" in r["url"]
    assert any(x["id"] == "265949" for x in out["results"])


def test_fetch_shape(tmp_path):
    svc = _service(tmp_path)
    out = do_fetch(svc, "265949")
    assert out["id"] == "265949"
    assert "Ley 27275" in out["title"]
    assert out["text"]  # dataset summary fields
    assert "verNorma.do?id=265949" in out["url"]
    assert out["metadata"]["tipo"] == "Ley"


def test_fetch_unknown_and_bad_id(tmp_path):
    svc = _service(tmp_path)
    assert "no encontrada" in do_fetch(svc, "999999")["title"]
    assert "invalido" in do_fetch(svc, "abc")["title"]
