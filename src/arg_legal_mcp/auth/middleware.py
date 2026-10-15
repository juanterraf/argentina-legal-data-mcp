"""Pure-ASGI auth middleware for the MCP HTTP transports.

Only discovery JSON-RPC methods (initialize, ping, notifications/*, tools|resources|
prompts listing) pass WITHOUT auth, so a client can connect and enumerate capabilities.
Everything else -tools/call, resources/read, prompts/get, resources/subscribe,
logging/setLevel, completion/complete, any future method, client responses and bodies
we cannot parse- requires a valid Bearer credential (allowlist, not denylist).

Implemented at the ASGI layer (not Starlette ``BaseHTTPMiddleware``) so the request
body is buffered once (with a size cap) and *replayed* to the inner MCP app. All
per-request state lives in local variables (the middleware instance is shared by
concurrent requests). Tool-call logging is done at the tool layer (see
``tools_common.instrument_tools``), so it also works with auth disabled.
"""

from __future__ import annotations

import json

from .api_keys import ApiKeyStore

# Methods that are safe to serve without a credential (discovery / lifecycle).
OPEN_METHODS = frozenset({
    "initialize",
    "ping",
    "tools/list",
    "resources/list",
    "resources/templates/list",
    "prompts/list",
})
OPEN_PREFIXES = ("notifications/",)

DEFAULT_MAX_BODY = 1_048_576  # 1 MiB


def _bearer_from_scope(scope) -> str | None:
    for name, value in scope.get("headers", []):
        if name == b"authorization":
            raw = value.decode("latin-1")
            if raw.lower().startswith("bearer "):
                return raw[7:].strip()
            return raw.strip()
    return None


def _parse(body: bytes):
    try:
        return json.loads(body or b"null")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None


def _methods(data) -> list[str] | None:
    """JSON-RPC methods in the body; None if the body is not a JSON-RPC message."""
    if isinstance(data, list):  # JSON-RPC batch
        if not data or not all(isinstance(d, dict) for d in data):
            return None
        return [str(d.get("method") or "") for d in data]
    if isinstance(data, dict):
        return [str(data.get("method") or "")]
    return None


def _is_open(method: str) -> bool:
    return method in OPEN_METHODS or method.startswith(OPEN_PREFIXES)


def _rpc_id(data):
    return data.get("id") if isinstance(data, dict) else None


class AuthASGIMiddleware:
    def __init__(self, app, store: ApiKeyStore, *, max_body_bytes: int = DEFAULT_MAX_BODY,
                 health=None):
        self.app = app
        self.store = store
        self.max_body_bytes = max_body_bytes
        self.health = health  # kept for signature compatibility (logging moved to tools)

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http" or scope.get("method") != "POST":
            return await self.app(scope, receive, send)

        # Buffer the full request body (capped) so we can both inspect and replay it.
        messages: list[dict] = []
        body = b""
        while True:
            message = await receive()
            messages.append(message)
            if message["type"] == "http.request":
                body += message.get("body", b"")
                if len(body) > self.max_body_bytes:
                    return await self._reply(send, 413, "Request body too large", None)
                if not message.get("more_body", False):
                    break
            else:  # http.disconnect
                break

        data = _parse(body)
        methods = _methods(data)
        needs_auth = methods is None or not all(_is_open(m) for m in methods)
        if needs_auth and not self._authorized(scope):
            return await self._reply(send, 401, "Authentication required", _rpc_id(data))

        pending = list(messages)  # per-request replay buffer

        async def replay():
            if pending:
                return pending.pop(0)
            return await receive()

        return await self.app(scope, replay, send)

    def _authorized(self, scope) -> bool:
        token = _bearer_from_scope(scope)
        if not token:
            return False
        return self.store.validate(token)

    @staticmethod
    async def _reply(send, status: int, message: str, rpc_id):
        payload = json.dumps(
            {
                "jsonrpc": "2.0",
                "error": {"code": -32001 if status == 401 else -32600, "message": message},
                "id": rpc_id,
            }
        ).encode()
        headers = [(b"content-type", b"application/json")]
        if status == 401:
            headers.append((b"www-authenticate", b'Bearer realm="argentina-legal-data-mcp"'))
        await send({"type": "http.response.start", "status": status, "headers": headers})
        await send({"type": "http.response.body", "body": payload})
