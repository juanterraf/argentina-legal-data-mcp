"""Core hardening: atomic/locked rebuild, safe ZIP, parse anomalies, ranking, fetch text,
request log without arguments, removed tools, tributaria pagination, auth, validation."""

from __future__ import annotations

import csv
import io
import json
import time
import zipfile
from pathlib import Path

import httpx
import pytest
import respx

from arg_legal_mcp.config import Settings
from arg_legal_mcp.infoleg import dataset as ds
from arg_legal_mcp.infoleg.client import InfoLegClient, LiveBudgetExceeded, live_budget
from arg_legal_mcp.infoleg.dataset import (
    COLUMNS,
    DatasetBuildError,
    DatasetBuildLockedError,
    DatasetStore,
    build_dataset_from_csv,
    dataset_build_lock,
    extract_csv_safely,
)
from arg_legal_mcp.infoleg.models import ModoVinculo
from arg_legal_mcp.infoleg.parsers import (
    InfoLegBusquedasParser,
    InfoLegNormaParser,
    ParseAnomalyError,
    VerVinculosParser,
)
from arg_legal_mcp.server import build_server, build_service
from arg_legal_mcp.sources import tributaria
from arg_legal_mcp.tools_search_fetch import do_fetch
from tests import fixtures

BASE = "https://servicios.infoleg.gob.ar/infolegInternet"


def _row(**kw) -> dict:
    row = {c: "" for c in COLUMNS}
    row.update({k: str(v) for k, v in kw.items()})
    return row


def _write_csv(path: Path, rows: list[dict]) -> Path:
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS)
        w.writeheader()
        for r in rows:
            w.writerow(r)
    return path


GANANCIAS_ROWS = [
    _row(id_norma=17699, tipo_norma="Ley", numero_norma="20628",
         organismo_origen="HONORABLE CONGRESO DE LA NACION ARGENTINA",
         fecha_sancion="1973-12-27", fecha_boletin="1973-12-31",
         titulo_resumido="REGIMEN LEGAL", titulo_sumario="IMPUESTOS A LAS GANANCIAS",
         texto_resumido="CREACION DEL IMPUESTO A LAS GANANCIAS.",
         observaciones="VEASE <A HREF='http://servicios.infoleg.gob.ar/infolegInternet/"
                       "verNorma.do;jsessionid=775B2F2C?id=332890'>DECRETO N 824/2019</A>.",
         texto_actualizado=f"{BASE}/anexos/15000-19999/17699/texact.htm",
         texto_original=f"{BASE}/anexos/15000-19999/17699/norma.htm",
         modificada_por="363", modifica_a="2"),
    *[
        _row(id_norma=500000 + i, tipo_norma="Resolución", numero_norma=str(5800 + i),
             organismo_origen="AGENCIA DE RECAUDACION Y CONTROL ADUANERO",
             fecha_sancion=f"2025-0{i + 1}-10", fecha_boletin=f"2025-0{i + 1}-11",
             titulo_resumido="IMPUESTOS",
             titulo_sumario="IMPUESTO A LAS GANANCIAS - REGIMEN DE RETENCION",
             texto_resumido="Impuesto a las ganancias. Regimen de retencion.",
             modificada_por="0", modifica_a="1")
        for i in range(8)
    ],
    _row(id_norma=395521, tipo_norma="Decreto", numero_norma="70", clase_norma="DNU",
         organismo_origen="PODER EJECUTIVO NACIONAL (P.E.N.)",
         fecha_sancion="2023-12-20", fecha_boletin="2023-12-21",
         titulo_sumario="BASES PARA LA RECONSTRUCCION DE LA ECONOMIA ARGENTINA",
         modificada_por="50"),
    _row(id_norma=60000, tipo_norma="Decreto", numero_norma="70",
         organismo_origen="PODER EJECUTIVO NACIONAL", fecha_sancion="1990-01-10",
         fecha_boletin="1990-01-12", titulo_sumario="ADMINISTRACION PUBLICA"),
    # Old law without publication date: must not vanish from date filters.
    _row(id_norma=302857, tipo_norma="Ley", numero_norma="1420",
         organismo_origen="CONGRESO", fecha_sancion="1884-07-08",
         titulo_sumario="EDUCACION COMUN"),
    # Non-tax norm from the PEN mentioning the economy (old heuristic let it in).
    _row(id_norma=99999, tipo_norma="Decreto", numero_norma="1000",
         organismo_origen="PODER EJECUTIVO NACIONAL", fecha_sancion="2020-05-01",
         fecha_boletin="2020-05-02", titulo_sumario="Medidas de emergencia economica"),
    # Ministerio de Economia: tax only through the title rule.
    _row(id_norma=70001, tipo_norma="Resolución", numero_norma="12",
         organismo_origen="MINISTERIO DE ECONOMIA", fecha_sancion="2024-02-01",
         fecha_boletin="2024-02-02", titulo_sumario="DERECHOS DE EXPORTACION"),
    _row(id_norma=70002, tipo_norma="Resolución", numero_norma="13",
         organismo_origen="MINISTERIO DE ECONOMIA", fecha_sancion="2024-02-03",
         fecha_boletin="2024-02-04", titulo_sumario="PRESUPUESTO - DISTRIBUCION"),
    _row(id_norma=80001, tipo_norma="Comunicación", numero_norma="A 1234",
         organismo_origen="BANCO CENTRAL DE LA REPUBLICA ARGENTINA (B.C.R.A.)",
         fecha_sancion="2024-03-01", fecha_boletin="2024-03-02",
         titulo_sumario="ENTIDADES FINANCIERAS"),
]


@pytest.fixture()
def store(tmp_path) -> DatasetStore:
    db = tmp_path / "d" / "infoleg.sqlite"
    build_dataset_from_csv(_write_csv(tmp_path / "g.csv", GANANCIAS_ROWS), db)
    return DatasetStore(db)


def _container(tmp_path, *, db=None, use_cache=False, chunk=8000):
    settings = Settings(
        data_dir=tmp_path / "data", cache_dir=tmp_path / "cache",
        dataset_path=db or (tmp_path / "data" / "infoleg.sqlite"),
        infoleg_base_url=BASE, text_chunk_size=chunk,
    )
    settings.ensure_dirs()
    return build_service(settings, use_cache=use_cache)


# ── 1. Rebuild: atomic, locked, safe ZIP ─────────────────────────────────────
def test_rebuild_is_atomic_and_visible_without_restart(tmp_path, store):
    assert store.count() == len(GANANCIAS_ROWS)
    reader = store  # same instance, as a running server would hold
    smaller = GANANCIAS_ROWS[:10]
    build_dataset_from_csv(_write_csv(tmp_path / "s.csv", smaller), store.db_path)
    assert reader.count() == 10  # new file picked up, no reopen needed
    leftovers = [p.name for p in Path(store.db_path).parent.iterdir()
                 if ".building-" in p.name or p.name.endswith(("-wal", "-shm"))]
    assert leftovers == []


def test_failed_rebuild_keeps_previous_dataset(tmp_path, store):
    empty = _write_csv(tmp_path / "empty.csv", [])
    with pytest.raises(DatasetBuildError):
        build_dataset_from_csv(empty, store.db_path)
    assert store.count() == len(GANANCIAS_ROWS)
    # a sudden drop of more than half of the rows is refused too
    with pytest.raises(DatasetBuildError):
        build_dataset_from_csv(_write_csv(tmp_path / "one.csv", GANANCIAS_ROWS[:1]),
                               store.db_path)
    assert store.count() == len(GANANCIAS_ROWS)


def test_rebuild_lock_prevents_concurrent_runs(tmp_path, store):
    with dataset_build_lock(store.db_path):
        with pytest.raises(DatasetBuildLockedError):
            build_dataset_from_csv(_write_csv(tmp_path / "x.csv", GANANCIAS_ROWS),
                                   store.db_path)
    # released: works again
    build_dataset_from_csv(_write_csv(tmp_path / "y.csv", GANANCIAS_ROWS), store.db_path)


def _zip(path: Path, members: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return path


@pytest.mark.parametrize("bad", ["../evil.csv", "/etc/evil.csv", "C:/evil.csv",
                                 "a/../../evil.csv"])
def test_extract_rejects_path_traversal(tmp_path, bad):
    z = _zip(tmp_path / "b.zip", {bad: b"id_norma\n1\n"})
    with pytest.raises(DatasetBuildError):
        extract_csv_safely(z, tmp_path / "out")
    assert not (tmp_path / "evil.csv").exists()


def test_extract_enforces_size_and_ratio(tmp_path):
    z = _zip(tmp_path / "big.zip", {"data.csv": b"a" * 200_000})
    with pytest.raises(DatasetBuildError):
        extract_csv_safely(z, tmp_path / "o1", max_total=1000)
    with pytest.raises(DatasetBuildError):  # 200 KB of 'a' compresses > 100x
        extract_csv_safely(z, tmp_path / "o2")


def test_extract_picks_largest_csv_with_safe_name(tmp_path):
    z = _zip(tmp_path / "ok.zip", {"x/readme.txt": b"hi", "x/small.csv": b"a,b\n",
                                   "x/base-infoleg.csv": b"id_norma,tipo_norma\n1,Ley\n"})
    out = extract_csv_safely(z, tmp_path / "o")
    assert out.name == "infoleg.csv" and out.parent == tmp_path / "o"
    assert b"tipo_norma" in out.read_bytes()


def test_download_and_build_https_cleans_up(tmp_path):
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=COLUMNS)
    w.writeheader()
    for r in GANANCIAS_ROWS:
        w.writerow(r)
    zbytes = io.BytesIO()
    with zipfile.ZipFile(zbytes, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("infoleg/base.csv", buf.getvalue())
    assert ds.ZIP_URL.startswith("https://")
    db = tmp_path / "data" / "infoleg.sqlite"
    with respx.mock(assert_all_called=True) as rsx:
        rsx.get(ds.ZIP_URL).mock(return_value=httpx.Response(200, content=zbytes.getvalue()))
        count = ds.download_and_build(db)
    assert count == len(GANANCIAS_ROWS)
    names = [p.name for p in db.parent.iterdir()]
    assert not any(n.startswith(".infoleg-build-") or n.endswith(".zip") for n in names)


def test_download_refuses_plain_http(tmp_path):
    with pytest.raises(DatasetBuildError):
        ds.download_and_build(tmp_path / "x.sqlite", zip_url="http://example.org/x.zip")


# ── 2. Live parsing anomalies: never cached, degrade with aviso ──────────────
def test_parsers_flag_anomalies():
    with pytest.raises(ParseAnomalyError):
        InfoLegNormaParser(1).parse("<html><body><p>Mantenimiento</p></body></html>")
    changed = ("<html><body><div class='nuevo'><a href='verNorma.do?id=111'>Decreto 1</a>"
               "</div></body></html>")
    with pytest.raises(ParseAnomalyError):
        VerVinculosParser(changed, 265949, ModoVinculo.MODIFICADA_POR).parse()
    with pytest.raises(ParseAnomalyError):
        InfoLegBusquedasParser().parse("<p>Cantidad de Normas Encontradas: 7 en 1</p>")
    empty = InfoLegBusquedasParser().parse("<p>Cantidad de Normas Encontradas: 0</p>")
    assert empty.total == 0 and empty.resultados == []


def test_empty_vinculos_with_known_modifications_degrades_and_is_not_cached(tmp_path):
    db = tmp_path / "d" / "infoleg.sqlite"
    build_dataset_from_csv(_write_csv(tmp_path / "g.csv", GANANCIAS_ROWS), db)
    container = _container(tmp_path, db=db, use_cache=True)
    with respx.mock(assert_all_called=False) as rsx:
        rsx.get(url__regex=r"verVinculos\.do").mock(
            return_value=httpx.Response(200, text="<html><body><table></table></body></html>"))
        out = container.infoleg.ver_vinculos(17699, ModoVinculo.MODIFICADA_POR)
    assert out["fuente"] == "dataset (degradado)"
    assert out["vinculos"] == [] and out["cantidad_segun_dataset"] == 363
    assert "NO significa" in out["aviso"]
    assert container.cache.get_vinculos(17699, int(ModoVinculo.MODIFICADA_POR)) is None
    container.close()


def test_garbage_ficha_degrades_to_dataset_and_is_not_cached(tmp_path):
    db = tmp_path / "d" / "infoleg.sqlite"
    build_dataset_from_csv(_write_csv(tmp_path / "g.csv", GANANCIAS_ROWS), db)
    container = _container(tmp_path, db=db, use_cache=True)
    with respx.mock(assert_all_called=False) as rsx:
        rsx.get(url__regex=r"verNorma\.do").mock(
            return_value=httpx.Response(200, text="<html><body>Error</body></html>"))
        out = container.infoleg.ver_norma(17699)
    assert out["fuente"] == "dataset (degradado)" and "aviso" in out
    assert out["normas_que_modifican_esta"] == 363
    assert "<" not in out["observaciones"] and "jsessionid" not in out["observaciones"]
    assert container.cache.get_norma(17699) is None
    container.close()


def test_live_budget_caps_requests():
    client = InfoLegClient(BASE)
    with httpx.Client() as http, live_budget(0.1):
        time.sleep(0.15)
        start = time.monotonic()
        with pytest.raises(LiveBudgetExceeded):
            client._request(http, "GET", f"{BASE}/verNorma.do?id=1")
        assert time.monotonic() - start < 1
    assert InfoLegClient.MAX_RETRIES <= 2


# ── 3. Ranking ────────────────────────────────────────────────────────────────
def test_ranking_base_law_first_and_recency_on_demand(store):
    rows, total = store.search(texto="impuesto a las ganancias", limit=5)
    assert total == 9
    assert (rows[0]["tipo_norma"], rows[0]["numero_norma"]) == ("Ley", "20628")
    rows, _ = store.search(texto="impuesto a las ganancias", limit=5, orden="recientes")
    assert rows[0]["tipo_norma"] == "Resolución"


def test_direct_references_go_first(store):
    rows, total = store.search(texto="Ley 20.628", limit=5)
    assert rows[0]["id_norma"] == 17699
    rows, _ = store.search(texto="DNU 70/2023", limit=5)
    assert [r["id_norma"] for r in rows] == [395521]
    rows, total = store.search(texto="decreto 70", limit=5)
    assert {r["id_norma"] for r in rows} == {395521, 60000} and total == 2


def test_date_filters_keep_norms_without_publication_date(store):
    rows, total = store.search(tipo_norma="Ley", hasta="1900-01-01", limit=5)
    assert [r["id_norma"] for r in rows] == [302857]


def test_tipos_catalog_covers_all_dataset_types(tmp_path, store):
    container = _container(tmp_path, db=Path(store.db_path))
    nombres = {t["nombre"] for t in container.catalogs.tipos}
    assert {"Ley", "Decreto", "Resolución", "Comunicación"} <= nombres
    out = container.infoleg.buscar_normas(tipo_norma="Comunicación")
    assert [r["id"] for r in out["resultados"]] == [80001]
    assert container.catalogs.tipo_canonico("DNU") == "Decreto"
    assert container.catalogs.buscar_organismos("BCRA")[0]["nombre"].startswith("BANCO CENTRAL")
    container.close()


def test_repo_tipos_catalog_is_complete():
    data = json.loads((Path(__file__).resolve().parent.parent / "data" /
                       "tipos_norma.json").read_text(encoding="utf-8"))
    nombres = {t["nombre"] for t in data}
    assert {"Ley", "Decreto", "Comunicación", "Decreto/Ley", "Acordada"} <= nombres


# ── 4. fetch returns clean text ───────────────────────────────────────────────
ANEXO = (
    "<html><body><h2>IMPUESTO A LAS GANANCIAS</h2>"
    "<p>ARTICULO 1 - Todas las ganancias obtenidas por personas humanas quedan alcanzadas "
    "por el impuesto. <a href='verNorma.do;jsessionid=ABCDEF0123?id=1'>ver</a></p>"
    + "<p>ARTICULO X - texto largo del articulado.</p>" * 50 +
    "<script>alert(1)</script></body></html>"
)


def test_fetch_returns_clean_text_and_how_to_continue(tmp_path, store):
    container = _container(tmp_path, db=Path(store.db_path), chunk=600)
    with respx.mock(assert_all_called=False) as rsx:
        rsx.get(url__regex=r"17699/texact\.htm").mock(return_value=httpx.Response(
            200, content=ANEXO.encode("latin-1"),
            headers={"content-type": "text/html; charset=ISO-8859-1"}))
        out = do_fetch(container.infoleg, "17699")
    text = out["text"]
    assert "Todas las ganancias obtenidas" in text
    assert "<" not in text.replace("<ruta>", "") or "<a" not in text
    assert "jsessionid" not in text and "&lt;" not in text and "alert(1)" not in text
    assert "DECRETO N 824/2019" in text  # observaciones, cleaned
    assert "infoleg_obtener_texto_actualizado(id=17699, inicio=" in text
    assert out["metadata"]["tipo_texto"] == "actualizado" and out["metadata"]["hay_mas"]
    container.close()


def test_fetch_when_text_unavailable_points_to_official_url(tmp_path, store):
    container = _container(tmp_path, db=Path(store.db_path))
    container.infoleg.client.MAX_RETRIES = 1
    with respx.mock(assert_all_called=False) as rsx:
        rsx.get(url__regex=r"anexos/").mock(side_effect=httpx.ConnectError)
        out = do_fetch(container.infoleg, "17699")
    assert "no esta disponible" in out["text"]
    assert out["metadata"]["url_texto_oficial"].endswith(".htm")
    container.close()


# ── 5/6. Server: request log without arguments, removed tools ────────────────
async def _call(mcp, name, args):
    """Run a registered tool (through the instrumentation wrapper) and get its raw dict."""
    return await mcp._tool_manager.get_tool(name).run(args)


def _server(tmp_path, store):
    settings = Settings(data_dir=tmp_path / "srv", cache_dir=tmp_path / "srvc",
                        dataset_path=Path(store.db_path), transport="stdio")
    settings.ensure_dirs()
    return build_server(settings)


async def test_removed_tools_are_gone_and_new_ones_present(tmp_path, store):
    mcp, container = _server(tmp_path, store)
    names = {t.name for t in await mcp.list_tools()}
    for gone in ("infoleg_actualizar_dataset", "infoleg_estado_dataset", "requests_recientes",
                 "infoleg_resolver_id", "infoleg_get_dependencia_by_id",
                 "infoleg_buscar_dependencias"):
        assert gone not in names
    assert {"data_health", "infoleg_buscar_normas", "infoleg_buscar_organismos",
            "search", "fetch"} <= names
    for t in await mcp.list_tools():
        if t.name.startswith("infoleg_") or t.name in ("search", "fetch", "data_health"):
            assert t.annotations and t.annotations.readOnlyHint is True
    container.close()


async def test_request_log_has_no_arguments(tmp_path, store, caplog):
    import logging

    secreto = "Juan Perez DNI 12345678"
    mcp, container = _server(tmp_path, store)
    with caplog.at_level(logging.INFO, logger="arg_legal_mcp.tools"):
        await mcp.call_tool("infoleg_buscar_normas", {"texto": secreto})
        await mcp.call_tool("infoleg_buscar_normas", {"publicado_desde": "31/12/2024"})
    reqs = container.health.recent_requests(10)
    assert [r["tool"] for r in reqs] == ["infoleg_buscar_normas"] * 2
    assert reqs[0]["ok"] == 0 and reqs[1]["ok"] == 1  # validation error counts as error
    raw = Path(container.health.db_path).read_bytes()
    assert b"12345678" not in raw and "12345678" not in caplog.text
    assert "tool=infoleg_buscar_normas" in caplog.text
    container.close()


async def test_data_health_merges_dataset_without_paths(tmp_path, store):
    mcp, container = _server(tmp_path, store)
    container.health.record_freshness("infoleg_dataset", healthy=True)
    container.health.record_freshness(
        "bcra", healthy=False, error="boom at /opt/argentina-legal-data-mcp/src/x.py line 3")
    out = await _call(mcp, "data_health", {})
    assert out["dataset_infoleg"]["cantidad_normas"] == len(GANANCIAS_ROWS)
    assert out["dataset_infoleg"]["fecha_construccion"]
    nombres = [f["nombre"] for f in out["fuentes"]]
    assert nombres.count("infoleg_dataset") == 1
    dump = json.dumps(out)
    assert str(tmp_path).replace("\\", "/") not in dump.replace("\\\\", "/")
    assert "/opt/argentina" not in dump and "Traceback" not in dump
    container.close()


# ── 7. Tributaria: SQL filter + exact pagination ─────────────────────────────
def test_tributaria_filters_in_sql_and_paginates(store):
    p1 = tributaria.buscar(store, limit=4, offset=0)
    p2 = tributaria.buscar(store, limit=4, offset=4)
    p3 = tributaria.buscar(store, limit=4, offset=8)
    ids = [i["id"] for p in (p1, p2, p3) for i in p["items"]]
    assert p1["total"] == p2["total"] == 10  # 8 ARCA + Ley 20628 + derechos de exportacion
    assert len(ids) == len(set(ids)) == 10
    assert 99999 not in ids and 70002 not in ids  # not tax matter
    assert 70001 in ids and 17699 in ids
    assert p1["hay_mas"] and not p3["hay_mas"]
    q = tributaria.buscar(store, query="emergencia economica")
    assert q["total"] == 0


# ── 8. Auth middleware ────────────────────────────────────────────────────────
class _Inner:
    def __init__(self):
        self.called = 0

    async def __call__(self, scope, receive, send):
        self.called += 1
        await receive()
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"{}"})


async def _post(mw, body: bytes, token: str | None = None):
    headers = [(b"authorization", f"Bearer {token}".encode())] if token else []
    scope = {"type": "http", "method": "POST", "path": "/mcp", "headers": headers}
    sent = []

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(ev):
        sent.append(ev)

    await mw(scope, receive, send)
    return next(e["status"] for e in sent if e["type"] == "http.response.start")


async def test_auth_allowlist_body_cap_and_constant_time(tmp_path):
    from arg_legal_mcp.auth.api_keys import ApiKeyStore
    from arg_legal_mcp.auth.middleware import AuthASGIMiddleware

    keys = tmp_path / "k.json"
    keys.write_text(json.dumps({"keys": [{"key": "k-1", "active": True}]}), encoding="utf-8")
    store = ApiKeyStore(keys)
    inner = _Inner()
    mw = AuthASGIMiddleware(inner, store, max_body_bytes=200)

    def rpc(method):
        return json.dumps({"jsonrpc": "2.0", "id": 1, "method": method}).encode()

    assert await _post(mw, rpc("tools/list")) == 200
    assert await _post(mw, rpc("notifications/initialized")) == 200
    for method in ("resources/subscribe", "logging/setLevel", "completion/complete",
                   "tools/call", "algo/nuevo"):
        assert await _post(mw, rpc(method)) == 401
    assert await _post(mw, b"not json") == 401
    assert await _post(mw, rpc("tools/call"), token="k-1") == 200
    assert await _post(mw, b"x" * 500, token="k-1") == 413
    assert store.validate("k-1") and not store.validate("k-2") and not store.validate("")
    import inspect

    from arg_legal_mcp.auth import api_keys
    assert "compare_digest" in inspect.getsource(api_keys)


# ── 9. Parameter validation ───────────────────────────────────────────────────
async def test_tool_parameter_validation(tmp_path, store):
    mcp, container = _server(tmp_path, store)

    async def call(name, args):
        return await _call(mcp, name, args)

    out = await call("infoleg_buscar_normas", {"texto": "x" * 301})
    assert "demasiado largo" in out["error"]
    out = await call("infoleg_buscar_normas", {"texto": "ley", "publicado_desde": "2024-13-01"})
    assert "YYYY-MM-DD" in out["error"]
    out = await call("infoleg_buscar_normas", {"texto": "ganancias", "nro_pag": 10_000_000})
    assert out["pagina_actual"] == 1000
    out = await call("infoleg_ver_norma", {"id": -5})
    assert "error" in out
    container.close()


def test_settings_defaults():
    s = Settings()
    assert s.text_chunk_size >= 8000
    assert "your-org" not in s.user_agent and "derechointeligente" in s.user_agent
    assert not hasattr(s, "jwt_audience")
    assert s.live_budget_seconds <= 30


def test_fixture_rows_still_valid(tmp_path):
    db = tmp_path / "f.sqlite"
    assert build_dataset_from_csv(fixtures.write_synthetic_csv(tmp_path / "f.csv"), db) == 3
