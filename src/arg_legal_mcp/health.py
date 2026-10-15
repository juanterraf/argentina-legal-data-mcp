"""Per-source health/freshness + internal request log, backed by a small SQLite DB.

Each source records its last successful fetch, last data date, a healthy flag and an
error message. ``data_health`` reports the InfoLEG dataset (count, build date, smoke
test) plus every other source, without filesystem paths or stack traces. The
``request_log`` (tool name, duration, ok/error type; never arguments) is for internal
metrics only and is not exposed through any tool.
"""

from __future__ import annotations

import re
import sqlite3
import threading
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

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

DATASET_SOURCE = "infoleg_dataset"
REQUEST_LOG_KEEP = 20_000  # rows kept in request_log (older ones are pruned)

_PATH_RE = re.compile(r"(?:[A-Za-z]:)?(?:[\\/][\w.\-~ ]+){2,}")


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def sanitize_error(error: str | None, max_len: int = 200) -> str | None:
    """Public-safe error text: first line only, no filesystem paths, bounded length."""
    if not error:
        return None
    first = str(error).strip().splitlines()[0] if str(error).strip() else ""
    parts = re.split(r"(https?://\S+)", first)  # keep URLs, scrub filesystem paths
    first = "".join(p if p.startswith("http") else _PATH_RE.sub("<ruta>", p) for p in parts)
    return first[:max_len] or None


class HealthStore:
    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        self._writes = 0
        self._lock = threading.Lock()
        with closing(self._connect()) as conn:
            conn.executescript(_SCHEMA)
            conn.commit()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=5.0)
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
                "last_successful_fetch=COALESCE(excluded.last_successful_fetch, last_successful_fetch), "
                "last_data_date=COALESCE(excluded.last_data_date, last_data_date), "
                "is_healthy=excluded.is_healthy, "
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
        """Internal metric. ``error`` must be an error TYPE/category, never user data."""
        with closing(self._connect()) as conn:
            conn.execute(
                "INSERT INTO request_log (ts, tool, ok, ms, error) VALUES (?, ?, ?, ?, ?)",
                (_now(), tool, 1 if ok else 0, ms, (error or None) and str(error)[:80]),
            )
            with self._lock:
                self._writes += 1
                prune = self._writes % 500 == 0
            if prune:
                conn.execute(
                    "DELETE FROM request_log WHERE id <= (SELECT MAX(id) FROM request_log) - ?",
                    (REQUEST_LOG_KEEP,),
                )
            conn.commit()

    def recent_requests(self, limit: int = 20) -> list[dict]:
        """For operators (sqlite/CLI). Not exposed as a tool."""
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT ts, tool, ok, ms, error FROM request_log ORDER BY id DESC LIMIT ?",
                (max(1, min(int(limit), 500)),),
            ).fetchall()
            return [dict(r) for r in rows]


def _dataset_status(dataset) -> dict:
    estado = "down"
    count = 0
    built_at = None
    error = None
    try:
        if dataset.available():
            count = dataset.count()
            built_at = dataset.get_meta("built_at")
            try:
                dataset.search(texto="ley", limit=1)
                estado = "healthy" if count > 0 else "down"
            except Exception:  # noqa: BLE001
                estado = "degraded"
                error = "La busqueda de texto completo no responde."
        else:
            error = "Dataset offline no construido o no accesible."
    except Exception:  # noqa: BLE001
        error = "No se pudo leer el dataset offline."
    out = {
        "nombre": DATASET_SOURCE,
        "estado": estado,
        "cantidad_normas": count,
        "fecha_construccion": built_at,
    }
    if built_at:
        try:
            dt = datetime.fromisoformat(built_at)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
            out["antiguedad_dias"] = (datetime.now(UTC) - dt).days
        except ValueError:
            pass
    if error:
        out["error"] = error
    return out


def data_health(store: HealthStore, dataset) -> dict:
    """Aggregate health: the InfoLEG dataset + every other recorded source."""
    ds = _dataset_status(dataset)
    fuentes = [ds]
    for f in store.freshness():
        if f["source_name"] == DATASET_SOURCE:  # reported above, from the dataset itself
            if not f.get("is_healthy"):  # last rebuild attempt failed (CLI/timer)
                ds["ultima_reconstruccion_fallida"] = f.get("updated_at")
                ds["error_reconstruccion"] = sanitize_error(f.get("error_message"))
            continue
        fuentes.append(
            {
                "nombre": f["source_name"],
                "estado": "healthy" if f.get("is_healthy") else "degraded",
                "ultima_actualizacion": f.get("last_successful_fetch"),
                "ultimo_dato": f.get("last_data_date"),
                "error": sanitize_error(f.get("error_message")),
            }
        )
    healthy = sum(1 for f in fuentes if f["estado"] == "healthy")
    return {
        "dataset_infoleg": {
            "disponible": ds["estado"] != "down",
            "cantidad_normas": ds["cantidad_normas"],
            "fecha_construccion": ds["fecha_construccion"],
        },
        "fuentes": fuentes,
        "resumen": f"{healthy}/{len(fuentes)} fuentes healthy.",
    }
