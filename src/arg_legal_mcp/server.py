"""Server assembly: wire dependencies and build the FastMCP application.

``build_service()`` constructs the InfoLEG service container (also used by tests).
``build_server()`` builds the FastMCP app and registers tools, the resource and prompts.
"""

from __future__ import annotations

from dataclasses import dataclass

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

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
from .tools_common import instrument_tools, register_common
from .tools_infoleg import register_infoleg
from .tools_search_fetch import register_search_fetch

SERVER_NAME = "Argentina Legal & Data MCP"
INSTRUCTIONS = (
    "Servidor MCP de datos publicos oficiales de Argentina, con foco en legislacion "
    "nacional. "
    "LEGISLACION (InfoLEG): infoleg_buscar_normas (por relevancia; acepta referencias "
    "como 'Ley 20628' o 'DNU 70/2023'), infoleg_ver_norma (ficha), "
    "infoleg_obtener_texto_actualizado / _original (texto paginado), "
    "infoleg_ver_normas_que_la_modifican / _que_modifica (vigencia; si avisan "
    "degradacion, una lista vacia NO significa 'ninguna'), "
    "infoleg_comparar_original_actualizado, infoleg_buscar_organismos y "
    "tributaria_buscar. "
    "BOLETIN OFICIAL de la Nacion (1ra seccion: normas y avisos; 3ra: contrataciones) "
    "y de la Ciudad: boletin_oficial_sumario (dia), boletin_oficial_buscar (texto y "
    "rango) y boletin_oficial_ver (texto completo). "
    "CALCULOS: calcular_actualizacion (CER, UVA, ICL, IPC), calcular_plazo (dias "
    "habiles con feriados nacionales). "
    "DATOS: dolar_cotizaciones, bcra_variables, bcra_cotizaciones, indec_buscar_series, "
    "indec_serie, feriados_nacionales. data_health informa el estado de las fuentes. "
    "Cita siempre la fuente y la fecha que devuelve cada tool. La informacion proviene "
    "de fuentes oficiales y NO constituye asesoramiento juridico; los calculos son "
    "orientativos."
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


def _transport_security(settings: Settings) -> TransportSecuritySettings | None:
    """DNS-rebinding protection for HTTP transports.

    FastMCP auto-enables protection (localhost-only hosts) whenever the bind host is
    127.0.0.1, which breaks any reverse-proxied deploy (the proxy forwards the public
    Host). So we set it explicitly: if ``allowed_hosts`` is configured, keep protection ON
    limited to those hosts; otherwise disable it (the localhost bind + nginx server_name
    already constrain access). For stdio, return None (HTTP validation doesn't apply).
    """
    if settings.transport == "stdio":
        return None
    hosts = [h.strip() for h in settings.allowed_hosts.split(",") if h.strip()]
    if hosts:
        return TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=hosts + ["127.0.0.1:*", "localhost:*", "[::1]:*"],
            allowed_origins=[o.strip() for o in settings.allowed_origins.split(",") if o.strip()],
        )
    return TransportSecuritySettings(enable_dns_rebinding_protection=False)


def build_service(settings: Settings | None = None, *, use_cache: bool = True) -> ServiceContainer:
    settings = settings or get_settings()
    settings.ensure_dirs()

    dataset = get_dataset(settings)
    catalogs = CatalogService(
        dependencias_path=settings.data_dir / "dependencias.json",
        tipos_path=settings.data_dir / "tipos_norma.json",
        dataset=dataset,
    )
    # The dataset's own status (count, build date) is read live by data_health.
    health = HealthStore(settings.data_dir / "health.sqlite")
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
        transport_security=_transport_security(settings),
    )
    register_infoleg(mcp, container.infoleg, container.catalogs)
    register_common(mcp, health=container.health, dataset=container.dataset)
    register_search_fetch(mcp, container.infoleg)
    register_sources(mcp, settings, dataset=container.dataset)
    register_prompts(mcp)
    # Last: every tool runs off the event loop and is logged (name/ms/ok, no arguments).
    instrument_tools(mcp, container.health)
    return mcp, container
