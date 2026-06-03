"""Pluggable backend: factory + SQLite/Postgres interface conformance.

These run without a live Postgres (psycopg is imported lazily inside PgDatasetStore),
so they validate the wiring and the shared interface. A real PG integration test is
guarded by ARGMCP_PG_DSN below.
"""

from __future__ import annotations

import os

import pytest

from arg_legal_mcp.config import Settings
from arg_legal_mcp.infoleg.backend import get_dataset
from arg_legal_mcp.infoleg.dataset import DatasetStore
from arg_legal_mcp.infoleg.dataset_pg import (
    PgDatasetStore,
    _parse_date,
    _sanitize_dsn,
    _to_websearch_query,
)

_INTERFACE = {"available", "count", "get_meta", "get_norma", "resolve_id", "search",
              "vinculos_fallback"}


def test_sqlite_and_pg_share_interface():
    sqlite_methods = {m for m in dir(DatasetStore) if not m.startswith("_")}
    pg_methods = {m for m in dir(PgDatasetStore) if not m.startswith("_")}
    assert _INTERFACE <= sqlite_methods
    assert _INTERFACE <= pg_methods


def test_factory_default_is_sqlite(tmp_path):
    s = Settings(backend="sqlite", dataset_path=tmp_path / "x.sqlite")
    assert isinstance(get_dataset(s), DatasetStore)


def test_factory_postgres_requires_dsn():
    with pytest.raises(RuntimeError):
        get_dataset(Settings(backend="postgres", pg_dsn=""))


def test_factory_postgres_returns_pg_store():
    store = get_dataset(Settings(backend="postgres", pg_dsn="postgresql://u:p@h:5432/db"))
    assert isinstance(store, PgDatasetStore)
    assert "***" in store.db_path  # password redacted in the display label


def test_parse_date():
    assert _parse_date("2016-11-16") == "2016-11-16"
    assert _parse_date("16/11/2016") == "2016-11-16"
    assert _parse_date("16-11-2016") == "2016-11-16"
    assert _parse_date("") is None
    assert _parse_date("garbage") is None


def test_transport_security_settings():
    from arg_legal_mcp.server import _transport_security

    # stdio: no HTTP validation
    assert _transport_security(Settings(transport="stdio")) is None
    # reverse-proxied (no allowlist) -> protection disabled so forwarded Host is accepted
    ts = _transport_security(Settings(transport="streamable-http"))
    assert ts.enable_dns_rebinding_protection is False
    # explicit allowlist -> protection ON, includes our host + localhost wildcards
    ts2 = _transport_security(Settings(transport="streamable-http",
                                       allowed_hosts="mcp.derechointeligente.com.ar"))
    assert ts2.enable_dns_rebinding_protection is True
    assert "mcp.derechointeligente.com.ar" in ts2.allowed_hosts
    assert "127.0.0.1:*" in ts2.allowed_hosts


def test_sanitize_dsn():
    assert _sanitize_dsn("postgresql://user:secret@host:5432/db") == \
        "postgresql://user:***@host:5432/db"


def test_to_websearch_query():
    # Postgres operator parity with the SQLite FTS5 translator (fix #8).
    assert _to_websearch_query("") is None
    assert _to_websearch_query("informacion publica") == "informacion publica"
    assert _to_websearch_query("aranceles Y aduaneros") == "aranceles aduaneros"  # Y -> adjacency
    assert _to_websearch_query("aranceles O aduaneros") == "aranceles or aduaneros"
    assert _to_websearch_query("energia NO eolica") == "energia -eolica"
    assert _to_websearch_query('"transporte de carga"') == '"transporte de carga"'
    assert _to_websearch_query("-eolica") == "-eolica"


@pytest.mark.skipif(not os.environ.get("ARGMCP_PG_DSN"), reason="no live Postgres (set ARGMCP_PG_DSN)")
def test_pg_roundtrip_integration(tmp_path):
    """Build a tiny PG dataset and query it (only runs when ARGMCP_PG_DSN is set)."""
    from arg_legal_mcp.infoleg.dataset_pg import import_csv_pg
    from tests import fixtures

    dsn = os.environ["ARGMCP_PG_DSN"]
    csv_path = fixtures.write_synthetic_csv(tmp_path / "infoleg.csv")
    count = import_csv_pg(dsn, csv_path)
    assert count == 3
    store = PgDatasetStore(dsn)
    assert store.available()
    assert store.resolve_id("Ley", 27275) == 265949
    rows, total = store.search(texto="informacion", limit=5)
    assert total >= 1 and rows[0]["id_norma"] in (265949, 16986)
