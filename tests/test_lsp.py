from __future__ import annotations

import asyncio
import json

import pytest

from test_environments import node_project, prepared_workspace, python_project, real_docker

PYTHON_FILES = {
    'pyproject.toml': python_project(),
    'library.py': 'def greeting(name: str) -> str:\n    return "Hello " + name\n',
    'consumer.py': (
        'from library import greeting\n'
        'message = "😀"; result = greeting("world")\n'
        'invalid: int = "wrong"\n'
    ),
}
TS_FILES = {
    'package.json': node_project(engines={'node': '>=22 <23'}),
    'tsconfig.json': json.dumps({'compilerOptions': {
        'strict': True, 'target': 'ES2022', 'module': 'ESNext',
        'moduleResolution': 'Bundler', 'jsx': 'react-jsx', 'noEmit': True,
    }, 'include': ['*.ts', '*.tsx', '*.vue']}),
    'library.ts': 'export function greeting(name: string): string { return "Hello " + name; }\n',
    'consumer.ts': (
        'import { greeting } from "./library";\n'
        'const emoji = "😀"; export const result = greeting("world");\n'
        'const invalid: number = "wrong";\n'
    ),
}
REACT_FILES = {
    **TS_FILES,
    'package.json': node_project(engines={'node': '>=22 <23'}, dependencies={
        'react': '19.1.0', '@types/react': '19.1.0',
    }),
    'App.tsx': (
        'import { greeting } from "./library";\n'
        'export function App() { return <div>{greeting("React")}</div>; }\n'
        'const invalid: number = "wrong";\n'
    ),
}
VUE_FILES = {
    **TS_FILES,
    'package.json': node_project(engines={'node': '>=22 <23'}, dependencies={'vue': '3.5.13'}),
    'App.vue': (
        '<script setup lang="ts">\n'
        'import { greeting } from "./library";\n'
        'const message = greeting("Vue");\n'
        'const invalid: number = "wrong";\n'
        '</script>\n'
        '<template><div>{{ message }}</div></template>\n'
    ),
}


def position(text: str, needle: str, *, occurrence: int = 0) -> dict[str, int]:
    """LSP characters are UTF-16 code units, not Python Unicode code points."""
    offset = -1
    for _ in range(occurrence + 1):
        offset = text.index(needle, offset + 1)
    prefix = text[:offset]
    line = prefix.count('\n')
    character = len(prefix.rsplit('\n', 1)[-1].encode('utf-16-le')) // 2
    return {'line': line, 'character': character}


def test_fixture_positions_account_for_non_bmp_characters():
    location = position(PYTHON_FILES['consumer.py'], 'greeting', occurrence=1)
    assert location == {'line': 1, 'character': 25}
    assert location['character'] != PYTHON_FILES['consumer.py'].splitlines()[1].index('greeting')


def test_public_unicode_positions_round_trip_and_reject_surrogate_split():
    from repopilot.execution.lsp.positions import from_lsp_position, to_lsp_position

    text = 'a😀é symbol\nnext\n'
    assert to_lsp_position(text, 1, 5) == {'line': 0, 'character': 5}
    assert from_lsp_position(text, {'line': 0, 'character': 5}) == {'line': 1, 'column': 5}
    with pytest.raises(ValueError):
        from_lsp_position(text, {'line': 0, 'character': 2})
    with pytest.raises(ValueError):
        to_lsp_position(text, 0, 1)
    with pytest.raises(ValueError):
        to_lsp_position(text, 1, 100)


def test_utf16_text_edits_preserve_emoji_and_apply_multiple_edits():
    from repopilot.execution.lsp.positions import apply_text_edits

    text = '😀 first second\n'
    edits = [
        {'range': {'start': {'line': 0, 'character': 3}, 'end': {'line': 0, 'character': 8}}, 'newText': 'alpha'},
        {'range': {'start': {'line': 0, 'character': 9}, 'end': {'line': 0, 'character': 15}}, 'newText': 'beta'},
    ]
    assert apply_text_edits(text, edits) == '😀 alpha beta\n'


def test_overlapping_lsp_edits_are_not_partially_applied():
    from repopilot.execution.lsp.positions import apply_text_edits

    edits = [
        {'range': {'start': {'line': 0, 'character': 0}, 'end': {'line': 0, 'character': 3}}, 'newText': 'one'},
        {'range': {'start': {'line': 0, 'character': 2}, 'end': {'line': 0, 'character': 4}}, 'newText': 'two'},
    ]
    with pytest.raises(ValueError):
        apply_text_edits('abcdef', edits)


def public_position(text: str, needle: str, *, occurrence: int = 0) -> dict[str, int]:
    offset = -1
    for _ in range(occurrence + 1):
        offset = text.index(needle, offset + 1)
    prefix = text[:offset]
    return {'line': prefix.count('\n') + 1, 'column': len(prefix.rsplit('\n', 1)[-1]) + 1}


async def lsp(workspace, name: str, **arguments):
    result = await workspace.lsp_tool('lsp_' + name, arguments)
    assert result['status'] == 'ok', result
    return result


async def diagnostics(workspace, path: str):
    # Real language servers publish asynchronously; not_ready is not empty success.
    for _ in range(3):
        result = await workspace.lsp_tool('lsp_diagnostics', {'path': path})
        if result['status'] != 'not_ready':
            assert result['status'] == 'ok', result
            return result
        await asyncio.sleep(0.1)
    pytest.fail(f'Language server never published diagnostics for {path}')


@real_docker
@pytest.mark.asyncio
@pytest.mark.parametrize('files,path,occurrence', [
    (PYTHON_FILES, 'consumer.py', 1),
    (TS_FILES, 'consumer.ts', 1),
    (REACT_FILES, 'App.tsx', 1),
    (VUE_FILES, 'App.vue', 1),
], ids=['python', 'typescript', 'react', 'vue'])
async def test_real_definitions_references_diagnostics_and_crossfile_rename(files, path, occurrence):
    async with prepared_workspace(files) as workspace:
        info = await workspace.environment_info()
        assert all(project['ready'] for project in info['projects']), info
        point = public_position(files[path], 'greeting', occurrence=occurrence)
        definition = await lsp(workspace, 'definition', path=path, **point)
        library = 'library.py' if path.endswith('.py') else 'library.ts'
        assert any(item.get('path') == library for item in definition['data']), definition
        references = await lsp(workspace, 'references', path=path, include_declaration=True, **point)
        assert any(item.get('path') == path for item in references['data']), references
        assert any(item.get('path') == library for item in references['data']), references
        reports = await diagnostics(workspace, path)
        assert any(report['diagnostics'] for report in reports['data']), reports
        # Import usages may rename only their local binding in TypeScript.
        # Renaming the exported declaration must update cross-file consumers.
        renamed = await lsp(workspace, 'rename', path=library, new_name='welcome',
                            **public_position(files[library], 'greeting'))
        assert isinstance(renamed['plan_id'], str) and renamed['plan_id']
        assert renamed['data']['files'] >= 2, renamed
        before = await workspace.tool('read_file', {'path': library})
        assert 'greeting' in before['content'] and 'welcome' not in before['content']
        await lsp(workspace, 'apply_workspace_edit', plan_id=renamed['plan_id'])
        for changed in (path, library):
            current = await workspace.tool('read_file', {'path': changed})
            assert 'welcome' in current['content'], current
            assert 'greeting' not in current['content'], current
        if path in ('consumer.py', 'consumer.ts'):
            assert '😀' in (await workspace.tool('read_file', {'path': path}))['content']


@real_docker
@pytest.mark.asyncio
async def test_real_multi_file_plan_rejects_stale_hash_atomically():
    async with prepared_workspace(PYTHON_FILES) as workspace:
        plan = await lsp(workspace, 'rename', path='consumer.py', new_name='welcome',
                         **public_position(PYTHON_FILES['consumer.py'], 'greeting', occurrence=1))
        original = await workspace.tool('read_file', {'path': 'library.py'})
        await workspace.tool('edit_file', {
            'path': 'library.py', 'expected_sha256': original['sha256'],
            'old_text': '"Hello "', 'new_text': '"Changed "',
        })
        rejected = await workspace.lsp_tool('lsp_apply_workspace_edit', {'plan_id': plan['plan_id']})
        assert rejected['status'] in {'error', 'not_ready'}, rejected
        # A stale hash rejects the entire multi-file edit, not just that file.
        for path in ('library.py', 'consumer.py'):
            text = (await workspace.tool('read_file', {'path': path}))['content']
            assert 'greeting' in text and 'welcome' not in text


@real_docker
@pytest.mark.asyncio
async def test_real_shell_change_restarts_session_and_never_returns_stale_locations():
    async with prepared_workspace(PYTHON_FILES) as workspace:
        point = public_position(PYTHON_FILES['consumer.py'], 'greeting', occurrence=1)
        old = await lsp(workspace, 'definition', path='consumer.py', **point)
        assert old['data'][0]['range']['start']['line'] == 1
        result = await workspace.shell(
            "python3 -c 'from pathlib import Path; p=Path(\"library.py\"); "
            "p.write_text(\"# new line\\n\\n\" + p.read_text())'"
        )
        assert result.exit_code == 0, result.output
        fresh = await lsp(workspace, 'definition', path='consumer.py', **point)
        assert fresh['data'][0]['range']['start']['line'] == 3, fresh


@real_docker
@pytest.mark.asyncio
@pytest.mark.parametrize('path', ['../outside.py', '/etc/passwd', '.git/config', 'linked.py'])
async def test_real_lsp_rejects_unsafe_paths_and_links(path):
    async with prepared_workspace(PYTHON_FILES) as workspace:
        result = await workspace.shell('ln -s /etc/passwd linked.py')
        assert result.exit_code == 0, result.output
        rejected = await workspace.lsp_tool('lsp_definition', {'path': path, 'line': 1, 'column': 1})
        assert rejected['status'] in {'error', 'not_ready'}, rejected


@real_docker
@pytest.mark.asyncio
async def test_real_monorepo_routes_to_innermost_python_and_node_projects():
    files = {f'backend/{name}': text for name, text in PYTHON_FILES.items()}
    files.update({f'frontend/{name}': text for name, text in TS_FILES.items()})
    async with prepared_workspace(files) as workspace:
        for root, filename, fixture, library in (
            ('backend', 'consumer.py', PYTHON_FILES, 'library.py'),
            ('frontend', 'consumer.ts', TS_FILES, 'library.ts'),
        ):
            result = await lsp(workspace, 'definition', path=f'{root}/{filename}',
                               **public_position(fixture[filename], 'greeting', occurrence=1))
            assert any(item.get('path') == f'{root}/{library}' for item in result['data']), result


@real_docker
@pytest.mark.asyncio
async def test_real_manifest_free_lsp_reports_unavailable_not_empty_success():
    async with prepared_workspace({'hello.py': 'value = 1\n'}) as workspace:
        result = await workspace.lsp_tool('lsp_hover', {'path': 'hello.py', 'line': 1, 'column': 1})
        assert result['status'] in {'unsupported', 'not_ready'}, result
        assert result.get('reason') or result.get('message') or result.get('error') or result.get('data')


class ControlledWriter:
    """A byte transport, not a mocked language server or semantic result."""
    def __init__(self):
        self.messages = asyncio.Queue()
        self.closed = False

    def write(self, data: bytes):
        header, body = data.split(b'\r\n\r\n', 1)
        assert int(header.split(b':', 1)[1]) == len(body)
        self.messages.put_nowait(json.loads(body))

    async def drain(self):
        pass

    def close(self):
        self.closed = True

    async def wait_closed(self):
        pass


def feed_message(reader: asyncio.StreamReader, message):
    body = json.dumps(message, ensure_ascii=False).encode()
    reader.feed_data(f'Content-Length: {len(body)}\r\n\r\n'.encode() + body)


@pytest.mark.asyncio
async def test_protocol_matches_ids_not_arrival_order():
    from repopilot.execution.lsp.protocol import JsonRpcClient

    reader, writer = asyncio.StreamReader(), ControlledWriter()
    client = JsonRpcClient(reader, writer)
    try:
        first = asyncio.create_task(client.request('first', {}))
        second = asyncio.create_task(client.request('second', {}))
        one = await asyncio.wait_for(writer.messages.get(), 1)
        two = await asyncio.wait_for(writer.messages.get(), 1)
        assert one['id'] != two['id']
        feed_message(reader, {'jsonrpc': '2.0', 'id': two['id'], 'result': 'second result'})
        feed_message(reader, {'jsonrpc': '2.0', 'id': one['id'], 'result': 'first result'})
        assert await asyncio.wait_for(first, 1) == 'first result'
        assert await asyncio.wait_for(second, 1) == 'second result'
    finally:
        await client.close()
    assert writer.closed


@pytest.mark.asyncio
async def test_protocol_timeout_cancels_and_ignores_late_response():
    from repopilot.execution.lsp.protocol import JsonRpcClient

    reader, writer = asyncio.StreamReader(), ControlledWriter()
    client = JsonRpcClient(reader, writer)
    try:
        pending = asyncio.create_task(client.request('slow', {}, timeout=0.01))
        initial = await writer.messages.get()
        with pytest.raises(TimeoutError):
            await pending
        cancel = await writer.messages.get()
        assert cancel['method'] == '$/cancelRequest'
        assert cancel['params']['id'] == initial['id']
        feed_message(reader, {'jsonrpc': '2.0', 'id': initial['id'], 'result': 'late'})
        fresh = asyncio.create_task(client.request('fresh', {}))
        request = await writer.messages.get()
        feed_message(reader, {'jsonrpc': '2.0', 'id': request['id'], 'result': 'fresh result'})
        assert await asyncio.wait_for(fresh, 1) == 'fresh result'
        assert client._pending == {}
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_protocol_cancellation_cleans_pending_and_sends_cancel():
    from repopilot.execution.lsp.protocol import JsonRpcClient

    reader, writer = asyncio.StreamReader(), ControlledWriter()
    client = JsonRpcClient(reader, writer)
    try:
        pending = asyncio.create_task(client.request('slow', {}))
        request = await writer.messages.get()
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        cancel = await writer.messages.get()
        assert cancel == {'jsonrpc': '2.0', 'method': '$/cancelRequest', 'params': {'id': request['id']}}
        assert client._pending == {}
    finally:
        await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('invalid', [
    {'result': 1, 'error': {'code': -1, 'message': 'both'}},
    {},
    {'error': 'not an error object'},
    {'id': []},
    {'id': 'x' * 1024, 'result': 1},
    {'id': 2 ** 64, 'result': 1},
    {'id': True, 'result': 1},
])
async def test_protocol_invalid_responses_and_ids_fail_pending_boundedly(invalid):
    from repopilot.execution.lsp.protocol import JsonRpcClient, ProtocolError

    reader, writer = asyncio.StreamReader(), ControlledWriter()
    client = JsonRpcClient(reader, writer)
    try:
        pending = asyncio.create_task(client.request('query', {}))
        request = await writer.messages.get()
        feed_message(reader, {'jsonrpc': '2.0', 'id': request['id'], **invalid})
        with pytest.raises(ProtocolError):
            await asyncio.wait_for(pending, 1)
    finally:
        await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('wire', [
    b'Content-Length: 0\r\n\r\n',
    b'Content-Length: 999999999\r\n\r\n',
    b'Content-Length: 2\r\nContent-Length: 2\r\n\r\n{}',
    b'Content-Length: 1\r\n\r\nx',
])
async def test_protocol_invalid_framing_fails_without_unbounded_read(wire):
    from repopilot.execution.lsp.protocol import JsonRpcClient, ProtocolError

    reader, writer = asyncio.StreamReader(), ControlledWriter()
    client = JsonRpcClient(reader, writer)
    try:
        pending = asyncio.create_task(client.request('query', {}))
        await writer.messages.get()
        reader.feed_data(wire)
        with pytest.raises(ProtocolError):
            await asyncio.wait_for(pending, 1)
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_protocol_server_crash_fails_all_pending_requests_and_closes_writer():
    from repopilot.execution.lsp.protocol import JsonRpcClient, ProtocolError

    reader, writer = asyncio.StreamReader(), ControlledWriter()
    client = JsonRpcClient(reader, writer)
    first = asyncio.create_task(client.request('first', {}))
    second = asyncio.create_task(client.request('second', {}))
    try:
        await writer.messages.get()
        await writer.messages.get()
        reader.feed_eof()
        for pending in (first, second):
            with pytest.raises(ProtocolError):
                await asyncio.wait_for(pending, 1)
        assert client.closed
        assert client._pending == {}
    finally:
        await client.close()
    assert writer.closed


@real_docker
@pytest.mark.asyncio
@pytest.mark.parametrize('unsafe', ['../outside.py', '/etc/passwd', '.git/config', 'linked.py'])
async def test_real_apply_helper_rejects_unsafe_plan_paths(unsafe):
    from repopilot.execution.workspace import ToolError

    async with prepared_workspace(PYTHON_FILES) as workspace:
        result = await workspace.shell('ln -s /etc/passwd linked.py')
        assert result.exit_code == 0, result.output
        original = await workspace.tool('read_file', {'path': 'library.py'})
        with pytest.raises(ToolError):
            await workspace.helper_action('lsp_apply', {
                'operations': [
                    {'kind': 'text', 'path': 'library.py', 'content': 'SHOULD NOT BE WRITTEN\n',
                     'expected_sha256': original['sha256']},
                    {'kind': 'create', 'path': unsafe, 'content': 'unsafe\n'},
                ],
                'expected': {'library.py': original['sha256']},
            })
        unchanged = await workspace.tool('read_file', {'path': 'library.py'})
        assert unchanged['sha256'] == original['sha256']


@real_docker
@pytest.mark.asyncio
async def test_real_cancelled_semantic_request_closes_language_server_processes(monkeypatch):
    from repopilot.execution.lsp.protocol import JsonRpcClient

    original_request = JsonRpcClient.request
    entered = asyncio.Event()

    async def controlled_request(self, method, params=None, **kwargs):
        if method == 'textDocument/hover':
            entered.set()
            await asyncio.Event().wait()
        return await original_request(self, method, params, **kwargs)

    async with prepared_workspace(PYTHON_FILES) as workspace:
        monkeypatch.setattr(JsonRpcClient, 'request', controlled_request)
        pending = asyncio.create_task(workspace.lsp_tool('lsp_hover', {
            'path': 'consumer.py', **public_position(PYTHON_FILES['consumer.py'], 'greeting', occurrence=1),
        }))
        await asyncio.wait_for(entered.wait(), 30)
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        status = await lsp(workspace, 'status')
        assert not status['data']['sessions'], status
        # Inspect without Shell: Shell's kill boundary would hide a cleanup bug.
        program = (
            'import os,pathlib,json; '
            'markers=("pyright", "typescript-language-server", "vue-language-server"); '
            'print(json.dumps([int(p.name) for p in pathlib.Path("/proc").iterdir() '
            'if p.name.isdigit() and int(p.name)!=os.getpid() '
            'and (p/"cmdline").exists() '
            'and any(m in (p/"cmdline").read_bytes().decode(errors="replace") for m in markers)]))'
        )
        rc, output, error = await workspace._docker(
            'exec', '--user=1000:1000', workspace.container_name,
            'python3', '-I', '-S', '-c', program,
        )
        assert rc == 0, error
        assert json.loads(output) == []


@real_docker
@pytest.mark.asyncio
async def test_real_absent_capability_is_unsupported_not_empty_success():
    async with prepared_workspace(PYTHON_FILES) as workspace:
        point = public_position(PYTHON_FILES['consumer.py'], 'greeting', occurrence=1)
        await lsp(workspace, 'hover', path='consumer.py', **point)
        session = next(iter(workspace._lsp_manager.sessions.values()))
        # Simulate a negotiated absence, without substituting semantic responses.
        session.capabilities.pop('implementationProvider', None)
        result = await workspace.lsp_tool('lsp_implementation', {'path': 'consumer.py', **point})
        assert result['status'] == 'unsupported', result
        assert result.get('message')


@real_docker
@pytest.mark.asyncio
async def test_real_python_navigation_symbols_calls_and_code_action_listing():
    async with prepared_workspace(PYTHON_FILES) as workspace:
        point = public_position(PYTHON_FILES['consumer.py'], 'greeting', occurrence=1)
        hover = await lsp(workspace, 'hover', path='consumer.py', **point)
        assert hover['data'], hover
        symbols = await lsp(workspace, 'document_symbols', path='library.py')
        assert any(item.get('name') == 'greeting' for item in symbols['data']), symbols
        found = await lsp(workspace, 'workspace_symbols', path='library.py', query='greeting')
        assert any(item.get('name') == 'greeting' for item in found['data']), found
        calls = await workspace.lsp_tool('lsp_call_hierarchy', {'path': 'library.py', 'line': 1, 'column': 5})
        assert calls['status'] in {'ok', 'unsupported'}, calls
        if calls['status'] == 'ok':
            assert calls['data'], calls
        actions = await workspace.lsp_tool('lsp_code_actions', {
            'path': 'consumer.py', 'line': 3, 'column': 1, 'end_line': 3, 'end_column': 23,
        })
        assert actions['status'] in {'ok', 'unsupported'}, actions
        if actions['status'] == 'ok':
            assert isinstance(actions['data']['actions'], list)
            assert all(isinstance(item['action_id'], str) for item in actions['data']['actions'])


@real_docker
@pytest.mark.asyncio
async def test_real_typescript_format_returns_reviewable_plan_then_applies():
    files = {**TS_FILES, 'messy.ts': 'export const answer={value:42,label:"fixture"};\n'}
    async with prepared_workspace(files) as workspace:
        formatted = await lsp(workspace, 'format', path='messy.ts', tab_size=2, insert_spaces=True)
        assert formatted['plan_id'], formatted
        assert formatted['data']['files'] == 1
        before = await workspace.tool('read_file', {'path': 'messy.ts'})
        assert before['content'] == files['messy.ts']
        await lsp(workspace, 'apply_workspace_edit', plan_id=formatted['plan_id'])
        after = await workspace.tool('read_file', {'path': 'messy.ts'})
        assert after['content'] != before['content']
        assert '42' in after['content'] and 'fixture' in after['content']


@pytest.mark.parametrize('path', ['/etc/passwd', '../file.py', '.git/config', 'node_modules/pkg/a.ts', '.venv/x.py', 'a/../b.py'])
def test_lsp_workspace_paths_reject_non_source_boundaries(path):
    from repopilot.execution.lsp.manager import safe_path

    with pytest.raises(ValueError):
        safe_path(path)


@pytest.mark.parametrize('uri', [
    'file:///etc/passwd', 'file:///workspace/../outside.py',
    'file:///workspace/%2e%2e/outside.py', 'file://evil/workspace/file.py',
    'https://example.com/file.py', 'file:///workspace/.git/config',
])
def test_external_or_escaping_edit_uris_are_not_workspace_paths(uri):
    from repopilot.execution.lsp.manager import uri_path

    assert uri_path(uri) is None


@real_docker
@pytest.mark.asyncio
async def test_real_typescript_type_definition_and_implementation():
    files = {
        **TS_FILES,
        'types.ts': 'export interface Greeter { greet(name: string): string; }\n',
        'typed.ts': (
            'import type { Greeter } from "./types";\n'
            'export class Concrete implements Greeter { greet(name: string) { return name; } }\n'
            'export const instance: Greeter = new Concrete();\n'
            'instance.greet("hello");\n'
        ),
    }
    async with prepared_workspace(files) as workspace:
        definition = await lsp(workspace, 'type_definition', path='typed.ts', line=4, column=1)
        assert any(item.get('path') == 'types.ts' for item in definition['data']), definition
        implementation = await lsp(workspace, 'implementation', path='types.ts', line=1, column=18)
        assert any(item.get('path') == 'typed.ts' for item in implementation['data']), implementation


@real_docker
@pytest.mark.asyncio
async def test_real_workspace_plan_rejects_stale_document_version_before_application():
    async with prepared_workspace(PYTHON_FILES) as workspace:
        await lsp(workspace, 'hover', path='consumer.py', line=1, column=21)
        manager = workspace._lsp_manager
        snapshot = await manager._snapshot()
        with pytest.raises(ValueError, match='version.*stale'):
            await manager._plan({'documentChanges': [{
                'textDocument': {'uri': 'file:///workspace/consumer.py', 'version': 999},
                'edits': [{'range': {'start': {'line': 0, 'character': 0},
                                     'end': {'line': 0, 'character': 0}},
                           'newText': '# not applied\n'}],
            }]}, snapshot)
        current = await workspace.tool('read_file', {'path': 'consumer.py'})
        assert current['content'] == PYTHON_FILES['consumer.py']


@real_docker
@pytest.mark.asyncio
async def test_real_python_format_uses_ruff_and_applies_version_checked_plan():
    files = {**PYTHON_FILES, 'messy.py': 'answer={"value":42,"label":"fixture"}\n'}
    async with prepared_workspace(files) as workspace:
        formatted = await lsp(workspace, 'format', path='messy.py')
        assert formatted['plan_id'], formatted
        assert formatted['data']['files'] == 1
        before = await workspace.tool('read_file', {'path': 'messy.py'})
        assert before['content'] == files['messy.py']
        await lsp(workspace, 'apply_workspace_edit', plan_id=formatted['plan_id'])
        after = await workspace.tool('read_file', {'path': 'messy.py'})
        assert after['content'] != before['content']
        assert '42' in after['content'] and 'fixture' in after['content']


def test_monorepo_routing_chooses_innermost_manifest_for_document_language():
    from repopilot.execution.lsp import LspManager

    manager = LspManager(None)
    projects = [
        {'root': '.', 'language': 'python'},
        {'root': 'apps', 'language': 'typescript'},
        {'root': 'apps/web', 'language': 'vue'},
        {'root': 'apps/web/api', 'language': 'python'},
    ]
    assert manager._project(projects, 'apps/web/App.vue') == projects[2]
    assert manager._project(projects, 'apps/web/utils.ts') == projects[2]
    assert manager._project(projects, 'apps/web/api/main.py') == projects[3]
    assert manager._project(projects, 'apps/web/api/client.ts') == projects[2]
    assert manager._project(projects, 'apps/web-other/index.ts') == projects[1]
    assert manager._project(projects, 'library.py') == projects[0]


@pytest.mark.asyncio
async def test_protocol_server_error_is_not_empty_success():
    from repopilot.execution.lsp.protocol import JsonRpcClient, JsonRpcError

    reader, writer = asyncio.StreamReader(), ControlledWriter()
    client = JsonRpcClient(reader, writer)
    try:
        pending = asyncio.create_task(client.request('textDocument/hover', {}))
        request = await writer.messages.get()
        feed_message(reader, {'jsonrpc': '2.0', 'id': request['id'],
                              'error': {'code': -32601, 'message': 'Method not supported'}})
        with pytest.raises(JsonRpcError) as caught:
            await asyncio.wait_for(pending, 1)
        assert caught.value.code == -32601
        assert 'not supported' in str(caught.value)
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_code_action_command_cannot_be_discarded_to_claim_edit_success():
    from repopilot.execution.lsp import LspManager
    from repopilot.execution.lsp.session import Session

    manager = LspManager(None)
    session = Session(None, {'root': '.', 'language': 'typescript'})
    manager.actions['opaque-action'] = {
        'session': session, 'snapshot': {},
        'action': {
            'title': 'Command plus edit',
            'command': {'command': 'untrusted.server.command', 'arguments': ['touch /workspace/file']},
            'edit': {'changes': {'file:///workspace/file.ts': []}},
        },
    }
    result = await manager._resolve_action('opaque-action', {})
    assert result['status'] == 'unsupported', result
    assert result['command'] == 'untrusted.server.command'
    assert manager.plans == {}


@pytest.mark.asyncio
async def test_code_action_ids_are_opaque_and_expire_on_workspace_change():
    from repopilot.execution.lsp import LspManager

    manager = LspManager(None)
    manager.actions['opaque-action'] = {'snapshot': {'file.ts': 'old-hash'}}
    with pytest.raises(ValueError, match='stale'):
        await manager._resolve_action('opaque-action', {'file.ts': 'new-hash'})
    with pytest.raises(ValueError, match='expired'):
        await manager._resolve_action('not-issued', {})


@real_docker
@pytest.mark.asyncio
@pytest.mark.parametrize('path,text,needle,bad_path,bad_text', [
    ('index.html', '<!DOCTYPE html><html><body><div>fixture</div></body></html>\n',
     'div', 'broken.html', '<html><head><style>.widget { color: ; }</style></head><body></body></html>\n'),
    ('style.css', '.widget{color:red;width:42px;}\n',
     'color', 'broken.css', '.widget { color: ; }\n'),
    ('document.json', '{"$schema":"./schema.json","value":42}\n',
     'value', 'broken.json', '{"value": }\n'),
], ids=['html', 'css', 'json'])
async def test_real_frontend_document_hover_diagnostics_and_format(path, text, needle, bad_path, bad_text):
    files = {
        'package.json': node_project(engines={'node': '>=22 <23'}),
        path: text,
        bad_path: bad_text,
        'schema.json': json.dumps({
            'type': 'object', 'properties': {'value': {
                'type': 'number', 'description': 'Fixture numeric value',
            }},
        }),
    }
    async with prepared_workspace(files) as workspace:
        hovered = await lsp(workspace, 'hover', path=path, **public_position(text, needle))
        assert hovered['data'], hovered
        reports = await diagnostics(workspace, bad_path)
        assert reports['data'], reports
        assert all(report['path'] == bad_path and report['version'] >= 1 for report in reports['data'])
        if path.endswith(('.css', '.json')):
            assert any(report['diagnostics'] for report in reports['data']), reports
        formatted = await lsp(workspace, 'format', path=path, tab_size=2, insert_spaces=True)
        assert formatted['plan_id'], formatted
        original = await workspace.tool('read_file', {'path': path})
        assert original['content'] == text
        await lsp(workspace, 'apply_workspace_edit', plan_id=formatted['plan_id'])
        changed = await workspace.tool('read_file', {'path': path})
        assert changed['content'] != text
        assert '42' in changed['content'] if path.endswith(('.css', '.json')) else 'fixture' in changed['content']


@real_docker
@pytest.mark.asyncio
@pytest.mark.parametrize('files,path', [(TS_FILES, 'consumer.ts'), (REACT_FILES, 'App.tsx')],
                         ids=['typescript', 'react'])
async def test_real_typescript_import_binding_rename_preserves_export(files, path):
    async with prepared_workspace(files) as workspace:
        plan = await lsp(workspace, 'rename', path=path, new_name='localWelcome',
                         **public_position(files[path], 'greeting', occurrence=1))
        assert plan['data']['files'] == 1, plan
        assert all(operation['path'] == path for operation in plan['data']['operations']), plan
        await lsp(workspace, 'apply_workspace_edit', plan_id=plan['plan_id'])
        library = await workspace.tool('read_file', {'path': 'library.ts'})
        assert library['content'] == files['library.ts']
        consumer = await workspace.tool('read_file', {'path': path})
        assert 'greeting as localWelcome' in consumer['content'], consumer
        assert 'localWelcome(' in consumer['content'], consumer
