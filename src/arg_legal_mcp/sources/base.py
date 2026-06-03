"""Shared helpers for the extra data sources: a small, resilient JSON GET.

TLS verification is always on. Transient failures get a short bounded retry; the tool
layer turns errors into a structured ``{"error": ...}`` rather than raising.
"""

from __future__ import annotations

import time
from typing import Any

import httpx


class SourceError(Exception):
    """A data source could not be reached or returned an unexpected payload."""


def get_json(
    url: str,
    *,
    user_agent: str,
    timeout: float = 15.0,
    params: dict | None = None,
    retries: int = 3,
    backoff: float = 0.5,
) -> Any:
    last: Exception | None = None
    headers = {"User-Agent": user_agent, "Accept": "application/json"}
    for attempt in range(retries):
        try:
            with httpx.Client(timeout=timeout, headers=headers, follow_redirects=True) as client:
                resp = client.get(url, params=params)
            if resp.status_code >= 500:
                raise SourceError(f"server error {resp.status_code} from {url}")
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPStatusError as exc:
            # 4xx is a permanent client error (404/410/...) — do not retry.
            raise SourceError(f"HTTP {exc.response.status_code} from {url}") from exc
        except (httpx.HTTPError, ValueError, SourceError) as exc:
            last = exc
            if attempt == retries - 1:
                break
            time.sleep(backoff * (2**attempt))
    raise SourceError(str(last))
