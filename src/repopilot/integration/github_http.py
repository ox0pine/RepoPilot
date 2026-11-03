"""Single GitHub REST transport policy: pinned API host, token headers, no ambient proxies."""

from __future__ import annotations

import httpx

API_BASE = 'https://api.github.com'


def headers(token: str) -> dict[str, str]:
    return {
        'Authorization': f'Bearer {token}',
        'Accept': 'application/vnd.github+json',
        'X-GitHub-Api-Version': '2026-03-10',
        'User-Agent': 'RepoPilot',
    }


def client(
    transport: httpx.AsyncBaseTransport | None, *, timeout: float, token: str,
) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=API_BASE, headers=headers(token), timeout=timeout,
        follow_redirects=False, trust_env=False, transport=transport,
    )
