"""PostgreSQL dataset backend (optional): tsvector full-text + pg_trgm + recency boost.

Mirrors the SQLite ``DatasetStore`` interface so the service layer is backend-agnostic.
``psycopg`` is an optional dependency (``pip install ".[postgres]"``); it is imported
lazily so this module can be imported (and introspected) without it installed.

Text search uses ``websearch_to_tsquery('spanish', ...)`` which natively understands
quoted phrases, ``OR`` and ``-term`` — close to the InfoLEG operator intent.
"""

from __future__ import annotations

import logging
import re
import sys
from datetime import datetime
from pathlib import Path

from .dataset import COLUMNS, _open_csv

log = logging.getLogger(__name__)


def _to_websearch_query(texto: str | None) -> str | None:
    """Translate InfoLEG-style operators into Postgres ``websearch_to_tsquery`` syntax.

    Mirrors the SQLite FTS5 translator's intent so both backends behave consistently:
    Y/AND -> adjacency (drop, websearch ANDs by default), O/OR -> ``or``, NO/NOT and
    ``-term`` -> ``-term``. Quoted phrases are preserved. (websearch has no prefix ``*``.)
    """
    texto = (texto or "").strip()
    if not texto:
        return None
    out: list[str] = []
    for m in re.finditer(r'"[^"]*"|\S+', texto):
        tok = m.group(0)
        up = tok.upper()
        if up in ("Y", "AND"):
            continue
        if up in ("O", "OR"):
            out.append("or")
            continue
        if up in ("NO", "NOT"):
            out.append("__NEG__")
            continue
        if tok.startswith('"') and tok.endswith('"') and len(tok) >= 2:
            out.append(tok)
            continue
        neg = tok.startswith("-")
        core = tok.lstrip("+-").rstrip("*")
        if not core:
            continue
        if out and out[-1] == "__NEG__":
            out[-1] = "-" + core
        else:
            out.append(("-" + core) if neg else core)
    q = " ".join(t for t in out if t != "__NEG__").strip()
    q = re.sub(r"^(or)\b", "", q).strip()
    q = re.sub(r"\b(or)$", "", q).strip()
    return q or None

_FTS_EXPR = (
    "to_tsvector('spanish', "
    "coalesce(titulo_sumario,'') || ' ' || coalesce(titulo_resumido,'') || ' ' || "
    "coalesce(texto_resumido,''))"
)

_DATE_COLS = {"fecha_sancion", "fecha_boletin"}

_SCHEMA_PG = f"""
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE TABLE IF NOT EXISTS normas (
    id_norma BIGINT PRIMARY KEY,
    {", ".join(f"{c} {'DATE' if c in _DATE_COLS else 'TEXT'}" for c in COLUMNS if c != "id_norma")},
    fts tsvector
);
CREATE INDEX IF NOT EXISTS idx_normas_fts ON normas USING GIN (fts);
CREATE INDEX IF NOT EXISTS idx_normas_org_trgm ON normas USING GIN (organismo_origen gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_normas_tipo_numero ON normas (lower(tipo_norma), numero_norma);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""

# Recency boost: newer normas float up among equally-relevant matches.
_BOOST_PG = (
    "(1.0 + 1.0 / (1.0 + GREATEST("
    "EXTRACT(EPOCH FROM (now() - COALESCE(fecha_boletin, fecha_sancion, DATE '1900-01-01')))"
    "/86400.0/365.25, 0.0)))"
)


def _parse_date(value: str | None) -> str | None:
    """Normalize a date cell to ISO 'YYYY-MM-DD', or None if unparseable."""
    if not value:
        return None
    value = value.strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(value, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _sanitize_dsn(dsn: str) -> str:
    return re.sub(r"://([^:/@]+):[^@]*@", r"://\1:***@", dsn)


class PgDatasetStore:
    def __init__(self, dsn: str):
        self.dsn = dsn
        self.db_path = _sanitize_dsn(dsn)  # for display/diagnostics

    def _connect(self):
        import psycopg
        from psycopg.rows import dict_row

        return psycopg.connect(self.dsn, row_factory=dict_row)

    def available(self) -> bool:
        try:
            return self.count() > 0
        except Exception:  # noqa: BLE001
            # count() returns 0 for an empty/not-built dataset; reaching here means a
            # real connection/auth/TLS error against the (sanitized) DSN — log it so an
            # operator isn't wrongly told to "rebuild".
            log.warning("PgDatasetStore unavailable (%s)", self.db_path, exc_info=True)
            return False

    def count(self) -> int:
        from psycopg import errors as pgerr

        try:
            with self._connect() as conn:
                row = conn.execute("SELECT count(*) AS c FROM normas").fetchone()
                return int(row["c"]) if row else 0
        except pgerr.UndefinedTable:
            return 0  # table not created yet => empty/not-built (rebuild is the right advice)

    def get_meta(self, key: str) -> str | None:
        with self._connect() as conn:
            row = conn.execute("SELECT value FROM meta WHERE key=%s", (key,)).fetchone()
            return row["value"] if row else None

    def get_norma(self, id_norma: int) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM normas WHERE id_norma=%s", (id_norma,)
            ).fetchone()
            return _stringify_dates(dict(row)) if row else None

    def resolve_id(self, tipo_nombre: str, numero: int | str) -> int | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id_norma FROM normas "
                "WHERE lower(tipo_norma)=lower(%s) AND numero_norma=%s "
                "ORDER BY fecha_boletin DESC NULLS LAST LIMIT 1",
                (str(tipo_nombre), str(numero)),
            ).fetchone()
            return int(row["id_norma"]) if row else None

    def search(
        self,
        *,
        texto: str | None = None,
        tipo_norma: str | None = None,
        numero: int | None = None,
        anio: int | None = None,
        organismo: str | None = None,
        desde: str | None = None,
        hasta: str | None = None,
        limit: int = 5,
        offset: int = 0,
    ) -> tuple[list[dict], int]:
        clauses, params = [], {}
        if tipo_norma:
            clauses.append("lower(tipo_norma)=lower(%(tipo)s)")
            params["tipo"] = tipo_norma
        if numero is not None:
            clauses.append("numero_norma=%(numero)s")
            params["numero"] = str(numero)
        if anio is not None:
            clauses.append("EXTRACT(YEAR FROM fecha_sancion)=%(anio)s")
            params["anio"] = int(anio)
        if organismo:
            clauses.append("organismo_origen ILIKE %(org)s")
            params["org"] = f"%{organismo}%"
        if desde:
            clauses.append("fecha_boletin >= %(desde)s")
            params["desde"] = desde
        if hasta:
            clauses.append("fecha_boletin <= %(hasta)s")
            params["hasta"] = hasta

        with self._connect() as conn:
            if texto:
                q = _to_websearch_query(texto)
                if not q:
                    return [], 0
                params["q"] = q
                where = "fts @@ websearch_to_tsquery('spanish', %(q)s)"
                if clauses:
                    where += " AND " + " AND ".join(clauses)
                total = conn.execute(
                    f"SELECT count(*) AS c FROM normas WHERE {where}", params
                ).fetchone()["c"]
                params["limit"] = limit
                params["offset"] = offset
                rows = conn.execute(
                    f"SELECT * FROM normas WHERE {where} "
                    f"ORDER BY ts_rank(fts, websearch_to_tsquery('spanish', %(q)s)) * {_BOOST_PG} DESC "
                    "LIMIT %(limit)s OFFSET %(offset)s",
                    params,
                ).fetchall()
            else:
                where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
                total = conn.execute(
                    f"SELECT count(*) AS c FROM normas{where}", params
                ).fetchone()["c"]
                params["limit"] = limit
                params["offset"] = offset
                rows = conn.execute(
                    f"SELECT * FROM normas{where} ORDER BY fecha_boletin DESC NULLS LAST "
                    "LIMIT %(limit)s OFFSET %(offset)s",
                    params,
                ).fetchall()
        return [_stringify_dates(dict(r)) for r in rows], int(total)

    def vinculos_fallback(self, id_norma: int, modo: int) -> list[dict]:
        norma = self.get_norma(id_norma)
        if not norma:
            return []
        col = "modifica_a" if modo == 1 else "modificada_por"
        raw = (norma.get(col) or "").strip()
        if not raw:
            return []
        ids = [int(x) for x in re.findall(r"\b(\d{3,7})\b", raw)]
        return [{"id": i, "raw": raw} for i in ids] or [{"id": None, "raw": raw}]


def _stringify_dates(row: dict) -> dict:
    """Render DATE columns as ISO strings and drop the internal tsvector."""
    row.pop("fts", None)
    for c in _DATE_COLS:
        v = row.get(c)
        if v is not None and not isinstance(v, str):
            row[c] = v.isoformat()
    return row


# ── ETL ───────────────────────────────────────────────────────────────────────
def import_csv_pg(dsn: str, csv_path: str | Path, batch_size: int = 1000) -> int:
    import psycopg

    with psycopg.connect(dsn) as conn:
        conn.execute(_SCHEMA_PG)
        conn.execute("TRUNCATE normas")
        cols = COLUMNS
        placeholders = ",".join(["%s"] * len(cols))
        insert = f"INSERT INTO normas ({','.join(cols)}) VALUES ({placeholders}) ON CONFLICT (id_norma) DO NOTHING"

        f, delimiter = _open_csv(csv_path)
        imported = 0
        try:
            import csv as _csv

            reader = _csv.DictReader(f, delimiter=delimiter)
            batch = []
            with conn.cursor() as cur:
                for record in reader:
                    raw_id = (record.get("id_norma") or "").strip()
                    if not raw_id.isdigit():
                        continue
                    row = []
                    for c in cols:
                        if c == "id_norma":
                            row.append(int(raw_id))
                        elif c in _DATE_COLS:
                            row.append(_parse_date(record.get(c)))
                        else:
                            row.append(record.get(c) or None)
                    batch.append(row)
                    if len(batch) >= batch_size:
                        cur.executemany(insert, batch)
                        imported += len(batch)
                        batch = []
                if batch:
                    cur.executemany(insert, batch)
                    imported += len(batch)
        finally:
            f.close()

        conn.execute(f"UPDATE normas SET fts = {_FTS_EXPR}")
        built_at = datetime.now().astimezone().isoformat(timespec="seconds")
        conn.execute(
            "INSERT INTO meta(key, value) VALUES ('row_count', %s), ('built_at', %s) "
            "ON CONFLICT (key) DO UPDATE SET value=excluded.value",
            (str(imported), built_at),
        )
        conn.commit()
    return imported


def download_and_build_pg(
    dsn: str, *, zip_url: str | None = None, user_agent: str = "argentina-legal-data-mcp/0.1",
    tmp_dir: str | Path | None = None,
) -> int:
    """Download the official ZIP and (re)build the Postgres dataset."""
    import tempfile
    import zipfile

    import httpx

    from .dataset import ZIP_URL

    zip_url = zip_url or ZIP_URL
    tmp = Path(tmp_dir or tempfile.gettempdir())
    tmp.mkdir(parents=True, exist_ok=True)
    zip_path = tmp / "infoleg-dump.zip"
    print(f"Downloading {zip_url} ...", file=sys.stderr)
    with httpx.Client(timeout=600, headers={"User-Agent": user_agent}, follow_redirects=True) as c:
        with c.stream("GET", zip_url) as resp:
            resp.raise_for_status()
            with open(zip_path, "wb") as fh:
                for chunk in resp.iter_bytes(chunk_size=1 << 20):
                    fh.write(chunk)
    extract_dir = tmp / "infoleg-dump"
    extract_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(extract_dir)
    csvs = sorted(extract_dir.rglob("*.csv"), key=lambda p: p.stat().st_size, reverse=True)
    if not csvs:
        raise FileNotFoundError("No CSV found inside the InfoLEG ZIP")
    print(f"Importing {csvs[0].name} into Postgres ...", file=sys.stderr)
    return import_csv_pg(dsn, csvs[0])
