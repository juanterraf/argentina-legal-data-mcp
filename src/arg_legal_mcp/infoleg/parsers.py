"""Parsers for the real InfoLEG HTML.

InfoLEG returns hand-rolled HTML (tables, named spans). These parsers target the
known structure but degrade gracefully: a missing field yields ``None``/empty
rather than an exception, so a partial layout change does not take down search.
"""

from __future__ import annotations

import re
from datetime import date
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup, Tag

from .models import (
    BusquedaConfig,
    BusquedaNormaResponse,
    Dependencia,
    ModoVinculo,
    NormaSummary,
    TipoNorma,
    VerNormaResponse,
    VerVinculosResponse,
    VinculoNormaSummary,
)

MESES = {
    "ene": 1, "feb": 2, "mar": 3, "abr": 4, "may": 5, "jun": 6,
    "jul": 7, "ago": 8, "sep": 9, "set": 9, "oct": 10, "nov": 11, "dic": 12,
}


class NormaNotFoundError(Exception):
    """The requested norma is not registered in InfoLEG."""


class SearchQueryMaximumExceeded(Exception):
    """The filters are too broad (InfoLEG caps overly generic queries)."""


def _soup(html: str) -> BeautifulSoup:
    # lxml is fast and lenient; falls back cleanly on malformed markup.
    return BeautifulSoup(html, "lxml")


def _clean(text: str | None) -> str:
    if not text:
        return ""
    return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()


def parse_fecha_es(texto: str | None) -> date | None:
    """Parse an InfoLEG ``dd-mes-yyyy`` Spanish date (e.g. ``16-nov-2016``)."""
    if not texto:
        return None
    m = re.search(r"(\d{1,2})[-/\s]([a-zA-Z]{3})[a-zA-Z]*[-/\s](\d{4})", texto.strip().lower())
    if not m:
        return None
    day, mes_str, year = m.groups()
    mes = MESES.get(mes_str[:3])
    if not mes:
        return None
    try:
        return date(int(year), mes, int(day))
    except ValueError:
        return None


def extract_id(source: str | None) -> int | None:
    """Extract a numeric InfoLEG id from a URL, query string, or annex path."""
    if not source:
        return None
    if "id=" in source:
        try:
            parsed = parse_qs(urlparse(source).query)
            if "id" in parsed:
                return int(parsed["id"][0])
        except (ValueError, IndexError):
            pass
        m = re.search(r"id=(\d+)", source)
        if m:
            return int(m.group(1))
    m = re.search(r"/(\d+)/(?:norma|texact)\.htm", source)
    if m:
        return int(m.group(1))
    return None


# ── Búsquedas (buscarNormas.do) ───────────────────────────────────────────────
class InfoLegBusquedasParser:
    def _total_y_pagina(self, html: str) -> tuple[int, int]:
        m = re.search(r"Cantidad de Normas Encontradas:\s*(\d+)[\s\S]*?en\s*(\d+)", html)
        if not m:
            return 0, 1
        return int(m.group(1)), int(m.group(2))

    def _es_fila_datos(self, tds: list[Tag]) -> bool:
        if len(tds) < 3:
            return False
        if "titulos_columnas" in tds[0].get("class", []):
            return False
        return True

    def _parse_td_norma(self, td: Tag) -> dict | None:
        a = td.find("a")
        if not a:
            return None
        id_norma = extract_id(a.get("href", ""))
        if id_norma is None:
            return None
        textos = list(td.stripped_strings)
        return {
            "id": id_norma,
            "identidad_norma": _clean(textos[0]) if textos else "",
            "organismo_emisor": _clean(textos[1]) if len(textos) > 1 else None,
        }

    def _parse_td_boletin(self, td: Tag) -> dict:
        a = td.find("a")
        if not a:
            return {}
        return {
            "id_boletin": extract_id(a.get("href", "")),
            "fecha_publicacion": parse_fecha_es(a.get_text(strip=True)),
        }

    def _parse_td_tema(self, td: Tag) -> dict:
        b = td.find("b")
        span = td.find("span")
        return {
            "tema": _clean(b.get_text()) if b else None,
            "sumario": _clean(span.get_text()) if span else None,
        }

    def parse(self, html: str) -> BusquedaNormaResponse:
        total, total_pags = self._total_y_pagina(html)
        soup = _soup(html)
        caja = soup.find("div", id="resultados_caja")
        tabla = caja.find("table") if caja else None
        if tabla is None:
            return BusquedaNormaResponse(resultados=[], total_pags=total_pags, total=total)

        resultados: list[NormaSummary] = []
        for tr in tabla.find_all("tr"):
            tds = tr.find_all("td")
            if not self._es_fila_datos(tds):
                continue
            datos_norma = self._parse_td_norma(tds[0])
            if not datos_norma:
                continue
            datos_boletin = self._parse_td_boletin(tds[1])
            datos_tema = self._parse_td_tema(tds[2])
            resultados.append(NormaSummary(**datos_norma, **datos_boletin, **datos_tema))

        return BusquedaNormaResponse(resultados=resultados, total_pags=total_pags, total=total)


# ── Ficha (verNorma.do) ───────────────────────────────────────────────────────
class InfoLegNormaParser:
    def __init__(self, norma_id: int):
        self.norma_id = norma_id

    def parse(self, html: str) -> VerNormaResponse:
        soup = _soup(html)
        error = soup.find("span", class_="error")
        if error and "no se encuentra registrada" in error.get_text().lower():
            raise NormaNotFoundError(_clean(error.get_text()))

        textos = soup.find("div", id="Textos_Completos")
        if textos is None:
            # No structured ficha — return a minimal summary rather than crash.
            return VerNormaResponse(summary=NormaSummary(id=self.norma_id))

        identidad, organismo_emisor = self._identidad(textos)
        id_boletin, fecha_pub, pagina = self._boletin(textos)
        url_completo, url_actualizado = self._urls(textos)
        modifica, modifican = self._vinculos_counts(textos)

        return VerNormaResponse(
            summary=NormaSummary(
                id=self.norma_id,
                identidad_norma=identidad,
                organismo_emisor=organismo_emisor,
                id_boletin=id_boletin,
                fecha_publicacion=fecha_pub,
                organismo_padre=self._first_text(textos, "span", "destacado"),
                tema=self._first_text(textos, "h1"),
                sumario=self._sumario(textos),
            ),
            fecha_emision=self._fecha_emision(textos),
            pagina_boletin=pagina,
            url_texto_completo=url_completo,
            url_texto_actualizado=url_actualizado,
            normas_que_modifica=modifica,
            normas_que_modifican_esta=modifican,
        )

    def _first_text(self, root: Tag, name: str, cls: str | None = None) -> str | None:
        tag = root.find(name, class_=cls) if cls else root.find(name)
        return _clean(tag.get_text()) if tag else None

    def _identidad(self, textos: Tag) -> tuple[str, str | None]:
        p = textos.find("p")
        strong = p.find("strong") if p else None
        if not strong:
            return "", None
        lines = [ln.strip() for ln in strong.get_text("\n").splitlines() if ln.strip()]
        if not lines:
            return "", None
        if len(lines) == 1:
            return _clean(lines[0]), None
        return _clean(" ".join(lines[:-1])), _clean(lines[-1])

    def _fecha_emision(self, textos: Tag) -> date | None:
        p = textos.find("p")
        span = p.find("span", class_="vr_azul11") if p else None
        return parse_fecha_es(span.get_text(strip=True)) if span else None

    def _boletin(self, textos: Tag) -> tuple[int | None, date | None, int | None]:
        p = next((p for p in textos.find_all("p") if "Publicada" in p.get_text()), None)
        if not p:
            return None, None, None
        links = p.find_all("a")
        id_boletin = extract_id(links[0].get("href", "")) if links else None
        fecha = parse_fecha_es(links[0].get_text(strip=True)) if links else None
        m = re.search(r"P[aá]gina:\s*(\d+)", p.get_text())
        return id_boletin, fecha, (int(m.group(1)) if m else None)

    def _sumario(self, textos: Tag) -> str | None:
        p = next((p for p in textos.find_all("p") if "Resumen" in p.get_text()), None)
        if not p:
            return None
        return _clean(re.sub(r"Resumen\s*:", "", p.get_text(separator=" ", strip=True)))

    def _urls(self, textos: Tag) -> tuple[str | None, str | None]:
        url_completo = url_actualizado = None
        for a in textos.find_all("a"):
            href = a.get("href", "")
            if "norma.htm" in href:
                url_completo = href
            elif "texact.htm" in href:
                url_actualizado = href
        return url_completo, url_actualizado

    def _vinculos_counts(self, textos: Tag) -> tuple[int | None, int | None]:
        modifica = modifican = None
        for a in textos.find_all("a"):
            m = re.search(r"(\d+)\s+norma", a.get_text(strip=True))
            if not m:
                continue
            href = a.get("href", "")
            if "modo=1" in href:
                modifica = int(m.group(1))
            elif "modo=2" in href:
                modifican = int(m.group(1))
        return modifica, modifican


# ── Vínculos (verVinculos.do) ─────────────────────────────────────────────────
class VerVinculosParser:
    def __init__(self, html: str, id_norma: int, modo: ModoVinculo):
        self._soup = _soup(html)
        self._id = id_norma
        self._modo = modo

    def parse(self) -> VerVinculosResponse:
        celdas = self._soup.select("td.vr_azul11")
        vinculos: list[VinculoNormaSummary] = []
        i = 0
        while i < len(celdas) - 2:
            v = self._parse_norma(celdas[i], celdas[i + 1], celdas[i + 2])
            if v:
                vinculos.append(v)
                i += 3
            else:
                i += 1
        return VerVinculosResponse(id=self._id, modo=self._modo, vinculos=vinculos)

    def _parse_norma(self, celda_norma: Tag, celda_fecha: Tag, celda_desc: Tag):
        link = celda_norma.find("a")
        if not link:
            return None
        id_vinculo = extract_id(link.get("href", ""))
        if not id_vinculo:
            return None
        textos = list(celda_norma.stripped_strings)
        org_padre = celda_desc.find("b")
        br = celda_desc.find("br")
        tema = _clean(str(br.next_sibling)) if br and br.next_sibling else ""
        return VinculoNormaSummary(
            id=id_vinculo,
            identidad_norma=_clean(textos[0]) if textos else None,
            organismo_emisor=_clean(textos[1]) if len(textos) > 1 else None,
            fecha_publicacion=parse_fecha_es(celda_fecha.get_text()),
            organismo_padre=_clean(org_padre.get_text()) if org_padre else None,
            tema=tema,
        )


# ── Config / catalogs (mostrarBusquedaNormas.do) ───────────────────────────────
class InfoLegConfigParser:
    def parse(self, html: str) -> BusquedaConfig:
        soup = _soup(html)
        return BusquedaConfig(
            dependencias=self._options(soup, "dependencia", Dependencia),
            tipos_norma=self._options(soup, "tipoNorma", TipoNorma),
        )

    def _options(self, soup: BeautifulSoup, select_name: str, model):
        select = soup.find("select", {"name": select_name})
        if not select:
            return []
        out = []
        for opt in select.find_all("option"):
            value = (opt.get("value") or "").strip()
            if value.isdigit():
                out.append(model(id=int(value), nombre=_clean(opt.get_text())))
        return out
