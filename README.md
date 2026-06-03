# Argentina Legal & Data MCP

> Un servidor **MCP** que le da a un asistente de IA (Claude, ChatGPT, etc.) acceso a
> **datos públicos de Argentina**, con foco profundo en **legislación nacional (InfoLEG)**
> y un conjunto extensible de fuentes (dólar, BCRA, INDEC, feriados, Boletín, AFIP).

Información de **fuentes oficiales**, **verificable**, con cita de fuente y un disclaimer
explícito. No reemplaza asesoramiento jurídico.

---

## Índice
- [¿Qué es esto?](#qué-es-esto)
- [El problema que resuelve](#el-problema-que-resuelve)
- [Cómo está construido (arquitectura)](#cómo-está-construido-arquitectura)
- [Qué puede hacer (herramientas)](#qué-puede-hacer-herramientas)
- [Casos de uso](#casos-de-uso)
- [Cómo usarlo](#cómo-usarlo) · [Local](#escenario-a--local-claude-desktop-stdio) · [Remoto / VPS](#escenario-b--remoto-en-un-vps-claudeai-chatgpt-equipos)
- [Datos y fuentes](#datos-y-fuentes)
- [Estado actual y limitaciones](#estado-actual-y-limitaciones)
- [Desarrollo](#desarrollo)
- [Licencia y créditos](#licencia-y-créditos)

---

## ¿Qué es esto?

El **Model Context Protocol (MCP)** es el estándar que usan los asistentes de IA para
hablar con sistemas externos: bases de datos, APIs, herramientas. Un **servidor MCP** es
un "enchufe" que expone *herramientas* que el asistente puede invocar cuando las necesita.

Este proyecto es un servidor MCP que conecta a la IA con **datos públicos argentinos**.
Vos le hablás a la IA en lenguaje natural ("¿qué dice la Ley de Acceso a la Información y
sigue vigente?"), y la IA **decide sola** qué herramientas llamar y cómo encadenarlas para
responderte con datos reales.

## El problema que resuelve

Un modelo de lenguaje **solo**, sin acceso a datos, tiene dos límites graves para temas
legales y económicos:

1. **Alucina** — te puede inventar el número de una ley, una fecha o un artículo.
2. **Está desactualizado** — solo "sabe" hasta su fecha de corte de entrenamiento.

Este servidor lo resuelve: la IA **busca y lee normas reales**, **rastrea modificaciones**,
trae **el dólar / reservas / IPC de hoy**, y **siempre cita la fuente**. Deja de adivinar.

## Cómo está construido (arquitectura)

La idea central es **híbrida y a prueba de fallos**:

```
            ┌─────────────────────────── Asistente de IA (Claude / ChatGPT) ───────────────────────────┐
            │                          habla MCP (stdio local  ó  HTTPS remoto)                         │
            └───────────────────────────────────────────┬──────────────────────────────────────────────┘
                                                         │
                                       ┌─────────────────▼──────────────────┐
                                       │     Argentina Legal & Data MCP      │
                                       │  (FastMCP · 23 tools · 3 prompts)   │
                                       └───────┬───────────────────┬─────────┘
                                  dataset-first │                   │ en vivo
                            ┌──────────────────▼──┐         ┌──────▼─────────────────────────┐
                            │  Capa OFFLINE        │         │  Capa EN VIVO                  │
                            │  SQLite + FTS5       │         │  InfoLEG · BCRA · INDEC ·      │
                            │  420k+ normas        │◀── degradación ── dólar · feriados · …  │
                            │  (robusto, sin red)  │  elegante └────────────────────────────┘
                            └──────────────────────┘
```

- **Capa offline** — un dataset oficial del Estado (las ~420.000 normas de InfoLEG) en una
  base local con buscador de texto completo (FTS5) y *ranking con boost por recencia*.
  Rápido, funciona sin internet, no depende de que InfoLEG esté arriba.
- **Capa en vivo** — las APIs reales para frescura y texto completo.
- **Degradación elegante** — si lo en vivo falla, usa el dataset y **lo avisa** en la
  respuesta (`fuente`, `aviso`), en vez de romperse o mentir.
- **Honestidad por diseño** — nunca "calcula" lo que debe leer (no inventa la carpeta de
  anexos: usa la URL real), **verifica TLS siempre**, pagina y trocea el texto para no
  saturar el contexto, y marca como **no vinculante** cualquier análisis heurístico.
- **Seguro y desplegable** — autenticación desde el día uno en el transporte remoto,
  transporte dual (local `stdio` / remoto `streamable-http`/`sse`), Docker + systemd +
  reverse proxy con HTTPS, health-checks y caché persistente.

> Diseño y trazabilidad completa de decisiones en [DECISIONS.md](DECISIONS.md).

## Qué puede hacer (herramientas)

**Legislación nacional — InfoLEG (lo profundo):**

| Pregunta del usuario | Herramienta |
|---|---|
| "¿Cuál es el id de la Ley 27.275?" | `infoleg_resolver_id` → 265949 |
| "Buscá leyes sobre información pública" | `infoleg_buscar_normas` (orden por recencia; modo dataset o `en_vivo`) |
| "Mostrame la ficha completa" | `infoleg_ver_norma` |
| "Leé el texto vigente / el original" | `infoleg_obtener_texto_actualizado` / `_original` (paginado) |
| "¿Qué normas la modificaron? ¿a cuáles modificó?" | `infoleg_ver_normas_que_la_modifican` / `_que_modifica` |
| "¿Qué cambió entre el original y lo vigente?" | `infoleg_comparar_original_actualizado` |
| "Buscá el organismo emisor 'ANSES'" | `infoleg_buscar_dependencias` / `infoleg_get_dependencia_by_id` |
| "Buscá normativa tributaria de AFIP" | `tributaria_buscar` |
| (admin) | `infoleg_estado_dataset` · `infoleg_actualizar_dataset` |

Recurso MCP: `infoleg://tipos-norma`. Prompts guiados: `auditar_norma`,
`comparar_versiones`, `buscar_ley_decreto`.

**Economía y finanzas (en vivo):**

| Herramienta | Fuente |
|---|---|
| `dolar_cotizaciones` | DolarAPI (oficial, blue, MEP, CCL, tarjeta…) |
| `bcra_variables` | BCRA Estadísticas Monetarias v4.0 (reservas, tipo de cambio, tasas… 1220 variables) |
| `bcra_cotizaciones` | BCRA Estadísticas Cambiarias (tabla de divisas / histórico) |
| `indec_serie` / `indec_buscar_series` | datos.gob.ar Series de Tiempo (IPC, EMAE, …) |

**Otros datos públicos:**

| Herramienta | Fuente / nota |
|---|---|
| `feriados_nacionales` | ArgentinaDatos |
| `boletin_oficial_buscar` | Boletín Oficial CABA (JSON); el nacional no tiene API JSON estable → punteros oficiales |
| `afip_padron` | Best-effort (no hay API gratuita oficial); validación de CUIT local y confiable |

**Diagnóstico:** `data_health` (estado/frescura por fuente + smoke test), `requests_recientes`.

## Casos de uso

- **Estudios jurídicos / abogados** — investigación legislativa rápida, con trazabilidad de
  modificaciones y estado de vigencia, citando fuente oficial.
- **Legaltech / desarrolladores** — un backend de datos legales+económicos listo para
  enchufar a un agente o a una app (vía MCP).
- **Periodismo de datos** — cruzar normativa con indicadores (dólar, IPC, reservas) con
  fuentes verificables.
- **Compliance / análisis económico** — monitorear normativa tributaria y variables del BCRA.
- **Uso personal / educativo** — "explicame qué dice la Ley X y si sigue vigente".

Ejemplo de encadenamiento que la IA hace sola:
`auditar_norma(265949)` → `ver_norma` → `obtener_texto_actualizado` (paginando) →
`ver_normas_que_la_modifican` → resumen estructurado con disclaimer.

Diez preguntas verificables de ejemplo: [EVALS.md](EVALS.md).

## Cómo usarlo

### Escenario A — Local (Claude Desktop, `stdio`)

Ideal para uso personal en tu máquina. **No requiere servidor ni VPS.**

```bash
python -m venv .venv && .venv\Scripts\activate      # Windows
pip install -e .

# (recomendado) construir el dataset offline de InfoLEG (~50 MB de descarga)
python -m arg_legal_mcp build-dataset
python -m arg_legal_mcp update-configs              # catálogo de organismos (cuando InfoLEG responda)
```

Agregá esto a `claude_desktop_config.json`
(`%APPDATA%\Claude\` en Windows, `~/Library/Application Support/Claude/` en macOS) y reiniciá Claude Desktop:

```json
{
  "mcpServers": {
    "argentina-legal-data": {
      "command": "C:/ruta/al/proyecto/.venv/Scripts/python.exe",
      "args": ["-m", "arg_legal_mcp"],
      "cwd": "C:/ruta/al/proyecto"
    }
  }
}
```

### Escenario B — Remoto en un VPS (claude.ai, ChatGPT, equipos)

**¿Por qué un VPS?** Los clientes **web** (claude.ai, ChatGPT) y móviles **no pueden lanzar
un proceso local** por `stdio`: necesitan un **endpoint MCP accesible por HTTPS**, prendido
24/7. Para eso sirve un VPS (o cualquier host). Además:

- la paginación en vivo de InfoLEG es *stateful* → corré **una sola instancia**;
- conviene tener el dataset (~430 MB) y la caché en disco persistente;
- la auth + HTTPS te permiten exponerlo de forma segura (y compartirlo con un equipo).

Ya viene todo listo: `Dockerfile`, `docker-compose.yml` (MCP + Caddy con HTTPS automático),
unidades `systemd/` y `Caddyfile`.

```bash
# en el VPS
cp secrets/api-keys.json.example secrets/api-keys.json   # generá tokens largos y aleatorios
docker compose run --rm mcp python -m arg_legal_mcp build-dataset   # una vez (pesado)
# editá Caddyfile (tu dominio + email), luego:
docker compose up -d
```

Esto deja el servidor en `https://tu-dominio/mcp` (transporte `streamable-http`),
con **auth por API-key** (header `Authorization: Bearer <token>`): el *descubrimiento* es
abierto y la *ejecución* (`tools/call`) requiere credencial. TLS lo termina Caddy
(Let's Encrypt automático).

**Conectarlo a cada plataforma** (el soporte de conectores MCP evoluciona rápido —
verificá la doc vigente de cada una):
- **claude.ai (Team/Enterprise/Pro)** — agregá un *conector / integración personalizada*
  apuntando a `https://tu-dominio/mcp`.
- **ChatGPT** — vía *connectors* (Developer mode) o la *Responses API*
  (`tools: [{ type: "mcp", server_url: "https://tu-dominio/mcp", ... }]`).
- **Claude Code / SDKs** — agregalo como servidor MCP remoto con el token.

> Especificaciones mínimas del VPS: ~1–2 GB de RAM y ~2–5 GB de disco (dataset + índices)
> alcanzan de sobra. Cualquier VPS chico sirve.

Todas las variables de configuración usan el prefijo `ARGMCP_` — ver [.env.example](.env.example).
Backend por defecto: SQLite + FTS5 (cero config). Opcional PostgreSQL
(`ARGMCP_BACKEND=postgres` + `ARGMCP_PG_DSN`, con `tsvector` + `pg_trgm`) para escala.

## Datos y fuentes

- **InfoLEG** — Información Legislativa y Documental, Ministerio de Justicia de la Nación.
  <https://www.infoleg.gob.ar>
- **Dataset "Base InfoLeg - Normativa Nacional"** — datos.jus.gob.ar / datos.gob.ar
  (licencia **CC BY 2.5 AR** — requiere atribución).
- BCRA (`api.bcra.gob.ar`), datos.gob.ar Series de Tiempo (INDEC), DolarAPI,
  ArgentinaDatos, Boletín Oficial (CABA), AFIP (no oficial).

Se respetan los términos de uso: caché, backoff, User-Agent honesto, sin scraping abusivo.

## Estado actual y limitaciones

- ✅ **Funciona completo offline**: búsqueda legislativa (FTS + recencia), resolución de id,
  fichas, normativa tributaria, y todas las fuentes económicas en vivo.
- ⏳ El **texto completo** de las normas se sirve leyendo la URL real del anexo (que el
  dataset ya provee); requiere que `servicios.infoleg.gob.ar` esté accesible. Mientras esté
  caído, las tools de texto devuelven la **URL oficial** como puntero (nunca texto falso) y
  bajan/cachean el contenido automáticamente cuando el sitio se recupera.
- ℹ️ **AFIP**: no existe API gratuita oficial; `afip_padron` es best-effort + validación de
  CUIT local. El Boletín **nacional** no expone API JSON estable (WAF); el de **CABA** sí.

Detalle completo de hecho/pendiente en [STATUS.md](STATUS.md).

## Desarrollo

```bash
pip install -e ".[dev]"
ruff check src tests
pytest -q          # 54 tests; sin red (HTTP mockeado con respx)
```

Estructura: `src/arg_legal_mcp/` (núcleo `infoleg/`, `sources/`, `auth/`, `server.py`).
Cómo agregar una fuente nueva: ver el patrón en [STATUS.md](STATUS.md).

## Licencia y créditos

Reimplementación limpia (clean-room) informada por el estudio de proyectos de referencia
(InfoLegMCP, argentina-data-mcp, InfoLeg-MCP) — sin copiar código. Ver [NOTICE](NOTICE) y
[CREDITS.md](CREDITS.md) para licencias y atribuciones.

> ⚖️ La información proviene de fuentes oficiales y **no constituye asesoramiento jurídico**.
> Verificá contra el portal oficial antes de cualquier uso probatorio o vinculante.
