"""Server assembly: wire dependencies and build the FastMCP application.

``build_service()`` constructs the InfoLEG service container (also used by tests).
``build_server()`` builds the FastMCP app and registers tools, the resource and prompts.
"""

from __future__ import annotations

from dataclasses import dataclass

from mcp.server.fastmcp import FastMCP

from .config import Settings, get_settings
from .health import HealthStore
from .infoleg.backend import DatasetBackend, get_dataset
from .infoleg.cache import InfoLegCache
from .infoleg.catalogs import CatalogService
from .infoleg.client import InfoLegClient
from .infoleg.services import InfoLegService
from .infoleg.session import SessionManager
from .prompts import register_prompts
from .sources.register import register_sources
from .tools_common import register_common
from .tools_infoleg import register_infoleg
from .tools_search_fetch import register_search_fetch

SERVER_NAME = "Argentina Legal & Data MCP"
INSTRUCTIONS = (
    "Servidor MCP de datos publicos de Argentina, con foco profundo en legislacion "
    "nacional (InfoLEG). Para legislacion: usa infoleg_buscar_normas (dataset por "
    "defecto; en_vivo=true para el buscador real), infoleg_ver_norma, los textos "
    "actualizado/original, y las tools de vinculos. La informacion proviene de fuentes "
    "oficiales y NO constituye asesoramiento juridico."
)


@dataclass
class ServiceContainer:
    settings: Settings
    catalogs: CatalogService
    dataset: DatasetBackend
    cache: InfoLegCache | None
    session_manager: SessionManager
    infoleg: InfoLegService
    health: HealthStore

    def close(self) -> None:
        self.session_manager.close()
        if self.cache:
            self.cache.close()


def build_service(settings: Settings | None = None, *, use_cache: bool = True) -> ServiceContainer:
    settings = settings or get_settings()
    settings.ensure_dirs()

    catalogs = CatalogService(
        dependencias_path=settings.data_dir / "dependencias.json",
        tipos_path=settings.data_dir / "tipos_norma.json",
    )
    dataset = get_dataset(settings)
    health = HealthStore(settings.data_dir / "health.sqlite")
    if dataset.available():
        health.record_freshness(
            "infoleg_dataset", healthy=True, last_data_date=dataset.get_meta("built_at")
        )
    cache = InfoLegCache(settings.cache_dir) if use_cache else None
    session_manager = SessionManager(
        user_agent=settings.user_agent,
        timeout=settings.http_timeout,
        ttl=settings.session_ttl,
        verify=True,
    )
    client = InfoLegClient(settings.infoleg_base_url)
    infoleg = InfoLegService(
        settings=settings,
        client=client,
        session_manager=session_manager,
        dataset=dataset,
        cache=cache,
        catalogs=catalogs,
    )
    return ServiceContainer(
        settings=settings,
        catalogs=catalogs,
        dataset=dataset,
        cache=cache,
        session_manager=session_manager,
        infoleg=infoleg,
        health=health,
    )


def build_server(settings: Settings | None = None) -> tuple[FastMCP, ServiceContainer]:
    settings = settings or get_settings()
    container = build_service(settings)

    mcp = FastMCP(
        SERVER_NAME,
        instructions=INSTRUCTIONS,
        host=settings.host,
        port=settings.port,
    )
    register_infoleg(mcp, container.infoleg, container.catalogs, health=container.health)
    register_common(mcp, health=container.health, dataset=container.dataset)
    register_search_fetch(mcp, container.infoleg)
    register_sources(mcp, settings, dataset=container.dataset)
    register_prompts(mcp)
    return mcp, container
