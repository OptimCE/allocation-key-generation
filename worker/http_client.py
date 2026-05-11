"""Thin httpx wrapper used by the worker to download source data files.

A single ``AsyncClient`` is created in ``worker.main`` and shared across all
algorithm dispatchers, so connection pooling kicks in when multiple
generations target the same storage host. The client is closed during the
worker's graceful shutdown sequence.
"""

from __future__ import annotations

import httpx

# Bounded timeouts so a slow storage backend can't stall the worker forever.
# Algorithms themselves are slow; downloads should be quick or fail loud.
_DEFAULT_TIMEOUT = httpx.Timeout(60.0, connect=10.0)


def make_http_client() -> httpx.AsyncClient:
    """Construct the worker-wide ``httpx.AsyncClient``.

    Follows redirects so pre-signed URLs that bounce through CDN/redirector
    layers (common with object storage) work transparently.
    """
    return httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT, follow_redirects=True)


async def download(client: httpx.AsyncClient, url: str) -> bytes:
    """Fetch ``url`` and return the response body.

    Raises ``httpx.HTTPStatusError`` on non-2xx and ``httpx.HTTPError`` on
    transport-level failures. Callers in ``worker.dispatcher`` use the
    distinction to decide between deterministic-failure (4xx → mark FAILED)
    and transient-failure (timeouts, 5xx → nak for redelivery).
    """
    response = await client.get(url)
    response.raise_for_status()
    return response.content
