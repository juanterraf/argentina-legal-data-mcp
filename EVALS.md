# EVALS — verifiable questions

Ten questions whose answers are checkable against official sources. Use them to sanity
-check the server end to end. Each lists the tools the agent should use and how to verify.

> Most legislation evals need the offline dataset built
> (`python -m arg_legal_mcp build-dataset`) or live InfoLEG reachable. The dólar/feriados
> evals hit live public APIs.

| # | Question | Tools | How to verify |
|---|----------|-------|---------------|
| 1 | What is the InfoLEG `id_norma` of **Ley 27.275**? | `infoleg_resolver_id(tipo="Ley", numero=27275)` | Expect **265949** (verifiable: `verNorma.do?id=265949`). |
| 2 | Give the title/subject and publication date of Ley 27.275. | `infoleg_ver_norma(265949)` | "Acceso a la Información Pública"; B.O. **16-nov-2016**. |
| 3 | Read article 1 of the **current** text of Ley 27.275. | `infoleg_obtener_texto_actualizado(265949)` | Text is fetched from the real `texact.htm` URL (never a computed folder). |
| 4 | Which norms **modify** Ley 27.275? | `infoleg_ver_normas_que_la_modifican(265949)` | Cross-check against the ficha's "modo=2" count. |
| 5 | Which norms does Ley 27.275 **modify**? | `infoleg_ver_normas_que_modifica(265949)` | Cross-check against the ficha's "modo=1" count. |
| 6 | Find national laws about **"información pública"**. | `infoleg_buscar_normas(texto="informacion publica")` | Results include Ley 27.275; newer norms rank higher (recency boost). |
| 7 | What changed between the original and current text of a modified law? | `infoleg_comparar_original_actualizado(<id>)` | Mechanical diff; explicitly **non-binding**. |
| 8 | What is the numeric id of the organism **"ANSES"**? | `infoleg_buscar_dependencias("ANSES")` | Fuzzy match returns the organism + id (needs `update-configs`). |
| 9 | What is the current **blue** dollar rate? | `dolar_cotizaciones()` | `casa="blue"` compra/venta, live from DolarAPI. |
| 10 | List the **national holidays of 2026**. | `feriados_nacionales(2026)` | ~19 holidays incl. 01-01 "Año nuevo" (ArgentinaDatos). |
| 11 | What are the BCRA's current **international reserves**? | `bcra_variables()` then `bcra_variables(id_variable=1)` | Catalog lists ~1220 variables; id 1 = "Reservas internacionales" (USD millions). |
| 12 | Find the INDEC **IPC** national series id. | `indec_buscar_series("ipc nivel general nacional")` then `indec_serie(<id>, sort="desc", limit=1)` | Search returns ids; serie returns `[date, value]` observations. |
| 13 | What sections did the **CABA** bulletin publish on a date? | `boletin_oficial_buscar(fecha="2026-06-02", jurisdiccion="caba")` | Real JSON sections. (`jurisdiccion="nacional"` returns honest pointers — no stable national JSON API.) |

## Robustness checks (no network)
- `pytest -q` → 37 tests green, including:
  - parsers against faithful HTML fixtures;
  - dataset FTS search + **recency boost** + id-resolution;
  - live text fetch via `respx` asserting the **real `texact.htm` URL** is requested;
  - graceful **degradation** to the dataset when live fails;
  - auth: discovery open / `tools/call` 401 / valid token / hot reload;
  - **regression test forbidding the 50,000-block** annex calculation.
