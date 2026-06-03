"""AFIP/ARCA padrón lookup — BEST EFFORT and explicitly non-guaranteed.

There is NO reliable, free, no-auth AFIP REST API (verified 2026-06-03). The only free
endpoint, ``soa.afip.gob.ar/sr-padron``, is undocumented, runs on an ancient stack, and
is frequently down (it was returning 404 during research). Productive use needs the
official SOAP padrón with WSAA token + X.509 certificate. Name search is not available
from any free source.

This module validates the CUIT locally (always works) and attempts the unofficial
endpoint, returning a clearly-labelled structured result either way.
"""

from __future__ import annotations

import re

from .base import SourceError, get_json

SOA = "https://soa.afip.gob.ar/sr-padron/v2/persona"
_NOTE = (
    "No existe API gratuita oficial de AFIP/ARCA. Este endpoint es NO OFICIAL e "
    "inestable; para uso productivo se requiere WSAA + certificado X.509. La validacion "
    "del CUIT es local y siempre confiable."
)


def cuit_valido(cuit: str) -> bool:
    """Validate the 11-digit CUIT/CUIL check digit (mod 11)."""
    if len(cuit) != 11 or not cuit.isdigit():
        return False
    weights = [5, 4, 3, 2, 7, 6, 5, 4, 3, 2]
    total = sum(int(cuit[i]) * weights[i] for i in range(10))
    dv = 11 - (total % 11)
    if dv == 11:
        dv = 0
    elif dv == 10:
        dv = 9
    return dv == int(cuit[10])


def padron(cuit: str, *, user_agent: str, timeout: float = 12.0) -> dict:
    clean = re.sub(r"\D", "", cuit or "")
    if len(clean) != 11:
        return {
            "available": False,
            "cuit": clean,
            "error": "El CUIT debe tener 11 digitos.",
            "fuente": "validacion local",
            "nota": _NOTE,
        }
    valido = cuit_valido(clean)
    try:
        data = get_json(f"{SOA}/{clean}", user_agent=user_agent, timeout=timeout, retries=2)
    except SourceError as exc:
        return {
            "available": False,
            "cuit": clean,
            "cuit_valido": valido,
            "error": f"Servicio AFIP no disponible: {exc}",
            "fuente": "soa.afip.gob.ar (no oficial)",
            "nota": _NOTE,
        }
    p = data.get("data", data) if isinstance(data, dict) else {}
    return {
        "available": True,
        "cuit": clean,
        "cuit_valido": valido,
        "tipo_persona": p.get("tipoPersona"),
        "estado": p.get("estadoClave"),
        "nombre": p.get("nombre") or p.get("razonSocial"),
        "domicilio": p.get("domicilioFiscal") or p.get("domicilio"),
        "fuente": "soa.afip.gob.ar (no oficial)",
        "nota": _NOTE,
    }
