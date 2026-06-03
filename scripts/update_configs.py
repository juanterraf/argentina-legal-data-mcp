#!/usr/bin/env python
"""Regenerate data/dependencias.json and data/tipos_norma.json from live InfoLEG.

Thin wrapper around arg_legal_mcp.scripts_update_configs.update_configs.
"""

from arg_legal_mcp.config import get_settings
from arg_legal_mcp.scripts_update_configs import update_configs

if __name__ == "__main__":
    raise SystemExit(update_configs(get_settings()))
