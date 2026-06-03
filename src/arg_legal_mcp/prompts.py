"""MCP prompts: reusable, guided workflows the agent can invoke.

These are *instructions* (templates), not legal opinions. Any analysis they trigger
is non-binding and must be checked against the official sources.
"""

from __future__ import annotations


def register_prompts(mcp) -> None:
    @mcp.prompt(title="Buscar ley o decreto")
    def buscar_ley_decreto(tipo: str = "Ley", numero: str = "", anio: str = "") -> str:
        """Guia para localizar una norma por tipo/numero/anio y devolver su ficha."""
        ref = f"{tipo} {numero}".strip() + (f"/{anio}" if anio else "")
        return (
            f"Necesito ubicar la norma: {ref}.\n\n"
            "Paso a paso:\n"
            f"1. Llama a infoleg_resolver_id(tipo='{tipo}', numero={numero or 'NUMERO'}) "
            "para obtener el id_norma. Si falla, usa infoleg_buscar_normas con "
            f"texto o tipo_norma+numero (en_vivo=true si hace falta).\n"
            "2. Con el id, llama a infoleg_ver_norma para traer la ficha completa.\n"
            "3. Resume: identidad, organismo, fechas de sancion y publicacion, y si "
            "tiene texto actualizado disponible.\n"
            "Inclui el disclaimer y recorda verificar en el portal oficial."
        )

    @mcp.prompt(title="Auditar norma completa")
    def auditar_norma(id_norma: str) -> str:
        """Encadena ficha -> texto vigente -> vinculos -> resumen estructurado."""
        return (
            f"Audita a fondo la norma InfoLEG id={id_norma}:\n\n"
            f"1. infoleg_ver_norma(id={id_norma}) -> metadata.\n"
            f"2. infoleg_obtener_texto_actualizado(id={id_norma}) -> lee el texto vigente "
            "completo, paginando con `siguiente_inicio` hasta hay_mas=false.\n"
            f"3. infoleg_ver_normas_que_la_modifican(id={id_norma}) -> historial de "
            "reformas (vigencia).\n"
            f"4. infoleg_ver_normas_que_modifica(id={id_norma}) -> impacto sobre otras.\n"
            "5. Entrega un resumen estructurado: objeto, articulado clave, estado de "
            "vigencia, y normas relacionadas. Marca toda inferencia como NO vinculante "
            "e inclui el disclaimer."
        )

    @mcp.prompt(title="Comparar versiones (original vs actualizado)")
    def comparar_versiones(id_norma: str) -> str:
        """Guia para comparar el texto original y el actualizado de una norma."""
        return (
            f"Compara el texto original y el actualizado de la norma id={id_norma}:\n\n"
            f"1. infoleg_comparar_original_actualizado(id={id_norma}) para el diff mecanico.\n"
            "2. Si el diff es relevante, lee ambos textos completos "
            f"(infoleg_obtener_texto_original e ..._actualizado, id={id_norma}).\n"
            "3. Explica los cambios materiales (articulos modificados, derogados, "
            "incorporados) con criterio juridico, dejando claro que es un analisis "
            "NO vinculante. Inclui el disclaimer."
        )
