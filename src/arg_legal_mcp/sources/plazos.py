"""Cómputo de plazos en días hábiles o corridos con fines de semana + feriados nacionales.

Fuente de feriados: ArgentinaDatos (feriados.py), que incluye inamovibles, trasladables y
"puentes" turísticos (días no laborables). Es una herramienta de apoyo: NO reemplaza el
cómputo procesal que hace el letrado con la normativa y el tribunal del caso.
"""

from __future__ import annotations

import unicodedata
from datetime import date, timedelta

from . import feriados as feriados_src
from .base import SourceError, error_result, parse_iso_date

FUENTE = "ArgentinaDatos (api.argentinadatos.com) + calendario"
DIAS_MAX_HABILES = 365
DIAS_MAX_CORRIDOS = 1100
_DIAS_ES = ["lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo"]

NO_CONTEMPLA = [
    "Feriados provinciales y municipales, y dias festivos de cada jurisdiccion.",
    "Ferias judiciales (enero, julio, receso invernal u otras) ni su suspension de plazos.",
    "Asuetos y dias inhabiles decretados por cada tribunal o dependencia (acordadas, "
    "resoluciones de superintendencia).",
    "El plazo de gracia de las primeras horas habiles del dia siguiente (ej. art. 124 CPCCN "
    "y equivalentes locales), que extiende la presentacion pero no el vencimiento.",
    "Feriados dispuestos por decreto con posterioridad a la ultima actualizacion de la fuente.",
]


def _tipo_norm(tipo: str) -> str | None:
    t = unicodedata.normalize("NFD", (tipo or "").strip().lower())
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    if t in ("habil", "habiles", "dias habiles"):
        return "habiles"
    if t in ("corrido", "corridos", "dias corridos"):
        return "corridos"
    return None


class _Calendario:
    def __init__(self, user_agent: str, timeout: float, incluir_puentes: bool):
        self.ua, self.timeout, self.puentes = user_agent, timeout, incluir_puentes
        self._anios: dict[int, dict] = {}

    def anio(self, y: int) -> dict:
        if y not in self._anios:
            self._anios[y] = feriados_src.feriados_por_fecha(
                y, user_agent=self.ua, timeout=self.timeout)
        return self._anios[y]

    def motivo_inhabil(self, d: date) -> dict | None:
        """Reason a day is non-working, or None if it is a working day."""
        if d.weekday() >= 5:
            return {"fecha": d.isoformat(), "dia": _DIAS_ES[d.weekday()],
                    "motivo": "fin de semana"}
        h = self.anio(d.year).get(d)
        if h is None:
            return None
        tipo, nombre = h
        if tipo == "puente":
            if not self.puentes:
                return None
            return {"fecha": d.isoformat(), "dia": _DIAS_ES[d.weekday()],
                    "motivo": f"{nombre} (dia no laborable con fines turisticos)", "tipo": tipo}
        return {"fecha": d.isoformat(), "dia": _DIAS_ES[d.weekday()],
                "motivo": f"feriado: {nombre}", "tipo": tipo}

    @property
    def anios(self) -> list[int]:
        return sorted(self._anios)

    def anios_sin_datos(self) -> list[int]:
        return [y for y, v in sorted(self._anios.items()) if not v]


def calcular(
    fecha_inicio: str, dias: int, tipo: str = "habiles", *, user_agent: str,
    timeout: float = 15.0, incluir_puentes: bool = True,
) -> dict:
    """Due date of a term of ``dias`` counted from the day after ``fecha_inicio``."""
    f0 = parse_iso_date(fecha_inicio)
    if f0 is None:
        return error_result("fecha_inicio debe ser una fecha ISO 'YYYY-MM-DD'.", FUENTE)
    if isinstance(dias, bool):
        return error_result("dias debe ser un entero positivo.", FUENTE)
    try:
        n = int(dias)
    except (TypeError, ValueError):
        return error_result("dias debe ser un entero positivo.", FUENTE)
    if n != dias or n < 1:
        return error_result("dias debe ser un entero positivo.", FUENTE)
    t = _tipo_norm(tipo)
    if t is None:
        return error_result("tipo debe ser 'habiles' o 'corridos'.", FUENTE)
    tope = DIAS_MAX_HABILES if t == "habiles" else DIAS_MAX_CORRIDOS
    if n > tope:
        return error_result(f"dias fuera de rango: maximo {tope} para plazos {t}.", FUENTE)
    if not 2000 <= f0.year <= 2099:
        return error_result("fecha_inicio fuera de rango (2000-2099).", FUENTE)

    cal = _Calendario(user_agent, timeout, incluir_puentes)
    inhabiles: list[dict] = []
    prorrogado = False
    fecha_calendario = None
    try:
        cursor = f0
        contados = 0
        if t == "habiles":
            while contados < n:
                cursor += timedelta(days=1)
                m = cal.motivo_inhabil(cursor)
                if m:
                    inhabiles.append(m)
                else:
                    contados += 1
            venc = cursor
        else:
            cursor = f0 + timedelta(days=n)
            for k in range(1, n + 1):
                m = cal.motivo_inhabil(f0 + timedelta(days=k))
                if m:
                    inhabiles.append(m)
            venc = cursor
            if cal.motivo_inhabil(venc):
                # Plazo corrido que vence en dia inhabil: se prorroga al primer dia habil siguiente
                # (criterio del art. 6 CCyC; verificar si la norma del caso dispone otra cosa).
                fecha_calendario = venc
                prorrogado = True
                while True:
                    venc += timedelta(days=1)
                    m = cal.motivo_inhabil(venc)
                    if not m:
                        break
                    inhabiles.append(m)
    except SourceError as exc:
        return error_result(f"No se pudieron obtener los feriados: {exc}", FUENTE)

    out: dict = {
        "fecha_inicio": f0.isoformat(),
        "dias": n,
        "tipo": t,
        "primer_dia_computado": (f0 + timedelta(days=1)).isoformat(),
        "fecha_vencimiento": venc.isoformat(),
        "dia_semana_vencimiento": _DIAS_ES[venc.weekday()],
        "dias_inhabiles": inhabiles,
        "total_dias_inhabiles": len(inhabiles),
        "anios_consultados": cal.anios,
        "computo": (
            "El computo arranca el dia siguiente a fecha_inicio; en plazos habiles solo cuentan "
            "lunes a viernes que no sean feriado nacional ni dia no laborable."
            if t == "habiles" else
            "Dias corridos desde el dia siguiente a fecha_inicio; si el ultimo dia es inhabil "
            "se prorroga al primer dia habil siguiente."
        ),
        "incluye_puentes_turisticos": incluir_puentes,
        "no_contempla": NO_CONTEMPLA,
        "fuente": FUENTE,
        "aviso": "Calculo orientativo: confirma el vencimiento con la normativa procesal y el "
        "tribunal del caso antes de depender de el.",
    }
    if prorrogado:
        out["vencimiento_sin_prorroga"] = fecha_calendario.isoformat()
        out["prorrogado_por_dia_inhabil"] = True
    sin = cal.anios_sin_datos()
    if sin:
        out["anios_sin_feriados_publicados"] = sin
        out["advertencia"] = (
            f"La fuente no tiene feriados cargados para {', '.join(map(str, sin))}: "
            "esos anios se calcularon solo con fines de semana."
        )
    return out
