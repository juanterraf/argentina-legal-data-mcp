"""CLI entrypoint.

Usage:
  python -m arg_legal_mcp                 # run server (transport from ARGMCP_TRANSPORT)
  python -m arg_legal_mcp serve [--transport stdio|streamable-http|sse]
  python -m arg_legal_mcp build-dataset   # download official ZIP (~49 MB) -> SQLite
  python -m arg_legal_mcp update-configs  # regenerate catalogs (live form + dataset)

The dataset rebuild is ONLY available here (and via the systemd timer), never as an
MCP tool. It is atomic (temp file + rename) and locked against concurrent runs; the
running server picks up the new file without a restart.
"""

from __future__ import annotations

import argparse
import logging
import sys

from .config import get_settings


def _setup_logging() -> None:
    # stderr only: on stdio transport, stdout is the MCP protocol channel.
    logging.basicConfig(
        level=logging.INFO, stream=sys.stderr,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    # httpx logs every request URL at INFO; URLs can carry user data (e.g. a CUIT).
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def _serve(transport: str | None) -> None:
    from .server import build_server

    _setup_logging()
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
    from .health import DATASET_SOURCE, HealthStore
    from .infoleg.dataset import DatasetBuildError, DatasetBuildLockedError

    settings = get_settings()
    settings.ensure_dirs()
    health = HealthStore(settings.data_dir / "health.sqlite")
    try:
        if settings.backend == "postgres":
            if not settings.pg_dsn:
                print("ERROR: ARGMCP_BACKEND=postgres requires ARGMCP_PG_DSN", file=sys.stderr)
                return 2
            from .infoleg.dataset_pg import download_and_build_pg

            count = download_and_build_pg(
                settings.pg_dsn, user_agent=settings.user_agent,
                lock_path=settings.data_dir / "infoleg-pg",
            )
            target = "postgres"
        else:
            from .infoleg.dataset import download_and_build

            count = download_and_build(settings.dataset_path, user_agent=settings.user_agent)
            target = str(settings.dataset_path)
    except DatasetBuildLockedError as exc:
        print(f"SKIP: {exc}", file=sys.stderr)
        return 3
    except (DatasetBuildError, OSError, ValueError) as exc:
        health.record_freshness(DATASET_SOURCE, healthy=False, error=str(exc)[:200])
        print(f"ERROR: no se pudo reconstruir el dataset: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # httpx errors, etc.
        health.record_freshness(DATASET_SOURCE, healthy=False, error=type(exc).__name__)
        print(f"ERROR: no se pudo reconstruir el dataset: {type(exc).__name__}: {exc}",
              file=sys.stderr)
        return 1
    health.record_freshness(DATASET_SOURCE, healthy=True)
    print(f"OK: {count} normas -> {target}")
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
