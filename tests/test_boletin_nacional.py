"""Boletín Oficial nacional: parseo de sumario/búsqueda/detalle con HTTP mockeado (respx).

Las fixtures de tests/fixtures_boletin/ son recortes de respuestas reales de
www.boletinoficial.gob.ar capturadas el 2026-10-15.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx
from bs4 import BeautifulSoup

from arg_legal_mcp.sources import boletin

UA = "argentina-legal-data-mcp-test (+https://example.test)"
FX = Path(__file__).parent / "fixtures_boletin"
BORA = boletin.BORA


def _fx(name: str) -> str:
    return (FX / name).read_text(encoding="utf-8")


@pytest.fixture(autouse=True)
def _limpiar_cache():
    boletin._CACHE.clear()
    yield
    boletin._CACHE.clear()


# ── sumario ───────────────────────────────────────────────────────────────────

def test_sumario_parsea_rubros_y_avisos():
    html = _fx("sumario_primera.html").replace("(24)", "(1)").replace("(2)", "(1)").replace("(7)", "(0)")
    with respx.mock(assert_all_called=False) as rsx:
        route = rsx.get(f"{BORA}/seccion/primera/20261015").mock(
            return_value=httpx.Response(200, text=html)
        )
        out = boletin.sumario_nacional(fecha="2026-10-15", seccion="primera", user_agent=UA)
    assert "error" not in out
    assert out["seccion"] == "primera" and out["fecha"] == "2026-10-15"
    assert out["total"] == 5
    first = out["avisos"][0]
    assert first["id"] == "348972"
    assert first["rubro"] == "RESOLUCIONES"
    assert first["organismo"].startswith("ADMINISTRACI")
    assert first["titulo"].startswith("Resoluci") and "297/2026" in first["titulo"]
    assert first["detalle"] == "RESOL-2026-297-ANSES-ANSES"
    assert first["url"] == f"{BORA}/detalleAviso/primera/348972/20261015"
    assert {r["id"] for r in out["rubros"]} == {"86", "88", "5006"}
    # el User-Agent honesto viaja en la request
    assert route.calls[0].request.headers["user-agent"] == UA


def test_sumario_cachea_y_no_repite_request():
    html = _fx("sumario_primera.html").replace("(24)", "(1)").replace("(2)", "(1)").replace("(7)", "(0)")
    with respx.mock(assert_all_called=False) as rsx:
        route = rsx.get(f"{BORA}/seccion/primera/20261015").mock(
            return_value=httpx.Response(200, text=html)
        )
        boletin.sumario_nacional(fecha="2026-10-15", seccion="primera", user_agent=UA)
        boletin.sumario_nacional(fecha="2026-10-15", seccion="primera", user_agent=UA)
    assert route.call_count == 1


def test_sumario_continua_por_paginas_cuando_el_dia_es_largo():
    # El menú declara más avisos (33) que los de la primera página (5): pide la página 2.
    with respx.mock(assert_all_called=False) as rsx:
        rsx.get(f"{BORA}/seccion/primera/20261001").mock(
            return_value=httpx.Response(200, text=_fx("sumario_primera.html"))
        )
        pag2 = rsx.get(f"{BORA}/seccion/actualizar/primera").mock(
            return_value=httpx.Response(200, json=json.loads(_fx("sumario_pag2.json")))
        )
        out = boletin.sumario_nacional(fecha="2026-10-01", seccion="primera", user_agent=UA)
    assert pag2.call_count == 1
    assert pag2.calls[0].request.url.params["pag"] == "2"
    assert pag2.calls[0].request.url.params["ult_rubro"]
    assert out["total"] == 7
    assert out["total_esperado"] == 33
    assert "parcial" in out["nota"]
    assert len({a["id"] for a in out["avisos"]}) == 7  # sin duplicados


def test_sumario_sin_edicion_es_error_estructurado():
    with respx.mock(assert_all_called=False) as rsx:
        rsx.get(f"{BORA}/seccion/primera/20261010").mock(
            return_value=httpx.Response(302, headers={"Location": f"{BORA}/"})
        )
        out = boletin.sumario_nacional(fecha="2026-10-10", seccion="primera", user_agent=UA)
    assert "No hay edición" in out["error"]
    assert out["fuente"]


@pytest.mark.parametrize(
    "kw, frag",
    [
        ({"fecha": "2026-13-45"}, "fecha inválido"),
        ({"fecha": "15/10/2026"}, "fecha inválido"),
        ({"fecha": "2999-01-01"}, "futura"),
        ({"fecha": "2026-10-15", "seccion": "cuarta"}, "Sección inválida"),
        ({"fecha": "2026-10-15", "rubro_id": "1;drop"}, "rubro_id"),
    ],
)
def test_sumario_validaciones_locales_sin_red(kw, frag):
    with respx.mock(assert_all_called=False) as rsx:
        route = rsx.route().mock(return_value=httpx.Response(500))
        out = boletin.sumario_nacional(user_agent=UA, **kw)
    assert frag.lower() in out["error"].lower()
    assert route.call_count == 0


def test_segunda_seccion_respeta_robots_y_no_hace_requests():
    with respx.mock(assert_all_called=False) as rsx:
        route = rsx.route().mock(return_value=httpx.Response(200))
        a = boletin.sumario_nacional(fecha="2026-10-15", seccion="segunda", user_agent=UA)
        b = boletin.buscar_nacional(texto="sociedad anonima", seccion="segunda", user_agent=UA)
        c = boletin.ver_nacional(id_aviso="segunda/123", fecha="2026-10-15", user_agent=UA)
    for out in (a, b, c):
        assert "robots.txt" in out["error"]
    assert route.call_count == 0


def test_sumario_error_de_red_no_levanta_excepcion(monkeypatch):
    monkeypatch.setattr(boletin.time, "sleep", lambda s: None)
    with respx.mock(assert_all_called=False) as rsx:
        rsx.get(f"{BORA}/seccion/primera/20261015").mock(side_effect=httpx.ConnectTimeout("x"))
        out = boletin.sumario_nacional(fecha="2026-10-15", seccion="primera", user_agent=UA)
    assert "no disponible" in out["error"]


def test_sumario_http_500_es_error_estructurado(monkeypatch):
    monkeypatch.setattr(boletin.time, "sleep", lambda s: None)
    with respx.mock(assert_all_called=False) as rsx:
        rsx.get(f"{BORA}/seccion/tercera/20261015").mock(return_value=httpx.Response(503))
        out = boletin.sumario_nacional(fecha="2026-10-15", seccion="tercera", user_agent=UA)
    assert "503" in out["error"]


# ── búsqueda ──────────────────────────────────────────────────────────────────

def test_buscar_parsea_resultados_cursor_y_envia_params():
    with respx.mock(assert_all_called=False) as rsx:
        route = rsx.post(f"{BORA}/busquedaAvanzada/realizarBusqueda").mock(
            return_value=httpx.Response(200, json=json.loads(_fx("busqueda.json")))
        )
        out = boletin.buscar_nacional(
            texto="ley 27742", seccion="primera", fecha_desde="2026-01-01",
            fecha_hasta="2026-10-15", user_agent=UA,
        )
    assert "error" not in out
    assert out["total_coincidencias"] == 6
    r0 = out["resultados"][0]
    assert r0["id"] == "340808"
    assert r0["fecha"] == "2026-04-16"  # tomada de "Fecha de Publicacion: 16/04/2026"
    assert "27742" in r0["extracto"]
    assert r0["url"].endswith("/detalleAviso/primera/340808/20260416")
    # devuelve cursor para seguir (hay más coincidencias que items)
    assert json.loads(out["cursor_siguiente"])["p"] == 2
    sent = route.calls[0].request
    body = dict(httpx.QueryParams(sent.content.decode()))
    params = json.loads(body["params"])
    assert params["texto"] == "ley 27742" and params["seccion"] == [1]
    assert params["fechaDesde"] == "01/01/2026" and params["fechaHasta"] == "15/10/2026"
    assert sent.headers["user-agent"] == UA


def test_buscar_con_cursor_reenvia_estado_de_paginacion():
    cursor = json.dumps({"p": 2, "s": "primera", "r": "RESOLUCIONES"})
    with respx.mock(assert_all_called=False) as rsx:
        route = rsx.post(f"{BORA}/busquedaAvanzada/realizarBusqueda").mock(
            return_value=httpx.Response(200, json=json.loads(_fx("busqueda.json")))
        )
        boletin.buscar_nacional(texto="decreto", seccion="todas", cursor=cursor, user_agent=UA)
    params = json.loads(dict(httpx.QueryParams(route.calls[0].request.content.decode()))["params"])
    assert params["numeroPagina"] == 2 and params["ultimoRubro"] == "RESOLUCIONES"
    assert params["seccion"] == [1, 3] and params["busquedaOriginal"] is False


def test_buscar_error_del_sitio_se_traduce():
    with respx.mock(assert_all_called=False) as rsx:
        rsx.post(f"{BORA}/busquedaAvanzada/realizarBusqueda").mock(
            return_value=httpx.Response(200, json=json.loads(_fx("busqueda_error.json")))
        )
        out = boletin.buscar_nacional(texto="decreto", user_agent=UA)
    assert "rechazó" in out["error"] and "fecha desde" in out["error"]


@pytest.mark.parametrize(
    "kw, frag",
    [
        ({"texto": "ab"}, "3 caracteres"),
        ({"texto": "decreto", "fecha_desde": "ayer"}, "fecha_desde"),
        ({"texto": "decreto", "fecha_hasta": "2026-02-30"}, "fecha_hasta"),
        ({"texto": "decreto", "fecha_desde": "2026-05-01", "fecha_hasta": "2026-04-01"}, "posterior"),
        ({"texto": "decreto", "seccion": "cuarta"}, "Sección inválida"),
        ({"texto": "decreto", "cursor": "no-es-json"}, "cursor inválido"),
    ],
)
def test_buscar_validaciones_locales_sin_red(kw, frag):
    with respx.mock(assert_all_called=False) as rsx:
        route = rsx.route().mock(return_value=httpx.Response(500))
        out = boletin.buscar_nacional(user_agent=UA, **kw)
    assert frag in out["error"]
    assert route.call_count == 0


def test_buscar_sin_resultados_y_html_roto_no_explotan():
    vacio = {"error": 0, "content": {"html": "", "sig_pag": 2, "cantidad_result_seccion": {"1": 0}}}
    with respx.mock(assert_all_called=False) as rsx:
        rsx.post(f"{BORA}/busquedaAvanzada/realizarBusqueda").mock(
            return_value=httpx.Response(200, json=vacio)
        )
        out = boletin.buscar_nacional(texto="zzzzzz", user_agent=UA)
    assert out["resultados"] == [] and out["total_coincidencias"] == 0
    assert "cursor_siguiente" not in out


def test_buscar_respuesta_no_json_es_error(monkeypatch):
    monkeypatch.setattr(boletin.time, "sleep", lambda s: None)
    with respx.mock(assert_all_called=False) as rsx:
        rsx.post(f"{BORA}/busquedaAvanzada/realizarBusqueda").mock(
            return_value=httpx.Response(200, text="<html>Request Rejected</html>")
        )
        out = boletin.buscar_nacional(texto="decreto", user_agent=UA)
    assert "error" in out and out["fuente"]


# ── detalle ───────────────────────────────────────────────────────────────────

def test_ver_limpia_html_y_devuelve_metadatos():
    with respx.mock(assert_all_called=False) as rsx:
        rsx.get(f"{BORA}/detalleAviso/primera/348894/20261014").mock(
            return_value=httpx.Response(200, text=_fx("detalle_decreto.html"))
        )
        out = boletin.ver_nacional(id_aviso="348894", fecha="2026-10-14", user_agent=UA)
    assert "error" not in out
    assert out["organismo"] == "ACUERDOS"
    assert out["titulo"] == "Decreto 1177/2026"
    assert out["referencia"].startswith("DECTO-2026-1177-APN-PTE")
    assert out["rubro"] == "Decretos"
    assert out["fecha_publicacion"] == "2026-10-14"
    assert out["texto"].startswith("Ciudad de Buenos Aires, 13/10/2026")
    assert "VISTO el Expediente" in out["texto"]
    assert "<p>" not in out["texto"] and "border-spacing" not in out["texto"]  # sin HTML ni CSS
    assert out["url"].endswith("/detalleAviso/primera/348894/20261014")
    assert out["url_pdf"].endswith("/pdf/aviso/primera/348894/20261014")


def test_ver_acepta_link_completo_y_tercera():
    link = f"{BORA}/detalleAviso/tercera/2418944/20261015"
    with respx.mock(assert_all_called=False) as rsx:
        rsx.get(link).mock(return_value=httpx.Response(200, text=_fx("detalle_tercera.html")))
        out = boletin.ver_nacional(id_aviso=link, user_agent=UA)
    assert out["seccion"] == "tercera" and out["id"] == "2418944"
    assert out["fecha_publicacion"] == "2026-10-15"
    assert out["texto"].startswith("Procedimiento: MESA DE COMPRAS")


def test_ver_pagina_textos_largos():
    largo = "<br>".join(f"<p>Artículo {i}. " + "texto " * 40 + "</p>" for i in range(60))
    html = (
        '<html><body><ol class="breadcrumb"><li><a href="/seccion/primera/20261014">P</a></li>'
        '<li><a href="#">Leyes</a></li><li><a href="#">Detalle</a></li></ol>'
        '<div id="tituloDetalleAviso"><h1>CONGRESO</h1><h2>Ley 1/2026</h2></div>'
        f'<div id="cuerpoDetalleAviso">{largo}</div></body></html>'
    )
    with respx.mock(assert_all_called=False) as rsx:
        route = rsx.get(f"{BORA}/detalleAviso/primera/1/20261014").mock(
            return_value=httpx.Response(200, text=html)
        )
        p1 = boletin.ver_nacional(id_aviso="1", fecha="2026-10-14", max_chars=3000, user_agent=UA)
        p2 = boletin.ver_nacional(
            id_aviso="1", fecha="2026-10-14", max_chars=3000, pagina=2, user_agent=UA
        )
        pN = boletin.ver_nacional(
            id_aviso="1", fecha="2026-10-14", max_chars=3000, pagina=99, user_agent=UA
        )
    assert route.call_count == 1  # la 2ª y la 3ª salen de caché
    assert p1["total_paginas"] > 1 and p1["pagina"] == 1 and "pagina=2" in p1["nota"]
    assert len(p1["texto"]) <= 3000 and p1["texto"] != p2["texto"]
    assert pN["pagina"] == pN["total_paginas"] and "nota" not in pN


def test_limpiar_cuerpo_tablas_y_entidades():
    soup = BeautifulSoup(
        '<div id="c"><p>Anexo&nbsp;I</p><table><tr><td>Código</td><td>Monto</td></tr>'
        "<tr><td>A</td><td>10</td></tr></table><style>td{x:y}</style></div>", "lxml"
    )
    txt = boletin._limpiar_cuerpo(soup.select_one("#c"))
    assert txt.splitlines()[0] == "Anexo I"
    assert "Código | Monto" in txt and "A | 10" in txt
    assert "td{x:y}" not in txt


def test_ver_aviso_inexistente_y_args_invalidos():
    sin_cuerpo = "<html><body><h1>Boletín Oficial</h1></body></html>"
    with respx.mock(assert_all_called=False) as rsx:
        rsx.get(f"{BORA}/detalleAviso/primera/999/20261014").mock(
            return_value=httpx.Response(200, text=sin_cuerpo)
        )
        rsx.get(f"{BORA}/detalleAviso/primera/998/20261014").mock(
            return_value=httpx.Response(302, headers={"Location": "/error/show"})
        )
        a = boletin.ver_nacional(id_aviso="999", fecha="2026-10-14", user_agent=UA)
        b = boletin.ver_nacional(id_aviso="998", fecha="2026-10-14", user_agent=UA)
    assert "No se encontró" in a["error"] and "No se encontró" in b["error"]
    assert "id_aviso inválido" in boletin.ver_nacional(id_aviso="abc", user_agent=UA)["error"]
    assert "YYYY-MM-DD" in boletin.ver_nacional(id_aviso="5", fecha="hoy", user_agent=UA)["error"]
    assert "enteros" in boletin.ver_nacional(
        id_aviso="5", fecha="2026-10-14", pagina="x", user_agent=UA
    )["error"]


# ── registro de tools ─────────────────────────────────────────────────────────

class _FakeMCP:
    def __init__(self):
        self.tools: dict = {}

    def tool(self, *, name, **kw):
        def deco(fn):
            self.tools[name] = fn
            return fn
        return deco


def test_register_expone_las_tres_tools_con_cuando_usarla():
    mcp = _FakeMCP()
    boletin.register(mcp, ro={}, user_agent=UA, timeout=5.0)
    assert set(mcp.tools) == {
        "boletin_oficial_sumario", "boletin_oficial_buscar", "boletin_oficial_ver",
    }
    for fn in mcp.tools.values():
        assert "CUANDO USARLA:" in fn.__doc__ and "LIMITES" in fn.__doc__


def test_tools_caba_y_validaciones():
    mcp = _FakeMCP()
    boletin.register(mcp, ro={}, user_agent=UA, timeout=5.0)
    data = [{"superseccion_id": 1, "nombre": "Normas", "descripcion": "d",
             "numero_boletin": 7378, "url_documento": "http://x/1"}]
    with respx.mock(assert_all_called=False) as rsx:
        rsx.get(url__regex=r"buenosaires\.gob\.ar/obtenerSeccionesBoletin/02-06-2026").mock(
            return_value=httpx.Response(200, json=data)
        )
        caba = mcp.tools["boletin_oficial_sumario"]("2026-06-02", jurisdiccion="caba")
        caba2 = mcp.tools["boletin_oficial_buscar"](fecha="2026-06-02", jurisdiccion="caba")
    assert caba["jurisdiccion"] == "caba" and caba["secciones"][0]["numero_boletin"] == 7378
    assert caba2["total"] == 1
    assert "jurisdiccion" in mcp.tools["boletin_oficial_sumario"]("2026-06-02", jurisdiccion="x")["error"]
    assert "texto" in mcp.tools["boletin_oficial_buscar"]()["error"]
