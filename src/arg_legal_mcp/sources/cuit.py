"""CUIT / CUIL / CDI — local validation (no network).

Checks the 11-digit structure, the modulo-11 check digit (weights 5,4,3,2,7,6,5,4,3,2),
normalises the input (with or without hyphens/spaces) and infers the kind of person from
the two-digit prefix. It does NOT verify that the number exists or is active in the ARCA
registry: that needs the official padrón web service (WSAA certificate).
"""

from __future__ import annotations

import re

from .base import error_result

FUENTE = "validacion local (algoritmo modulo 11 de ARCA/AFIP)"
_WEIGHTS = (5, 4, 3, 2, 7, 6, 5, 4, 3, 2)

# prefix -> (tipo_persona, detalle)
_PREFIJOS = {
    "20": ("fisica", "persona humana (generalmente masculino)"),
    "27": ("fisica", "persona humana (generalmente femenino)"),
    "23": ("fisica", "persona humana (prefijo usado cuando hay colision de digito verificador)"),
    "24": ("fisica", "persona humana (prefijo usado cuando hay colision de digito verificador)"),
    "30": ("juridica", "persona juridica / sociedad / ente"),
    "33": ("juridica", "persona juridica / sociedad / ente"),
    "34": ("juridica", "persona juridica / sociedad / ente"),
}


def digito_verificador(base10: str) -> int | None:
    """Expected check digit for the first 10 digits, or None if the algorithm yields 10."""
    total = sum(int(base10[i]) * _WEIGHTS[i] for i in range(10))
    dv = 11 - (total % 11)
    if dv == 11:
        return 0
    if dv == 10:
        return None  # ARCA never issues such a number (the prefix is changed instead)
    return dv


def cuit_valido(cuit: str) -> bool:
    """True if ``cuit`` (11 digits, any separators) has a correct check digit."""
    d = re.sub(r"\D", "", cuit or "")
    if len(d) != 11:
        return False
    return digito_verificador(d[:10]) == int(d[10])


def formatear(d: str) -> str:
    return f"{d[:2]}-{d[2:10]}-{d[10]}"


def validar(cuit: str) -> dict:
    """Validate and normalise a CUIT/CUIL. Never raises; never touches the network."""
    if not isinstance(cuit, str) or not cuit.strip():
        return error_result("Indica un CUIT/CUIL (ej: 20-12345678-6 o 20123456786).", FUENTE)
    raw = cuit.strip()
    if len(raw) > 20 or re.search(r"[^\d\s\-./]", raw):
        return {
            "cuit_ingresado": raw[:30], "valido": False,
            "motivo": "Contiene caracteres que no son digitos ni separadores (- . / espacio).",
            "fuente": FUENTE,
        }
    d = re.sub(r"\D", "", raw)
    if len(d) != 11:
        return {
            "cuit_ingresado": raw, "valido": False,
            "motivo": f"Debe tener 11 digitos y tiene {len(d)}.",
            "fuente": FUENTE,
        }
    esperado = digito_verificador(d[:10])
    prefijo = d[:2]
    tipo, detalle = _PREFIJOS.get(
        prefijo, ("indeterminado", "prefijo no reconocido como persona humana ni juridica comun")
    )
    out: dict = {
        "cuit_ingresado": raw,
        "cuit": formatear(d),
        "cuit_sin_guiones": d,
        "prefijo": prefijo,
        "tipo_persona": tipo,
        "tipo_detalle": detalle,
        "digito_verificador": int(d[10]),
        "digito_verificador_esperado": esperado,
        "fuente": FUENTE,
        "nota": "Valida solo la estructura y el digito verificador; no confirma que el CUIT "
        "exista ni que este activo en ARCA. Para eso: constancia de inscripcion en arca.gob.ar.",
    }
    if esperado is None:
        out["valido"] = False
        out["motivo"] = "El digito verificador calculado da 10: ARCA no emite numeros asi."
    elif esperado != int(d[10]):
        out["valido"] = False
        out["motivo"] = f"Digito verificador incorrecto: deberia ser {esperado}."
    else:
        out["valido"] = True
    if tipo == "fisica":
        out["dni"] = d[2:10].lstrip("0") or "0"
        out["dni_nota"] = "El bloque central de una persona humana coincide con su DNI."
    if tipo == "indeterminado" and out["valido"]:
        out["aviso"] = "El digito es correcto pero el prefijo no es uno de los habituales."
    return out
