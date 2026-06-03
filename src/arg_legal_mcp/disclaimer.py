"""Legal disclaimer attached to every legal-information response.

Per the project brief: information comes from official sources, does not replace
legal advice, and must be verified against the official portal before evidentiary use.
"""

from __future__ import annotations

DISCLAIMER = (
    "Informacion proveniente de fuentes oficiales (InfoLEG / datos.gob.ar). "
    "No constituye asesoramiento juridico. Verifique contra el portal oficial "
    "(https://www.infoleg.gob.ar) antes de cualquier uso probatorio o vinculante."
)


def with_disclaimer(payload: dict) -> dict:
    """Return ``payload`` with the standard ``_disclaimer`` field added."""
    return {**payload, "_disclaimer": DISCLAIMER}
