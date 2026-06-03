# Argentina Legal & Data MCP

A first-class [Model Context Protocol](https://modelcontextprotocol.io) server for
Argentine public data, with **deep coverage of national legislation (InfoLEG)** and a
pluggable set of additional public data sources.

It combines the best of the existing projects and fixes their mistakes (see
[DECISIONS.md](DECISIONS.md) and [NOTICE](NOTICE)):

- **Deep InfoLEG** — search *and read* the text of norms (original and consolidated),
  trace modifications in both directions, native search operators.
- **Robust** — a local **dataset (SQLite + FTS5)** is the offline backbone; the live
  InfoLEG site adds depth/freshness. If live fails, it **degrades to the dataset** and
  says so.
- **Correct** — it **never computes** the annex folder (the common 50,000-block bug);
  it reads the **real** `norma.htm` / `texact.htm` URL from the ficha or dataset. TLS
  verification is always on.
- **Well tested** — parsers, dataset, id-resolution, live fetch (mocked), and a
  regression test that forbids the 50,000-block calculation.

> ⚖️ Information comes from official sources and **does not constitute legal advice**.
> Verify against the official portal before any evidentiary or binding use.

## Quick start (local, stdio)

```bash
python -m venv .venv && . .venv/Scripts/activate     # Windows: .venv\Scripts\activate
pip install -e ".[dev]"

# (optional but recommended) build the offline dataset and catalogs
python -m arg_legal_mcp update-configs     # regenerate data/*.json from the live form
python -m arg_legal_mcp build-dataset       # download the official ZIP -> data/infoleg.sqlite (heavy)

# run the server over stdio
python -m arg_legal_mcp
```

### Add to Claude Desktop (stdio)

```json
{
  "mcpServers": {
    "argentina-legal-data": {
      "command": "python",
      "args": ["-m", "arg_legal_mcp"],
      "cwd": "C:/path/to/argentina-legal-data-mcp",
      "env": { "ARGMCP_DATA_DIR": "C:/path/to/argentina-legal-data-mcp/data" }
    }
  }
}
```

## InfoLEG tools

| Tool | Purpose |
|------|---------|
| `infoleg_buscar_normas` | Search norms (dataset by default; `en_vivo=true` for the real engine). |
| `infoleg_resolver_id` | "Ley 27275" → `id_norma`. |
| `infoleg_ver_norma` | Full metadata of a norm by id. |
| `infoleg_obtener_texto_actualizado` / `_original` | Paginated full text (reads the real URL). |
| `infoleg_ver_normas_que_modifica` / `_que_la_modifican` | Modification graph (active / passive). |
| `infoleg_comparar_original_actualizado` | Mechanical, non-binding diff. |
| `infoleg_buscar_dependencias` / `infoleg_get_dependencia_by_id` | Fuzzy organism lookup. |
| `infoleg_estado_dataset` / `infoleg_actualizar_dataset` | Inspect / rebuild the offline dataset. |

Resource: `infoleg://tipos-norma` (catalog of norm types).
Prompts: `buscar_ley_decreto`, `auditar_norma`, `comparar_versiones`.

## Other public-data tools

All endpoints below were **live-verified**; each tool returns a structured dict with a
`fuente` and degrades to a clear `error`/`nota` rather than crashing.

| Tool | Source | Notes |
|------|--------|-------|
| `dolar_cotizaciones` | DolarAPI | Oficial, blue, MEP, CCL, tarjeta… |
| `bcra_variables` | BCRA Estadísticas Monetarias **v4.0** | Catalog (no id) or a variable's time series. v2/v3 are 410 Gone. |
| `bcra_cotizaciones` | BCRA Estadísticas Cambiarias v1.0 | FX table (snapshot) or per-currency history. |
| `indec_serie` / `indec_buscar_series` | datos.gob.ar Series de Tiempo | IPC, EMAE, FX… search ids then fetch. |
| `feriados_nacionales` | ArgentinaDatos | National holidays for a year. |
| `tributaria_buscar` | InfoLEG dataset (filtered) | Tax-matter norms (heuristic, non-binding). |
| `boletin_oficial_buscar` | Boletín Oficial | CABA has a clean JSON API; **national has no stable JSON API** (WAF) — returns official pointers. |
| `afip_padron` | AFIP (unofficial) | **Best effort.** No free official API exists; CUIT validation is local & reliable, lookup may be unavailable. |

Backend: SQLite + FTS5 by default; set `ARGMCP_BACKEND=postgres` + `ARGMCP_PG_DSN` for a
PostgreSQL store (tsvector + pg_trgm) via the same interface.

## Remote deployment (HTTP)

The server speaks two transports, chosen by `ARGMCP_TRANSPORT`:
`stdio` (local) and `streamable-http` / `sse` (remote).

### Auth model

- **Discovery is open** (`initialize`, `*_/list`, `ping`, `notifications/*`) so a client
  can connect and enumerate capabilities.
- **Execution is protected** (`tools/call`, `resources/read`, `prompts/get`) when
  `ARGMCP_AUTH_ENABLED=true` — requires a `Authorization: Bearer <key>` header.
- API keys live in a JSON file (`ARGMCP_API_KEYS_PATH`) that is **hot-reloaded** (~60s,
  by mtime) with `dev`/`user` roles. See [secrets/api-keys.json.example](secrets/api-keys.json.example).
- **stdio needs no auth** (it's a local pipe). TLS verification on outbound requests is
  always on.

> ⚠️ Run a **single instance**. Live InfoLEG pagination keeps in-process session state;
> multiple replicas would split it. Scale by serving from the dataset, not by replicas.

### Docker + Caddy (HTTPS)

```bash
cp secrets/api-keys.json.example secrets/api-keys.json   # then edit the keys
docker compose run --rm mcp python -m arg_legal_mcp build-dataset   # one-time, heavy
# edit Caddyfile (domain + email), then:
docker compose up -d
```

Caddy terminates TLS (automatic Let's Encrypt) and proxies to the MCP container on
`:8000`. The MCP app still enforces API-key auth behind the proxy (defense in depth).

### systemd (no Docker)

Install under `/opt/argentina-legal-data-mcp` with a venv, then:

```bash
sudo cp systemd/argentina-legal-data-mcp*.service systemd/*.timer /etc/systemd/system/
sudo systemctl enable --now argentina-legal-data-mcp.service
sudo systemctl enable --now argentina-legal-data-mcp-dataset.timer   # weekly dataset refresh
```

Front it with the [Caddyfile](Caddyfile) (or any reverse proxy that terminates HTTPS).

### Add as a remote connector in Claude

Point your MCP client at `https://<your-domain>/mcp` (streamable-http) and set the
header `Authorization: Bearer <your-api-key>`.

## Health & diagnostics

- `data_health` — per-source availability, freshness, and a dataset FTS smoke test.
- `requests_recientes` — recent tool calls (HTTP transport request log).
- `infoleg_estado_dataset` — dataset availability / row count / build date.

## Configuration

All settings use the `ARGMCP_` env prefix — see [.env.example](.env.example).

## Development

```bash
pip install -e ".[dev]"
ruff check src tests
pytest -q          # 43 tests; no network required (HTTP mocked with respx)
```

> Note: during development the public InfoLEG site (`servicios.infoleg.gob.ar`) was
> returning `503`/`403` to automated clients. That is exactly the scenario the offline
> dataset + graceful degradation handle; the live code paths are covered by mocked tests.
> Run `python -m arg_legal_mcp update-configs` and `build-dataset` when the site/dataset
> hosts are reachable to populate `data/dependencias.json` and `data/infoleg.sqlite`.
