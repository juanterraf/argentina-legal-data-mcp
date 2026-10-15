"""Catalogs: tipos de norma and organismos (issuing bodies), with fuzzy lookup.

* ``tipos_norma.json`` lists every norm type present in the dataset (name + count) and,
  when known, the id the live InfoLEG form uses (``id_infoleg``). Regenerate it with
  ``python -m arg_legal_mcp update-configs``.
* Organismos come from the dataset itself (``organismo_origen``, exactly as InfoLEG
  writes them), so the names returned are the ones the dataset filter matches. The
  optional ``dependencias.json`` (live form ids) is only used to map a name to the live
  ``dependencia`` id for ``en_vivo`` searches.

Fuzzy search uses rapidfuzz (WRatio) and is accent-insensitive; common acronyms (AFIP,
ARCA, ANSES, BCRA...) are expanded first.
"""

from __future__ import annotations

import json
import os
import threading
import unicodedata
from pathlib import Path

from rapidfuzz import fuzz, process


def _normalize(text: str) -> str:
    text = (text or "").lower()
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii").strip()


# Common acronyms -> how InfoLEG names the body (substring of organismo_origen).
ORGANISMO_ALIASES = {
    "afip": "ADMINISTRACION FEDERAL DE INGRESOS PUBLICOS",
    "arca": "AGENCIA DE RECAUDACION Y CONTROL ADUANERO",
    "dgi": "DIRECCION GENERAL IMPOSITIVA",
    "anses": "ADMINISTRACION NACIONAL DE LA SEGURIDAD SOCIAL",
    "bcra": "BANCO CENTRAL DE LA REPUBLICA ARGENTINA",
    "cnv": "COMISION NACIONAL DE VALORES",
    "enacom": "ENTE NACIONAL DE COMUNICACIONES",
    "anmat": "ADM.NAC.DE MEDICAMENTOS",
    "senasa": "SERVICIO NACIONAL DE SANIDAD",
    "pen": "PODER EJECUTIVO NACIONAL",
    "congreso": "CONGRESO DE LA NACION",
    "enre": "ENTE NACIONAL REGULADOR DE LA ELECTRICIDAD",
    "enargas": "ENTE NACIONAL REGULADOR DEL GAS",
    "ssn": "SUPERINTENDENCIA DE SEGUROS DE LA NACION",
    "igj": "INSPECCION GENERAL DE JUSTICIA",
    "uif": "UNIDAD DE INFORMACION FINANCIERA",
    "jgm": "JEFATURA DE GABINETE DE MINISTROS",
    "csjn": "CORTE SUPREMA DE JUSTICIA",
}

# Accepted spellings for norm types -> dataset name.
_TIPO_ALIASES = {
    "dnu": "Decreto",
    "decreto de necesidad y urgencia": "Decreto",
    "decreto ley": "Decreto/Ley",
    "decreto-ley": "Decreto/Ley",
    "da": "Decisión Administrativa",
    "res": "Resolución",
    "disp": "Disposición",
}

# Ids used by the live InfoLEG search form (only the ones we know for sure).
TIPOS_ID_INFOLEG = {
    "Ley": 1, "Decreto": 2, "Resolución": 3, "Disposición": 4, "Decisión Administrativa": 8,
}


class CatalogService:
    def __init__(self, dependencias_path: str | Path, tipos_path: str | Path, dataset=None):
        self.dependencias_path = Path(dependencias_path)
        self.tipos_path = Path(tipos_path)
        self.dataset = dataset
        self._deps: list[dict] | None = None
        self._tipos: list[dict] | None = None
        self._orgs: list[tuple[str, int]] | None = None
        self._orgs_key: tuple | None = None
        self._lock = threading.Lock()

    # ── loading ──────────────────────────────────────────────────────────────
    @staticmethod
    def _load(path: Path) -> list[dict]:
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                return data if isinstance(data, list) else []
            except (json.JSONDecodeError, OSError):
                return []
        return []

    @property
    def dependencias(self) -> list[dict]:
        if self._deps is None:
            self._deps = self._load(self.dependencias_path)
        return self._deps

    @property
    def tipos(self) -> list[dict]:
        if self._tipos is None:
            tipos = self._load(self.tipos_path)
            if not tipos and self.dataset is not None:
                tipos = tipos_catalogo(self.dataset.tipos_norma())
            self._tipos = tipos
        return self._tipos

    # ── tipos ────────────────────────────────────────────────────────────────
    def tipo_canonico(self, valor: str | int | None) -> str | None:
        """Map a user value (name, alias or legacy live id) to the dataset type name."""
        if valor is None or str(valor).strip() == "":
            return None
        raw = str(valor).strip()
        if raw.isdigit():
            n = int(raw)
            hit = next((t for t in self.tipos if t.get("id_infoleg", t.get("id")) == n), None)
            if hit:
                return hit["nombre"]
            return next((k for k, v in TIPOS_ID_INFOLEG.items() if v == n), None)
        norm = _normalize(raw)
        if norm in _TIPO_ALIASES:
            return _TIPO_ALIASES[norm]
        hit = next((t["nombre"] for t in self.tipos if _normalize(t["nombre"]) == norm), None)
        if hit:
            return hit
        # tolerate plurals ("leyes", "resoluciones")
        for suf in ("es", "s"):
            if norm.endswith(suf):
                base = norm[: -len(suf)]
                hit = next((t["nombre"] for t in self.tipos if _normalize(t["nombre"]) == base),
                           None)
                if hit:
                    return hit
        return None

    def tipo_id_infoleg(self, nombre: str | None) -> int | None:
        if not nombre:
            return None
        hit = next((t for t in self.tipos if t.get("nombre") == nombre), None)
        if hit and isinstance(hit.get("id_infoleg", hit.get("id")), int):
            return hit.get("id_infoleg", hit.get("id"))
        return TIPOS_ID_INFOLEG.get(nombre)

    # Back-compat helpers (id -> name).
    def tipo_nombre(self, tipo_id: int) -> str | None:
        return self.tipo_canonico(tipo_id)

    # ── organismos ───────────────────────────────────────────────────────────
    def _organismos(self) -> list[tuple[str, int]]:
        if self.dataset is None:
            return [(d["nombre"], 0) for d in self.dependencias if d.get("nombre")]
        try:
            st = os.stat(self.dataset.db_path)
            key = (st.st_mtime_ns, st.st_size)
        except (OSError, AttributeError, TypeError):
            key = None
        with self._lock:
            if self._orgs is None or key != self._orgs_key:
                orgs = self.dataset.organismos() if self.dataset.available() else []
                if not orgs:
                    orgs = [(d["nombre"], 0) for d in self.dependencias if d.get("nombre")]
                self._orgs, self._orgs_key = orgs, key
            return self._orgs

    def expandir_organismo(self, texto: str | None) -> str | None:
        """Acronym -> InfoLEG name fragment (``AFIP`` -> ``ADMINISTRACION FEDERAL ...``)."""
        if not texto or not texto.strip():
            return None
        return ORGANISMO_ALIASES.get(_normalize(texto), texto.strip())

    def buscar_organismos(self, query: str, limit: int = 10) -> list[dict]:
        orgs = self._organismos()
        if not orgs or not (query or "").strip():
            return []
        q = _normalize(query)
        alias = ORGANISMO_ALIASES.get(q)
        out: list[dict] = []
        seen: set[str] = set()
        if alias:
            for nombre, n in orgs:
                if _normalize(alias) in _normalize(nombre) and nombre not in seen:
                    out.append({"nombre": nombre, "cantidad_normas": n, "score": 100.0})
                    seen.add(nombre)
        choices = {i: _normalize(nombre) for i, (nombre, _) in enumerate(orgs)}
        matches = process.extract(q, choices, scorer=fuzz.WRatio, limit=limit * 3,
                                  score_cutoff=70)
        # Prefer strong matches, then the bodies that issued more norms.
        matches.sort(key=lambda m: (-round(m[1]), -orgs[m[2]][1]))
        for _, score, i in matches:
            nombre, n = orgs[i]
            if nombre not in seen:
                out.append({"nombre": nombre, "cantidad_normas": n, "score": round(score, 1)})
                seen.add(nombre)
        return out[:limit]

    # Live form ids (only if dependencias.json was generated from the live form).
    def dependencia_id_infoleg(self, nombre: str | None) -> int | None:
        if not nombre:
            return None
        n = _normalize(nombre)
        hit = next((d for d in self.dependencias if _normalize(d.get("nombre", "")) == n), None)
        return int(hit["id"]) if hit and str(hit.get("id", "")).isdigit() else None


def tipos_catalogo(conteos: list[tuple[str, int]], live: list[dict] | None = None) -> list[dict]:
    """Build the tipos catalog from dataset counts (+ live form ids when available)."""
    ids = dict(TIPOS_ID_INFOLEG)
    for t in live or []:
        if t.get("nombre") and isinstance(t.get("id"), int):
            ids[t["nombre"]] = t["id"]
    out = []
    for nombre, cantidad in conteos:
        entry = {"nombre": nombre, "cantidad": cantidad}
        if nombre in ids:
            entry["id_infoleg"] = ids[nombre]
        out.append(entry)
    return out
