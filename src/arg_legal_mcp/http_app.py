"""HTTP transport assembly: wrap the FastMCP ASGI app with auth + CORS, then serve.

Discovery stays open; execution is protected when ``ARGMCP_AUTH_ENABLED=true``.
Run a SINGLE instance for the live InfoLEG pagination (session state is in-process).
"""

from __future__ import annotations


def build_http_app(mcp, container, settings):
    """Return the ASGI app for the configured HTTP transport."""
    if settings.transport == "sse":
        app = mcp.sse_app()
    else:  # streamable-http (default remote transport)
        app = mcp.streamable_http_app()

    if settings.auth_enabled:
        from .auth.api_keys import ApiKeyStore
        from .auth.middleware import AuthASGIMiddleware

        store = ApiKeyStore(settings.api_keys_path)
        app = AuthASGIMiddleware(
            app,
            store,
            jwt_audience=settings.jwt_audience or None,
            health=getattr(container, "health", None),
        )

    # CORS outermost so preflight is handled before auth.
    from starlette.middleware.cors import CORSMiddleware

    app = CORSMiddleware(
        app,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["mcp-session-id"],
    )
    return app


def run_http(mcp, container, settings) -> None:
    import uvicorn

    app = build_http_app(mcp, container, settings)
    if not settings.auth_enabled:
        print(
            "[warn] AUTH DISABLED on an HTTP transport. Set ARGMCP_AUTH_ENABLED=true and "
            "provide an API-keys file before exposing this server.",
            flush=True,
        )
    uvicorn.run(app, host=settings.host, port=settings.port, log_level="info")
