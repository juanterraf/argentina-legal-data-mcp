"""Pure-ASGI auth middleware for the MCP HTTP transports.

Discovery JSON-RPC methods (initialize, *_/list, ping, notifications/*) pass WITHOUT
auth so a client can connect and enumerate capabilities. Execution methods
(tools/call, resources/read, prompts/get) require a valid Bearer credential.

Implemented at the ASGI layer (not Starlette ``BaseHTTPMiddleware``) so the request
body is buffered once and *replayed* to the inner MCP app — avoiding the classic
"body already consumed" problem.
"""

from __future__ import annotations

import json
import time

from .api_keys import ApiKeyStore

# Methods that mutate/execute — these require a credential.
PROTECTED_METHODS = {"tools/call", "resources/read", "prompts/get"}


def _bearer_from_scope(scope) -> str | None:
    for name, value in scope.get("headers", []):
        if name == b"authorization":
            raw = value.decode("latin-1")
            if raw.lower().startswith("bearer "):
                return raw[7:].strip()
            return raw.strip()
    return None


def _methods_in_body(body: bytes) -> list[str]:
    try:
        data = json.loads(body or b"{}")
    except json.JSONDecodeError:
        return []
    if isinstance(data, list):  # JSON-RPC batch
        return [d.get("method", "") for d in data if isinstance(d, dict)]
    if isinstance(data, dict):
        return [data.get("method", "")]
    return []


def _tool_name(body: bytes) -> str | None:
    try:
        data = json.loads(body or b"{}")
        if isinstance(data, dict):
            return (data.get("params") or {}).get("name")
    except (json.JSONDecodeError, AttributeError):
        pass
    return None


def _rpc_id(body: bytes):
    try:
        data = json.loads(body or b"{}")
        if isinstance(data, dict):
            return data.get("id")
    except json.JSONDecodeError:
        pass
    return None


class AuthASGIMiddleware:
    def __init__(self, app, store: ApiKeyStore, *, jwt_audience: str | None = None, health=None):
        self.app = app
        self.store = store
        self.jwt_audience = jwt_audience
        self.health = health  # optional HealthStore for request_log

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http" or scope.get("method") != "POST":
            return await self.app(scope, receive, send)

        # Buffer the full request body so we can both inspect and replay it.
        messages = []
        body = b""
        while True:
            message = await receive()
            messages.append(message)
            if message["type"] == "http.request":
                body += message.get("body", b"")
                if not message.get("more_body", False):
                    break
            else:
                break

        methods = _methods_in_body(body)
        needs_auth = any(m in PROTECTED_METHODS for m in methods)

        if needs_auth and not self._authorized(scope):
            return await self._deny(send, _rpc_id(body))

        if self.health is not None and "tools/call" in methods:
            self._log_start = time.time()
            tool = _tool_name(body)
        else:
            tool = None

        # Replay buffered messages, then defer to the live receive channel.
        async def replay():
            if messages:
                return messages.pop(0)
            return await receive()

        if tool is None:
            return await self.app(scope, replay, send)

        # Wrap send to capture status for the request log.
        status_holder = {"status": 200}

        async def logging_send(event):
            if event["type"] == "http.response.start":
                status_holder["status"] = event["status"]
            await send(event)

        try:
            await self.app(scope, replay, logging_send)
        finally:
            ms = int((time.time() - self._log_start) * 1000)
            try:
                self.health.log_request(tool, status_holder["status"] < 400, ms)
            except Exception:  # never let logging break a request
                pass

    def _authorized(self, scope) -> bool:
        token = _bearer_from_scope(scope)
        if not token:
            return False
        return self.store.validate(token)

    async def _deny(self, send, rpc_id):
        payload = json.dumps(
            {
                "jsonrpc": "2.0",
                "error": {"code": -32001, "message": "Authentication required"},
                "id": rpc_id,
            }
        ).encode()
        await send(
            {
                "type": "http.response.start",
                "status": 401,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"www-authenticate", b'Bearer realm="argentina-legal-data-mcp"'),
                ],
            }
        )
        await send({"type": "http.response.body", "body": payload})
