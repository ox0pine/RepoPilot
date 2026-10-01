from __future__ import annotations

import httpx


class ModelProviderError(Exception):
    pass


async def fetch_models(base_url: str, api_key: str) -> list[str]:
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=False, trust_env=False) as client:
            response = await client.get(f"{base_url}/models", headers=headers)
            response.raise_for_status()
    except (httpx.HTTPError, ValueError):
        raise ModelProviderError("Unable to retrieve models from the provider") from None
    try:
        payload = response.json()
    except (ValueError, UnicodeError):
        raise ModelProviderError("The model provider returned an invalid response") from None
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise ModelProviderError("The model provider returned an invalid response")
    models: set[str] = set()
    for row in payload["data"]:
        if not isinstance(row, dict) or not isinstance(row.get("id"), str) or not row["id"].strip():
            raise ModelProviderError("The model provider returned an invalid response")
        models.add(row["id"])
    return sorted(models)
