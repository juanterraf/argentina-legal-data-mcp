"""Annex (texto) URL resolution.

CRITICAL RULE (see DECISIONS.md): never *compute* the annex folder for a norma.
Always read the real relative URL (``norma.htm`` / ``texact.htm``) from the ficha
(``verNorma.do``) or from the offline dataset.

The voftec reference computed the folder using blocks of 50,000, which is wrong —
InfoLEG groups annex folders in blocks of **5,000** (e.g. Ley 27.275, id 265949,
lives in ``anexos/265000-269999/265949/norma.htm``). We keep a computed helper ONLY
as a last-resort fallback, and every value it produces is flagged unverified.
"""

from __future__ import annotations

from .models import TipoTexto

# InfoLEG groups annex folders in blocks of 5,000. This is NOT 50,000.
ANEXO_BLOCK_SIZE = 5000


def resolve_anexo_url(base_url: str, relative: str) -> str:
    """Resolve a relative annex href (read from the ficha/dataset) to an absolute URL.

    Hrefs are relative to the ``infolegInternet`` root; we strip any leading ``../``
    and re-anchor under the base, matching InfoLEG's actual layout.
    """
    relative = (relative or "").strip()
    if not relative:
        raise ValueError("Annex URL is empty; cannot resolve.")
    if relative.startswith(("http://", "https://")):
        return relative
    rel = relative.lstrip("/")
    while rel.startswith("../"):
        rel = rel[3:]
    return f"{base_url.rstrip('/')}/{rel}"


def computed_anexo_range(id_norma: int) -> str:
    """Return the 5,000-block folder name for an id (e.g. 265949 -> '265000-269999').

    UNVERIFIED. Prefer the real URL from the ficha/dataset.
    """
    floor = (id_norma // ANEXO_BLOCK_SIZE) * ANEXO_BLOCK_SIZE
    return f"{floor}-{floor + ANEXO_BLOCK_SIZE - 1}"


def computed_anexo_url(base_url: str, id_norma: int, tipo: TipoTexto) -> str:
    """Last-resort, UNVERIFIED annex URL. Callers must flag the result accordingly.

    InfoLEG sometimes deviates from the block convention, so this can 404 or point
    at the wrong file. It exists only so the server can offer *something* when neither
    the ficha nor the dataset yields a real URL.
    """
    fname = "texact.htm" if tipo == TipoTexto.ACTUALIZADO else "norma.htm"
    rng = computed_anexo_range(id_norma)
    return f"{base_url.rstrip('/')}/anexos/{rng}/{id_norma}/{fname}"
