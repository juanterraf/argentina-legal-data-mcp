"""Offline InfoLEG dataset: SQLite + FTS5 built from the official datos.jus.gob.ar ZIP.

This is the robust, offline backbone of search and the fallback for text retrieval
when the live site is down or changes. Highlights:

  * FTS5 with ``unicode61 remove_diacritics 2`` (accent-insensitive search).
  * Relevance ranking (default): weighted bm25 (title columns weigh more) combined with
    boosts for *base* norms (how many norms modify it: a heavily modified law is a base
    law), norm type (Ley > Decreto/Ley > Decreto), a title match, and a mild recency
    tie-break. ``orden="recientes"`` sorts by date instead.
  * Direct hits: "Ley 20628", "Decreto 70/2023", "DNU 70/2023" go first.
  * The official columns ``modificada_por`` / ``modifica_a`` are COUNTS (not ids).
  * ``texto_original`` / ``texto_actualizado`` hold the official annex URL (sometimes text).
  * Rebuilds are atomic (temp file + ``os.replace``) and serialized by a lock file;
    readers open a fresh connection per call, so a running server picks up a new file
    without restarting.
"""

from __future__ import annotations

import csv
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import unicodedata
import zipfile
from collections.abc import Iterable, Iterator
from contextlib import closing, contextmanager
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
    "https://datos.jus.gob.ar/dataset/d9a963ea-8b1d-4ca3-9dd9-07a4773e8c23/"
    "resource/bf0ec116-ad4e-4572-a476-e57167a84403/download/"
    "base-infoleg-normativa-nacional.zip"
)

# Safety limits for the (untrusted) download. The real ZIP is ~49 MB.
MAX_ZIP_BYTES = 500 * 1024 * 1024
MAX_CSV_BYTES = 3 * 1024 * 1024 * 1024
MAX_COMPRESSION_RATIO = 100
# Refuse to replace a good dataset with one that lost more than half of its rows.
MIN_ROWS_RATIO = 0.5

ORDENES = ("relevancia", "recientes")

_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS normas (
    {", ".join(f"{c} TEXT" for c in COLUMNS if c != "id_norma")},
    id_norma INTEGER PRIMARY KEY
);
CREATE INDEX IF NOT EXISTS idx_normas_tipo_numero ON normas(tipo_norma, numero_norma);
CREATE INDEX IF NOT EXISTS idx_normas_fecha_bol ON normas(fecha_boletin);
CREATE INDEX IF NOT EXISTS idx_normas_fecha_pub ON normas(COALESCE(fecha_boletin, fecha_sancion));
CREATE VIRTUAL TABLE IF NOT EXISTS normas_fts USING fts5(
    titulo_sumario, titulo_resumido, texto_resumido,
    content='normas', content_rowid='id_norma',
    tokenize='unicode61 remove_diacritics 2'
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""


class DatasetBuildError(RuntimeError):
    """The rebuild failed; the previous dataset (if any) was left untouched."""


class DatasetBuildLockedError(DatasetBuildError):
    """Another rebuild is already running."""


def connect(db_path: str | Path) -> sqlite3.Connection:
    """Reader connection. Never changes the journal mode (the file is read-only for us)."""
    conn = sqlite3.connect(str(db_path), timeout=5.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA query_only=1")
    return conn


def create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(_SCHEMA)


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFKD", (text or "").lower())
    return text.encode("ascii", "ignore").decode("ascii")


# ── FTS5 query translation ────────────────────────────────────────────────────
_STOPWORDS = {
    "a", "al", "ante", "bajo", "con", "contra", "de", "del", "desde", "e", "el", "en",
    "entre", "hacia", "hasta", "la", "las", "lo", "los", "para", "por", "que", "se",
    "segun", "sin", "sobre", "su", "sus", "tras", "u", "un", "una", "unas", "unos",
}


def _variantes(core: str) -> list[str]:
    """Singular/plural variants of a bare Spanish word (cheap, no stemmer)."""
    low = core.lower()
    if low.isdigit() or len(low) <= 2:
        return [low]
    out = [low]
    if len(low) > 4 and low.endswith("es"):
        out += [low[:-2], low[:-1]]
    elif len(low) > 3 and low.endswith("s"):
        out.append(low[:-1])
    elif _norm(low[-1]) in "aeiou":
        out.append(low + "s")
    else:
        out.append(low + "es")
    seen: list[str] = []
    for v in out:
        if v and v not in seen:
            seen.append(v)
    return seen


def to_fts5_query(texto: str | None, *, expand: bool = False) -> str | None:
    """Translate an InfoLEG-style query into a safe FTS5 MATCH expression.

    Maps Y/AND, O/OR, NO/NOT; keeps "phrases"; supports trailing ``*`` prefixes;
    ``-term`` becomes ``NOT term``. Anything unsafe is stripped. ``?`` (single-char
    wildcard) is unsupported by FTS5 and dropped.

    With ``expand=True`` (used by search): Spanish stopwords are dropped (unless the
    query is only stopwords) and each bare word matches its singular/plural variants.
    """
    texto = (texto or "").strip()
    if not texto:
        return None
    items: list[tuple[str, str]] = []  # (kind, value): kind in {"op", "term"}
    for m in re.finditer(r'"[^"]*"|\S+', texto):
        tok = m.group(0)
        up = tok.upper()
        if up in ("AND", "Y"):
            items.append(("op", "AND"))
            continue
        if up in ("OR", "O"):
            items.append(("op", "OR"))
            continue
        if up in ("NOT", "NO"):
            items.append(("op", "NOT"))
            continue
        if tok.startswith('"') and tok.endswith('"') and len(tok) >= 2:
            inner = re.sub(r'["]', "", tok).strip()
            if inner:
                items.append(("term", f'"{inner}"'))
            continue
        neg = tok.startswith("-")
        tok = tok.lstrip("+-").replace("?", "")
        prefix = tok.endswith("*")
        core = re.sub(r"[^0-9A-Za-zÁÉÍÓÚÜÑáéíóúüñ]", "", tok.rstrip("*"))
        if not core:
            continue
        if neg:
            items.append(("op", "NOT"))
        if prefix:
            items.append(("term", core + "*"))
        elif expand:
            items.append(("word", core))
        else:
            items.append(("term", f'"{core}"'))

    if expand:
        words = [v for k, v in items if k == "word"]
        only_stop = words and all(_norm(w) in _STOPWORDS for w in words)
        expanded: list[tuple[str, str]] = []
        for kind, value in items:
            if kind != "word":
                expanded.append((kind, value))
                continue
            if not only_stop and _norm(value) in _STOPWORDS:
                # drop the stopword together with a dangling NOT in front of it
                if expanded and expanded[-1] == ("op", "NOT"):
                    expanded.pop()
                continue
            vs = _variantes(value)
            term = f'"{vs[0]}"' if len(vs) == 1 else "(" + " OR ".join(f'"{v}"' for v in vs) + ")"
            expanded.append(("term", term))
        items = expanded

    # Normalize operators: no leading AND/OR, no trailing operator, no doubled operators,
    # explicit AND between adjacent operands (required once groups use parentheses).
    out: list[str] = []
    negated: list[str] = []  # expand mode: leading exclusions, moved to the end
    prev_kind = None
    pending_not = False
    for kind, value in items:
        if kind == "op":
            if prev_kind in (None, "op"):
                if value == "NOT" and prev_kind is None:
                    if expand:
                        pending_not = True  # FTS5 NOT is binary: defer "-x" to the end
                    else:
                        # Keep a leading NOT (exclusion intent); search() falls back.
                        out.append(value)
                        prev_kind = "op"
                continue
            out.append(value)
            prev_kind = "op"
        else:
            if pending_not:
                negated.append(value)
                pending_not = False
                continue
            if prev_kind == "term" and expand:
                out.append("AND")
            out.append(value)
            prev_kind = "term"
    while out and out[-1] in ("AND", "OR", "NOT"):
        out.pop()
    if expand and negated:
        if not out:
            return None  # only exclusions: nothing to search for
        for term in negated:
            out += ["NOT", term]
    q = " ".join(out).strip()
    return q or None


# ── Direct references: "Ley 20628", "Decreto 70/2023", "DNU 70/2023" ─────────────
_TIPO_ALIASES = [
    (r"decreto[\s/-]*ley", "Decreto/Ley"),
    (r"decision(?:es)? administrativas?", "Decisión Administrativa"),
    (r"leyes|ley", "Ley"),
    (r"decretos?|dnu|dec\.?", "Decreto"),
    (r"resoluci(?:on|ones)|res\.?", "Resolución"),
    (r"disposici(?:on|ones)|disp\.?", "Disposición"),
    (r"comunicaci(?:on|ones)", "Comunicación"),
    (r"acordadas?", "Acordada"),
]
_REF_RE = re.compile(
    r"\b(" + "|".join(a for a, _ in _TIPO_ALIASES) + r")\s*"
    r"(?:n(?:ro|o|um|umero)?\s*[.:°º]?\s*)?"
    r"(\d{1,3}(?:\.\d{3})+|\d{1,6})(?:\s*/\s*(\d{4}|\d{2}))?\b",
    re.IGNORECASE,
)


def parse_referencias(texto: str | None) -> tuple[list[tuple[str, str, int | None]], str]:
    """Find "tipo + numero[/anio]" references. Returns (refs, remaining_text)."""
    raw = texto or ""
    normalized = _norm(raw)
    if len(normalized) != len(raw):  # keep offsets aligned with the original string
        normalized = raw.lower()
    refs: list[tuple[str, str, int | None]] = []
    spans: list[tuple[int, int]] = []
    for m in _REF_RE.finditer(normalized):
        alias = m.group(1).lower()
        tipo = next((t for pat, t in _TIPO_ALIASES if re.fullmatch(pat, alias)), None)
        if tipo is None:
            continue
        numero = m.group(2).replace(".", "")
        anio = None
        if m.group(3):
            y = int(m.group(3))
            if y < 100:
                y += 2000 if y <= datetime.now().year % 100 else 1900
            anio = y
        refs.append((tipo, numero, anio))
        spans.append(m.span())
    if not refs and re.search(r"(?:^|\W)ley(?:es)?(?:\W|$)", normalized):
        # "ley de contrato de trabajo 20744": a bare number next to the word "ley".
        for m in re.finditer(r"(?<![\d./-])(\d{1,3}(?:\.\d{3})+|\d{3,6})(?![\d./-])", normalized):
            refs.append(("Ley", m.group(1).replace(".", ""), None))
            spans.append(m.span())
    rest = raw
    for a, b in reversed(spans):
        rest = rest[:a] + " " + rest[b:]
    return refs, re.sub(r"\s+", " ", rest).strip()


# ── Materia tributaria (shared with the Postgres backend) ─────────────────────
TRIBUTARIA_ORGANISMOS = [
    "%INGRESOS PUBLICOS%", "%IMPOSITIV%", "%ADUAN%", "%RECAUDACION%", "%AFIP%",
    "%TRIBUNAL FISCAL%",
]
TRIBUTARIA_TITULOS = [
    "%IMPUEST%", "%TRIBUT%", "%IMPOSITIV%", "%VALOR AGREGADO%", "%I.V.A%",
    "%GANANCIAS%", "%ADUANER%", "%DERECHOS DE EXPORTACION%", "%DERECHOS DE IMPORTACION%",
    "%PROCEDIMIENTO FISCAL%", "%BIENES PERSONALES%", "%CONTRIBUCIONES PATRONALES%",
]


def tributaria_clause(like: str = "LIKE", ph: str = "?") -> tuple[str, list[str]]:
    """SQL predicate (alias ``n``) selecting tax-matter norms + its params."""
    org = [f"n.organismo_origen {like} {ph}" for _ in TRIBUTARIA_ORGANISMOS]
    tit = [f"n.titulo_sumario {like} {ph}" for _ in TRIBUTARIA_TITULOS]
    tit += [f"n.titulo_resumido {like} {ph}" for _ in TRIBUTARIA_TITULOS]
    sql = "(" + " OR ".join(org + tit) + ")"
    return sql, [*TRIBUTARIA_ORGANISMOS, *TRIBUTARIA_TITULOS, *TRIBUTARIA_TITULOS]


# ── Ranking ───────────────────────────────────────────────────────────────────
_FECHA_PUB = "COALESCE(n.fecha_boletin, n.fecha_sancion)"
_REL = "(-bm25(normas_fts, 5.0, 2.0, 1.0))"
_MODS = "COALESCE(CAST(n.modificada_por AS REAL), 0.0)"
_BASE_BOOST = f"(1.0 + 2.0 * {_MODS} / ({_MODS} + 15.0))"
_TIPO_BOOST = (
    "(CASE n.tipo_norma WHEN 'Ley' THEN 1.6 WHEN 'Decreto/Ley' THEN 1.4 "
    "WHEN 'Decreto' THEN 1.25 ELSE 1.0 END)"
)
_RECENCY = (
    "(1.0 + 0.15 / (1.0 + COALESCE("
    f"(julianday('now') - julianday({_FECHA_PUB})) / 365.25, 1000.0)))"
)
_TITLE_BOOST = (
    "(CASE WHEN n.id_norma IN (SELECT rowid FROM normas_fts WHERE normas_fts MATCH ?) "
    "THEN 2.0 ELSE 1.0 END)"
)


class DatasetStore:
    """Read access to the offline dataset (one short-lived connection per call)."""

    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)

    def _conn(self):
        return closing(connect(self.db_path))

    def available(self) -> bool:
        if not Path(self.db_path).exists():
            return False
        try:
            with self._conn() as conn:
                return conn.execute("SELECT 1 FROM normas LIMIT 1").fetchone() is not None
        except sqlite3.Error:
            return False

    def count(self) -> int:
        if not Path(self.db_path).exists():
            return 0
        try:
            with self._conn() as conn:
                return conn.execute("SELECT COUNT(*) FROM normas").fetchone()[0]
        except sqlite3.Error:
            return 0

    def get_meta(self, key: str) -> str | None:
        try:
            with self._conn() as conn:
                row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
                return row[0] if row else None
        except sqlite3.Error:
            return None

    def get_norma(self, id_norma: int) -> dict | None:
        try:
            with self._conn() as conn:
                row = conn.execute(
                    "SELECT * FROM normas WHERE id_norma=?", (id_norma,)
                ).fetchone()
                return dict(row) if row else None
        except sqlite3.Error:  # missing file / table not built yet
            return None

    def resolve_id(self, tipo_nombre: str, numero: int | str) -> int | None:
        """Resolve e.g. ("Ley", 27275) -> id_norma. Returns the most recent match."""
        try:
            with self._conn() as conn:
                row = conn.execute(
                    "SELECT id_norma FROM normas "
                    "WHERE lower(tipo_norma)=lower(?) AND numero_norma=? "
                    f"ORDER BY {_FECHA_PUB.replace('n.', '')} DESC LIMIT 1",
                    (str(tipo_nombre), str(numero)),
                ).fetchone()
                return int(row[0]) if row else None
        except sqlite3.Error:
            return None

    def vinculos_count(self, id_norma: int, modo: int) -> int | None:
        """How many norms this one modifies (modo 1) / modify it (modo 2), per the dataset.

        The official ``modifica_a`` / ``modificada_por`` columns are counts, not ids.
        """
        norma = self.get_norma(id_norma)
        if not norma:
            return None
        raw = str(norma.get("modifica_a" if modo == 1 else "modificada_por") or "").strip()
        return int(raw) if raw.isdigit() else (0 if not raw else None)

    def tipos_norma(self) -> list[tuple[str, int]]:
        try:
            with self._conn() as conn:
                rows = conn.execute(
                    "SELECT tipo_norma, COUNT(*) FROM normas WHERE tipo_norma IS NOT NULL "
                    "GROUP BY tipo_norma ORDER BY 2 DESC"
                ).fetchall()
                return [(r[0], int(r[1])) for r in rows]
        except sqlite3.Error:
            return []

    def organismos(self) -> list[tuple[str, int]]:
        try:
            with self._conn() as conn:
                rows = conn.execute(
                    "SELECT organismo_origen, COUNT(*) FROM normas "
                    "WHERE organismo_origen IS NOT NULL GROUP BY organismo_origen "
                    "ORDER BY 2 DESC"
                ).fetchall()
                return [(r[0], int(r[1])) for r in rows]
        except sqlite3.Error:
            return []

    # ── search ───────────────────────────────────────────────────────────────
    def search(
        self,
        *,
        texto: str | None = None,
        tipo_norma: str | None = None,
        numero: int | str | None = None,
        anio: int | None = None,
        organismo: str | None = None,
        desde: str | None = None,  # ISO date (publication; sanction when missing)
        hasta: str | None = None,  # ISO date
        limit: int = 5,
        offset: int = 0,
        orden: str = "relevancia",
        materia: str | None = None,  # None | "tributaria"
    ) -> tuple[list[dict], int]:
        limit = max(1, int(limit))
        offset = max(0, int(offset))
        clauses, params = self._filters(tipo_norma, numero, anio, organismo, desde, hasta,
                                         materia)
        with self._conn() as conn:
            if not (texto or "").strip():
                return self._search_filters(conn, clauses, params, limit, offset)
            refs, resto = parse_referencias(texto)
            direct = self._direct_hits(conn, refs, clauses, params) if refs else []
            head = direct[offset: offset + limit]
            if not to_fts5_query(resto, expand=True):
                return head, len(direct)
            exclude = [r["id_norma"] for r in direct]
            fts_offset = max(0, offset - len(direct))
            fts_limit = limit - len(head)
            rows, total = self._search_fts(conn, resto, clauses, params, exclude,
                                           fts_limit, fts_offset, orden)
            if total == 0 and re.search(r"\d", resto):
                # Bare numbers are rarely in the indexed summaries: retry without them.
                sin_numeros = re.sub(r"\b\d[\d./-]*\b", " ", resto)
                if to_fts5_query(sin_numeros, expand=True):
                    rows, total = self._search_fts(conn, sin_numeros, clauses, params,
                                                   exclude, fts_limit, fts_offset, orden)
        return head + rows, total + len(direct)

    @staticmethod
    def _filters(tipo_norma, numero, anio, organismo, desde, hasta, materia=None):
        clauses, params = [], []
        if tipo_norma:
            clauses.append("lower(n.tipo_norma)=lower(?)")
            params.append(tipo_norma)
        if numero is not None and str(numero).strip():
            clauses.append("n.numero_norma=?")
            params.append(str(numero).strip())
        if anio is not None:
            clauses.append("substr(n.fecha_sancion,1,4)=?")
            params.append(str(anio))
        if organismo:
            clauses.append("n.organismo_origen LIKE ?")
            params.append(f"%{organismo}%")
        # Publication date; ~7k old norms have no fecha_boletin -> use fecha_sancion.
        if desde:
            clauses.append(f"{_FECHA_PUB} >= ?")
            params.append(desde)
        if hasta:
            clauses.append(f"{_FECHA_PUB} <= ?")
            params.append(hasta)
        if materia == "tributaria":
            sql, p = tributaria_clause()
            clauses.append(sql)
            params.extend(p)
        elif materia:
            raise ValueError(f"materia desconocida: {materia}")
        return clauses, params

    @staticmethod
    def _direct_hits(conn, refs, clauses, params) -> list[dict]:
        out: list[dict] = []
        seen: set[int] = set()
        extra = (" AND " + " AND ".join(clauses)) if clauses else ""
        for tipo, numero, anio in refs[:5]:
            sql = "SELECT n.* FROM normas n WHERE n.tipo_norma=? AND n.numero_norma=?"
            p: list = [tipo, numero]
            if anio is not None:
                sql += (" AND (substr(n.fecha_sancion,1,4)=? OR substr(n.fecha_boletin,1,4)=?)")
                p += [str(anio), str(anio)]
            sql += extra + f" ORDER BY {_FECHA_PUB} DESC LIMIT 10"
            for r in conn.execute(sql, [*p, *params]).fetchall():
                if r["id_norma"] not in seen:
                    seen.add(r["id_norma"])
                    out.append(dict(r))
        return out

    def _search_fts(self, conn, texto, clauses, params, exclude, limit, offset, orden):
        q = to_fts5_query(texto, expand=True)
        if not q:
            return [], 0
        extra = list(clauses)
        extra_params = list(params)
        if exclude:
            extra.append(f"n.id_norma NOT IN ({','.join('?' for _ in exclude)})")
            extra_params.extend(exclude)
        where_extra = (" AND " + " AND ".join(extra)) if extra else ""
        base = (
            "FROM normas_fts f JOIN normas n ON n.id_norma = f.rowid "
            "WHERE normas_fts MATCH ?" + where_extra
        )

        def run(match: str, with_title: bool):
            total = conn.execute(f"SELECT COUNT(*) {base}", [match, *extra_params]).fetchone()[0]
            if limit <= 0:
                return [], total
            if orden == "recientes":
                order, order_params = f"{_FECHA_PUB} DESC", []
            elif with_title:
                order = f"{_REL} * {_BASE_BOOST} * {_TIPO_BOOST} * {_TITLE_BOOST} * {_RECENCY} DESC"
                order_params = ["{titulo_sumario titulo_resumido} : (" + match + ")"]
            else:
                order = f"{_REL} * {_BASE_BOOST} * {_TIPO_BOOST} * {_RECENCY} DESC"
                order_params = []
            rows = conn.execute(
                f"SELECT n.* {base} ORDER BY {order} LIMIT ? OFFSET ?",
                [match, *extra_params, *order_params, limit, offset],
            ).fetchall()
            return [dict(r) for r in rows], total

        try:
            return run(q, True)
        except sqlite3.OperationalError:
            pass
        try:
            return run(q, False)
        except sqlite3.OperationalError:
            # Fall back to a plain phrase match if the translated query is rejected.
            safe = '"' + re.sub(r'["*()]', " ", texto).strip() + '"'
            if safe == '""':
                return [], 0
            return run(safe, False)

    @staticmethod
    def _search_filters(conn, clauses, params, limit, offset):
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        total = conn.execute(f"SELECT COUNT(*) FROM normas n{where}", params).fetchone()[0]
        rows = conn.execute(
            f"SELECT n.* FROM normas n{where} ORDER BY {_FECHA_PUB} DESC "
            "LIMIT ? OFFSET ?",
            [*params, limit, offset],
        ).fetchall()
        return [dict(r) for r in rows], total


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


@contextmanager
def dataset_build_lock(db_path: str | Path) -> Iterator[None]:
    """Exclusive, non-blocking lock so two rebuilds never run at the same time."""
    lock_path = Path(str(db_path) + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o640)
    locked = False
    try:
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            locked = True
        except OSError as exc:
            raise DatasetBuildLockedError(
                "Ya hay una reconstruccion del dataset en curso."
            ) from exc
        yield
    finally:
        if locked:
            try:
                if os.name == "nt":
                    import msvcrt

                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError:
                pass
        os.close(fd)


def _current_rows(db_path: Path) -> int:
    return DatasetStore(db_path).count() if db_path.exists() else 0


def _build_atomic(csv_path: str | Path, db_path: Path) -> int:
    """Build into a temp file next to the target, validate, then ``os.replace``."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_db = db_path.with_name(f".{db_path.name}.building-{os.getpid()}")
    for leftover in (tmp_db, Path(f"{tmp_db}-journal")):
        leftover.unlink(missing_ok=True)
    try:
        conn = sqlite3.connect(str(tmp_db))
        try:
            conn.execute("PRAGMA journal_mode=OFF")
            conn.execute("PRAGMA synchronous=OFF")
            count = import_csv(conn, csv_path)
            if count <= 0:
                raise DatasetBuildError("El CSV no produjo ninguna norma; se conserva el dataset actual.")
            previous = _current_rows(db_path)
            if previous and count < previous * MIN_ROWS_RATIO:
                raise DatasetBuildError(
                    f"El nuevo dataset tiene {count} normas contra {previous} del actual "
                    "(caida sospechosa); se conserva el dataset actual."
                )
            ok = conn.execute("PRAGMA quick_check").fetchone()[0]
            if ok != "ok":
                raise DatasetBuildError(f"quick_check fallo: {ok}")
            conn.execute("PRAGMA journal_mode=DELETE")
            conn.commit()
        finally:
            conn.close()
        os.replace(tmp_db, db_path)
    finally:
        tmp_db.unlink(missing_ok=True)
    # WAL/SHM files of the replaced file (older builds used WAL) must not linger.
    for stale in (Path(f"{db_path}-wal"), Path(f"{db_path}-shm")):
        try:
            stale.unlink(missing_ok=True)
        except OSError:
            pass
    return count


def build_dataset_from_csv(csv_path: str | Path, db_path: str | Path) -> int:
    """Atomically (re)build the SQLite dataset from a CSV (locked, temp file + replace)."""
    db_path = Path(db_path)
    with dataset_build_lock(db_path):
        return _build_atomic(csv_path, db_path)


def _download_zip(
    zip_url: str, zip_path: Path, *, user_agent: str, max_bytes: int = MAX_ZIP_BYTES
) -> None:
    """Stream the ZIP to disk with per-operation timeouts and a hard size cap."""
    import httpx

    if not zip_url.startswith("https://"):
        raise DatasetBuildError("La URL del ZIP debe ser https.")
    timeout = httpx.Timeout(connect=30.0, read=120.0, write=120.0, pool=30.0)
    print(f"Downloading {zip_url} ...", file=sys.stderr, flush=True)
    with httpx.Client(timeout=timeout, headers={"User-Agent": user_agent},
                      follow_redirects=True, max_redirects=5) as client:
        with client.stream("GET", zip_url) as resp:
            resp.raise_for_status()
            if not str(resp.url).startswith("https://"):
                raise DatasetBuildError("La descarga fue redirigida fuera de https.")
            total = int(resp.headers.get("content-length") or 0)
            if total > max_bytes:
                raise DatasetBuildError(f"ZIP demasiado grande ({total} bytes).")
            done = last = 0
            with open(zip_path, "wb") as fh:
                for chunk in resp.iter_bytes(chunk_size=1 << 20):
                    done += len(chunk)
                    if done > max_bytes:
                        raise DatasetBuildError("ZIP demasiado grande (supera el tope).")
                    fh.write(chunk)
                    if done - last >= 10 * (1 << 20):
                        last = done
                        pct = f" ({100 * done / total:.0f}%)" if total else ""
                        print(f"  ... {done / 1e6:.0f} MB{pct}", file=sys.stderr, flush=True)
    print(f"Downloaded {zip_path.stat().st_size / 1e6:.1f} MB", file=sys.stderr, flush=True)


def _unsafe_member(name: str) -> bool:
    n = name.replace("\\", "/")
    return (
        n.startswith("/")
        or re.match(r"^[A-Za-z]:", n) is not None
        or any(part == ".." for part in n.split("/"))
        or "\x00" in n
    )


def extract_csv_safely(
    zip_path: str | Path,
    dest_dir: str | Path,
    *,
    max_total: int = MAX_CSV_BYTES,
    max_ratio: int = MAX_COMPRESSION_RATIO,
) -> Path:
    """Extract the largest CSV of the ZIP to ``dest_dir/infoleg.csv``, defensively.

    Never uses ``extractall``: member names are validated (no absolute paths, drive
    letters, ``..`` or symlinks), the declared sizes and compression ratio are checked,
    and the actual bytes written are capped (headers can lie).
    """
    import stat

    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as zf:
        infos = zf.infolist()
        for info in infos:
            if _unsafe_member(info.filename):
                raise DatasetBuildError(f"ZIP con ruta insegura: {info.filename!r}")
            if stat.S_ISLNK(info.external_attr >> 16):
                raise DatasetBuildError(f"ZIP con enlace simbolico: {info.filename!r}")
        declared = sum(i.file_size for i in infos)
        if declared > max_total:
            raise DatasetBuildError("El contenido del ZIP excede el tamano maximo permitido.")
        csvs = [i for i in infos if i.filename.lower().endswith(".csv") and not i.is_dir()]
        if not csvs:
            raise DatasetBuildError("No hay ningun CSV dentro del ZIP de InfoLEG.")
        member = max(csvs, key=lambda i: i.file_size)
        if member.compress_size and member.file_size / member.compress_size > max_ratio:
            raise DatasetBuildError("Relacion de compresion sospechosa (posible zip bomb).")
        out = dest / "infoleg.csv"
        written = 0
        with zf.open(member) as src, open(out, "wb") as dst:
            while True:
                chunk = src.read(1 << 20)
                if not chunk:
                    break
                written += len(chunk)
                if written > max_total:
                    raise DatasetBuildError("El CSV excede el tamano maximo permitido.")
                dst.write(chunk)
    return out


def download_and_build(
    db_path: str | Path,
    *,
    zip_url: str = ZIP_URL,
    user_agent: str = "argentina-legal-data-mcp",
    tmp_dir: str | Path | None = None,
) -> int:
    """Download the official ZIP (~49 MB), extract the CSV safely and rebuild atomically.

    Everything temporary lives in a private work dir (next to the dataset by default,
    so the final ``os.replace`` stays on the same filesystem) and is always removed.
    """
    db_path = Path(db_path)
    with dataset_build_lock(db_path):
        base = Path(tmp_dir) if tmp_dir else db_path.parent
        base.mkdir(parents=True, exist_ok=True)
        work = Path(tempfile.mkdtemp(prefix=".infoleg-build-", dir=base))
        try:
            zip_path = work / "infoleg.zip"
            _download_zip(zip_url, zip_path, user_agent=user_agent)
            csv_path = extract_csv_safely(zip_path, work / "csv")
            zip_path.unlink(missing_ok=True)
            print("Importing CSV ...", file=sys.stderr, flush=True)
            count = _build_atomic(csv_path, db_path)
        finally:
            shutil.rmtree(work, ignore_errors=True)
    print(f"Imported {count} normas", file=sys.stderr, flush=True)
    return count


def iter_rows_for_test(rows: Iterable[dict]) -> list[list]:
    """Helper used by tests to shape dict rows into COLUMNS order."""
    return [[r.get(c) for c in COLUMNS] for r in rows]
