"""Offline InfoLEG dataset: SQLite + FTS5 built from the official datos.jus.gob.ar ZIP.

This is the robust, offline backbone of search and the fallback for text retrieval
when the live site is down or changes. Highlights:

  * FTS5 with ``unicode61 remove_diacritics 2`` (accent-insensitive search).
  * Recency-boosted ranking: ``relevance * (1 + 1/(1 + age_in_years))`` over bm25.
  * ``resolve_id`` maps "Ley 27275" -> id_norma (e.g. 265949).
  * Stores ``texto_original``/``texto_actualizado`` so text can be served offline.

The schema column names are the official dataset column names (a documented fact).
"""

from __future__ import annotations

import csv
import sqlite3
import sys
from collections.abc import Iterable
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

# Official dataset column order (datos.jus.gob.ar "Base InfoLeg - Normativa Nacional").
COLUMNS = [
    "id_norma", "tipo_norma", "numero_norma", "clase_norma", "organismo_origen",
    "fecha_sancion", "numero_boletin", "fecha_boletin", "pagina_boletin",
    "titulo_resumido", "titulo_sumario", "texto_resumido", "observaciones",
    "texto_original", "texto_actualizado", "modificada_por", "modifica_a",
]

ZIP_URL = (
    "http://datos.jus.gob.ar/dataset/d9a963ea-8b1d-4ca3-9dd9-07a4773e8c23/"
    "resource/bf0ec116-ad4e-4572-a476-e57167a84403/download/"
    "base-infoleg-normativa-nacional.zip"
)

_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS normas (
    {", ".join(f"{c} TEXT" for c in COLUMNS if c != "id_norma")},
    id_norma INTEGER PRIMARY KEY
);
CREATE INDEX IF NOT EXISTS idx_normas_tipo_numero ON normas(tipo_norma, numero_norma);
CREATE INDEX IF NOT EXISTS idx_normas_fecha_bol ON normas(fecha_boletin);
CREATE VIRTUAL TABLE IF NOT EXISTS normas_fts USING fts5(
    titulo_sumario, titulo_resumido, texto_resumido,
    content='normas', content_rowid='id_norma',
    tokenize='unicode61 remove_diacritics 2'
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""


def connect(db_path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(_SCHEMA)


# ── FTS5 query translation ────────────────────────────────────────────────────
def to_fts5_query(texto: str | None) -> str | None:
    """Translate an InfoLEG-style query into a safe FTS5 MATCH expression.

    Maps Y/AND, O/OR, NO/NOT; keeps "phrases"; supports trailing ``*`` prefixes;
    ``-term`` becomes ``NOT term``. Anything unsafe is stripped. ``?`` (single-char
    wildcard) is unsupported by FTS5 and dropped.
    """
    import re

    texto = (texto or "").strip()
    if not texto:
        return None
    out: list[str] = []
    for m in re.finditer(r'"[^"]*"|\S+', texto):
        tok = m.group(0)
        up = tok.upper()
        if up in ("AND", "Y"):
            out.append("AND")
            continue
        if up in ("OR", "O"):
            out.append("OR")
            continue
        if up in ("NOT", "NO"):
            out.append("NOT")
            continue
        if tok.startswith('"') and tok.endswith('"') and len(tok) >= 2:
            inner = re.sub(r'["]', "", tok)
            if inner.strip():
                out.append(f'"{inner.strip()}"')
            continue
        neg = tok.startswith("-")
        tok = tok.lstrip("+-").replace("?", "")
        prefix = tok.endswith("*")
        core = re.sub(r"[^0-9A-Za-zÁÉÍÓÚÜÑáéíóúüñ]", "", tok.rstrip("*"))
        if not core:
            continue
        term = (core + "*") if prefix else f'"{core}"'
        if neg:
            out.append("NOT")
        out.append(term)
    q = " ".join(out).strip()
    # Drop a dangling leading binary operator (AND/OR) — invalid at the start.
    # A leading NOT is kept (it expresses an exclusion intent); if FTS5 rejects it,
    # DatasetStore.search falls back to a plain phrase query.
    q = re.sub(r"^(AND|OR)\b", "", q).strip()
    q = re.sub(r"\b(AND|OR|NOT)$", "", q).strip()
    return q or None


# Recency-boosted ordering expression (newer normas float up among equal matches).
_BOOST = (
    "(1.0 + 1.0 / (1.0 + COALESCE("
    "(julianday('now') - julianday(COALESCE(fecha_boletin, fecha_sancion))) / 365.25"
    ", 1000.0)))"
)


class DatasetStore:
    """Read access to the offline dataset."""

    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)

    def available(self) -> bool:
        if not Path(self.db_path).exists():
            return False
        try:
            return self.count() > 0
        except sqlite3.Error:
            return False

    def count(self) -> int:
        with closing(connect(self.db_path)) as conn:
            try:
                return conn.execute("SELECT COUNT(*) FROM normas").fetchone()[0]
            except sqlite3.OperationalError:
                return 0

    def get_meta(self, key: str) -> str | None:
        with closing(connect(self.db_path)) as conn:
            row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
            return row[0] if row else None

    def get_norma(self, id_norma: int) -> dict | None:
        try:
            with closing(connect(self.db_path)) as conn:
                row = conn.execute(
                    "SELECT * FROM normas WHERE id_norma=?", (id_norma,)
                ).fetchone()
                return dict(row) if row else None
        except sqlite3.OperationalError:  # table not built yet
            return None

    def resolve_id(self, tipo_nombre: str, numero: int | str) -> int | None:
        """Resolve e.g. ("Ley", 27275) -> id_norma. Returns the most recent match."""
        try:
            with closing(connect(self.db_path)) as conn:
                row = conn.execute(
                    "SELECT id_norma FROM normas "
                    "WHERE lower(tipo_norma)=lower(?) AND numero_norma=? "
                    "ORDER BY fecha_boletin DESC LIMIT 1",
                    (str(tipo_nombre), str(numero)),
                ).fetchone()
                return int(row[0]) if row else None
        except sqlite3.OperationalError:
            return None

    def search(
        self,
        *,
        texto: str | None = None,
        tipo_norma: str | None = None,
        numero: int | None = None,
        anio: int | None = None,
        organismo: str | None = None,
        desde: str | None = None,  # ISO date
        hasta: str | None = None,  # ISO date
        limit: int = 5,
        offset: int = 0,
    ) -> tuple[list[dict], int]:
        with closing(connect(self.db_path)) as conn:
            if texto:
                return self._search_fts(conn, texto, tipo_norma, numero, anio,
                                        organismo, desde, hasta, limit, offset)
            return self._search_filters(conn, tipo_norma, numero, anio,
                                        organismo, desde, hasta, limit, offset)

    def _filters(self, tipo_norma, numero, anio, organismo, desde, hasta):
        clauses, params = [], []
        if tipo_norma:
            clauses.append("lower(n.tipo_norma)=lower(?)")
            params.append(tipo_norma)
        if numero is not None:
            clauses.append("n.numero_norma=?")
            params.append(str(numero))
        if anio is not None:
            clauses.append("substr(n.fecha_sancion,1,4)=?")
            params.append(str(anio))
        if organismo:
            clauses.append("n.organismo_origen LIKE ?")
            params.append(f"%{organismo}%")
        if desde:
            clauses.append("n.fecha_boletin >= ?")
            params.append(desde)
        if hasta:
            clauses.append("n.fecha_boletin <= ?")
            params.append(hasta)
        return clauses, params

    def _search_fts(self, conn, texto, tipo_norma, numero, anio, organismo,
                    desde, hasta, limit, offset):
        q = to_fts5_query(texto)
        if not q:
            return [], 0
        clauses, params = self._filters(tipo_norma, numero, anio, organismo, desde, hasta)
        where_extra = (" AND " + " AND ".join(clauses)) if clauses else ""

        base = (
            "FROM normas_fts f JOIN normas n ON n.id_norma = f.rowid "
            "WHERE normas_fts MATCH ?" + where_extra
        )
        try:
            total = conn.execute(f"SELECT COUNT(*) {base}", [q, *params]).fetchone()[0]
            rows = conn.execute(
                f"SELECT n.* {base} "
                f"ORDER BY (-bm25(normas_fts)) * {_BOOST} DESC "
                "LIMIT ? OFFSET ?",
                [q, *params, limit, offset],
            ).fetchall()
        except sqlite3.OperationalError:
            # Fall back to a plain phrase match if the translated query is rejected.
            safe = '"' + texto.replace('"', " ").strip() + '"'
            total = conn.execute(f"SELECT COUNT(*) {base}", [safe, *params]).fetchone()[0]
            rows = conn.execute(
                f"SELECT n.* {base} ORDER BY (-bm25(normas_fts)) * {_BOOST} DESC "
                "LIMIT ? OFFSET ?",
                [safe, *params, limit, offset],
            ).fetchall()
        return [dict(r) for r in rows], total

    def _search_filters(self, conn, tipo_norma, numero, anio, organismo,
                        desde, hasta, limit, offset):
        clauses, params = self._filters(tipo_norma, numero, anio, organismo, desde, hasta)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        total = conn.execute(f"SELECT COUNT(*) FROM normas n{where}", params).fetchone()[0]
        rows = conn.execute(
            f"SELECT n.* FROM normas n{where} ORDER BY n.fecha_boletin DESC "
            "LIMIT ? OFFSET ?",
            [*params, limit, offset],
        ).fetchall()
        return [dict(r) for r in rows], total

    def vinculos_fallback(self, id_norma: int, modo: int) -> list[dict]:
        """Best-effort vinculos from the dataset's ``modifica_a`` / ``modificada_por``.

        These columns are free text; we extract any numeric ids and keep the raw text.
        """
        import re

        norma = self.get_norma(id_norma)
        if not norma:
            return []
        col = "modifica_a" if modo == 1 else "modificada_por"
        raw = (norma.get(col) or "").strip()
        if not raw:
            return []
        ids = [int(x) for x in re.findall(r"\b(\d{3,7})\b", raw)]
        return [{"id": i, "raw": raw} for i in ids] or [{"id": None, "raw": raw}]


# ── Builders (ETL) ────────────────────────────────────────────────────────────
def _open_csv(csv_path: str | Path):
    for enc in ("utf-8-sig", "latin-1"):
        try:
            f = open(csv_path, encoding=enc, newline="")
            sample = f.read(8192)
            f.seek(0)
            try:
                dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
                delimiter = dialect.delimiter
            except csv.Error:
                delimiter = ","
            return f, delimiter
        except UnicodeDecodeError:
            continue
    raise UnicodeError(f"Could not decode {csv_path} as utf-8 or latin-1")


def import_csv(conn: sqlite3.Connection, csv_path: str | Path, batch_size: int = 1000) -> int:
    """Import an InfoLEG CSV into the ``normas`` table (TRUNCATE + batched insert)."""
    create_schema(conn)
    conn.execute("DELETE FROM normas")
    placeholders = ",".join("?" for _ in COLUMNS)
    insert = f"INSERT OR REPLACE INTO normas ({','.join(COLUMNS)}) VALUES ({placeholders})"

    f, delimiter = _open_csv(csv_path)
    imported = 0
    try:
        reader = csv.DictReader(f, delimiter=delimiter)
        batch: list[list] = []
        for record in reader:
            raw_id = (record.get("id_norma") or "").strip()
            if not raw_id.isdigit():
                continue
            # Build values in COLUMNS order; normalize empty strings to NULL.
            ordered = [int(raw_id) if c == "id_norma" else (record.get(c) or None) for c in COLUMNS]
            batch.append(ordered)
            if len(batch) >= batch_size:
                conn.executemany(insert, batch)
                imported += len(batch)
                batch = []
        if batch:
            conn.executemany(insert, batch)
            imported += len(batch)
        conn.commit()
    finally:
        f.close()

    # (Re)build the FTS index from the content table.
    conn.execute("INSERT INTO normas_fts(normas_fts) VALUES('rebuild')")
    # Distinct count (the CSV can repeat id_norma; INSERT OR REPLACE dedupes by PK).
    distinct = conn.execute("SELECT COUNT(*) FROM normas").fetchone()[0]
    built_at = datetime.now(UTC).isoformat(timespec="seconds")
    conn.executemany(
        "INSERT OR REPLACE INTO meta(key, value) VALUES(?, ?)",
        [("row_count", str(distinct)), ("rows_read", str(imported)), ("built_at", built_at)],
    )
    conn.commit()
    return distinct


def build_dataset_from_csv(csv_path: str | Path, db_path: str | Path) -> int:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    with closing(connect(db_path)) as conn:
        return import_csv(conn, csv_path)


def _download_zip(zip_url: str, zip_path: Path, *, user_agent: str) -> None:
    """Stream a (large) ZIP to disk, printing progress so it never looks frozen.

    Uses per-operation timeouts (not a single total cap) so a multi-hundred-MB
    download can take as long as it needs, as long as bytes keep arriving.
    """
    import httpx

    timeout = httpx.Timeout(connect=30.0, read=120.0, write=120.0, pool=30.0)
    print(f"Downloading {zip_url} ...", file=sys.stderr, flush=True)
    with httpx.Client(timeout=timeout, headers={"User-Agent": user_agent},
                      follow_redirects=True) as client:
        with client.stream("GET", zip_url) as resp:
            resp.raise_for_status()
            total = int(resp.headers.get("content-length") or 0)
            if total:
                print(f"  total: {total / 1e6:.0f} MB", file=sys.stderr, flush=True)
            done = last = 0
            with open(zip_path, "wb") as fh:
                for chunk in resp.iter_bytes(chunk_size=1 << 20):
                    fh.write(chunk)
                    done += len(chunk)
                    if done - last >= 20 * (1 << 20):  # progress every ~20 MB
                        last = done
                        pct = f" ({100 * done / total:.0f}%)" if total else ""
                        print(f"  ... {done / 1e6:.0f} MB{pct}", file=sys.stderr, flush=True)
    print(f"Downloaded {zip_path.stat().st_size / 1e6:.1f} MB", file=sys.stderr, flush=True)


def download_and_build(
    db_path: str | Path,
    *,
    zip_url: str = ZIP_URL,
    user_agent: str = "argentina-legal-data-mcp/0.1",
    tmp_dir: str | Path | None = None,
) -> int:
    """Download the official ZIP, extract the CSV, and (re)build the SQLite dataset."""
    import tempfile
    import zipfile

    tmp = Path(tmp_dir or tempfile.gettempdir())
    tmp.mkdir(parents=True, exist_ok=True)
    zip_path = tmp / "infoleg-dump.zip"

    _download_zip(zip_url, zip_path, user_agent=user_agent)

    extract_dir = tmp / "infoleg-dump"
    extract_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(extract_dir)
    csvs = sorted(extract_dir.rglob("*.csv"), key=lambda p: p.stat().st_size, reverse=True)
    if not csvs:
        raise FileNotFoundError("No CSV found inside the InfoLEG ZIP")
    csv_path = csvs[0]
    print(f"Importing {csv_path.name} ...", file=sys.stderr)
    count = build_dataset_from_csv(csv_path, db_path)
    print(f"Imported {count} normas into {db_path}", file=sys.stderr)
    return count


def iter_rows_for_test(rows: Iterable[dict]) -> list[list]:
    """Helper used by tests to shape dict rows into COLUMNS order."""
    return [[r.get(c) for c in COLUMNS] for r in rows]
