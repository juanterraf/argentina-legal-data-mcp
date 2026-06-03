"""Auth middleware: discovery open, execution protected, hot-reload key store."""

from __future__ import annotations

import json

from arg_legal_mcp.auth.api_keys import ApiKeyStore
from arg_legal_mcp.auth.middleware import AuthASGIMiddleware


def _keys_file(tmp_path, key="secret-key", role="user"):
    path = tmp_path / "keys.json"
    path.write_text(
        json.dumps({"keys": [{"key": key, "name": "tester", "active": True, "role": role}]}),
        encoding="utf-8",
    )
    return path


class _FakeInner:
    """Minimal inner ASGI app that records whether it ran and returns 200."""

    def __init__(self):
        self.called = False

    async def __call__(self, scope, receive, send):
        self.called = True
        await receive()  # consume (replayed) body
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b'{"ok":true}'})


def _scope(token: str | None = None):
    headers = []
    if token is not None:
        headers.append((b"authorization", f"Bearer {token}".encode()))
    return {"type": "http", "method": "POST", "path": "/mcp", "headers": headers}


async def _drive(mw, scope, body: dict):
    body_bytes = json.dumps(body).encode()
    sent = []

    async def receive():
        return {"type": "http.request", "body": body_bytes, "more_body": False}

    async def send(event):
        sent.append(event)

    await mw(scope, receive, send)
    return sent


def _status(sent):
    return next(e["status"] for e in sent if e["type"] == "http.response.start")


async def test_discovery_passes_without_token(tmp_path):
    inner = _FakeInner()
    mw = AuthASGIMiddleware(inner, ApiKeyStore(_keys_file(tmp_path)))
    sent = await _drive(mw, _scope(), {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert inner.called
    assert _status(sent) == 200


async def test_tools_call_without_token_is_401(tmp_path):
    inner = _FakeInner()
    mw = AuthASGIMiddleware(inner, ApiKeyStore(_keys_file(tmp_path)))
    sent = await _drive(
        mw,
        _scope(),
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
         "params": {"name": "infoleg_ver_norma", "arguments": {"id": 1}}},
    )
    assert not inner.called
    assert _status(sent) == 401


async def test_tools_call_with_valid_token_passes(tmp_path):
    inner = _FakeInner()
    mw = AuthASGIMiddleware(inner, ApiKeyStore(_keys_file(tmp_path, key="abc123")))
    sent = await _drive(
        mw,
        _scope(token="abc123"),
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
         "params": {"name": "infoleg_ver_norma", "arguments": {"id": 1}}},
    )
    assert inner.called
    assert _status(sent) == 200


async def test_tools_call_with_invalid_token_is_401(tmp_path):
    inner = _FakeInner()
    mw = AuthASGIMiddleware(inner, ApiKeyStore(_keys_file(tmp_path, key="right")))
    sent = await _drive(
        mw,
        _scope(token="wrong"),
        {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
         "params": {"name": "infoleg_ver_norma", "arguments": {"id": 1}}},
    )
    assert not inner.called
    assert _status(sent) == 401


def test_api_key_store_hot_reload(tmp_path):
    path = _keys_file(tmp_path, key="k1")
    store = ApiKeyStore(path, reload_interval=0)  # always re-check
    assert store.validate("k1")
    assert not store.validate("k2")
    # Rotate keys on disk; with reload_interval=0 the next check reloads.
    path.write_text(
        json.dumps({"keys": [{"key": "k2", "name": "t", "active": True}]}), encoding="utf-8"
    )
    import os
    import time

    os.utime(path, (time.time() + 1, time.time() + 1))  # bump mtime
    assert store.validate("k2")
    assert not store.validate("k1")
