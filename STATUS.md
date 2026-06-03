# STATUS — what's done and what's pending

## Definition of done (from the brief §6)

- [x] `infoleg_buscar_normas` works in **dataset** mode and **live** mode (native operators).
- [x] Reads the **text** (original + actualizado), paginated, from the **real URL**
      (test forbids the 50,000-block calculation).
- [x] Modification traceability in **both** directions (live `verVinculos`, dataset fallback).
- [x] Fuzzy dependency search + tipos resource.
- [x] Graceful **dataset ↔ live degradation** with an `aviso` in the response.
- [x] **Auth** on HTTP (discovery open / execution protected); disabled on stdio.
- [x] **TLS verified** on every request (httpx default; never disabled).
- [x] Persistent **cache** (diskcache) + **health-check** (`data_health`, `data_freshness`).
- [x] **Dockerfile** builds; server runs on stdio and http; systemd + Caddy documented.
- [x] `pytest` green (37 tests, incl. mocked integration) and `ruff` clean.
- [x] README complete (local + remote + Claude connector + env vars).
- [x] No hardcoded secrets; `.env.example` + `secrets/api-keys.json.example` provided.

## Phase coverage

- **Phase 0** — ✅ skeleton, pyproject, ruff, pytest, FastMCP server.
- **Phase 1** — ✅ InfoLEG core: dataset (SQLite+FTS5, recency boost), live client
  (stateful session, POST `buscarNormas.do`, `verNorma`, `verVinculos`, `consultar_anexo`,
  `mostrarBusquedaNormas`), defensive parsers, catalogs + rapidfuzz, diskcache,
  2-level pagination + chunking, 12 InfoLEG tools, resource, prompts, tests.
- **Phase 2** — ✅ dual transport (stdio + streamable-http/sse), auth (hot-reload API
  keys + ASGI middleware), health/freshness + request log, Docker/compose/systemd/Caddy,
  README.
- **Phase 3** — ✅ extensible **sources** framework + 8 extra-source tools, all with the
  API contracts **live-verified** (a research workflow fetched real samples) and covered
  by mocked tests:
  - **dólar** (DolarAPI), **BCRA** variables + cotizaciones (v4.0 — v2/v3 are 410 Gone),
    **INDEC**/datos.gob.ar series + search, **feriados** (ArgentinaDatos),
    **tributaria** (filtered InfoLEG dataset), **Boletín Oficial** (CABA JSON; national
    has no stable JSON API — honest pointers), **AFIP** padrón (best-effort + local CUIT
    validation; no free official API exists).
  - Optional **PostgreSQL** backend (`ARGMCP_BACKEND=postgres`): tsvector +
    `websearch_to_tsquery` + pg_trgm + recency boost, same interface as SQLite, with an
    importer and a guarded integration test.
- **Phase 4** — ✅ diff tool, MCP prompts, `EVALS.md`, bounded retry/backoff, structured
  errors, and a multi-agent **adversarial review** of the new sources/Postgres code that
  surfaced 9 confirmed issues — all fixed and covered by tests (notably: BCRA catalog
  pagination so all 1220 variables are returned rather than silently truncated at 1000;
  no-retry on permanent 4xx; Postgres↔SQLite search-operator parity; tighter error
  handling). Pending: a broader live eval run for InfoLEG specifically (blocked by InfoLEG
  availability; the other sources are verified live).

## Known limitations / TODO

- **Live InfoLEG was returning 503/403** to automated clients during development. The
  default **dataset** mode and graceful degradation are designed for exactly this; live
  code paths are covered by mocked tests. Run `update-configs` / `build-dataset` when the
  hosts are reachable to populate `data/dependencias.json` and `data/infoleg.sqlite`.
- `data/dependencias.json` ships **empty** (138 KB live-generated catalog). `tipos_norma.json`
  ships a small **bootstrap**; `update-configs` regenerates both fully.
- `BusquedaNormaRequest.anio_sancion` uses the field name observed in the reference's
  working client; verify against the live form when reachable.
- Complementary vínculos datasets (`0c4fdafe…`, `dea3c247…`) are not yet loaded; vínculos
  use live `verVinculos` with the main dataset's `modifica_a`/`modificada_por` as fallback.
- PostgreSQL backend is implemented (`ARGMCP_BACKEND=postgres` + `ARGMCP_PG_DSN`); its
  integration test is skipped unless a live `ARGMCP_PG_DSN` is provided (SQLite remains
  the zero-config default).
- AFIP has **no** free official API; `afip_padron` is best-effort against an unofficial,
  frequently-down endpoint. CUIT validation is local and reliable. National Boletín
  Oficial has no stable public JSON API (WAF); the CABA bulletin does.

## How to extend with a new source (Phase 3 pattern)

1. Add `src/arg_legal_mcp/sources/<name>.py` with a function returning a dict
   (use `sources.base.get_json`).
2. Register a tool in `sources/register.py`.
3. Add a mocked test in `tests/test_sources.py` (respx).
