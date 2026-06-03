"""Pure HTTP client for the live InfoLEG service (no caching here — see services.py).

Speaks the real ``servicios.infoleg.gob.ar/infolegInternet`` contract:
  * POST ``buscarNormas.do``       — search (and pagination, same session)
  * GET  ``verNorma.do?id=``       — ficha / metadata
  * GET  ``verVinculos.do?modo=&id=`` — modifications graph (modo 1=activa, 2=pasiva)
  * GET  ``mostrarBusquedaNormas.do`` — catalogs (dependencia / tipoNorma selects)
  * GET  annex ``norma.htm`` / ``texact.htm`` — full text (ISO-8859-1 -> Markdown)
"""

from __future__ import annotations

import time

import httpx
from markdownify import markdownify as md

from .models import (
    BusquedaConfig,
    BusquedaNormaRequest,
    BusquedaNormaResponse,
    PaginacionRequest,
    ParamsVerNorma,
    ParamsVerVinculos,
    VerNormaResponse,
    VerVinculosResponse,
)
from .parsers import (
    InfoLegBusquedasParser,
    InfoLegConfigParser,
    InfoLegNormaParser,
    VerVinculosParser,
)


class InfoLegClient:
    MAX_RETRIES = 4
    BACKOFF = 0.5  # seconds: 0.5, 1, 2, ...

    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")

    def _request(self, client: httpx.Client, method: str, url: str, **kwargs) -> httpx.Response:
        last_exc: Exception | None = None
        for attempt in range(self.MAX_RETRIES):
            try:
                resp = client.request(method, url, **kwargs)
                # Retry transient server errors with backoff; fail fast on 4xx.
                if resp.status_code >= 500:
                    raise httpx.HTTPStatusError(
                        f"server error {resp.status_code}", request=resp.request, response=resp
                    )
                resp.raise_for_status()
                return resp
            except (httpx.TimeoutException, httpx.TransportError, httpx.HTTPStatusError) as exc:
                last_exc = exc
                if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code < 500:
                    raise
                if attempt == self.MAX_RETRIES - 1:
                    raise
                time.sleep(self.BACKOFF * (2**attempt))
        assert last_exc is not None
        raise last_exc

    # ── search ────────────────────────────────────────────────────────────---
    def buscar_normas(
        self, client: httpx.Client, request: BusquedaNormaRequest
    ) -> BusquedaNormaResponse:
        payload = request.model_dump(exclude_none=True)
        resp = self._request(client, "POST", f"{self.base_url}/buscarNormas.do", data=payload)
        return InfoLegBusquedasParser().parse(resp.text)

    def navegar_normas(
        self, client: httpx.Client, request: PaginacionRequest
    ) -> BusquedaNormaResponse:
        payload = request.model_dump(exclude_none=True)
        if hasattr(request.desplazamiento, "value"):
            payload["desplazamiento"] = request.desplazamiento.value
        resp = self._request(client, "POST", f"{self.base_url}/buscarNormas.do", data=payload)
        return InfoLegBusquedasParser().parse(resp.text)

    # ── ficha / vinculos / catalogs ───────────────────────────────────────---
    def ver_norma(self, client: httpx.Client, params: ParamsVerNorma) -> VerNormaResponse:
        resp = self._request(
            client, "GET", f"{self.base_url}/verNorma.do",
            params=params.model_dump(exclude_none=True),
        )
        return InfoLegNormaParser(params.id).parse(resp.text)

    def ver_vinculos(self, client: httpx.Client, params: ParamsVerVinculos) -> VerVinculosResponse:
        resp = self._request(
            client, "GET", f"{self.base_url}/verVinculos.do",
            params={"id": params.id, "modo": int(params.modo)},
        )
        return VerVinculosParser(resp.text, params.id, params.modo).parse()

    def mostrar_opciones_busqueda(self, client: httpx.Client) -> BusquedaConfig:
        resp = self._request(client, "GET", f"{self.base_url}/mostrarBusquedaNormas.do")
        return InfoLegConfigParser().parse(resp.text)

    # ── full text (annex) ─────────────────────────────────────────────────---
    def consultar_anexo(self, client: httpx.Client, url_absoluta: str) -> str:
        """GET a resolved annex URL and return Markdown. Handles InfoLEG's latin-1."""
        from bs4 import BeautifulSoup

        resp = self._request(client, "GET", url_absoluta)
        enc = (resp.charset_encoding or "").lower()
        if not enc or enc in ("iso-8859-1", "latin-1", "ascii"):
            html = resp.content.decode("latin-1", errors="replace")
        else:
            html = resp.text

        soup = BeautifulSoup(html, "lxml")
        for tag in soup(["script", "style", "header", "footer", "nav"]):
            tag.decompose()
        return md(str(soup), heading_style="ATX").strip()
