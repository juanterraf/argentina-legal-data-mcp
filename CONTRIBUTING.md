# Cómo contribuir

¡Gracias por el interés! Este es un **proyecto abierto de
[derechointeligente.com.ar](https://derechointeligente.com.ar)** cuyo objetivo es **difundir
las posibilidades de la IA generativa** con datos públicos de Argentina. Toda contribución
que sume fuentes, mejore la robustez o aclare la documentación es bienvenida.

## Formas de contribuir
- 🐛 **Issues**: reportá un bug, una fuente que cambió de formato, o una idea.
- 🔌 **Nuevas fuentes**: sumá otra fuente de datos pública argentina (ver más abajo).
- 📚 **Documentación / casos de uso**: agregá ejemplos a [`docs/casos-de-uso.md`](docs/casos-de-uso.md).
- ✅ **Tests / fixes**: mejoras de parsing, manejo de errores, performance.

## Setup de desarrollo

```bash
git clone https://github.com/juanterraf/argentina-legal-data-mcp.git
cd argentina-legal-data-mcp
python -m venv .venv && . .venv/Scripts/activate     # Windows: .venv\Scripts\activate
pip install -e ".[dev]"

ruff check src tests        # lint
pytest -q                   # tests (no requieren red: el HTTP está mockeado con respx)
```

Requisitos: **Python 3.12+**. Para probar el servidor localmente:
`python -m arg_legal_mcp` (stdio). Para construir el dataset offline:
`python -m arg_legal_mcp build-dataset`.

## Estructura del proyecto
```
src/arg_legal_mcp/
  server.py            # arma el FastMCP y registra tools/recursos/prompts
  config.py            # settings (env ARGMCP_*)
  infoleg/             # núcleo InfoLEG: client, parsers, dataset, session, cache, services…
  sources/             # fuentes extra: base, dolar, bcra, indec, afip, feriados, boletin…
  auth/                # auth opcional (API-key) para el transporte HTTP
  http_app.py          # ASGI + transporte remoto
tests/                 # un archivo por área (parsers, dataset, sources, auth, search/fetch…)
docs/                  # casos de uso y documentación
```

## Agregar una nueva fuente de datos (patrón)
1. Creá `src/arg_legal_mcp/sources/<nombre>.py` con una función que devuelva un `dict`,
   usando `sources.base.get_json(...)` (TLS verificado + retry/backoff incluidos).
2. Registrá la tool en `sources/register.py` (con docstring claro estilo "CUÁNDO USARLA").
3. Agregá un test en `tests/test_sources.py` mockeando el HTTP con `respx`.
4. Si la fuente expone un esquema, normalizá la salida a un `dict`/`TypedDict` y devolvé
   un `error` estructurado ante fallos (nunca dejes caer una tool).

## Reglas no negociables (vienen de bugs reales — ver [`DECISIONS.md`](DECISIONS.md))
- **TLS siempre verificado.** Nunca desactivar la verificación de certificados.
- **No "calcular" lo que se puede leer.** Para los anexos de InfoLEG se lee la URL real
  (no se computa la carpeta); hay un test que prohíbe el cálculo erróneo de bloques de 50.000.
- **Las tools nunca crashean**: devuelven un `dict` con `error`/`aviso`.
- **Disclaimer y atribución.** Las salidas legales llevan el aviso de "no es asesoramiento";
  respetá las licencias y atribuciones de las fuentes (InfoLEG/dataset = CC BY 2.5 AR).
- **Buen ciudadano de la infraestructura pública**: caché, backoff y User-Agent honesto;
  sin scraping abusivo.

## Pull requests
1. Hacé un fork y una rama (`feat/...` o `fix/...`).
2. Asegurate de que `ruff check` y `pytest -q` pasen.
3. Commits descriptivos; explicá el "por qué" en el PR.
4. Para cambios grandes, abrí primero un issue para acordar el enfoque.

## Licencia
Al contribuir aceptás que tu aporte se publique bajo la licencia **MIT** del proyecto
(ver [`LICENSE`](LICENSE)).
