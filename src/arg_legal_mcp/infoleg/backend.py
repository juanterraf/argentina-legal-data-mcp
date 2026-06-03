"""Pluggable dataset backend selection.

Default is SQLite + FTS5 (zero config). Setting ``ARGMCP_BACKEND=postgres`` (with
``ARGMCP_PG_DSN``) swaps in a PostgreSQL store (tsvector + pg_trgm) that exposes the
same interface — so the service layer is backend-agnostic.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from .dataset import DatasetStore


@runtime_checkable
class DatasetBackend(Protocol):
    """The read interface the service layer depends on (SQLite and Postgres both satisfy it)."""

    db_path: str

    def available(self) -> bool: ...
    def count(self) -> int: ...
    def get_meta(self, key: str) -> str | None: ...
    def get_norma(self, id_norma: int) -> dict | None: ...
    def resolve_id(self, tipo_nombre: str, numero: int | str) -> int | None: ...
    def search(self, **kwargs) -> tuple[list[dict], int]: ...
    def vinculos_fallback(self, id_norma: int, modo: int) -> list[dict]: ...


def get_dataset(settings) -> DatasetBackend:
    if settings.backend == "postgres":
        if not settings.pg_dsn:
            raise RuntimeError("ARGMCP_BACKEND=postgres requires ARGMCP_PG_DSN to be set.")
        from .dataset_pg import PgDatasetStore  # lazy: psycopg is an optional dependency

        return PgDatasetStore(settings.pg_dsn)
    return DatasetStore(settings.dataset_path)
