"""HTTP transport assembly: body-size cap + optional auth + CORS around the FastMCP app.

Discovery stays open; execution is protected when ``ARGMCP_AUTH_ENABLED=true``.
Tool calls are logged (name, duration, ok/error; never arguments) at the tool layer
(``tools_common.instrument_tools``), so the log exists with or without auth.
Run a SINGLE instance for the live InfoLEG pagination (session state is in-process).
"""

from __future__ import annotations

import json
import logging

log = logging.getLogger("arg_legal_mcp.http")


class BodySizeLimitMiddleware:
    """Reject request bodies larger than ``max_bytes`` (413), declared or streamed."""

    def __init__(self, app, max_bytes: int):
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            return await self.app(scope, receive, send)
        for name, value in scope.get("headers", []):
            if name == b"content-length":
                try:
                    declared = int(value)
                except ValueError:
                    declared = self.max_bytes + 1
                if declared > self.max_bytes:
                    return await _too_large(send)
        seen = 0

        async def limited_receive():
            nonlocal seen
            message = await receive()
            if message.get("type") == "http.request":
                seen += len(message.get("body", b""))
                if seen > self.max_bytes:
                    log.warning("request body over %d bytes; connection dropped", self.max_bytes)
                    return {"type": "http.disconnect"}
            return message

        return await self.app(scope, limited_receive, send)


async def _too_large(send):
    body = json.dumps({"jsonrpc": "2.0", "id": None,
                       "error": {"code": -32600, "message": "Request body too large"}}).encode()
    await send({"type": "http.response.start", "status": 413,
                "headers": [(b"content-type", b"application/json")]})
    await send({"type": "http.response.body", "body": body})


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
        app = AuthASGIMiddleware(app, store, max_body_bytes=settings.max_body_bytes)

    app = BodySizeLimitMiddleware(app, settings.max_body_bytes)

    # CORS outermost so preflight is handled before auth.
    from starlette.middleware.cors import CORSMiddleware

    app = CORSMiddleware(
        app,
        allow_origins=["*"],
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=["*"],
        expose_headers=["mcp-session-id"],
    )
    return app


def run_http(mcp, container, settings) -> None:
    import uvicorn

    app = build_http_app(mcp, container, settings)
    if not settings.auth_enabled:
        log.warning(
            "AUTH DISABLED on an HTTP transport: every tool is public. Set "
            "ARGMCP_AUTH_ENABLED=true and provide an API-keys file to restrict it."
        )
    uvicorn.run(app, host=settings.host, port=settings.port, log_level="info",
                proxy_headers=True, forwarded_allow_ips="127.0.0.1",
                timeout_keep_alive=15)
