"""API-key whitelist loaded from disk with hot reload.

File format (JSON)::

    { "keys": [ { "key": "secret", "name": "alice", "active": true, "role": "user" } ] }

The file is re-checked at most once every ``reload_interval`` seconds (by mtime), so
keys can be revoked/added without restarting the server. Roles: ``dev`` | ``user``.
"""

from __future__ import annotations

import hmac
import json
import sys
import threading
import time
from pathlib import Path

Role = str  # "dev" | "user"


class ApiKeyStore:
    def __init__(self, path: str | Path, reload_interval: float = 60.0):
        self.path = Path(path)
        self.reload_interval = reload_interval
        self._by_key: dict[str, Role] = {}
        self._mtime: float = -1.0
        self._last_check: float = 0.0
        self._lock = threading.Lock()
        self._load(force=True)

    def _load(self, *, force: bool = False) -> None:
        try:
            mtime = self.path.stat().st_mtime
        except FileNotFoundError:
            if force:
                print(
                    f"[auth] No API keys file at {self.path} — all protected calls will be denied.",
                    file=sys.stderr,
                )
            self._by_key = {}
            self._mtime = -1.0
            return
        if not force and mtime == self._mtime:
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            keys = data.get("keys", []) if isinstance(data, dict) else []
            self._by_key = {
                k["key"]: (k.get("role") or "user")
                for k in keys
                if k.get("active", True) and k.get("key")
            }
            self._mtime = mtime
            print(f"[auth] Loaded {len(self._by_key)} active API keys.", file=sys.stderr)
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            print(f"[auth] Invalid API keys file ({exc}); keeping previous set.", file=sys.stderr)

    def _maybe_reload(self) -> None:
        with self._lock:
            now = time.time()
            if now - self._last_check >= self.reload_interval:
                self._last_check = now
                self._load()

    def _match(self, token: str) -> Role | None:
        """Constant-time lookup: compares against EVERY key, no early exit."""
        if not token:
            return None
        given = token.encode("utf-8", "surrogatepass")
        found: Role | None = None
        for key, role in list(self._by_key.items()):
            if hmac.compare_digest(given, key.encode("utf-8", "surrogatepass")):
                found = role
        return found

    def validate(self, token: str) -> bool:
        self._maybe_reload()
        return self._match(token) is not None

    def role(self, token: str) -> Role | None:
        self._maybe_reload()
        return self._match(token)
