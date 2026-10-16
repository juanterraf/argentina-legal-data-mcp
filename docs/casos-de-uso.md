# Casos de uso prácticos

Ejemplos reales para usar **Argentina Legal & Data MCP** desde Claude o ChatGPT. Una vez
[conectado el conector](../README.md#-cómo-conectarlo), copiás el texto entre comillas y se
lo escribís al asistente: él elige y encadena las herramientas solo.

> ⚖️ Es un **insumo no vinculante**: verifica y cita fuentes oficiales, pero la decisión
> profesional es humana. No incluye jurisprudencia ni normativa provincial. Los **cálculos**
> (actualización de montos, plazos) son orientativos. El **texto completo** de las normas y los
> vínculos dependen de que el sitio de InfoLEG esté online; si no, el servidor degrada al
> dataset y lo avisa (una lista vacía con aviso no significa "ninguna").

Índice:
- [Ejemplos por herramienta (3 c/u)](#ejemplos-por-fuente)
- [21 casos para abogados y relatores](#21-casos-para-abogados-y-relatores)
- [21 casos para tributaristas](#21-casos-para-tributaristas)

---

## Ejemplos por fuente

### 1. Búsqueda y vigencia de normas (`infoleg_buscar_normas`, dataset offline)
- *"¿La **Ley 27.551** de alquileres sigue vigente o fue derogada? ¿Por qué norma y desde cuándo?"*
- *"Buscá **decretos sobre 'emergencia'** publicados en 2024 y decime cuáles siguen vigentes."*
- *"Buscá la **Ley 26.994 (CCyC)** y mostrame la ficha: sanción, B.O., organismo y cuántas normas la modificaron."*

### 2. Texto completo y comparación de versiones (`infoleg_obtener_texto_*`, `infoleg_comparar_original_actualizado`)
- *"Traeme el **texto vigente del art. 245 de la LCT (Ley 20.744)**."*
- *"**Compará** el texto original y el actualizado de la **Ley 24.240** y decime qué artículos cambiaron."*
- *"Dame el texto actualizado de la **Ley de Amparo 16.986** completo."*

### 3. Trazabilidad de reformas (`infoleg_ver_normas_que_la_modifican` / `_que_modifica`)
- *"¿Qué normas **modificaron** a la **Ley 20.744** desde 2023?"*
- *"La **Ley 27.742 (Bases)**: listame **qué normas modifica**, con fechas."*
- *"Verificá si el **art. 1078 del CCyC** fue modificado y desde cuándo rige la nueva redacción."*

### 4. Dólar (cotización actual)
- *"Convertí una **condena de USD 10.000** al **dólar oficial de hoy**."*
- *"¿A cuánto está el **dólar MEP** ahora?"*
- *"Dame todas las cotizaciones del dólar de hoy (oficial/blue/MEP/CCL)."*

### 5. BCRA — tipo de cambio histórico, CER y tasas (`bcra_cotizaciones`, `bcra_variables`)
- *"Dame el **tipo de cambio mayorista del BCRA del 28/04/2026** para una diferencia de cambio."*
- *"Traeme la **serie del CER** entre marzo 2023 y hoy para actualizar un monto de sentencia."*
- *"¿Cuál es el último valor de **reservas** y de la **tasa de política monetaria**?"*

### 6. INDEC / IPC (`indec_buscar_series`, `indec_serie`)
- *"**IPC** de los últimos 12 meses para actualizar una **cuota alimentaria**."*
- *"Variación del **IPC entre 03/2024 y hoy** para liquidar la actualización de una sentencia."*
- *"Buscá el **id de la serie del IPC nivel general nacional** y dame el último dato."*

### 7. Feriados y plazos (`feriados_nacionales`, `calcular_plazo`)
- *"¿Cuándo vence un plazo de **10 días hábiles** contado desde el **18/12**?"* (descuenta fines de semana, feriados nacionales y puentes, y lista los días salteados)
- *"Me notificaron el **09/06/2026**, tengo **5 días hábiles** para apelar: ¿cuándo vence?"*
- *"Listame los **feriados nacionales de 2026**."*

> `calcular_plazo` **no** contempla feriados provinciales, ferias judiciales ni días inhábiles
> de cada tribunal, ni el plazo de gracia: confirmá siempre con el código procesal aplicable.

### 8. Actualización de montos (`calcular_actualizacion`)
- *"**Actualizá $500.000 por CER** desde el 15/03/2023 hasta hoy."*
- *"Actualizá un alquiler de $400.000 por **ICL** (o por **UVA**) entre dos fechas y mostrame el coeficiente."*
- *"Actualizá una cuota alimentaria de $300.000 fijada en enero por **IPC** del INDEC hasta el último mes publicado."*

> Calcula coeficiente y monto con CER, UVA, ICL (BCRA) o IPC (INDEC). **No** calcula intereses
> ni tasas; el índice aplicable lo define el contrato, la sentencia o la norma.

### 9. Boletín Oficial (`boletin_oficial_sumario`, `_buscar`, `_ver`)
- *"¿Qué publicó **hoy el Boletín Oficial** sobre **monotributo**?"*
- *"Mostrame el sumario de la **primera sección** del 02/06/2026 y leé la resolución que más te llame la atención."*
- *"Buscá en el Boletín Oficial las **licitaciones de la tercera sección** que mencionen 'hospital' en el último mes."*
- *"¿Qué se publicó en el Boletín Oficial de la **Ciudad de Buenos Aires** el 02/06/2026?"*

> Cubre 1ra y 3ra sección de la Nación y el Boletín de CABA. La 2da sección (sociedades) no
> está: el `robots.txt` del sitio la excluye. No hay edición en fines de semana ni feriados.

### 10. CUIT (`cuit_validar`)
- *"¿Es **válido este CUIT**: 20-12345678-6? Verificá el dígito verificador."*
- *"Normalizá estos CUIT a XX-XXXXXXXX-X y decime cuáles son de persona humana y cuáles jurídica."*

> Es un cálculo local: **no** consulta a ARCA ni confirma que el CUIT exista, esté activo o a
> quién pertenece (no devuelve razón social).

---

## 21 casos para abogados y relatores

### Vigencia y derecho aplicable
1. **Chequear vigencia antes de citar** — *"¿La Ley 27.551 sigue vigente? Listá qué la modificó/derogó (incluido el DNU 70/2023), con fechas."*
2. **Qué texto regía a la fecha del hecho** — *"Línea de tiempo de modificaciones de la Ley 20.744 (2020→hoy): ¿qué redacción del art. 245 regía en 03/2023?"*
3. **Trazabilidad de una reforma** — *"Ley 27.742: ¿qué normas modifica y qué normas la modifican, con fecha?"*
4. **Comparar versiones** — *"Compará original vs actualizado de la Ley 24.240 e indicá qué artículos cambiaron."*
5. **Ubicar una cita ambigua** — *"'La ley de violencia de género': buscá la norma y decime número, id, organismo y vigencia."*

### Cómputo de plazos
6. **Vencimiento en días hábiles** — *"Notificado el 09/06/2026, 5 días hábiles para apelar: ¿cuándo vence? (descontá feriados)."*
7. **Planificación con feriados** — *"Feriados 2026 de julio a septiembre para reprogramar audiencias y vencimientos."*

### Actualización de montos
8. **Indemnización/deuda por IPC** — *"IPC del INDEC entre 03/2023 y hoy: variación acumulada para actualizar una indemnización."*
9. **Cuota alimentaria por IPC** — *"Cuota fijada en $X en 01/2025: ¿cuánto sería hoy actualizada por IPC?"*
10. **Obligación en USD a una fecha** — *"Obligación en USD vencida el 28/04/2026: tipo de cambio oficial del BCRA de esa fecha."*
11. **Actualización por CER** — *"Actualizá por CER $1.000.000 desde el 01/03/2023 hasta hoy."*

### Research y encuadre normativo
12. **Mapear un tema** — *"Normas nacionales con 'datos personales Y proteccion'; decime cuáles siguen vigentes y la ley marco actual."*
13. **Encuadre de un caso** — *"Despido por discriminación: ¿qué leyes/decretos nacionales aplican? Con id, fecha y resumen."*
14. **Por organismo y fechas** — *"Decretos del PEN entre 2024-01-01 y 2024-06-30 sobre 'emergencia' o 'desregulacion'."*
15. **Due diligence regulatoria** — *"Dictamen sobre fintech: relevá normativa nacional (BCRA, UIF, consumidor), marcá vigentes, agrupá por tema."*

### Relatores (trabajo judicial)
16. **Verificador de citas de un proyecto** ⭐ — *"Normas citadas: Ley 26.994, 24.240, 27.551, Decreto 70/2023. Confirmá que existen, número correcto, y si el artículo citado sigue vigente. Marcá en rojo lo inexistente/desactualizado."*
17. **Bloque normativo de un proyecto** — *"Responsabilidad por productos: armá el bloque normativo (CCyC + Ley 24.240) con texto vigente y cita formal."*
18. **¿La norma invocada cambió tras el hecho?** — *"La actora invoca el art. 245 LCT; el hecho es de 2022. ¿Fue modificado entre 2022 y hoy? ¿Qué redacción aplica?"*
19. **Ficha para cita formal** — *"Ficha citable de la Ley 26.485: tipo, número, fechas de sanción y B.O., organismo y URL oficial."*
20. **Novedades normativas** — *"Leyes y decretos nacionales publicados entre 26/05 y 02/06/2026 en materia civil/laboral/procesal: número, organismo, fecha y resumen."*
21. **Qué publicó el Boletín Oficial** — *"¿Qué publicó hoy el Boletín Oficial sobre el régimen de alquileres? Dame las normas con link y leé la más relevante."*

---

## 21 casos para tributaristas

### Procedimiento y vigencia
1. **¿Vigente esa RG antes de citarla?** — *"RG AFIP 830/2000: resolvé id, ficha y modificatorias en orden cronológico, marcando cambios de alícuotas/mínimos. ¿Sigue vigente?"*
2. **Texto de Ganancias en un período pasado** — *"Ley 20.628: modificatorias 2017→hoy con fechas; ¿qué versión regía en el ejercicio 2018?"*
3. **¿Impuesto PAÍS vigente para ese período?** — *"Ley 27.541 (Impuesto PAÍS): vigencia original y normas que lo modificaron/prorrogaron/derogaron. ¿Correspondía liquidarlo en 2025?"*
4. **Ubicar una RG ambigua** — *"'La RG de AFIP del régimen de información de Bienes Personales': candidatas con número, id, organismo y vigencia (usá el organismo exacto de InfoLEG)."*

### Research y marco normativo
5. **Expediente de un régimen de retención** — *"Régimen de retención de IVA (RG 2854): id, ficha, modificatorias y texto vigente, citando InfoLEG."*
6. **Mapear con operadores** — *"Normas con 'factura electronica Y exportacion' de AFIP; cuáles vigentes y la RG marco del comprobante clase E."*
7. **Due diligence sectorial (granos)** — *"Normativa AFIP/DGI sobre 'compraventa de granos retencion' y 'registro fiscal operadores granos'; vigentes, agrupadas por régimen."*
8. **Trazabilidad de la 27.430** — *"Ley 27.430: qué modifica y qué la modifica, con fecha; después comparativo original vs actualizado."*
9. **Barrido por organismo y fechas** — *"Normas de AFIP publicadas en 2024 que mencionen 'regimen de informacion' o 'percepcion', por fecha desc."*

### Plazos, prescripción y actualización
10. **Plazo art. 17 (vista)** — *"Vista notificada el 12/06/2026, 15 días hábiles: vencimiento descontando feriados + texto vigente del art. 17 Ley 11.683."*
11. **Art. 76: reconsideración vs. TFN** — *"Resolución con multa notificada el 22/05/2026: vencimiento de los 15 días hábiles del art. 76 + ¿fue modificado?"*
12. **¿Prescripción con suspensiones?** — *"Texto vigente del art. 56 Ley 11.683 + normas que suspendieron la prescripción 2020-2022 para Ganancias 2019."*
13. **IPC vs. interés resarcitorio** — *"Deuda de IVA 11/2024 regularizada en 06/2026: variación del IPC del período + resolución vigente de la tasa del art. 37."*
14. **Actualizar por CER un monto viejo** — *"Actualizá por CER $500.000 desde el 01/03/2023 hasta hoy y mostrame coeficiente y valores del índice."*

### Macro-financiero
15. **Percepción + cotización del día** — *"RG de percepción sobre consumos en dólares: texto vigente de alícuota/base + cotización oficial y tarjeta de hoy."*
16. **Bienes Personales: USD al 31/12** — *"Dólar oficial tipo comprador del último día hábil de diciembre 2025 (+ euro/real) y qué dice el art. 22 Ley 23.966."*
17. **Diferencias de cambio por fecha** — *"Exportación facturada 15/03/2026 y cobrada 28/04/2026: tipo de cambio mayorista del BCRA en ambas fechas."*
18. **Ajuste por inflación impositivo** — *"IPC del ejercicio 2025 + texto vigente del Título VI de la Ley 20.628 y qué normas lo modificaron (umbrales, tercios)."*

### Compliance y producto
19. **Verificador de citas del dictamen** ⭐ — *"Normas citadas: Ley 20.628, 27.430, 11.683, Decreto 862/2019, RG 5391: confirmá existencia, número y vigencia del artículo citado."*
20. **Monitoreo semanal tributario** — *"Normativa tributaria nacional publicada esta semana: RGs de ARCA/AFIP y decretos del PEN, con número/organismo/fecha/resumen; marcá retención/percepción/información."*
21. **Boletín Oficial del día** — *"¿Qué publicó hoy el Boletín Oficial sobre monotributo o ganancias? Listá las resoluciones generales con link."*
