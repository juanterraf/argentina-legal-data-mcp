"""Runtime configuration, sourced from environment variables (prefix ``ARGMCP_``).

All knobs have sensible defaults so the server runs with zero configuration over
``stdio``. See ``.env.example`` for the full list.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="ARGMCP_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── Transport ────────────────────────────────────────────────────────────
    transport: str = "stdio"  # stdio | streamable-http | sse
    host: str = "127.0.0.1"
    port: int = 8000

    # ── Storage / cache ──────────────────────────────────────────────────────
    data_dir: Path = Path("./data")
    cache_dir: Path = Path("./.cache")
    dataset_path: Path = Path("./data/infoleg.sqlite")

    # ── InfoLEG live client ────────────────────────────────────────────────--
    infoleg_base_url: str = "https://servicios.infoleg.gob.ar/infolegInternet"
    user_agent: str = (
        "argentina-legal-data-mcp/0.1 "
        "(+https://github.com/your-org/argentina-legal-data-mcp)"
    )
    http_timeout: float = 20.0
    session_ttl: int = 300

    # ── Pagination / chunking ──────────────────────────────────────────────--
    infoleg_page_size: int = 50  # InfoLEG returns 50 results per real page
    mcp_page_size: int = 5  # we re-paginate to small pages for the agent
    text_chunk_size: int = 2000  # characters per text chunk

    # ── Auth (HTTP transports only) ────────────────────────────────────────--
    auth_enabled: bool = False
    api_keys_path: Path = Path("./secrets/api-keys.json")
    jwt_audience: str = ""

    # ── Backend ──────────────────────────────────────────────────────────────
    backend: str = "sqlite"  # sqlite | postgres
    pg_dsn: str = ""

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.cache_dir.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    return Settings()
