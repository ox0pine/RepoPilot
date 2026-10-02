from __future__ import annotations

import asyncio
import hashlib
from datetime import datetime, timezone

import httpx
import pytest

from repopilot.domain.tasks import CreateTask, TaskError
from repopilot.integration.task_sources import GitHubSourceClient, ISSUE_BODY_LIMIT


pytestmark = pytest.mark.asyncio
TOKEN = "source-test-token-not-a-real-credential"
SHA = "ab" * 20
UPSTREAM_SECRET = "upstream-private-response-marker"


@pytest.fixture
def payload() -> CreateTask:
    return CreateTask(
        repository_url="https://github.com/Example/Project.git/",
        baseline_commit=SHA.upper(),
        issue_url="https://github.com/Example/Project/issues/7",
    )


def issue_row(**changes: object) -> dict:
    return {
        "number": 7,
        "title": "  修复来源问题  ",
        "body": "Issue 内容，不是系统命令",
        "html_url": "https://github.com/example/project/issues/7",
        "updated_at": "2026-10-02T08:30:00Z",
        "unused_secret": UPSTREAM_SECRET,
        **changes,
    }


def safe_error(error: TaskError, status: int) -> None:
    assert error.status_code == status
    assert error.detail == str(error)
    assert TOKEN not in error.detail
    assert UPSTREAM_SECRET not in error.detail
    assert any("\u4e00" <= char <= "\u9fff" for char in error.detail)


@pytest.mark.parametrize("body", [None, "", "a" * ISSUE_BODY_LIMIT, "汉" * (ISSUE_BODY_LIMIT // 3)])
async def test_fetch_saves_only_validated_snapshot(payload: CreateTask, body: str | None) -> None:
    requests: list[httpx.Request] = []
    before = datetime.now(timezone.utc)

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if "/commits/" in request.url.path:
            return httpx.Response(200, json={"sha": SHA, "unused_secret": UPSTREAM_SECRET})
        if '/git/trees/' in request.url.path:
            content = b'# Project\n'
            blob_sha = hashlib.sha1(b'blob ' + str(len(content)).encode() + b'\0' + content).hexdigest()
            return httpx.Response(200, json={
                'sha': 'c' * 40, 'truncated': False,
                'tree': [{'path': 'README.md', 'type': 'blob', 'mode': '100644',
                          'sha': blob_sha, 'size': len(content)}],
            })
        if '/git/blobs/' in request.url.path:
            return httpx.Response(200, content=b'# Project\n')
        return httpx.Response(200, json=issue_row(body=body))

    snapshot = await GitHubSourceClient(transport=httpx.MockTransport(respond)).fetch(payload, TOKEN)

    assert snapshot.repository_url == "https://github.com/example/project"
    assert snapshot.baseline_commit == SHA
    assert snapshot.issue_number == 7
    assert snapshot.issue_title == "  修复来源问题  "
    assert snapshot.issue_body == (body or "")
    assert snapshot.issue_url == payload.issue_url
    assert snapshot.issue_updated_at == datetime(2026, 10, 2, 8, 30, tzinfo=timezone.utc)
    assert before <= snapshot.fetched_at <= datetime.now(timezone.utc)
    assert UPSTREAM_SECRET not in snapshot.model_dump_json()
    assert TOKEN not in snapshot.model_dump_json()
    assert snapshot.repository_context.commit == SHA
    assert snapshot.repository_context.files[0].content == '# Project\n'
    for request in requests:
        assert request.method == "GET"
        assert request.url.scheme == "https"
        assert request.url.host == "api.github.com"
        assert request.url.port is None
        if '/git/trees/' in request.url.path:
            assert dict(request.url.params) == {'recursive': '1'}
        else:
            assert not request.url.query
        assert not request.content
        assert request.headers["Authorization"] == f"Bearer {TOKEN}"
        expected_accept = ('application/vnd.github.raw+json' if '/git/blobs/' in request.url.path
                           else 'application/vnd.github+json')
        assert request.headers['Accept'] == expected_accept
        assert request.headers["X-GitHub-Api-Version"] == "2026-03-10"
        assert request.headers["User-Agent"] == "RepoPilot"
        assert TOKEN not in str(request.url)


@pytest.mark.parametrize("phase", ["commit", "issue"])
@pytest.mark.parametrize(
    ("status", "expected"),
    [(401, 422), (403, 422), (404, 422), (422, 422), (429, 502), (500, 502), (302, 502)],
)
async def test_status_errors_are_safe_and_never_follow_redirects(
    payload: CreateTask, phase: str, status: int, expected: int
) -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if phase == "commit" or "/issues/" in request.url.path:
            return httpx.Response(
                status,
                json={"message": f"{TOKEN} {UPSTREAM_SECRET}"},
                headers={"Location": "https://attacker.invalid/steal"},
            )
        return httpx.Response(200, json={"sha": SHA})

    with pytest.raises(TaskError) as caught:
        await GitHubSourceClient(transport=httpx.MockTransport(respond)).fetch(payload, TOKEN)
    safe_error(caught.value, expected)
    assert len(requests) == (1 if phase == "commit" else 2)
    assert all(request.url.host == "api.github.com" for request in requests)


@pytest.mark.parametrize("row", [None, [], {}, {"sha": None}, {"sha": 123}, {"sha": "ab"}, {"sha": "cd" * 20}])
async def test_invalid_commit_stops_before_issue(payload: CreateTask, row: object) -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=row)

    with pytest.raises(TaskError) as caught:
        await GitHubSourceClient(transport=httpx.MockTransport(respond)).fetch(payload, TOKEN)
    safe_error(caught.value, 502)
    assert len(requests) == 1


@pytest.mark.parametrize(
    "changes",
    [
        {"number": True}, {"number": "7"}, {"number": 8},
        {"title": None}, {"title": 8}, {"title": "  "},
        {"body": []}, {"body": 12},
        {"html_url": None}, {"html_url": "https://attacker.invalid/issues/7"},
        {"html_url": "https://github.com/example/other/issues/7"},
        {"html_url": "https://github.com/example/project/issues/8"},
        {"html_url": "https://github.com/example/project/issues/7?token=secret"},
        {"updated_at": None}, {"updated_at": 1234567890},
        {"updated_at": "invalid"}, {"updated_at": "2026-10-02T08:30:00"},
    ],
)
async def test_invalid_issue_fields_fail_without_snapshot(payload: CreateTask, changes: dict) -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        assert '/git/' not in request.url.path, 'Invalid Issue must stop before context fetch'
        row = {"sha": SHA} if "/commits/" in request.url.path else issue_row(**changes)
        return httpx.Response(200, json=row)

    with pytest.raises(TaskError) as caught:
        await GitHubSourceClient(transport=httpx.MockTransport(respond)).fetch(payload, TOKEN)
    safe_error(caught.value, 502)


@pytest.mark.parametrize("field", ["number", "title", "body", "html_url", "updated_at"])
async def test_missing_issue_fields_are_not_defaulted(payload: CreateTask, field: str) -> None:
    row = issue_row()
    del row[field]

    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"sha": SHA} if "/commits/" in request.url.path else row)

    with pytest.raises(TaskError) as caught:
        await GitHubSourceClient(transport=httpx.MockTransport(respond)).fetch(payload, TOKEN)
    safe_error(caught.value, 502)


@pytest.mark.parametrize("pull_request", [None, {}, {"url": "https://api.github.com/pulls/7"}])
async def test_pull_request_rejected_even_with_null_marker(payload: CreateTask, pull_request: object) -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        row = {"sha": SHA} if "/commits/" in request.url.path else issue_row(pull_request=pull_request)
        return httpx.Response(200, json=row)

    with pytest.raises(TaskError) as caught:
        await GitHubSourceClient(transport=httpx.MockTransport(respond)).fetch(payload, TOKEN)
    safe_error(caught.value, 422)


@pytest.mark.parametrize("body", ["a" * (ISSUE_BODY_LIMIT + 1), "汉" * (ISSUE_BODY_LIMIT // 3 + 1)])
async def test_oversized_issue_is_rejected_not_truncated(payload: CreateTask, body: str) -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        row = {"sha": SHA} if "/commits/" in request.url.path else issue_row(body=body)
        return httpx.Response(200, json=row)

    with pytest.raises(TaskError) as caught:
        await GitHubSourceClient(transport=httpx.MockTransport(respond)).fetch(payload, TOKEN)
    safe_error(caught.value, 422)


@pytest.mark.parametrize("phase", ["commit", "issue"])
@pytest.mark.parametrize("content", [b"not-json", b"[]", b"null", b"\xff"])
async def test_invalid_json_is_safe(payload: CreateTask, phase: str, content: bytes) -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        if phase == "commit" or "/issues/" in request.url.path:
            return httpx.Response(200, content=content)
        return httpx.Response(200, json={"sha": SHA})

    with pytest.raises(TaskError) as caught:
        await GitHubSourceClient(transport=httpx.MockTransport(respond)).fetch(payload, TOKEN)
    safe_error(caught.value, 502)


@pytest.mark.parametrize("phase", ["commit", "issue"])
@pytest.mark.parametrize(
    ("exception", "status"),
    [(httpx.ConnectTimeout, 504), (httpx.ReadTimeout, 504), (httpx.ConnectError, 502), (TimeoutError, 504)],
)
async def test_network_errors_do_not_leak_or_retry(
    payload: CreateTask, phase: str, exception: type[Exception], status: int
) -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if phase == "commit" or "/issues/" in request.url.path:
            raise exception(f"{TOKEN} {UPSTREAM_SECRET}")
        return httpx.Response(200, json={"sha": SHA})

    with pytest.raises(TaskError) as caught:
        await GitHubSourceClient(transport=httpx.MockTransport(respond)).fetch(payload, TOKEN)
    safe_error(caught.value, status)
    assert len(requests) == (1 if phase == "commit" else 2)


async def test_missing_token_never_contacts_github(payload: CreateTask) -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        pytest.fail("GitHub must not be contacted without credentials")

    with pytest.raises(TaskError) as caught:
        await GitHubSourceClient(transport=httpx.MockTransport(respond)).fetch(payload, "  ")
    safe_error(caught.value, 503)


async def test_cancellation_is_not_swallowed(payload: CreateTask) -> None:
    async def respond(request: httpx.Request) -> httpx.Response:
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await GitHubSourceClient(transport=httpx.MockTransport(respond)).fetch(payload, TOKEN)
