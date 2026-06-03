"""Regenerate the catalogs (dependencias.json / tipos_norma.json) from the live form.

We generate these ourselves from ``mostrarBusquedaNormas.do`` rather than vendoring
any reference's data files. Run via ``python -m arg_legal_mcp update-configs``.
"""

from __future__ import annotations

import json

from .config import Settings
from .infoleg.client import InfoLegClient
from .infoleg.session import SessionManager


def update_configs(settings: Settings) -> int:
    settings.ensure_dirs()
    sm = SessionManager(user_agent=settings.user_agent, timeout=settings.http_timeout)
    client = InfoLegClient(settings.infoleg_base_url)
    try:
        cfg = client.mostrar_opciones_busqueda(sm.get_client())
    finally:
        sm.close()

    deps = [d.model_dump() for d in cfg.dependencias]
    tipos = [t.model_dump() for t in cfg.tipos_norma]
    (settings.data_dir / "dependencias.json").write_text(
        json.dumps(deps, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (settings.data_dir / "tipos_norma.json").write_text(
        json.dumps(tipos, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"Guardadas {len(deps)} dependencias y {len(tipos)} tipos de norma en {settings.data_dir}")
    return 0
