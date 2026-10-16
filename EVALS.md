# EVALS — preguntas verificables

Casos cuya respuesta se puede contrastar con fuentes oficiales. Sirven para probar el
servidor de punta a punta. Cada uno indica las tools que debería usar el agente y cómo verificar.

> Los casos de legislación necesitan el dataset construido
> (`python -m arg_legal_mcp build-dataset`); los de texto, vínculos y `en_vivo` además
> InfoLEG disponible. Dólar, feriados, BCRA, INDEC y Boletín Oficial consultan fuentes en vivo.
> Los valores de índices cambian con el tiempo: se verifica contra la fuente en la fecha.

| # | Pregunta | Tools | Cómo verificar |
|---|----------|-------|----------------|
| 1 | ¿Cuál es el id de InfoLEG de la **Ley 27.275**? | `infoleg_buscar_normas(texto="Ley 27275")` | La Ley 27.275 sale primera; id **265949** (`verNorma.do?id=265949`). |
| 2 | ¿Título y fecha de publicación de la Ley 27.275? | `infoleg_ver_norma(265949)` | "Acceso a la Información Pública"; B.O. **16-nov-2016**. |
| 3 | Leé el art. 1 del texto **vigente** de la Ley 27.275. | `infoleg_obtener_texto_actualizado(265949)` | Texto de la URL real de InfoLEG, en fragmentos de ~8.000 caracteres; `hay_mas` / `siguiente_inicio` coherentes. |
| 4 | ¿Qué normas **modifican** a la Ley 27.275? | `infoleg_ver_normas_que_la_modifican(265949)` | Coincide con la cantidad de la ficha; si InfoLEG no responde, lista vacía **con `aviso`**. |
| 5 | ¿Qué normas **modifica** la Ley 27.275? | `infoleg_ver_normas_que_modifica(265949)` | Idem, contra el contador de la ficha. |
| 6 | Buscá leyes sobre **"información pública"**. | `infoleg_buscar_normas(texto="informacion publica")` | La Ley 27.275 aparece entre los primeros resultados. |
| 7 | Buscá **"impuesto a las ganancias"**. | `infoleg_buscar_normas(texto="impuesto a las ganancias")` | La **Ley 20.628** (norma base) viene primera. |
| 8 | Buscá el **DNU 70/2023**. | `infoleg_buscar_normas(texto="DNU 70/2023")` | La referencia directa va primera (Decreto 70/2023). |
| 9 | ¿Qué cambió entre el texto original y el vigente de una ley modificada? | `infoleg_comparar_original_actualizado(<id>)` | Diff mecánico de líneas, explícitamente no vinculante. |
| 10 | ¿Cómo registra InfoLEG a **ANSES**? | `infoleg_buscar_organismos("ANSES")` | Nombre exacto del organismo y cantidad de normas; luego sirve como `organismo` en `infoleg_buscar_normas`. |
| 11 | Normas tributarias de ARCA/AFIP sobre "monotributo". | `tributaria_buscar(query="monotributo")` | Resultados del dataset con paginación (`nro_pag`); no exhaustivo. |
| 12 | Buscá "ley de defensa del consumidor" y leé la norma (flujo ChatGPT). | `search("defensa del consumidor")` → `fetch(<id>)` | `search` devuelve `results[{id,title,url}]`; `fetch` devuelve ficha + texto sin HTML y, si sigue, cómo pedir el resto. |
| 13 | ¿Qué se publicó en la **primera sección** del Boletín Oficial en un día hábil reciente? | `boletin_oficial_sumario(fecha="<YYYY-MM-DD>")` | Avisos con id, rubro, organismo y título; se cruzan con el sitio oficial. Un sábado o feriado devuelve error (no hay edición). |
| 14 | Buscá en el Boletín Oficial una norma por número. | `boletin_oficial_buscar(texto="decreto 70/2023", fecha_desde="2023-12-20", fecha_hasta="2023-12-21")` → `boletin_oficial_ver(<id_aviso>, fecha)` | El DNU 70/2023 se publicó el **21/12/2023**; `ver` devuelve el texto, `url` y `url_pdf`. |
| 15 | Pedí la **segunda sección** del Boletín Oficial. | `boletin_oficial_sumario(fecha=..., seccion="segunda")` | Error explícito: no está disponible (el `robots.txt` del sitio la excluye). |
| 16 | ¿Qué publicó el Boletín de la **Ciudad de Buenos Aires** en una fecha? | `boletin_oficial_sumario(fecha="2026-06-02", jurisdiccion="caba")` | Secciones reales de la API JSON de la Ciudad. |
| 17 | Actualizá **$100.000** por **CER** entre dos fechas. | `calcular_actualizacion(100000, "2023-03-01", "2026-03-01", "CER")` | Coeficiente = CER final / CER inicial (BCRA, variable 30, vía `bcra_variables`); monto = 100.000 × coeficiente. |
| 18 | Actualizá un monto por **IPC**. | `calcular_actualizacion(monto, desde, hasta, "IPC")` | Coincide con el cociente del IPC nivel general nacional del INDEC (`indec_serie`). |
| 19 | Pedí una actualización con fechas inválidas (desde posterior a hasta, o futuras). | `calcular_actualizacion(...)` | Devuelve `error` claro, sin cálculo. |
| 20 | Un plazo de **10 días hábiles** desde el **18/12/2026**: ¿cuándo vence? | `calcular_plazo("2026-12-18", 10)` | El cómputo arranca el día siguiente; descuenta fines de semana, el 25/12 y el 01/01, y lista los días salteados. Verificar con `feriados_nacionales(2026/2027)`. |
| 21 | Plazo de 30 días **corridos** y límites. | `calcular_plazo("2026-06-09", 30, tipo="corridos")` | Fecha exacta + 30 días; el resultado aclara que no contempla ferias judiciales ni feriados provinciales. |
| 22 | ¿Cuánto vale hoy la **UVA**? | `bcra_variables(buscar="UVA")` y luego `bcra_variables(id_variable=31)` | La primera coincidencia es la variable 31 (Unidad de valor adquisitivo); el valor viene con su fecha. |
| 23 | ¿Cuál es el dólar **blue** hoy? | `dolar_cotizaciones()` | `casa="blue"` compra/venta de DolarAPI (referencia). |
| 24 | Tipo de cambio oficial del BCRA a una fecha. | `bcra_cotizaciones(moneda="USD", fechadesde=..., fechahasta=...)` | Cotización publicada por el BCRA ese día. |
| 25 | Valor actual de las **reservas** del BCRA. | `bcra_variables(buscar="reservas")` → `bcra_variables(id_variable=1)` | Catálogo de ~1.600 variables; id 1 = reservas internacionales (USD millones). |
| 26 | Serie del **IPC** nacional y su último dato. | `indec_buscar_series("ipc nivel general nacional")` → `indec_serie(<id>, sort="desc", limit=1)` | Serie vigente y nacional primero; observación `[fecha, valor]`. |
| 27 | **Feriados nacionales de 2026**. | `feriados_nacionales(2026)` | ~19 feriados incl. 01-01 "Año nuevo" (ArgentinaDatos); sin provinciales. |
| 28 | ¿Cuándo se reconstruyó la base y cómo están las fuentes? | `data_health()` | Cantidad de normas (~420.000), fecha de construcción y estado de cada fuente. |

## Controles sin red

- `pytest -q` corre sin red (HTTP mockeado con `respx`) y cubre, entre otros:
  - parsers contra fixtures HTML fieles de InfoLEG y del Boletín Oficial;
  - búsqueda en el dataset (FTS5, ranking, referencias directas) y filtros de `tributaria_buscar`;
  - degradación a dataset con `aviso` y no cacheo de respuestas vacías o irreconocibles;
  - cálculo de plazos y actualización por índice;
  - `search` / `fetch`, autenticación (descubrimiento abierto / `tools/call` protegido) y
    endurecimiento del núcleo;
  - un test que prohíbe el cálculo erróneo de bloques de 50.000 para los anexos de InfoLEG.
