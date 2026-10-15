"""Text hygiene for everything we hand back to the model.

The official dataset mixes plain text with raw HTML (``<A HREF=...;jsessionid=...>``),
HTML entities and InfoLEG session ids. ``limpiar`` turns any of that into plain,
readable text; ``html_a_markdown`` is for full bodies (annexes, embedded HTML).
"""

from __future__ import annotations

import html
import re

_JSESSION = re.compile(r";jsessionid=[0-9A-Za-z._-]+", re.IGNORECASE)
_TAG = re.compile(r"<[^>]{0,2000}>")
_WS = re.compile(r"[ \t\r\f\v\xa0]+")
_NL = re.compile(r"\n{3,}")


def sin_jsessionid(texto: str) -> str:
    """Drop InfoLEG ``;jsessionid=...`` fragments from URLs inside a text."""
    return _JSESSION.sub("", texto)


def limpiar(texto: str | None) -> str | None:
    """Plain text from a dataset cell: no tags, no entities, no session ids."""
    if texto is None:
        return None
    t = str(texto)
    if not t.strip():
        return None
    # Entities first (cells may hold escaped markup like ``&lt;A HREF...``), then tags.
    for _ in range(2):
        unescaped = html.unescape(t)
        if unescaped == t:
            break
        t = unescaped
    t = sin_jsessionid(t)
    t = _TAG.sub(" ", t)
    t = _WS.sub(" ", t)
    t = "\n".join(line.strip() for line in t.splitlines())
    t = _NL.sub("\n\n", t).strip()
    return t or None


def html_a_markdown(raw_html: str) -> str:
    """Convert an HTML body (annex page or embedded cell) into clean Markdown."""
    from bs4 import BeautifulSoup
    from markdownify import markdownify as md

    soup = BeautifulSoup(raw_html, "lxml")
    for tag in soup(["script", "style", "header", "footer", "nav", "noscript"]):
        tag.decompose()
    out = md(str(soup), heading_style="ATX")
    out = sin_jsessionid(out)
    out = "\n".join(line.rstrip() for line in out.splitlines())
    return _NL.sub("\n\n", out).strip()
