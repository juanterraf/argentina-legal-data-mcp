"""Regenerate the catalogs (tipos_norma.json / dependencias.json).

* ``tipos_norma.json``: every norm type present in the dataset (name + count), plus the
  live form id when known. Works offline (dataset only) if InfoLEG is unreachable.
* ``dependencias.json``: live form ids for organismos (only used by ``en_vivo``
  searches filtered by organismo); written only when the live form answers.

Run via ``python -m arg_legal_mcp update-configs``.
"""

from __future__ import annotations

import json
import os
import sys

from .config import Settings
from .infoleg.backend import get_dataset
from .infoleg.catalogs import tipos_catalogo
from .infoleg.client import InfoLegClient
from .infoleg.session import SessionManager


def _write_json(path, data) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def update_configs(settings: Settings) -> int:
    settings.ensure_dirs()
    live_tipos: list[dict] = []
    deps: list[dict] = []
    sm = SessionManager(user_agent=settings.user_agent, timeout=settings.http_timeout)
    client = InfoLegClient(settings.infoleg_base_url)
    try:
        cfg = client.mostrar_opciones_busqueda(sm.get_client())
        deps = [d.model_dump() for d in cfg.dependencias]
        live_tipos = [t.model_dump() for t in cfg.tipos_norma]
    except Exception as exc:  # noqa: BLE001 — offline is fine: tipos come from the dataset
        print(f"[warn] formulario en vivo no disponible ({type(exc).__name__}); "
              "se usan solo los datos del dataset.", file=sys.stderr)
    finally:
        sm.close()

    dataset = get_dataset(settings)
    conteos = dataset.tipos_norma() if dataset.available() else []
    if conteos:
        tipos = tipos_catalogo(conteos, live_tipos)
    else:
        tipos = [{"nombre": t["nombre"], "id_infoleg": t["id"]} for t in live_tipos]
    if tipos:
        _write_json(settings.data_dir / "tipos_norma.json", tipos)
    if deps:
        _write_json(settings.data_dir / "dependencias.json", deps)
    print(f"Guardados {len(tipos)} tipos de norma y {len(deps)} dependencias en "
          f"{settings.data_dir}")
    return 0 if tipos else 1
