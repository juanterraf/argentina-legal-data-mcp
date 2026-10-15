"""Boletín Oficial — nacional (BORA) y Ciudad de Buenos Aires.

NACIONAL (verificado 2026-10-15): www.boletinoficial.gob.ar responde a un cliente HTTP
común con User-Agent honesto (sin desafío del WAF). No hay API JSON documentada; el sitio
sirve HTML renderizado en servidor y este módulo lee esas mismas páginas públicas, con
pocas requests y caché:

* sumario por sección y día:  GET /seccion/{primera|tercera}/{YYYYMMDD}[?rubro=ID]
  (continuación de días muy cargados: GET /seccion/actualizar/{seccion}?pag=N&ult_rubro=...)
* detalle de aviso:           GET /detalleAviso/{seccion}/{id}/{YYYYMMDD}
* búsqueda avanzada:          POST /busquedaAvanzada/realizarBusqueda (form ``params``=JSON)
* PDF del aviso:              /pdf/aviso/{seccion}/{id}/{YYYYMMDD} (solo se enlaza)

La SEGUNDA sección (sociedades) NO se consulta: el robots.txt del sitio veta
``/seccion/segunda`` y ``/detalleAviso/segunda``; se respeta.

CABA: API JSON ``api-restboletinoficial.buenosaires.gob.ar`` (otra institución).
"""

from __future__ import annotations

import json
import re
import time
from datetime import date, datetime
from typing import Any

import httpx
from bs4 import BeautifulSoup

from .base import SourceError, get_json

BA_BASE = "https://api-restboletinoficial.buenosaires.gob.ar"
BORA = "https://www.boletinoficial.gob.ar"


def _to_ddmmyyyy(fecha_iso: str) -> str | None:
    """Validate an ISO 'YYYY-MM-DD' date and render it zero-padded as 'DD-MM-YYYY'."""
    try:
        dt = datetime.strptime((fecha_iso or "").strip(), "%Y-%m-%d")
    except ValueError:
        return None
    return dt.strftime("%d-%m-%Y")


def buscar(
    *,
    fecha: str | None = None,
    jurisdiccion: str = "caba",
    user_agent: str,
    timeout: float = 20.0,
) -> dict:
    """Compat: sumario de CABA; para 'nacional' devuelve punteros (usar las tools nuevas)."""
    if jurisdiccion == "nacional":
        return {
            "jurisdiccion": "nacional",
            "error": "Para el Boletín Oficial NACIONAL usá boletin_oficial_sumario, "
                     "boletin_oficial_buscar y boletin_oficial_ver.",
            "alternativas": [
                "Usá jurisdiccion='caba' (API JSON de la Ciudad de Buenos Aires).",
                "Búsqueda oficial (HTML): https://www.argentina.gob.ar/normativa/buscar-boletin",
                "Para normas nacionales publicadas, usá las tools de InfoLEG.",
            ],
            "fuente": "boletinoficial.gob.ar",
        }

    # CABA — real JSON API.
    if not fecha:
        return {"error": "Indicá 'fecha' (YYYY-MM-DD) para consultar el boletín de CABA.",
                "fuente": "Boletín Oficial CABA"}
    dd = _to_ddmmyyyy(fecha)
    if not dd:
        return {"error": "Formato de fecha inválido; usá YYYY-MM-DD.",
                "fuente": "Boletín Oficial CABA"}
    try:
        data = get_json(
            f"{BA_BASE}/obtenerSeccionesBoletin/{dd}", user_agent=user_agent, timeout=timeout
        )
    except SourceError as exc:
        return {"error": f"Boletín CABA no disponible: {exc}", "fuente": "Boletín Oficial CABA"}

    secciones = [
        {
            "superseccion_id": s.get("superseccion_id"),
            "nombre": s.get("nombre"),
            "descripcion": s.get("descripcion"),
            "numero_boletin": s.get("numero_boletin"),
            "documento": s.get("url_documento"),
        }
        for s in (data if isinstance(data, list) else [])
    ]
    return {
        "jurisdiccion": "caba",
        "fecha": fecha,
        "secciones": secciones,
        "total": len(secciones),
        "fuente": "Boletín Oficial de la Ciudad de Buenos Aires",
    }


# ═══════════════════════════════════════════════════════════════════════════════
# BORA nacional (HTML público, lectura respetuosa)
# ═══════════════════════════════════════════════════════════════════════════════

FUENTE_BORA = "Boletín Oficial de la República Argentina (boletinoficial.gob.ar)"
SECCIONES = {"primera": 1, "tercera": 3}
_SECCION_NOMBRE = {
    "primera": "Legislación y Avisos Oficiales",
    "tercera": "Contrataciones del Estado",
}
MAX_PAGINAS_SUMARIO = 6
MAX_RESULTADOS = 100
DEFAULT_CHARS = 12000
MAX_CHARS = 30000

_CACHE: dict[str, tuple[float, Any]] = {}
_CACHE_MAX = 256


def _cache_get(key: str) -> Any | None:
    hit = _CACHE.get(key)
    if hit and hit[0] > time.monotonic():
        return hit[1]
    _CACHE.pop(key, None)
    return None


def _cache_put(key: str, value: Any, ttl: float) -> None:
    if len(_CACHE) >= _CACHE_MAX:
        for k in sorted(_CACHE, key=lambda k: _CACHE[k][0])[: _CACHE_MAX // 4]:
            _CACHE.pop(k, None)
    _CACHE[key] = (time.monotonic() + ttl, value)


def _ttl(fecha: date | None, *, pasado: float = 86400.0, hoy: float = 600.0) -> float:
    """Los días pasados no cambian; el día en curso sí (se siguen publicando avisos)."""
    return pasado if fecha and fecha < date.today() else hoy


def _err(msg: str) -> dict:
    return {"error": msg, "fuente": FUENTE_BORA}


def _parse_iso(fecha: str | None) -> date | None:
    try:
        return datetime.strptime((fecha or "").strip(), "%Y-%m-%d").date()
    except ValueError:
        return None


def _norm_seccion(seccion: str | None) -> str | None:
    s = (seccion or "").strip().lower()
    return s if s in ("primera", "segunda", "tercera") else None


def _segunda_vetada() -> dict:
    return {
        "error": "La Segunda sección (sociedades) no se consulta desde este servidor: el "
                 "robots.txt del Boletín Oficial veta /seccion/segunda y /detalleAviso/segunda "
                 "y lo respetamos. Consultala a mano en el sitio oficial.",
        "alternativa": f"{BORA}/seccion/segunda",
        "fuente": FUENTE_BORA,
    }


def _headers(user_agent: str) -> dict:
    return {
        "User-Agent": user_agent,
        "Accept": "text/html,application/json;q=0.9,*/*;q=0.5",
        "Accept-Language": "es-AR,es;q=0.9",
    }


def _request(client: httpx.Client, method: str, url: str, **kw) -> httpx.Response:
    """Una request con un reintento corto ante 5xx/timeouts. No sigue redirecciones."""
    last: Exception | None = None
    for attempt in range(2):
        try:
            resp = client.request(method, url, **kw)
            if resp.status_code >= 500:
                raise SourceError(f"el Boletín Oficial respondió HTTP {resp.status_code}")
            return resp
        except (httpx.HTTPError, SourceError) as exc:
            last = exc
            if attempt == 0:
                time.sleep(0.6)
    raise SourceError(str(last) or last.__class__.__name__)


def _text(el) -> str:
    return re.sub(r"\s+", " ", el.get_text(" ", strip=True)).strip() if el else ""


def _soup(content: bytes | str) -> BeautifulSoup:
    if isinstance(content, bytes):
        content = content.decode("utf-8", errors="replace")
    return BeautifulSoup(content, "lxml")


_HREF_AVISO = re.compile(r"/detalleAviso/(primera|segunda|tercera|cuarta)/(\d+)/(\d{8})")


def _fecha_iso8(ymd: str) -> str:
    return f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:]}"


def _parse_items(soup: BeautifulSoup, rubro_inicial: str = "") -> tuple[list[dict], str]:
    """Items de un listado (sumario o búsqueda). Devuelve (items, último rubro visto)."""
    cont = soup.select_one("#avisosSeccionDiv") or soup
    rubro = rubro_inicial
    items: list[dict] = []
    vistos: set[str] = set()
    for el in cont.find_all(["h5", "a"]):
        if el.name == "h5":
            if "seccion-rubro" in (el.get("class") or []):
                rubro = _text(el)
            continue
        m = _HREF_AVISO.search(el.get("href") or "")
        if not m or not el.select_one(".linea-aviso"):
            continue
        sec, id_, ymd = m.groups()
        if id_ in vistos or sec == "segunda":
            continue
        vistos.add(id_)
        smalls = [_text(s) for s in el.select("p.item-detalle small")]
        item: dict = {
            "id": id_,
            "seccion": sec,
            "fecha": _fecha_iso8(ymd),
            "rubro": rubro,
            "organismo": _text(el.select_one("p.item")),
            "titulo": smalls[0] if smalls else "",
            "url": f"{BORA}/detalleAviso/{sec}/{id_}/{ymd}",
        }
        extra = [x for x in smalls[1:] if x]
        if extra:
            item["detalle"] = " · ".join(extra)
        items.append(item)
    return items, rubro


def _rubros_menu(soup: BeautifulSoup, seccion: str) -> list[dict]:
    out = []
    pat = re.compile(r"buscarRubro'\s*,\s*'" + seccion + r"'\s*,\s*'(\d+)'")
    for a in soup.select("span.menuRubros a"):
        m = pat.search(a.get("onclick") or "")
        if not m:
            continue
        n = re.search(r"\((\d+)\)\s*$", _text(a))
        out.append({
            "id": m.group(1),
            "nombre": re.sub(r"\s*\(\d+\)\s*$", "", _text(a)),
            "cantidad": int(n.group(1)) if n else None,
        })
    return out


def sumario_nacional(
    *, fecha: str, seccion: str = "primera", rubro_id: str | None = None,
    user_agent: str, timeout: float = 20.0,
) -> dict:
    """Avisos de una sección del BORA en un día (primera o tercera)."""
    sec = _norm_seccion(seccion)
    if sec is None:
        return _err("Sección inválida; usá 'primera' o 'tercera'.")
    if sec == "segunda":
        return _segunda_vetada()
    d = _parse_iso(fecha)
    if d is None:
        return _err("Formato de fecha inválido; usá YYYY-MM-DD.")
    if d > date.today():
        return _err("La fecha es futura: todavía no hay edición.")
    if rubro_id is not None and not re.fullmatch(r"\d{1,6}", str(rubro_id)):
        return _err("rubro_id inválido (es numérico; salen en el campo 'rubros' del sumario).")

    ymd = d.strftime("%Y%m%d")
    key = f"sum|{sec}|{ymd}|{rubro_id or ''}"
    hit = _cache_get(key)
    if hit is not None:
        return hit

    url = f"{BORA}/seccion/{sec}/{ymd}"
    params = {"rubro": str(rubro_id)} if rubro_id else None
    esperado = None
    try:
        with httpx.Client(timeout=timeout, headers=_headers(user_agent)) as client:
            resp = _request(client, "GET", url, params=params)
            if resp.status_code in (301, 302, 303, 307, 308):
                return _err(
                    f"No hay edición del Boletín Oficial el {fecha} "
                    "(fin de semana, feriado o sin publicación)."
                )
            if resp.status_code != 200:
                return _err(f"El Boletín Oficial respondió HTTP {resp.status_code}.")
            soup = _soup(resp.content)
            items, ult_rubro = _parse_items(soup)
            rubros = _rubros_menu(soup, sec)
            if not rubro_id:
                esperado = sum(r["cantidad"] or 0 for r in rubros)
            ids = {i["id"] for i in items}
            pag = 2
            # Días muy cargados (~100+ avisos): el sitio continúa por scroll.
            while esperado and len(ids) < esperado and pag <= MAX_PAGINAS_SUMARIO:
                r2 = _request(
                    client, "GET", f"{BORA}/seccion/actualizar/{sec}",
                    params={"pag": str(pag), "ult_rubro": ult_rubro},
                    headers={"X-Requested-With": "XMLHttpRequest"},
                )
                if r2.status_code != 200:
                    break
                data = r2.json()
                html = data.get("html") or ""
                if not html.strip():
                    break
                nuevos, ult_rubro = _parse_items(_soup(html), ult_rubro)
                nuevos = [i for i in nuevos if i["id"] not in ids]
                if not nuevos:
                    break
                items += nuevos
                ids |= {i["id"] for i in nuevos}
                if not data.get("hay_mas_datos"):
                    break
                pag = int(data.get("sig_pag") or pag + 1)
    except (SourceError, httpx.HTTPError, ValueError) as exc:
        return _err(f"Boletín Oficial no disponible: {exc}")

    if not items and not rubro_id:
        return _err(f"No se encontraron avisos en la sección {sec} del {fecha}.")
    out = {
        "jurisdiccion": "nacional",
        "seccion": sec,
        "seccion_nombre": _SECCION_NOMBRE[sec],
        "fecha": fecha,
        "total": len(items),
        "total_esperado": esperado,
        "rubros": rubros,
        "avisos": items,
        "fuente": FUENTE_BORA,
        "url": url,
    }
    if esperado and len(items) < esperado:
        out["nota"] = "Sumario parcial: el sitio no devolvió todas las páginas; filtrá por rubro_id."
    _cache_put(key, out, _ttl(d))
    return out


# ── búsqueda ──────────────────────────────────────────────────────────────────

def _params_busqueda(texto: str, secs: list[int], desde: str, hasta: str, todas: bool,
                     cursor: dict | None) -> dict:
    return {
        "texto": texto, "seccion": secs, "rubros": [], "nroNorma": "", "anioNorma": "",
        "ordenamiento": "", "denominacion": "", "tipoContratacion": "",
        "anioContratacion": "", "nroContratacion": "", "fechaDesde": desde,
        "fechaHasta": hasta, "tipoBusqueda": "Avanzada",
        "numeroPagina": cursor["p"] if cursor else 1,
        "ultimoRubro": cursor["r"] if cursor else "", "busquedaRubro": False,
        "hayMasResultadosBusqueda": True, "ejecutandoLlamadaAsincronicaBusqueda": False,
        "ultimaSeccion": cursor["s"] if cursor else "", "todasLasPalabras": todas,
        "filtroPorRubrosSeccion": False, "filtroPorRubroBusqueda": False,
        "filtroPorSeccionBusqueda": False, "busquedaOriginal": cursor is None,
        "comienzaDenominacion": False, "ordenamientoSegunda": True,
        "seccionesOriginales": secs, "ultimoItemExterno": None, "ultimoItemInterno": None,
    }


def _parse_cursor(cursor: str | None) -> dict | None:
    if not cursor:
        return None
    try:
        c = json.loads(cursor)
        return {"p": int(c["p"]), "s": str(c.get("s", "")), "r": str(c.get("r", ""))}
    except (ValueError, KeyError, TypeError) as exc:
        raise ValueError(
            "cursor inválido: usá el 'cursor_siguiente' devuelto por la búsqueda anterior."
        ) from exc


def _enriquecer_busqueda(soup: BeautifulSoup, items: list[dict]) -> None:
    """Fecha de publicación y extracto con la coincidencia, propios del listado de búsqueda."""
    por_id = {i["id"]: i for i in items}
    for a in soup.select("a[href*='/detalleAviso/']"):
        m = _HREF_AVISO.search(a.get("href") or "")
        it = por_id.get(m.group(2)) if m else None
        if not it or "extracto" in it:
            continue
        smalls = [_text(x) for x in a.select("p.item-detalle small")]
        resto = []
        for sm in smalls[1:]:
            f = re.match(r"Fecha de Publicaci\S*:\s*(\d{2})/(\d{2})/(\d{4})", sm)
            if f:
                it["fecha"] = f"{f.group(3)}-{f.group(2)}-{f.group(1)}"
            else:
                resto.append(sm)
        it.pop("detalle", None)
        if resto:
            it["extracto"] = resto[-1][:400]


def buscar_nacional(
    *, texto: str, seccion: str = "primera", fecha_desde: str | None = None,
    fecha_hasta: str | None = None, todas_las_palabras: bool = True,
    cursor: str | None = None, user_agent: str, timeout: float = 25.0,
) -> dict:
    """Búsqueda avanzada de texto en el BORA (primera y/o tercera sección)."""
    texto = (texto or "").strip()
    if len(texto) < 3:
        return _err("Indicá un 'texto' de al menos 3 caracteres.")
    s = (seccion or "").strip().lower()
    if s == "segunda":
        return _segunda_vetada()
    if s == "todas":
        secs = [1, 3]
    elif s in SECCIONES:
        secs = [SECCIONES[s]]
    else:
        return _err("Sección inválida; usá 'primera', 'tercera' o 'todas' (segunda no disponible).")
    d1 = d2 = None
    if fecha_desde:
        d1 = _parse_iso(fecha_desde)
        if d1 is None:
            return _err("fecha_desde inválida; usá YYYY-MM-DD.")
    if fecha_hasta:
        d2 = _parse_iso(fecha_hasta)
        if d2 is None:
            return _err("fecha_hasta inválida; usá YYYY-MM-DD.")
    if d1 and d2 and d1 > d2:
        return _err("fecha_desde no puede ser posterior a fecha_hasta.")
    try:
        cur = _parse_cursor(cursor)
    except ValueError as exc:
        return _err(str(exc))

    desde = d1.strftime("%d/%m/%Y") if d1 else ""
    hasta = d2.strftime("%d/%m/%Y") if d2 else ""
    key = f"bus|{texto.lower()}|{s}|{desde}|{hasta}|{todas_las_palabras}|{cursor or ''}"
    hit = _cache_get(key)
    if hit is not None:
        return hit

    params = _params_busqueda(texto, secs, desde, hasta, todas_las_palabras, cur)
    try:
        with httpx.Client(timeout=timeout, headers=_headers(user_agent)) as client:
            resp = _request(
                client, "POST", f"{BORA}/busquedaAvanzada/realizarBusqueda",
                data={"params": json.dumps(params), "array_volver": "[]"},
                headers={"X-Requested-With": "XMLHttpRequest"},
            )
        if resp.status_code != 200:
            return _err(f"El Boletín Oficial respondió HTTP {resp.status_code} a la búsqueda.")
        data = resp.json()
    except (SourceError, httpx.HTTPError, ValueError) as exc:
        return _err(f"Búsqueda en el Boletín Oficial no disponible: {exc}")

    if data.get("error"):
        msgs = "; ".join(map(str, data.get("mensajes") or []))
        return _err("El Boletín Oficial rechazó la búsqueda" + (f": {msgs}" if msgs else "."))
    content = data.get("content") or {}
    soup = _soup(content.get("html") or "")
    items, _ = _parse_items(soup)
    _enriquecer_busqueda(soup, items)
    cantidades = content.get("cantidad_result_seccion") or {}
    total = 0
    for v in (cantidades.values() if isinstance(cantidades, dict) else cantidades):
        try:
            total += int(v)
        except (TypeError, ValueError):
            pass
    items = items[:MAX_RESULTADOS]
    out: dict = {
        "jurisdiccion": "nacional",
        "texto": texto,
        "seccion": s,
        "fecha_desde": fecha_desde,
        "fecha_hasta": fecha_hasta,
        "total_coincidencias": total,
        "devueltos": len(items),
        "resultados": items,
        "fuente": FUENTE_BORA,
    }
    if items and content.get("sig_pag") and (cur is not None or len(items) < total):
        out["cursor_siguiente"] = json.dumps({
            "p": int(content["sig_pag"]),
            "s": content.get("ult_seccion") or "",
            "r": content.get("ult_rubro") or "",
        })
    if not items:
        out["nota"] = "Sin resultados."
    _cache_put(key, out, 600.0)
    return out


# ── detalle ───────────────────────────────────────────────────────────────────

_BLOQUES = ("p", "div", "br", "tr", "li", "h1", "h2", "h3", "h4", "h5", "h6")


def _limpiar_cuerpo(cuerpo) -> str:
    for t in cuerpo.find_all(["style", "script"]):
        t.decompose()
    for td in cuerpo.find_all(["td", "th"]):
        td.append(" | ")
    for t in cuerpo.find_all(_BLOQUES):
        t.append("\n")
    txt = cuerpo.get_text("").replace("\xa0", " ")
    txt = re.sub(r"[ \t]+", " ", txt)
    txt = re.sub(r" ?\| ?\n", "\n", txt)
    txt = re.sub(r"\n[ \t]*", "\n", txt)
    return re.sub(r"\n{3,}", "\n\n", txt).strip()


def _trozo(texto: str, pagina: int, max_chars: int) -> tuple[str, int, int]:
    """Parte `texto` en páginas de ~max_chars cortando en salto de línea."""
    paginas: list[str] = []
    resto = texto
    while len(resto) > max_chars:
        corte = resto.rfind("\n", int(max_chars * 0.6), max_chars)
        corte = corte if corte > 0 else max_chars
        paginas.append(resto[:corte].strip())
        resto = resto[corte:].lstrip("\n")
    paginas.append(resto)
    pagina = min(max(pagina, 1), len(paginas))
    return paginas[pagina - 1], len(paginas), pagina


def _resolver_ref(id_aviso: str, fecha: str | None, seccion: str | None):
    m = _HREF_AVISO.search(id_aviso or "")
    if m:
        return m.group(2), _fecha_iso8(m.group(3)), m.group(1)
    m = re.fullmatch(r"\s*(?:(primera|segunda|tercera)[/:])?(\d{1,9})\s*", id_aviso or "")
    if m:
        return m.group(2), fecha, m.group(1) or seccion
    return None, fecha, seccion


def ver_nacional(
    *, id_aviso: str, fecha: str | None = None, seccion: str = "primera",
    pagina: int = 1, max_chars: int = DEFAULT_CHARS, user_agent: str, timeout: float = 20.0,
) -> dict:
    """Texto completo de un aviso/norma del BORA (primera o tercera), limpio de HTML."""
    id_, fecha, seccion_r = _resolver_ref(str(id_aviso), fecha, seccion)
    if not id_:
        return _err("id_aviso inválido: pasá el id numérico o el link /detalleAviso/... del sumario.")
    sec = _norm_seccion(seccion_r)
    if sec is None:
        return _err("Sección inválida; usá 'primera' o 'tercera'.")
    if sec == "segunda":
        return _segunda_vetada()
    d = _parse_iso(fecha)
    if d is None:
        return _err("Indicá 'fecha' de publicación en formato YYYY-MM-DD (figura en el sumario).")
    try:
        pagina = int(pagina)
        max_chars = max(2000, min(int(max_chars), MAX_CHARS))
    except (TypeError, ValueError):
        return _err("pagina y max_chars deben ser enteros.")

    ymd = d.strftime("%Y%m%d")
    key = f"ver|{sec}|{id_}|{ymd}"
    base = _cache_get(key)
    if base is None:
        url = f"{BORA}/detalleAviso/{sec}/{id_}/{ymd}"
        try:
            with httpx.Client(timeout=timeout, headers=_headers(user_agent)) as client:
                resp = _request(client, "GET", url)
        except (SourceError, httpx.HTTPError) as exc:
            return _err(f"Boletín Oficial no disponible: {exc}")
        if resp.status_code != 200:
            return _err(f"No se encontró el aviso {id_} ({sec}, {fecha}): HTTP {resp.status_code}.")
        soup = _soup(resp.content)
        cuerpo = soup.select_one("#cuerpoDetalleAviso")
        if cuerpo is None:
            return _err(f"No se encontró el aviso {id_} en la sección {sec} para {fecha}; "
                        "revisá el id y la fecha de publicación.")
        titulo = soup.select_one("#tituloDetalleAviso")
        bc = soup.select(".breadcrumb li a")
        fecha_pub = fecha
        for a in bc:
            f = re.search(r"/seccion/\w+/(\d{8})", a.get("href") or "")
            if f:
                fecha_pub = _fecha_iso8(f.group(1))
                break
        h6 = titulo.find("h6") if titulo else None
        base = {
            "id": id_,
            "seccion": sec,
            "fecha_publicacion": fecha_pub,
            "rubro": _text(bc[1]) if len(bc) >= 3 else None,
            "organismo": _text(titulo.find("h1")) if titulo else None,
            "titulo": _text(titulo.find("h2")) if titulo else None,
            "referencia": _text(h6) if h6 else None,
            "texto": _limpiar_cuerpo(cuerpo),
            "url": url,
            "url_pdf": f"{BORA}/pdf/aviso/{sec}/{id_}/{ymd}",
            "fuente": FUENTE_BORA,
        }
        _cache_put(key, base, _ttl(d, hoy=1800.0))
    trozo, total, pag = _trozo(base["texto"], pagina, max_chars)
    out = {k: v for k, v in base.items() if k != "texto"}
    out.update({
        "texto": trozo,
        "pagina": pag,
        "total_paginas": total,
        "caracteres_totales": len(base["texto"]),
    })
    if pag < total:
        out["nota"] = f"Texto truncado: pedí pagina={pag + 1} para continuar."
    return out


# ═══════════════════════════════════════════════════════════════════════════════
# Registro de tools
# ═══════════════════════════════════════════════════════════════════════════════

def register(mcp, *, ro: dict, user_agent: str, timeout: float) -> None:
    """Registra todas las tools del Boletín Oficial en ``mcp``."""
    ua = user_agent

    @mcp.tool(name="boletin_oficial_sumario", **ro)
    def boletin_oficial_sumario(
        fecha: str,
        seccion: str = "primera",
        jurisdiccion: str = "nacional",
        rubro_id: str | None = None,
    ) -> dict:
        """Sumario del Boletin Oficial de un dia: avisos publicados, con id y link.

        CUANDO USARLA: para ver que se publico en el Boletin Oficial NACIONAL en una fecha
        (ISO 'YYYY-MM-DD'): seccion='primera' (decretos, resoluciones, disposiciones y
        avisos oficiales) o 'tercera' (contrataciones del Estado). Cada aviso trae id,
        fecha, rubro, organismo, titulo (p. ej. 'Resolucion 297/2026') y url; el campo
        'rubros' lista los rubros con su id para filtrar con rubro_id. Despues usa
        boletin_oficial_ver con el id y la fecha para leer el texto. LIMITES: la seccion
        'segunda' (sociedades) NO esta disponible (el robots.txt del sitio la veta); no hay
        edicion en fines de semana ni feriados (devuelve error); dias muy cargados pueden
        venir parciales (ver 'nota'). jurisdiccion='caba' devuelve en cambio las secciones
        del Boletin Oficial de la Ciudad de Buenos Aires (otra institucion; ignora 'seccion').
        """
        j = (jurisdiccion or "").strip().lower()
        if j == "caba":
            return buscar(fecha=fecha, jurisdiccion="caba", user_agent=ua, timeout=timeout)
        if j != "nacional":
            return _err("jurisdiccion inválida; usá 'nacional' o 'caba'.")
        return sumario_nacional(
            fecha=fecha, seccion=seccion, rubro_id=rubro_id, user_agent=ua, timeout=timeout
        )

    @mcp.tool(name="boletin_oficial_buscar", **ro)
    def boletin_oficial_buscar(
        texto: str | None = None,
        seccion: str = "primera",
        fecha_desde: str | None = None,
        fecha_hasta: str | None = None,
        todas_las_palabras: bool = True,
        cursor: str | None = None,
        jurisdiccion: str = "nacional",
        fecha: str | None = None,
    ) -> dict:
        """Busqueda de texto en el Boletin Oficial nacional (primera y tercera seccion).

        CUANDO USARLA: para encontrar avisos y normas publicados por palabra clave (numero
        de ley o decreto, organismo, tema), opcionalmente en un rango de fechas ISO
        'YYYY-MM-DD'. seccion='primera' (legislacion y avisos oficiales), 'tercera'
        (contrataciones) o 'todas' (ambas). Devuelve id, fecha de publicacion, organismo,
        titulo y un extracto con la coincidencia; 'total_coincidencias' es el total en el
        sitio y 'cursor_siguiente' (si existe) se pasa como 'cursor' para la pagina
        siguiente. Luego usa boletin_oficial_ver. LIMITES: texto de 3+ caracteres; la
        seccion 'segunda' (sociedades) NO esta disponible (robots.txt del sitio); es la
        busqueda textual del sitio oficial (no semantica) y cada pagina trae hasta ~100
        avisos. Compat.: jurisdiccion='caba' con 'fecha' devuelve el sumario de la Ciudad
        de Buenos Aires (preferi boletin_oficial_sumario).
        """
        if (jurisdiccion or "").strip().lower() == "caba":
            return buscar(fecha=fecha, jurisdiccion="caba", user_agent=ua, timeout=timeout)
        if not texto:
            return _err(
                "Indicá 'texto' a buscar (para ver un día completo usá boletin_oficial_sumario)."
            )
        return buscar_nacional(
            texto=texto, seccion=seccion, fecha_desde=fecha_desde, fecha_hasta=fecha_hasta,
            todas_las_palabras=todas_las_palabras, cursor=cursor, user_agent=ua,
            timeout=min(timeout, 30.0),
        )

    @mcp.tool(name="boletin_oficial_ver", **ro)
    def boletin_oficial_ver(
        id_aviso: str,
        fecha: str | None = None,
        seccion: str = "primera",
        pagina: int = 1,
        max_chars: int = DEFAULT_CHARS,
    ) -> dict:
        """Texto completo de un aviso o norma del Boletin Oficial nacional, sin HTML.

        CUANDO USARLA: para leer el texto publicado (decreto, resolucion, disposicion,
        aviso, llamado a contratacion) de un id obtenido con boletin_oficial_sumario o
        boletin_oficial_buscar. Pasa el id y la fecha de publicacion ISO 'YYYY-MM-DD' (o
        directamente el link '/detalleAviso/primera/ID/YYYYMMDD'). Devuelve organismo,
        titulo, rubro, fecha_publicacion, texto, url oficial y url_pdf. Los textos largos
        se paginan con 'pagina' y 'max_chars' (hasta 30000); mira 'total_paginas'. LIMITES:
        solo primera y tercera seccion (la segunda no esta disponible por robots.txt); no
        incluye anexos ni imagenes (usa url_pdf); es la version publicada, no el texto
        actualizado: para eso usa InfoLEG.
        """
        return ver_nacional(
            id_aviso=id_aviso, fecha=fecha, seccion=seccion, pagina=pagina,
            max_chars=max_chars, user_agent=ua, timeout=timeout,
        )
