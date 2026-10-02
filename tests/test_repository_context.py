from __future__ import annotations

import asyncio
import hashlib

import httpx
import pytest
from pydantic import ValidationError

from repopilot.domain.context import ContextFile, RepositoryContext
from repopilot.domain.tasks import CreateTask, TaskError
from repopilot.integration.context import (
    BLOB_DOWNLOAD_LIMIT,
    ENTRY_FILES,
    FILE_CONTENT_LIMIT,
    TOTAL_CONTENT_LIMIT,
    TREE_ENTRY_LIMIT,
    TREE_RESPONSE_LIMIT,
    RepositoryContextClient,
)
from repopilot.integration.task_sources import GitHubSourceClient

pytestmark = pytest.mark.asyncio
COMMIT = 'a' * 40
TOKEN = 'context-test-secret'


def payload() -> CreateTask:
    return CreateTask(repository_url='https://github.com/example/project',
                      baseline_commit=COMMIT,
                      issue_url='https://github.com/example/project/issues/7')


def blob_sha(content: bytes) -> str:
    return hashlib.sha1(b'blob ' + str(len(content)).encode() + b'\0' + content).hexdigest()


def fixture(files: dict[str, bytes], *, extra: list[dict] | None = None, truncated=False):
    entries = [
        {'path': path, 'type': 'blob', 'mode': '100644',
         'sha': blob_sha(content), 'size': len(content)}
        for path, content in files.items()
    ]
    entries.extend(extra or [])
    tree = {'sha': 'b' * 40, 'truncated': truncated, 'tree': entries}
    blobs = {blob_sha(content): content for content in files.values()}
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.url.host == 'api.github.com'
        assert request.url.scheme == 'https'
        assert request.headers['Authorization'] == f'Bearer {TOKEN}'
        if '/git/trees/' in request.url.path:
            assert request.url.path.endswith('/' + COMMIT)
            return httpx.Response(200, json=tree)
        if '/git/blobs/' in request.url.path:
            return httpx.Response(200, content=blobs[request.url.path.rsplit('/', 1)[1]])
        pytest.fail(f'Unexpected URL: {request.url}')

    return tree, blobs, requests, respond


async def test_context_reads_applicable_conventions_and_real_source_without_secrets():
    files = {
        'AGENTS.md': b'Root rules\n', 'README.md': b'Project overview\n',
        'pyproject.toml': b'[project]\nname="fixture"\n',
        'src/AGENTS.md': b'Source rules\n', 'src/pkg/AGENTS.md': b'Package rules\n',
        'src/pkg/calc.py': b'def add(a, b): return a - b\n',
        '.env': b'SECRET=private\n', 'node_modules/lib.js': b'private dependency\n',
        'credentials.json': b'private credentials\n',
        'src/sample.py': b'# deterministic sample\n',
    }
    links = [
        {'path': 'src/link.py', 'mode': '120000', 'type': 'blob', 'sha': 'c' * 40},
        {'path': 'submodule', 'mode': '160000', 'type': 'commit', 'sha': 'd' * 40},
    ]
    _, _, requests, respond = fixture(files, extra=links)
    context = await RepositoryContextClient(transport=httpx.MockTransport(respond)).fetch(
        payload(), TOKEN, 'Fix `src/pkg/calc.py` and inspect src/link.py',
    )
    actual = {item.path: item for item in context.files}
    assert actual['src/pkg/calc.py'].content == files['src/pkg/calc.py'].decode()
    conventions = [item.path for item in context.files if item.path.endswith('AGENTS.md')]
    assert conventions == ['AGENTS.md', 'src/AGENTS.md', 'src/pkg/AGENTS.md']
    assert actual['src/sample.py'].reason == '按路径排序的源码样本，不代表已完成问题定位'
    assert all(path not in context.tree for path in ('.env', 'src/link.py', 'submodule'))
    assert 'private' not in context.model_dump_json()
    assert any('跳过' in item for item in context.omissions)
    assert context.commit == COMMIT
    assert not context.tree_truncated
    assert all(request.url.host == 'api.github.com' for request in requests)


async def test_issue_directory_selects_its_conventions_and_samples_are_bounded():
    files = {'src/AGENTS.md': b'Rules\n', **{
        f'src/{index}.py': f'number = {index}\n'.encode() for index in range(8)
    }}
    directory = {'path': 'src', 'mode': '040000', 'type': 'tree', 'sha': 'c' * 40}
    _, _, _, respond = fixture(files, extra=[directory])
    context = await RepositoryContextClient(transport=httpx.MockTransport(respond)).fetch(
        payload(), TOKEN, 'Problem in `src`',
    )
    assert context.files[0].path == 'src/AGENTS.md'
    assert [item.path for item in context.files[1:]] == ['src/0.py', 'src/1.py', 'src/2.py']


async def test_content_budgets_are_visible_and_utf8_is_not_split():
    content = ('汉' * (FILE_CONTENT_LIMIT // 3 + 30)).encode()
    files = {'AGENTS.md': content, 'README.md': content,
             **{name: content for name in ENTRY_FILES}}
    _, _, requests, respond = fixture(files)
    context = await RepositoryContextClient(transport=httpx.MockTransport(respond)).fetch(
        payload(), TOKEN, '',
    )
    assert sum(len(item.content.encode()) for item in context.files) <= TOTAL_CONTENT_LIMIT
    assert all(len(item.content.encode()) <= FILE_CONTENT_LIMIT for item in context.files)
    assert all(item.truncated for item in context.files)
    assert all('�' not in item.content for item in context.files)
    assert any('截断' in omission for omission in context.omissions)
    assert any('预算已用尽' in omission for omission in context.omissions)
    assert len([request for request in requests if '/blobs/' in request.url.path]) <= 12


async def test_blob_count_limit_does_not_inject_extra_files():
    files = {'AGENTS.md': b'root', 'README.md': b'readme',
             **{name: name.encode() for name in ENTRY_FILES},
             'first.py': b'first', 'second.py': b'second', 'third.py': b'third'}
    _, _, requests, respond = fixture(files)
    context = await RepositoryContextClient(transport=httpx.MockTransport(respond)).fetch(
        payload(), TOKEN, 'first.py second.py third.py',
    )
    assert len(context.files) == 12
    assert 'second.py' not in {item.path for item in context.files}
    assert any('second.py' in item and '预算' in item for item in context.omissions)
    assert len([request for request in requests if '/blobs/' in request.url.path]) == 12


async def test_tree_entry_and_saved_path_limits_are_explicit():
    entries = [{'path': f'files/{index:05}.py', 'mode': '100644', 'type': 'blob',
                'sha': 'c' * 40, 'size': 1} for index in range(TREE_ENTRY_LIMIT + 1)]
    _, _, _, respond = fixture({}, extra=entries, truncated=True)
    context = await RepositoryContextClient(transport=httpx.MockTransport(respond)).fetch(
        payload(), TOKEN, '',
    )
    assert len(context.tree) == 500
    assert context.tree_truncated
    assert any('10000' in item for item in context.omissions)
    assert any('500' in item for item in context.omissions)
    assert any('GitHub' in item and '截断' in item for item in context.omissions)


@pytest.mark.parametrize('content', [b'\xff', b'abc\0def', b'abc\x01def'])
async def test_non_text_blobs_are_omitted(content):
    _, _, _, respond = fixture({'README.md': content})
    context = await RepositoryContextClient(transport=httpx.MockTransport(respond)).fetch(
        payload(), TOKEN, '',
    )
    assert context.files == []
    assert any('README.md' in omission for omission in context.omissions)


@pytest.mark.parametrize('path', [
    '../escape.py', '/absolute.py', 'src/../escape.py', 'src\\escape.py',
    'src/.env.local', '.ssh/id_rsa', 'secret.key', '.aws/config',
    'dist/bundle.js', 'src/\x00bad.py',
])
async def test_unsafe_paths_are_never_requested(path):
    _, _, requests, respond = fixture({path: b'private'})
    context = await RepositoryContextClient(transport=httpx.MockTransport(respond)).fetch(
        payload(), TOKEN, path,
    )
    assert context.tree == []
    assert context.files == []
    assert not any('/blobs/' in request.url.path for request in requests)


async def test_oversize_blobs_are_omitted_before_download():
    _, _, requests, respond = fixture({'README.md': b'x' * (BLOB_DOWNLOAD_LIMIT + 1)})
    context = await RepositoryContextClient(transport=httpx.MockTransport(respond)).fetch(
        payload(), TOKEN, '',
    )
    assert context.files == []
    assert any('64 KiB' in item for item in context.omissions)
    assert not any('/blobs/' in request.url.path for request in requests)


class Chunks(httpx.AsyncByteStream):
    def __init__(self, chunks):
        self.chunks = chunks
        self.consumed = 0

    async def __aiter__(self):
        for chunk in self.chunks:
            self.consumed += 1
            yield chunk


@pytest.mark.parametrize('phase', ['tree', 'blob'])
async def test_stream_limits_stop_reading_excess_bytes(phase):
    tree, _, _, _ = fixture({'README.md': b'initial'})
    limit = TREE_RESPONSE_LIMIT if phase == 'tree' else BLOB_DOWNLOAD_LIMIT
    stream = Chunks([b'x' * limit, b'x', b'must-not-be-read'])

    def transport(request):
        if (phase == 'tree' and '/trees/' in request.url.path) or '/blobs/' in request.url.path:
            return httpx.Response(200, stream=stream)
        return httpx.Response(200, json=tree)

    client = RepositoryContextClient(transport=httpx.MockTransport(transport))
    if phase == 'tree':
        with pytest.raises(TaskError) as caught:
            await client.fetch(payload(), TOKEN, '')
        assert caught.value.status_code == 502
    else:
        context = await client.fetch(payload(), TOKEN, '')
        assert context.files == []
        assert any('下载超过' in item for item in context.omissions)
    assert stream.consumed == 2


async def test_hash_mismatch_is_a_safe_error_not_partial_context():
    _, blobs, _, respond = fixture({'README.md': b'original'})
    blobs[blob_sha(b'original')] = b'tampered'
    with pytest.raises(TaskError) as caught:
        await RepositoryContextClient(transport=httpx.MockTransport(respond)).fetch(payload(), TOKEN, '')
    assert caught.value.status_code == 502
    assert TOKEN not in caught.value.detail
    assert 'tampered' not in caught.value.detail


@pytest.mark.parametrize('tree', [
    None, [], {}, {'sha': 'b' * 40, 'tree': [], 'truncated': 'false'},
    {'sha': 'bad', 'tree': [], 'truncated': False},
    {'sha': 'b' * 40, 'tree': [None], 'truncated': False},
    {'sha': 'b' * 40, 'tree': [{'path': 'README.md'}], 'truncated': False},
])
async def test_invalid_tree_response_is_rejected(tree):
    with pytest.raises(TaskError) as caught:
        await RepositoryContextClient(transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json=tree),
        )).fetch(payload(), TOKEN, '')
    assert caught.value.status_code == 502


@pytest.mark.parametrize('phase', ['tree', 'blob'])
@pytest.mark.parametrize(('status', 'expected'), [(403, 422), (401, 422), (302, 502), (500, 502)])
async def test_context_status_errors_never_follow_redirects_or_leak(phase, status, expected):
    _, _, requests, respond = fixture({'README.md': b'readme'})

    def transport(request):
        if (phase == 'tree' and '/trees/' in request.url.path) or '/blobs/' in request.url.path:
            requests.append(request)
            return httpx.Response(status, text=TOKEN,
                                  headers={'Location': 'https://attacker.invalid/steal'})
        return respond(request)

    with pytest.raises(TaskError) as caught:
        await RepositoryContextClient(transport=httpx.MockTransport(transport)).fetch(payload(), TOKEN, '')
    assert caught.value.status_code == expected
    assert TOKEN not in caught.value.detail
    assert all(request.url.host == 'api.github.com' for request in requests)


@pytest.mark.parametrize(('exception', 'expected'), [
    (httpx.ConnectError, 502), (httpx.ReadTimeout, 504),
])
async def test_context_network_errors_are_safe_and_not_retried(exception, expected):
    calls = []

    def respond(request):
        calls.append(request)
        raise exception(TOKEN)

    with pytest.raises(TaskError) as caught:
        await RepositoryContextClient(transport=httpx.MockTransport(respond)).fetch(payload(), TOKEN, '')
    assert caught.value.status_code == expected
    assert TOKEN not in caught.value.detail
    assert len(calls) == 1


async def test_context_fetch_remains_inside_overall_source_deadline(monkeypatch):
    real_timeout = asyncio.timeout
    entered = asyncio.Event()

    def short_timeout(seconds):
        return real_timeout(0.05 if seconds == 35 else seconds)

    async def respond(request):
        if '/commits/' in request.url.path:
            return httpx.Response(200, json={'sha': COMMIT})
        if '/issues/' in request.url.path:
            return httpx.Response(200, json={
                'number': 7, 'title': 'Issue', 'body': 'Fix',
                'html_url': payload().issue_url, 'updated_at': '2026-10-02T00:00:00Z',
            })
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(asyncio, 'timeout', short_timeout)
    with pytest.raises(TaskError) as caught:
        await GitHubSourceClient(transport=httpx.MockTransport(respond)).fetch(payload(), TOKEN)
    assert entered.is_set()
    assert caught.value.status_code == 504


async def test_context_cancellation_is_not_swallowed():
    async def respond(request):
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await RepositoryContextClient(transport=httpx.MockTransport(respond)).fetch(payload(), TOKEN, '')


async def test_context_contract_rejects_extra_fields():
    with pytest.raises(ValidationError):
        ContextFile(path='file.py', blob_sha='a' * 40, content='', truncated=False,
                    reason='Issue', extra='untrusted')
    with pytest.raises(ValidationError):
        RepositoryContext(commit=COMMIT, tree=[], files=[], omissions=[],
                          tree_truncated=False, extra='untrusted')


async def test_blob_exactly_at_download_limit_is_verified_and_content_truncated():
    content = b'x' * BLOB_DOWNLOAD_LIMIT
    _, _, _, respond = fixture({'README.md': content})
    context = await RepositoryContextClient(transport=httpx.MockTransport(respond)).fetch(
        payload(), TOKEN, '',
    )
    assert context.files[0].blob_sha == blob_sha(content)
    assert context.files[0].content == 'x' * FILE_CONTENT_LIMIT
    assert context.files[0].truncated


@pytest.mark.parametrize('content', [b'not-json', b'\xff', b'[]', b'null'])
async def test_malformed_tree_json_is_safe(content):
    with pytest.raises(TaskError) as caught:
        await RepositoryContextClient(transport=httpx.MockTransport(
            lambda request: httpx.Response(200, content=content),
        )).fetch(payload(), TOKEN, '')
    assert caught.value.status_code == 502
    assert TOKEN not in caught.value.detail
