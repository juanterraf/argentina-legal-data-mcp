# CREDITS

This project stands on the shoulders of public work and open data. Thank you.

## Reference projects (studied for behaviour & patterns — see `NOTICE` for licenses)
- **martindieser/InfoLegMCP** — the best open reverse-engineering of the real
  InfoLEG search; primary reference for the InfoLEG logic.
- **abenassi/argentina-data-mcp** — the most mature product reference; auth,
  health/freshness, scheduled collectors, recency-boosted ranking.
- **voftec/InfoLeg-MCP** — diff/boletín/prompt ideas, and an instructive set of
  anti-patterns we explicitly avoid.

## Data
- **InfoLEG** — Información Legislativa y Documental, Ministerio de Justicia de la
  Nación Argentina. <https://www.infoleg.gob.ar>
- **datos.jus.gob.ar / datos.gob.ar** — "Base InfoLeg - Normativa Nacional"
  (CC BY 2.5 AR).

## Tooling
- Model Context Protocol & the Python SDK / FastMCP (`mcp[cli]`).
- httpx, BeautifulSoup, lxml, markdownify, rapidfuzz, diskcache, pydantic.
