# DECISIONS.md — Argentina Legal & Data MCP

This document records the architectural decisions for this server and, per the
project brief, **what we took from each reference repo and why**. We studied the
references for *behaviour and patterns*; no source code was copied verbatim
(see [§ Licenses](#licenses) and `NOTICE`).

---

## What we took from each reference

### `martindieser/InfoLegMCP` (Python) — primary reference for InfoLEG logic
The repo that best solves the *real* InfoLEG search. We reimplemented (clean) the
behaviours, not the code:

- **Live HTTP contract** (facts, not copyrightable):
  - `BASE_URL = https://servicios.infoleg.gob.ar/infolegInternet`
  - `buscarNormas.do` is a **POST** with form fields (`tipoNorma`, `numero`,
    `anio_sancion`, `texto`, `dependencia`, `diaPubDesde/mesPubDesde/anioPubDesde`,
    and the `...Hasta` variants). The bare GET only returns the form.
  - Pagination repeats the POST on the **same session** with
    `desplazamiento=AP` (avanzar) / `RP` (retroceder) + `irAPagina=N`.
  - `verNorma.do?id=` → metadata ficha.
  - `verVinculos.do?modo=1|2&id=` → **modo=1 = normas que esta modifica**
    (active), **modo=2 = normas que la modifican** (passive).
  - `mostrarBusquedaNormas.do` → form whose `<select name="dependencia">` and
    `<select name="tipoNorma">` are the catalogs.
  - Text bodies live at **relative URLs** (`norma.htm` / `texact.htm`) read from
    the ficha; encoding is ISO-8859-1 → decode latin-1; convert HTML→Markdown.
- **Stateful session manager** with TTL, keyed by a hash of the search params —
  because InfoLEG pagination is session-bound.
- **Two-level pagination**: InfoLEG returns 50/page; the MCP re-paginates to a
  small page size so the agent's context isn't flooded. Plus **text chunking**
  (fixed-size character windows with a "continue from N" header).
- **Fuzzy organism search** (today `infoleg_buscar_organismos`) with `rapidfuzz` (`WRatio`, `score_cutoff=70`,
  accent-insensitive normalization).
- **Persistent cache** with `diskcache` (fichas/texts/vínculos 24 h, search
  pages shorter).
- **Tool docstring style**: "CUÁNDO USARLA", "NO CONFUNDIR con la inversa",
  documented text operators, restrictions. We copied the *style*, wrote our own text.
- **Catalogs**: we **regenerate** `data/dependencias.json` / `data/tipos_norma.json`
  ourselves with `scripts/update_configs.py` against `mostrarBusquedaNormas.do`
  (we do not vendor the reference's data files).

### `abenassi/argentina-data-mcp` (TypeScript) — engineering / ops / extra sources
The most mature as a product. We reimplemented its *patterns* in Python:

- **Auth pattern**: discovery methods (`initialize`, `tools/list`, `notifications/*`,
  `ping`) pass without auth; execution (`tools/call`) requires a credential.
  API-key whitelist loaded from a file on disk with **hot reload** (re-stat every
  60 s by mtime), with `dev`/`user` roles. Optional JWT.
- **Recency-boosted ranking** for dataset search:
  `relevance * (1 + 1/(1 + age_in_years))`. We adopt the idea on top of FTS5 `bm25`.
- **Health / freshness**: a `data_freshness` row per source (last fetch, last data
  date, healthy flag, error) + a `data_health` tool that adds row counts and a
  smoke test. Plus `request_log`.
- **Dataset import** from the official `datos.jus.gob.ar` ZIP using TRUNCATE +
  batched inserts. We confirmed the column schema from here.
- **Scheduled ETL collectors** for the extra sources (dólar, BCRA, INDEC, AFIP,
  feriados, tributaria, boletín) + a cron runner — Phase 3.
- **systemd units** + **testing discipline** (incl. DB integration tests).

### `voftec/InfoLeg-MCP` (TypeScript, MIT) — mostly a cautionary tale
- **ANTI-PATTERN 1 (critical):** `getInfoLegRange()` computes the annex folder with
  blocks of **50,000** (`Math.floor(id/50000)*50000`). This is **wrong** — InfoLEG
  uses blocks of **5,000** (Ley 27.275, id 265949 → `anexos/265000-269999/265949/norma.htm`).
  → **Rule: never compute the folder. Read the real URL from the ficha/dataset.**
  A 5,000-block computation exists only as a last-resort fallback, explicitly
  flagged "no verificado".
- **ANTI-PATTERN 2 (security):** `new https.Agent({ rejectUnauthorized: false })` /
  `NODE_TLS_REJECT_UNAUTHORIZED=0`. → **Rule: TLS verification ALWAYS on.**
- **Salvaged (reimplemented):** original-vs-actualizado diff, boletín ideas, and the
  **MCP prompts** (`buscar_ley_decreto`, `auditar_norma`, `comparar_versiones`).
  Any regex "legal analysis" is offered only as an explicitly **non-binding** aid.

---

## Architecture decisions (binding)

1. **Single language: Python 3.12+ / FastMCP** (`mcp[cli]`). One codebase.
2. **Hybrid dataset + live.** Default search uses the **local dataset** (SQLite +
   FTS5) — robust and offline. Live mode (`en_vivo=true`) uses the real InfoLEG
   search (native operators, full text, vínculos). **Graceful degradation**: if
   live fails, fall back to the dataset and say so in the response.
3. **Never compute the annex folder.** Read `texto_original`/`texto_actualizado`
   URLs from the ficha or dataset. Last-resort fallback uses **5,000** blocks and
   is marked unverified. A regression test forbids the `50000` constant.
4. **TLS always verified** (`httpx` default `verify=True`). No exceptions.
5. **Auth from day one on HTTP**: discovery open, execution protected. API keys
   with hot reload + roles; JWT optional. **stdio needs no auth** (local).
6. **Two transports**: `stdio` (Claude Desktop / local) and
   `streamable-http`/`sse` (remote), chosen by env/flag.
7. **InfoLEG session state encapsulated** with TTL + cleanup. For remote deploy run
   **one instance** (live pagination is session-bound) or page via the dataset.
   No loose mutable globals shared across concurrent requests.
8. **Persistent cache** (`diskcache`) for fichas, texts, vínculos, search pages.
9. **Two-level pagination + text chunking** to protect the agent's context.
10. **Pluggable data backend**: default **SQLite + FTS5** (zero config); optional
    **PostgreSQL + tsvector/pg_trgm** for scale (Phase 3).
11. **Per-source health/freshness** + `request_log` + a `data_health` tool.
12. **Encoding**: handle ISO-8859-1/latin-1; HTML→Markdown via `markdownify`.
13. **Recency-boosted ranking** in dataset search.
14. **Sync `httpx.Client`**: the live flow is inherently sequential (session-bound
    pagination) and the storage layers (SQLite, diskcache) are sync; sync keeps the
    session/pagination logic simple and is fully testable with `respx`. FastMCP runs
    sync tool functions safely.
15. **Disclaimer on every legal output**: official sources, not legal advice; verify
    against the official portal before evidentiary use.

## Decisiones del 15/10/2026 (revisión integral)

Estas decisiones se toman sobre el código ya en producción y prevalecen sobre lo anterior
si lo contradicen (p. ej. el punto 14 de arriba: las tools síncronas ya no corren en el event loop).

16. **Fuera del MCP público las tools peligrosas.** `infoleg_actualizar_dataset` dejaba que
    cualquiera disparara la descarga y reconstrucción de la base, y `requests_recientes`
    mostraba las consultas de otros usuarios. La base se reconstruye solo por CLI
    (`build-dataset`) o con el timer semanal; el servidor público es de solo lectura y no
    expone datos de otros usuarios.
17. **Boletín Oficial sin evadir el WAF y respetando `robots.txt`.** Se consulta el sitio con
    un User-Agent honesto, sin técnicas para esquivar protecciones. El `robots.txt` veta la
    2da sección (sociedades), así que no se cubre: es una decisión, no un faltante. Se sirven
    1ra y 3ra sección.
18. **Ranking con prioridad a normas base.** Además de bm25 con peso en el título, las normas
    muy modificadas y las referencias directas ("Ley 20628", "DNU 70/2023") van primero:
    para "impuesto a las ganancias" importa la Ley 20.628, no la última resolución que la
    menciona. Reemplaza el simple *recency boost* como criterio principal.
19. **Degradación con aviso, no cachear vacíos.** Si InfoLEG cambia de formato o devuelve algo
    vacío o irreconocible, se degrada al dataset con `aviso` y esa respuesta no se cachea:
    cachear un vacío lo convertiría en un dato falso durante todo el TTL. El respaldo de
    vínculos ya no infiere normas a partir de contadores: avisa que vacío no significa "ninguna".
20. **Tools fuera del event loop.** Todas las tools corren en un hilo (`instrument_tools`) con
    presupuesto de 25 s: una consulta lenta a una fuente externa no frena a los demás
    usuarios. Cada llamada se registra (tool, ms, ok/error) **sin argumentos**, porque pueden
    contener datos personales.
21. **Se eliminan tools que no podían cumplir lo que prometían.** `afip_padron` (el endpoint
    no oficial da 404), `infoleg_estado_dataset` (absorbida por `data_health`),
    `infoleg_resolver_id` e `infoleg_get_dependencia_by_id` (`infoleg_buscar_normas` ya resuelve
    referencias). `infoleg_buscar_dependencias` pasa a `infoleg_buscar_organismos` y el
    parámetro `dependencia` a `organismo`, sobre los organismos reales del dataset.
22. **Cálculos explícitos sobre lo que no cubren.** `calcular_actualizacion` (CER, UVA, ICL, IPC)
    no calcula intereses; `calcular_plazo` no contempla feriados provinciales, ferias judiciales
    ni días inhábiles de cada tribunal. El límite va en la
    respuesta y en la documentación: son orientativos.
23. **Reconstrucción atómica de la base.** ZIP por https, extracción defensiva, base armada en
    un archivo temporal con lock y reemplazo atómico; el servidor toma la base nueva sin
    reiniciar. Si falla, queda la anterior.
24. **Sin `cuit_validar` (16/10/2026).** Solo verificaba el dígito: sin padrón no confirma que
    el CUIT exista ni a quién pertenece, y su presencia sugería una verificación que no hace.
    Si vuelve, que sea con el padrón oficial de ARCA como dataset offline.

## Official data source (schema)
- Dataset (CC BY 2.5 AR): `https://datos.gob.ar/dataset/justicia-base-infoleg-normativa-nacional`
- ZIP: `http://datos.jus.gob.ar/dataset/d9a963ea-8b1d-4ca3-9dd9-07a4773e8c23/resource/bf0ec116-ad4e-4572-a476-e57167a84403/download/base-infoleg-normativa-nacional.zip`
- Complementary vínculos resources (same dataset uuid): `0c4fdafe-...` (modificadas),
  `dea3c247-...` (modificatorias).
- Columns: `id_norma, tipo_norma, numero_norma, clase_norma, organismo_origen,
  fecha_sancion, numero_boletin, fecha_boletin, pagina_boletin, titulo_resumido,
  titulo_sumario, texto_resumido, observaciones, texto_original, texto_actualizado,
  modificada_por, modifica_a`.

## Licenses
- `abenassi/argentina-data-mcp`: **PolyForm Noncommercial** → patterns only, no code.
- `martindieser/InfoLegMCP`: **no explicit license** (all rights reserved) → no
  verbatim code; behavioural facts about InfoLEG are not copyrightable.
- `voftec/InfoLeg-MCP`: **MIT** → may adapt with attribution.
- Data: InfoLEG / dataset are CC BY 2.5 AR — attribute the source; cache + backoff +
  honest User-Agent; no abusive scraping.
