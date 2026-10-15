"""Actualización de montos con índices oficiales: CER, UVA, ICL (BCRA v4.0) e IPC (INDEC).

Ids verificados contra la API real el 2026-10-15:
  * CER -> BCRA Monetarias idVariable 30  (Coeficiente de Estabilización de Referencia, base 2.2.02=1)
  * UVA -> BCRA Monetarias idVariable 31  (Unidad de Valor Adquisitivo, base 31.3.16=14.05)
  * ICL -> BCRA Monetarias idVariable 40  (Índice para Contratos de Locación, base 30.6.20=1)
  * IPC -> datos.gob.ar serie 148.3_INIVELNAL_DICI_M_26 (IPC Nivel General Nacional, base dic 2016,
           mensual; la serie 145.3_INGNACNAL_DICI_M_15 es equivalente y se usa de respaldo)

CER/UVA/ICL son diarios: si no hay dato el día pedido se usa el último anterior (hasta 15 días
atrás). El IPC es mensual: se usa el valor del mes de cada fecha (o el último mes publicado).
El resultado es orientativo: el índice aplicable depende del contrato, la sentencia o la norma.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from . import bcra
from .base import TTL_INDEC, SourceError, error_result, get_json, parse_iso_date

INDICES = {
    "CER": {
        "tipo": "bcra", "id": 30,
        "nombre": "CER — Coeficiente de Estabilización de Referencia (BCRA)",
        "url": "https://www.bcra.gob.ar/PublicacionesEstadisticas/Principales_variables.asp",
    },
    "UVA": {
        "tipo": "bcra", "id": 31,
        "nombre": "UVA — Unidad de Valor Adquisitivo (BCRA)",
        "url": "https://www.bcra.gob.ar/PublicacionesEstadisticas/Principales_variables.asp",
    },
    "ICL": {
        "tipo": "bcra", "id": 40,
        "nombre": "ICL — Índice para Contratos de Locación (BCRA)",
        "url": "https://www.bcra.gob.ar/PublicacionesEstadisticas/Principales_variables.asp",
    },
    "IPC": {
        "tipo": "indec", "id": "148.3_INIVELNAL_DICI_M_26", "respaldo": "145.3_INGNACNAL_DICI_M_15",
        "nombre": "IPC Nivel General Nacional, base dic 2016, mensual (INDEC)",
        "url": "https://www.indec.gob.ar/indec/web/Nivel4-Tema-3-5-31",
    },
}
MONTO_MAX = Decimal("1e15")
_LOOKBACK_DIAS = 15
_SERIES_URL = "https://apis.datos.gob.ar/series/api/series"
_AVISO = (
    "Calculo orientativo con datos publicos oficiales; no constituye asesoramiento juridico "
    "ni liquidacion. El indice aplicable (y su forma de aplicacion, ej. mes de referencia o "
    "capitalizacion) depende del caso: contrato, sentencia o norma. Verifica el resultado."
)


def _dec(v) -> Decimal | None:
    try:
        d = Decimal(str(v))
    except (InvalidOperation, ValueError):
        return None
    return d if d.is_finite() else None


def _valor_diario(var_id: int, dia: date, *, user_agent: str, timeout: float) -> tuple[date, Decimal]:
    desde = (dia - timedelta(days=_LOOKBACK_DIAS)).isoformat()
    puntos = bcra.serie_puntos(var_id, desde, dia.isoformat(), user_agent=user_agent,
                               timeout=timeout)
    mejor: tuple[date, Decimal] | None = None
    for p in puntos:
        f = parse_iso_date(p.get("fecha"))
        v = _dec(p.get("valor"))
        if f is None or v is None or f > dia:
            continue
        if mejor is None or f > mejor[0]:
            mejor = (f, v)
    if mejor is None:
        raise SourceError(f"sin dato de la variable {var_id} hasta {dia.isoformat()} "
                          f"(se busco {_LOOKBACK_DIAS} dias hacia atras)")
    return mejor


def _valor_mensual(serie_id: str, dia: date, *, user_agent: str, timeout: float) -> tuple[date, Decimal]:
    primero = dia.replace(day=1)
    data = get_json(
        _SERIES_URL, user_agent=user_agent, timeout=timeout, ttl=TTL_INDEC,
        params={"ids": serie_id, "format": "json", "sort": "desc", "limit": 1,
                "end_date": primero.isoformat()},
    )
    rows = data.get("data", []) if isinstance(data, dict) else []
    if not rows or not isinstance(rows[0], list) or len(rows[0]) < 2:
        raise SourceError(f"sin dato de IPC hasta {primero.isoformat()} (la serie empieza en dic-2016)")
    f = parse_iso_date(rows[0][0])
    v = _dec(rows[0][1])
    if f is None or v is None:
        raise SourceError("dato de IPC ilegible")
    return f, v


def _punta(indice: str, dia: date, *, user_agent: str, timeout: float) -> dict:
    cfg = INDICES[indice]
    if cfg["tipo"] == "bcra":
        f, v = _valor_diario(cfg["id"], dia, user_agent=user_agent, timeout=timeout)
        desfase = (dia - f).days
        nota = None if desfase == 0 else f"Sin dato el {dia.isoformat()}; se uso el ultimo dia anterior."
    else:
        try:
            f, v = _valor_mensual(cfg["id"], dia, user_agent=user_agent, timeout=timeout)
        except SourceError:
            f, v = _valor_mensual(cfg["respaldo"], dia, user_agent=user_agent, timeout=timeout)
        mes_pedido = dia.replace(day=1)
        nota = None
        if f != mes_pedido:
            nota = (f"El IPC de {mes_pedido:%Y-%m} no esta publicado; se uso el ultimo mes "
                    f"disponible ({f:%Y-%m}).")
    out = {
        "fecha_solicitada": dia.isoformat(),
        "fecha_efectiva": f.isoformat(),
        "valor": float(v),
    }
    if cfg["tipo"] == "indec":
        out["periodo"] = f"{f:%Y-%m}"
    if nota:
        out["nota"] = nota
    out["_dec"] = v
    return out


def calcular(
    monto, fecha_desde: str, fecha_hasta: str, indice: str, *, user_agent: str,
    timeout: float = 20.0, hoy: date | None = None,
) -> dict:
    """Update ``monto`` from ``fecha_desde`` to ``fecha_hasta`` using the chosen index."""
    fuente = "BCRA / INDEC"
    hoy = hoy or date.today()
    idx = (indice or "").strip().upper()
    if idx not in INDICES:
        return error_result(f"indice invalido: usa uno de {', '.join(INDICES)}.", fuente)
    if isinstance(monto, bool):
        return error_result("monto debe ser un numero mayor que 0.", fuente)
    m = _dec(monto)
    if m is None or m <= 0:
        return error_result("monto debe ser un numero mayor que 0.", fuente)
    if m > MONTO_MAX:
        return error_result("monto fuera de rango.", fuente)
    d0, d1 = parse_iso_date(fecha_desde), parse_iso_date(fecha_hasta)
    if d0 is None or d1 is None:
        return error_result("fecha_desde y fecha_hasta deben ser fechas ISO 'YYYY-MM-DD'.", fuente)
    if d0 >= d1:
        return error_result("fecha_desde debe ser anterior a fecha_hasta.", fuente)
    if d1 > hoy:
        return error_result(
            f"fecha_hasta no puede ser futura (hoy es {hoy.isoformat()}); no se proyectan indices.",
            fuente)
    cfg = INDICES[idx]
    try:
        p0 = _punta(idx, d0, user_agent=user_agent, timeout=timeout)
        p1 = _punta(idx, d1, user_agent=user_agent, timeout=timeout)
    except SourceError as exc:
        return error_result(f"No se pudo obtener el indice {idx}: {exc}", cfg["nombre"])
    v0, v1 = p0.pop("_dec"), p1.pop("_dec")
    if v0 <= 0:
        return error_result(f"Valor inicial del indice {idx} invalido (<= 0).", cfg["nombre"])
    coef = v1 / v0
    actualizado = (m * coef).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    variacion = ((coef - 1) * 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    out = {
        "indice": idx,
        "monto_original": float(m),
        "fecha_desde": d0.isoformat(),
        "fecha_hasta": d1.isoformat(),
        "valor_indice_desde": p0,
        "valor_indice_hasta": p1,
        "coeficiente": float(coef.quantize(Decimal("0.00000001"), rounding=ROUND_HALF_UP)),
        "variacion_porcentual": float(variacion),
        "monto_actualizado": float(actualizado),
        "fuente": {"nombre": cfg["nombre"], "url": cfg["url"], "api": _api_url(cfg)},
        "aviso": _AVISO,
    }
    if idx == "IPC" and p0["fecha_efectiva"] == p1["fecha_efectiva"]:
        out["nota"] = "Ambas fechas caen en el mismo mes de IPC: el coeficiente es 1."
    return out


def _api_url(cfg: dict) -> str:
    if cfg["tipo"] == "bcra":
        return f"{bcra.URL_MONETARIAS}/{cfg['id']}"
    return f"{_SERIES_URL}?ids={cfg['id']}"
