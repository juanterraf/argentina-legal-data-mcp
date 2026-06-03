"""Synthetic fixtures faithful to the real InfoLEG HTML structure (no live network)."""

from __future__ import annotations

import csv
from pathlib import Path

from arg_legal_mcp.infoleg.dataset import COLUMNS

# ── buscarNormas.do results page ──────────────────────────────────────────────
BUSQUEDA_HTML = """
<html><body>
<p>Cantidad de Normas Encontradas: 1 en 1</p>
<div id="resultados_caja">
<table>
  <tr><td class="titulos_columnas">Norma</td><td class="titulos_columnas">Boletin</td>
      <td class="titulos_columnas">Tema</td></tr>
  <tr>
    <td><a href="verNorma.do?id=265949">Ley 27275<br>HONORABLE CONGRESO DE LA NACION ARGENTINA</a></td>
    <td><a href="verBoletin.do?id=12345">16-nov-2016</a></td>
    <td><b>DERECHO A LA INFORMACION</b><span>Acceso a la informacion publica</span></td>
  </tr>
</table>
</div>
</body></html>
"""

# ── verNorma.do ficha ─────────────────────────────────────────────────────────
FICHA_HTML = """
<html><body>
<div id="Textos_Completos">
  <h1>ACCESO A LA INFORMACION PUBLICA</h1>
  <span class="destacado">HONORABLE CONGRESO DE LA NACION ARGENTINA</span>
  <p><strong>Ley 27275
Acceso a la Informacion Publica</strong> <span class="vr_azul11">14-sep-2016</span></p>
  <p>Publicada en el Boletin Oficial del <a href="verBoletin.do?id=12345">16-nov-2016</a> - Pagina: 3</p>
  <p>Resumen: Derecho de acceso a la informacion publica del Estado.</p>
  <p><a href="../anexos/265000-269999/265949/norma.htm">Texto completo de la norma</a></p>
  <p><a href="../anexos/265000-269999/265949/texact.htm">Texto actualizado</a></p>
  <p><a href="verVinculos.do?modo=1&id=265949">5 normas que modifica</a></p>
  <p><a href="verVinculos.do?modo=2&id=265949">3 normas la modifican</a></p>
</div>
</body></html>
"""

FICHA_NOT_FOUND_HTML = """
<html><body>
<span class="error">La norma que usted busca no se encuentra registrada.</span>
</body></html>
"""

# ── verVinculos.do ────────────────────────────────────────────────────────────
VINCULOS_HTML = """
<html><body>
<table>
  <tr>
    <td class="vr_azul11"><a href="verNorma.do?id=111">Decreto 222<br>PODER EJECUTIVO NACIONAL</a></td>
    <td class="vr_azul11">10-ene-2017</td>
    <td class="vr_azul11"><b>REGLAMENTACION</b><br>Reglamenta la ley de acceso</td>
  </tr>
  <tr>
    <td class="vr_azul11"><a href="verNorma.do?id=112">Resolucion 5<br>JEFATURA DE GABINETE</a></td>
    <td class="vr_azul11">02-feb-2018</td>
    <td class="vr_azul11"><b>COMPLEMENTA</b><br>Complementa el regimen</td>
  </tr>
</table>
</body></html>
"""

# ── mostrarBusquedaNormas.do config ───────────────────────────────────────────
CONFIG_HTML = """
<html><body><form>
<select name="tipoNorma">
  <option value="">-- Todos --</option>
  <option value="1">Ley</option>
  <option value="2">Decreto</option>
</select>
<select name="dependencia">
  <option value="">-- Todas --</option>
  <option value="5">ADMINISTRACION DE PARQUES NACIONALES</option>
  <option value="310">ADM. GRAL. DEL SERVICIO NACIONAL DE SANIDAD ANIMAL</option>
</select>
</form></body></html>
"""

# Annex full-text page (note the latin-1 accented chars in the source bytes).
TEXACT_HTML = (
    "<html><body><h2>Ley 27.275</h2>"
    "<p>Artículo 1º.- Derecho de acceso a la información pública (texto actualizado).</p>"
    "<script>ignore()</script></body></html>"
)


# ── Synthetic dataset CSV ─────────────────────────────────────────────────────
_ROWS = [
    {
        "id_norma": "265949", "tipo_norma": "Ley", "numero_norma": "27275",
        "clase_norma": "", "organismo_origen": "HONORABLE CONGRESO DE LA NACION ARGENTINA",
        "fecha_sancion": "2016-09-14", "numero_boletin": "33502", "fecha_boletin": "2016-11-16",
        "pagina_boletin": "3", "titulo_resumido": "ACCESO A LA INFORMACION PUBLICA",
        "titulo_sumario": "Derecho de acceso a la informacion publica",
        "texto_resumido": "informacion publica transparencia acceso",
        "observaciones": "", "texto_original": "<p>Articulo 1 - Derecho de acceso.</p>",
        "texto_actualizado": "<p>Articulo 1 - Derecho de acceso (consolidado).</p>",
        "modificada_por": "300100", "modifica_a": "111 222",
    },
    {
        "id_norma": "16986", "tipo_norma": "Ley", "numero_norma": "16986",
        "clase_norma": "", "organismo_origen": "PODER LEGISLATIVO NACIONAL",
        "fecha_sancion": "1966-10-18", "numero_boletin": "21000", "fecha_boletin": "1966-10-20",
        "pagina_boletin": "1", "titulo_resumido": "ACCION DE AMPARO",
        "titulo_sumario": "Regimen de la accion de amparo e informacion",
        "texto_resumido": "amparo informacion garantias", "observaciones": "",
        "texto_original": "<p>Amparo.</p>", "texto_actualizado": "",
        "modificada_por": "", "modifica_a": "",
    },
    {
        "id_norma": "99999", "tipo_norma": "Decreto", "numero_norma": "1000",
        "clase_norma": "", "organismo_origen": "PODER EJECUTIVO NACIONAL",
        "fecha_sancion": "2020-05-01", "numero_boletin": "34000", "fecha_boletin": "2020-05-02",
        "pagina_boletin": "2", "titulo_resumido": "EMERGENCIA",
        "titulo_sumario": "Medidas de emergencia economica",
        "texto_resumido": "emergencia economica", "observaciones": "",
        "texto_original": "<p>Emergencia.</p>", "texto_actualizado": "",
        "modificada_por": "", "modifica_a": "",
    },
]


def write_synthetic_csv(path: str | Path) -> Path:
    path = Path(path)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=COLUMNS)
        writer.writeheader()
        for row in _ROWS:
            writer.writerow({c: row.get(c, "") for c in COLUMNS})
    return path
