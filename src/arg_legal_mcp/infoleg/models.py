"""Pydantic models for the InfoLEG domain.

These mirror the *shape* of the live InfoLEG service (form field names, vinculo
modes) — those are facts about the public API — but the code is our own.
"""

from __future__ import annotations

from datetime import date
from enum import Enum, IntEnum

from pydantic import BaseModel, ConfigDict, Field, computed_field


# ── Catalogs ────────────────────────────────────────────────────────────────--
class TipoNorma(BaseModel):
    id: int
    nombre: str


class Dependencia(BaseModel):
    id: int
    nombre: str


class BusquedaConfig(BaseModel):
    tipos_norma: list[TipoNorma] = Field(default_factory=list)
    dependencias: list[Dependencia] = Field(default_factory=list)


# ── buscarNormas.do (POST form) ───────────────────────────────────────────────
class BusquedaNormaRequest(BaseModel):
    """Body of the ``buscarNormas.do`` POST. Field names are the InfoLEG form names."""

    tipoNorma: int | None = None
    numero: int | None = None
    anio_sancion: int | None = None
    texto: str | None = None
    dependencia: int | None = None
    diaPubDesde: int | None = None
    mesPubDesde: int | None = None
    anioPubDesde: int | None = None
    diaPubHasta: int | None = None
    mesPubHasta: int | None = None
    anioPubHasta: int | None = None


class ModoDesplazamiento(str, Enum):
    AVANZAR = "AP"
    RETROCEDER = "RP"


class PaginacionRequest(BaseModel):
    """Body of the pagination POST (same endpoint, same session)."""

    desplazamiento: ModoDesplazamiento = ModoDesplazamiento.AVANZAR
    irAPagina: int = 1


class NormaSummary(BaseModel):
    id: int
    identidad_norma: str = ""
    organismo_emisor: str | None = None
    id_boletin: int | None = None
    fecha_publicacion: date | None = None
    organismo_padre: str | None = None
    tema: str | None = None
    sumario: str | None = None


class BusquedaNormaResponse(BaseModel):
    resultados: list[NormaSummary] = Field(default_factory=list)
    total_pags: int = 1
    total: int = 0

    @computed_field  # type: ignore[prop-decorator]
    @property
    def cant_resultados(self) -> int:
        return len(self.resultados)


# ── verNorma.do ───────────────────────────────────────────────────────────────
class ParamsVerNorma(BaseModel):
    id: int
    resaltar: bool | None = None


class VerNormaResponse(BaseModel):
    summary: NormaSummary
    fecha_emision: date | None = None
    pagina_boletin: int | None = None
    url_texto_completo: str | None = None  # norma.htm (texto original)
    url_texto_actualizado: str | None = None  # texact.htm (texto vigente)
    normas_que_modifica: int | None = None
    normas_que_modifican_esta: int | None = None


# ── verVinculos.do ────────────────────────────────────────────────────────────
class ModoVinculo(IntEnum):
    MODIFICA_A = 1  # normas que ESTA norma modifica/complementa (activa)
    MODIFICADA_POR = 2  # normas que modifican/complementan a ESTA (pasiva)


class ParamsVerVinculos(BaseModel):
    id: int
    modo: ModoVinculo
    model_config = ConfigDict(use_enum_values=True)


class VinculoNormaSummary(BaseModel):
    id: int
    identidad_norma: str | None = None
    organismo_emisor: str | None = None
    fecha_publicacion: date | None = None
    organismo_padre: str | None = None
    tema: str | None = None


class VerVinculosResponse(BaseModel):
    id: int
    modo: ModoVinculo
    vinculos: list[VinculoNormaSummary] = Field(default_factory=list)
    model_config = ConfigDict(use_enum_values=True)


class TipoTexto(str, Enum):
    ACTUALIZADO = "actualizado"
    ORIGINAL = "original"
