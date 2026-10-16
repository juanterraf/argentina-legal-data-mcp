# STATUS — estado real al 15/10/2026

Servidor en producción en `https://mcp.derechointeligente.com.ar/mcp` ("MCP LEAL" en claude.ai).
**23 tools** de solo lectura, probadas de punta a punta por HTTP contra las fuentes reales
(también desde el VPS). Tests: **155 pasan, 1 salteado** (PostgreSQL sin DSN); `ruff` limpio.

## Qué funciona

- **InfoLEG (dataset offline + en vivo).** `infoleg_buscar_normas` (dataset por defecto,
  `en_vivo=true` para el buscador real), `infoleg_ver_norma`, texto actualizado y original
  paginados en fragmentos de ~8.000 caracteres, vínculos en ambas direcciones,
  `infoleg_comparar_original_actualizado`, `infoleg_buscar_organismos`, `tributaria_buscar`
  (filtra en SQL con paginación exacta) y el recurso `infoleg://tipos-norma`.
- **Ranking por relevancia.** bm25 con peso en el título y prioridad a normas base (muy
  modificadas) y a referencias directas: "impuesto a las ganancias" trae primero la Ley 20.628.
- **Degradación honesta.** Si InfoLEG cae o cambia de formato, ficha, vínculos y búsqueda vacíos
  o irreconocibles degradan al dataset con `aviso` y no se cachean. Una lista de vínculos
  vacía con aviso no significa "ninguna".
- **Boletín Oficial de la Nación** (1ra y 3ra sección): `boletin_oficial_sumario`, `_buscar`
  y `_ver`; más el sumario de la Ciudad de Buenos Aires (`jurisdiccion="caba"`). La 2da sección
  (sociedades) no está: el `robots.txt` del sitio la excluye y se respeta.
- **Cálculos.** `calcular_actualizacion` (CER, UVA, ICL vía BCRA; IPC vía INDEC) y
  `calcular_plazo` (días hábiles o corridos, con feriados nacionales y puentes).
- **Datos económicos.** `dolar_cotizaciones`, `bcra_variables` (lee las ~1.610 variables y
  encuentra CER, UVA e ICL por sigla), `bcra_cotizaciones`, `indec_buscar_series` (prioriza
  series vigentes y nacionales), `indec_serie`, `feriados_nacionales`. Caché con TTL en todas
  las fuentes.
- **Diagnóstico y ChatGPT.** `data_health` (incluye el estado del dataset), `search` y `fetch`
  (texto de la norma limpio de HTML, en fragmentos de 8.000).
- **Seguridad y operación.**
  - Sin tools de mantenimiento ni de consulta de logs en el MCP público.
  - Registro de cada llamada (tool, ms, ok/error) sin argumentos, aun con la auth apagada.
  - Tope de body de 1 MiB; claves comparadas en tiempo constante; con auth activa, solo el
    descubrimiento queda abierto.
  - Tools fuera del event loop, con presupuesto de 25 s por tool.
  - systemd endurecido (`ProtectSystem=strict`, escritura solo en `data` y `.cache`, filtro de
    syscalls).
- **Base de datos.** Se reconstruye con `python -m arg_legal_mcp build-dataset` o con el timer
  semanal de systemd (domingos 04:00): ZIP de ~49 MB por https, extracción defensiva, archivo
  temporal con lock y reemplazo atómico. El servidor toma la base nueva sin reiniciar.
- **Deploy.** `bash scripts/deploy-vps.sh`: sube `HEAD`, instala servicio y timer, ajusta
  permisos y corta si el servicio no queda activo. Provisión inicial detallada en el encabezado
  del script.
- **Auth opcional** (`ARGMCP_AUTH_ENABLED`, API keys con recarga en caliente); en producción
  el servidor es público y *authless*.

## Pendiente

- **Tool de intereses judiciales.** La tasa activa del Banco Nación no está en la API del BCRA.
  La tasa pasiva de uso de justicia (Com. "P" 14.290) sí está (variable id 43), así que se
  podría cubrir esa; la tasa activa BNA requiere otra fuente.
- **Padrón de ARCA.** No hay API oficial gratuita: solo existe el archivo descargable oficial.
  Sería un dataset offline (descarga + reconstrucción, como el de InfoLEG), no una consulta en vivo.
- **PostgreSQL sin probar contra una base real.** El backend (`ARGMCP_BACKEND=postgres` +
  `ARGMCP_PG_DSN`) está implementado y su test de integración se saltea sin DSN; SQLite es el
  camino por defecto y el que corre en producción.
- Los datasets complementarios de vínculos de datos.jus.gob.ar no están cargados: los vínculos
  usan InfoLEG en vivo, con el contador del dataset como respaldo (solo para avisar).
- La 2da sección del Boletín Oficial sigue sin cubrirse (decisión de respetar el `robots.txt`).

## Cómo agregar una fuente nueva

1. Crear `src/arg_legal_mcp/sources/<nombre>.py` con una función que devuelva un `dict`
   (usar `sources.base`: TLS verificado, retry/backoff, caché).
2. Registrar la tool en `sources/register.py`, con docstring "CUANDO USARLA" y los límites.
3. Agregar un test mockeado (respx) en `tests/`.
