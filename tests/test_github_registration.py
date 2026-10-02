from __future__ import annotations

import json

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

from repopilot.integration.github import GitHubAPIError, GitHubClient


TOKEN = "github-registration-test-token-not-a-real-credential"
INVALID_RESPONSE = "GitHub returned an invalid response"
NETWORK_ERROR = "Unable to contact GitHub; the result may be unknown. Try again to check the key"
REJECTED_KEY = "GitHub rejected the SSH public key; it may already belong to another account"
pytestmark = pytest.mark.asyncio


@pytest.fixture
def public_key() -> str:
    return ed25519.Ed25519PrivateKey.generate().public_key().public_bytes(
        serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH
    ).decode()


def unrelated_keys(count: int = 100) -> list[dict[str, object]]:
    # Existing keys need not use a supported local algorithm or be parsed locally.
    return [
        {"id": index + 1, "key": f"sk-ssh-ed25519@openssh.com remote-data-{index} old-key"}
        for index in range(count)
    ]


def assert_request_boundary(requests: list[httpx.Request], public_key: str) -> None:
    for request in requests:
        assert request.url.scheme == "https"
        assert request.url.host == "api.github.com"
        assert request.url.port is None
        assert request.url.path == "/user/keys"
        assert TOKEN not in str(request.url)
        assert request.headers["Authorization"] == f"Bearer {TOKEN}"
        assert request.headers["Accept"] == "application/vnd.github+json"
        assert request.headers["X-GitHub-Api-Version"] == "2026-03-10"
        assert request.headers["User-Agent"] == "RepoPilot"
        assert TOKEN.encode() not in request.content
        if request.method == "POST":
            # Exact allowlist: neither a private key nor token can enter the JSON body.
            assert json.loads(request.content) == {"title": "RepoPilot", "key": public_key}
            assert not request.url.query
        else:
            assert request.method == "GET"
            assert request.content == b""
            assert request.url.params["per_page"] == "100"


def assert_safe_error(error: GitHubAPIError, detail: str, status_code: int) -> None:
    assert error.status_code == status_code
    assert error.detail == detail
    assert str(error) == detail
    assert TOKEN not in str(error)


async def test_paginated_existing_key_ignores_comments_and_untrusted_link(public_key: str) -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.method == "GET"
        page = int(request.url.params["page"])
        if page in (1, 2):
            return httpx.Response(
                200,
                json=unrelated_keys(),
                headers={"Link": '<https://attacker.invalid/steal?page=999>; rel="next"'},
            )
        assert page == 3
        algorithm, data = public_key.split()
        return httpx.Response(200, json=[{"id": 321, "key": f"{algorithm}\t{data} comment"}])

    result = await GitHubClient(transport=httpx.MockTransport(respond)).register_public_key(
        public_key=public_key, api_token=TOKEN
    )

    assert result.model_dump() == {
        "status": "already_exists", "key_id": 321, "public_key": public_key
    }
    assert [request.url.params["page"] for request in requests] == ["1", "2", "3"]
    assert_request_boundary(requests, public_key)


async def test_creates_only_after_complete_paginated_search(public_key: str) -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET":
            page = int(request.url.params["page"])
            assert page in (1, 2)
            return httpx.Response(200, json=unrelated_keys(100 if page == 1 else 1))
        return httpx.Response(201, json={"id": 42, "key": f"{public_key} RepoPilot"})

    result = await GitHubClient(transport=httpx.MockTransport(respond)).register_public_key(
        public_key=public_key, api_token=TOKEN
    )

    assert result.model_dump() == {"status": "created", "key_id": 42, "public_key": public_key}
    assert [request.method for request in requests] == ["GET", "GET", "POST"]
    assert [request.url.params["page"] for request in requests[:-1]] == ["1", "2"]
    assert_request_boundary(requests, public_key)


async def test_422_rechecks_all_pages_and_finds_concurrent_creation(public_key: str) -> None:
    requests: list[httpx.Request] = []
    posted = False

    def respond(request: httpx.Request) -> httpx.Response:
        nonlocal posted
        requests.append(request)
        if request.method == "POST":
            assert not posted
            posted = True
            return httpx.Response(422, json={"message": TOKEN})
        page = int(request.url.params["page"])
        if not posted:
            assert page == 1
            return httpx.Response(200, json=[])
        if page == 1:
            return httpx.Response(200, json=unrelated_keys())
        assert page == 2
        return httpx.Response(200, json=[{"id": 87, "key": f"{public_key} concurrent-client"}])

    result = await GitHubClient(transport=httpx.MockTransport(respond)).register_public_key(
        public_key=public_key, api_token=TOKEN
    )

    assert result.model_dump() == {
        "status": "already_exists", "key_id": 87, "public_key": public_key
    }
    assert [request.method for request in requests] == ["GET", "POST", "GET", "GET"]
    assert [request.url.params["page"] for request in requests if request.method == "GET"] == [
        "1", "1", "2"
    ]
    assert_request_boundary(requests, public_key)


async def test_422_without_duplicate_is_safe_rejection(public_key: str) -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(200, json=[])
        return httpx.Response(422, json={"message": TOKEN, "errors": ["private-key-marker"]})

    with pytest.raises(GitHubAPIError) as caught:
        await GitHubClient(transport=httpx.MockTransport(respond)).register_public_key(
            public_key=public_key, api_token=TOKEN
        )

    assert_safe_error(caught.value, REJECTED_KEY, 422)
    assert [request.method for request in requests] == ["GET", "POST", "GET"]
    assert_request_boundary(requests, public_key)


@pytest.mark.parametrize("phase", ["list", "create", "recheck"])
@pytest.mark.parametrize(
    ("status", "detail"),
    [
        (401, "GitHub API token is invalid or expired"),
        (403, "GitHub denied the request; check token permissions and rate limits"),
        (429, "GitHub denied the request; check token permissions and rate limits"),
        (500, INVALID_RESPONSE),
        (302, INVALID_RESPONSE),
    ],
)
async def test_upstream_errors_never_become_workspace_401_or_leak_body(
    public_key: str, phase: str, status: int, detail: str
) -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        failing_request = (
            phase == "list"
            or (phase == "create" and request.method == "POST")
            or (phase == "recheck" and len(requests) == 3)
        )
        if failing_request:
            return httpx.Response(
                status,
                json={"message": TOKEN, "private_key": "private-key-marker"},
                headers={"Location": "https://attacker.invalid/steal", "X-Secret": TOKEN},
            )
        if request.method == "POST":
            return httpx.Response(422, json={"message": TOKEN})
        return httpx.Response(200, json=[])

    with pytest.raises(GitHubAPIError) as caught:
        await GitHubClient(transport=httpx.MockTransport(respond)).register_public_key(
            public_key=public_key, api_token=TOKEN
        )

    assert_safe_error(caught.value, detail, 502)
    assert len(requests) == {"list": 1, "create": 2, "recheck": 3}[phase]
    assert_request_boundary(requests, public_key)


@pytest.mark.parametrize("phase", ["list", "create", "recheck"])
async def test_bad_json_is_safe_invalid_response(public_key: str, phase: str) -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        failing_request = (
            phase == "list"
            or (phase == "create" and request.method == "POST")
            or (phase == "recheck" and len(requests) == 3)
        )
        if failing_request:
            return httpx.Response(201 if phase == "create" else 200, content=f"not-json-{TOKEN}")
        if request.method == "POST":
            return httpx.Response(422)
        return httpx.Response(200, json=[])

    with pytest.raises(GitHubAPIError) as caught:
        await GitHubClient(transport=httpx.MockTransport(respond)).register_public_key(
            public_key=public_key, api_token=TOKEN
        )

    assert_safe_error(caught.value, INVALID_RESPONSE, 502)
    assert len(requests) == {"list": 1, "create": 2, "recheck": 3}[phase]
    assert_request_boundary(requests, public_key)


@pytest.mark.parametrize(
    "row",
    [
        None, [], "row", {}, {"id": 1}, {"key": "ssh-ed25519 data"},
        {"id": True, "key": "ssh-ed25519 data"},
        {"id": 0, "key": "ssh-ed25519 data"},
        {"id": -1, "key": "ssh-ed25519 data"},
        {"id": "1", "key": "ssh-ed25519 data"},
        {"id": 1.5, "key": "ssh-ed25519 data"},
        {"id": 1, "key": None}, {"id": 1, "key": 123},
        {"id": 1, "key": ""}, {"id": 1, "key": "ssh-ed25519"},
    ],
)
@pytest.mark.parametrize("phase", ["list", "create"])
async def test_invalid_key_rows_are_rejected(public_key: str, row: object, phase: str) -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if phase == "list":
            return httpx.Response(200, json=[row])
        if request.method == "GET":
            return httpx.Response(200, json=[])
        return httpx.Response(201, json=row)

    with pytest.raises(GitHubAPIError) as caught:
        await GitHubClient(transport=httpx.MockTransport(respond)).register_public_key(
            public_key=public_key, api_token=TOKEN
        )

    assert_safe_error(caught.value, INVALID_RESPONSE, 502)
    assert len(requests) == (1 if phase == "list" else 2)
    assert_request_boundary(requests, public_key)


@pytest.mark.parametrize("body", [None, {}, {"keys": []}, "keys", 100])
async def test_list_requires_array(public_key: str, body: object) -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, content=json.dumps(body), headers={"Content-Type": "application/json"})

    with pytest.raises(GitHubAPIError) as caught:
        await GitHubClient(transport=httpx.MockTransport(respond)).register_public_key(
            public_key=public_key, api_token=TOKEN
        )

    assert_safe_error(caught.value, INVALID_RESPONSE, 502)
    assert len(requests) == 1
    assert_request_boundary(requests, public_key)


async def test_created_key_must_match_requested_public_key(public_key: str) -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(200, json=[])
        return httpx.Response(201, json={"id": 9, "key": "ssh-ed25519 different-data"})

    with pytest.raises(GitHubAPIError) as caught:
        await GitHubClient(transport=httpx.MockTransport(respond)).register_public_key(
            public_key=public_key, api_token=TOKEN
        )

    assert_safe_error(caught.value, INVALID_RESPONSE, 502)
    assert [request.method for request in requests] == ["GET", "POST"]
    assert_request_boundary(requests, public_key)


@pytest.mark.parametrize("phase", ["list", "create"])
@pytest.mark.parametrize("failure", [httpx.ConnectError, httpx.ReadTimeout])
async def test_network_failure_never_retries_write(
    public_key: str, phase: str, failure: type[httpx.RequestError]
) -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if phase == "list" or request.method == "POST":
            raise failure(f"upstream-secret-{TOKEN}", request=request)
        return httpx.Response(200, json=[])

    with pytest.raises(GitHubAPIError) as caught:
        await GitHubClient(transport=httpx.MockTransport(respond)).register_public_key(
            public_key=public_key, api_token=TOKEN
        )

    assert_safe_error(caught.value, NETWORK_ERROR, 502)
    assert [request.method for request in requests] == (
        ["GET"] if phase == "list" else ["GET", "POST"]
    )
    assert_request_boundary(requests, public_key)


async def test_post_timeout_next_invocation_queries_and_converges(public_key: str) -> None:
    requests: list[httpx.Request] = []
    saved_keys: list[dict[str, object]] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(200, json=saved_keys)
        saved_keys.append({"id": 55, "key": public_key})
        raise httpx.ReadTimeout(f"response-lost-{TOKEN}", request=request)

    client = GitHubClient(transport=httpx.MockTransport(respond))
    with pytest.raises(GitHubAPIError) as caught:
        await client.register_public_key(public_key=public_key, api_token=TOKEN)

    assert_safe_error(caught.value, NETWORK_ERROR, 502)
    assert [request.method for request in requests] == ["GET", "POST"]

    result = await client.register_public_key(public_key=public_key, api_token=TOKEN)

    assert result.model_dump() == {
        "status": "already_exists", "key_id": 55, "public_key": public_key
    }
    assert saved_keys == [{"id": 55, "key": public_key}]
    assert [request.method for request in requests] == ["GET", "POST", "GET"]
    assert_request_boundary(requests, public_key)
