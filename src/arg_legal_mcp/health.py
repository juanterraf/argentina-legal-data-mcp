"""Per-source health/freshness + request log, backed by a small SQLite DB.

Mirrors the abenassi ``data_freshness`` idea: each source records its last successful
fetch, last data date, a healthy flag and an error message. ``data_health`` adds row
counts and a smoke test. Also keeps a lightweight ``request_log`` (HTTP transport).
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from .infoleg.dataset import DatasetStore

_SCHEMA = """
CREATE TABLE IF NOT EXISTS data_freshness (
    source_name TEXT PRIMARY KEY,
    last_successful_fetch TEXT,
    last_data_date TEXT,
    is_healthy INTEGER DEFAULT 1,
    error_message TEXT,
    updated_at TEXT
);
CREATE TABLE IF NOT EXISTS request_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT,
    tool TEXT,
    ok INTEGER,
    ms INTEGER,
    error TEXT
);
"""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class HealthStore:
    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        with closing(self._connect()) as conn:
            conn.executescript(_SCHEMA)
            conn.commit()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def record_freshness(
        self,
        source: str,
        *,
        healthy: bool,
        last_data_date: str | None = None,
        error: str | None = None,
    ) -> None:
        with closing(self._connect()) as conn:
            conn.execute(
                "INSERT INTO data_freshness "
                "(source_name, last_successful_fetch, last_data_date, is_healthy, error_message, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(source_name) DO UPDATE SET "
                "last_successful_fetch=excluded.last_successful_fetch, "
                "last_data_date=excluded.last_data_date, is_healthy=excluded.is_healthy, "
                "error_message=excluded.error_message, updated_at=excluded.updated_at",
                (
                    source,
                    _now() if healthy else None,
                    last_data_date,
                    1 if healthy else 0,
                    error,
                    _now(),
                ),
            )
            conn.commit()

    def freshness(self) -> list[dict]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT * FROM data_freshness ORDER BY source_name"
            ).fetchall()
            return [dict(r) for r in rows]

    def log_request(self, tool: str, ok: bool, ms: int, error: str | None = None) -> None:
        with closing(self._connect()) as conn:
            conn.execute(
                "INSERT INTO request_log (ts, tool, ok, ms, error) VALUES (?, ?, ?, ?, ?)",
                (_now(), tool, 1 if ok else 0, ms, error),
            )
            conn.commit()

    def recent_requests(self, limit: int = 20) -> list[dict]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT ts, tool, ok, ms, error FROM request_log ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]


def data_health(store: HealthStore, dataset: DatasetStore) -> dict:
    """Aggregate health: dataset availability + smoke test + freshness rows."""
    fuentes = []

    # InfoLEG dataset source with an FTS smoke test.
    available = dataset.available()
    count = dataset.count() if available else 0
    estado = "down"
    smoke_error = None
    if available and count > 0:
        try:
            dataset.search(texto="ley", limit=1)
            estado = "healthy"
        except Exception as exc:  # noqa: BLE001
            estado = "degraded"
            smoke_error = f"FTS smoke test failed: {exc}"
    fuentes.append(
        {
            "nombre": "infoleg_dataset",
            "estado": estado,
            "registros": count,
            "error": smoke_error,
        }
    )

    freshness_by_source = {f["source_name"]: f for f in store.freshness()}
    for name, f in freshness_by_source.items():
        fuentes.append(
            {
                "nombre": name,
                "estado": "healthy" if f.get("is_healthy") else "degraded",
                "ultima_actualizacion": f.get("last_successful_fetch"),
                "ultimo_dato": f.get("last_data_date"),
                "error": f.get("error_message"),
            }
        )

    healthy = sum(1 for f in fuentes if f["estado"] == "healthy")
    resumen = f"{healthy}/{len(fuentes)} fuentes healthy."
    return {"fuentes": fuentes, "resumen": resumen}
