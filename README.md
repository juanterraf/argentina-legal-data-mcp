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

## Configuration

All settings use the `ARGMCP_` env prefix — see [.env.example](.env.example).

## Development

```bash
pip install -e ".[dev]"
ruff check src tests
pytest -q
```

Remote/HTTP deployment (auth, Docker, systemd, Caddy) is documented below once Phase 2
lands. The tests do not require network access (HTTP is mocked with `respx`).
