"""Regression guard: the incorrect 50,000-block annex calculation must never return.

This is the spec-mandated test: it fails if anyone reintroduces the voftec bug of
computing the annex folder with blocks of 50,000 instead of 5,000.
"""

from __future__ import annotations

import re
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src" / "arg_legal_mcp"

# The forbidden numeric literal: 50000 or 50_000 (with optional separators), but not
# 5000 and not "50,000" inside prose. We scan code only.
FORBIDDEN = re.compile(r"\b50[_]?000\b")


def test_no_50000_block_literal_in_source():
    offenders = []
    for py in SRC.rglob("*.py"):
        for i, line in enumerate(py.read_text(encoding="utf-8").splitlines(), 1):
            # Ignore comment prose that explicitly warns about the bug.
            code = line.split("#", 1)[0]
            if FORBIDDEN.search(code):
                offenders.append(f"{py.name}:{i}: {line.strip()}")
    assert not offenders, (
        "Found a 50,000-block annex computation (use 5,000-blocks; better: read the "
        "real URL). Offending lines:\n" + "\n".join(offenders)
    )


def test_computed_range_matches_5000_convention():
    from arg_legal_mcp.infoleg.anexos import computed_anexo_range

    assert computed_anexo_range(265949) == "265000-269999"
