from __future__ import annotations

import httpx

from repopilot.domain.settings import GitHubPublicKeyResult


class GitHubAPIError(Exception):
    def __init__(self, detail: str, status_code: int = 502) -> None:
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


def _invalid() -> GitHubAPIError:
    return GitHubAPIError("GitHub returned an invalid response")


def _check_status(response: httpx.Response, expected: int) -> None:
    if response.status_code == 401:
        raise GitHubAPIError("GitHub API token is invalid or expired")
    if response.status_code in (403, 429):
        raise GitHubAPIError("GitHub denied the request; check token permissions and rate limits")
    if response.status_code != expected:
        raise _invalid()


def _json(response: httpx.Response):
    try:
        return response.json()
    except (ValueError, UnicodeError):
        raise _invalid() from None


def _key(row: object) -> tuple[int, str]:
    if not isinstance(row, dict):
        raise _invalid()
    key_id, key = row.get("id"), row.get("key")
    if type(key_id) is not int or key_id <= 0 or not isinstance(key, str):
        raise _invalid()
    parts = key.split()
    if len(parts) < 2:
        raise _invalid()
    return key_id, " ".join(parts[:2])


class GitHubClient:
    def __init__(self, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.transport = transport

    async def _find(self, client: httpx.AsyncClient, public_key: str) -> int | None:
        page = 1
        while True:
            response = await client.get("/user/keys", params={"per_page": 100, "page": page})
            _check_status(response, 200)
            rows = _json(response)
            if not isinstance(rows, list):
                raise _invalid()
            keys = [_key(row) for row in rows]
            for key_id, key in keys:
                if key == public_key:
                    return key_id
            if len(rows) < 100:
                return None
            page += 1

    async def register_public_key(self, *, public_key: str, api_token: str) -> GitHubPublicKeyResult:
        headers = {
            "Authorization": f"Bearer {api_token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2026-03-10",
            "User-Agent": "RepoPilot",
        }
        try:
            async with httpx.AsyncClient(
                base_url="https://api.github.com", timeout=15.0,
                follow_redirects=False, trust_env=False, transport=self.transport, headers=headers,
            ) as client:
                key_id = await self._find(client, public_key)
                if key_id is not None:
                    return GitHubPublicKeyResult(status="already_exists", key_id=key_id, public_key=public_key)
                response = await client.post("/user/keys", json={"title": "RepoPilot", "key": public_key})
                if response.status_code == 422:
                    key_id = await self._find(client, public_key)
                    if key_id is not None:
                        return GitHubPublicKeyResult(status="already_exists", key_id=key_id, public_key=public_key)
                    raise GitHubAPIError("GitHub rejected the SSH public key; it may already belong to another account", 422)
                _check_status(response, 201)
                key_id, key = _key(_json(response))
                if key != public_key:
                    raise _invalid()
                return GitHubPublicKeyResult(status="created", key_id=key_id, public_key=public_key)
        except httpx.RequestError:
            raise GitHubAPIError("Unable to contact GitHub; the result may be unknown. Try again to check the key") from None
