"""CLI entrypoint.

Usage:
  python -m arg_legal_mcp                 # run server (transport from ARGMCP_TRANSPORT)
  python -m arg_legal_mcp serve [--transport stdio|streamable-http|sse]
  python -m arg_legal_mcp build-dataset   # download official ZIP -> SQLite (heavy)
  python -m arg_legal_mcp update-configs  # regenerate catalogs from the live form
"""

from __future__ import annotations

import argparse
import sys

from .config import get_settings


def _serve(transport: str | None) -> None:
    from .server import build_server

    settings = get_settings()
    if transport:
        settings.transport = transport
    mcp, container = build_server(settings)
    try:
        if settings.transport == "stdio":
            mcp.run(transport="stdio")
        else:
            # HTTP transports: optional auth middleware lives in http_app.
            from .http_app import run_http

            run_http(mcp, container, settings)
    finally:
        container.close()


def _build_dataset() -> int:
    from .infoleg.dataset import download_and_build

    settings = get_settings()
    settings.ensure_dirs()
    count = download_and_build(settings.dataset_path, user_agent=settings.user_agent)
    print(f"OK: {count} normas -> {settings.dataset_path}")
    return 0


def _update_configs() -> int:
    from .scripts_update_configs import update_configs

    settings = get_settings()
    return update_configs(settings)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="arg_legal_mcp", description=__doc__)
    sub = parser.add_subparsers(dest="command")

    serve = sub.add_parser("serve", help="Run the MCP server")
    serve.add_argument("--transport", choices=["stdio", "streamable-http", "sse"], default=None)
    sub.add_parser("build-dataset", help="Download the official ZIP and build the dataset")
    sub.add_parser("update-configs", help="Regenerate catalogs from the live InfoLEG form")

    args = parser.parse_args(argv)

    if args.command == "build-dataset":
        return _build_dataset()
    if args.command == "update-configs":
        return _update_configs()
    # default + 'serve'
    _serve(getattr(args, "transport", None))
    return 0


if __name__ == "__main__":
    sys.exit(main())
