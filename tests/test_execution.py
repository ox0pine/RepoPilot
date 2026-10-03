from __future__ import annotations

import hashlib
import json
import os
import shutil
from collections import deque
from uuid import uuid4

import httpx
import pytest
from test_workspace import TOKEN as GITHUB_TOKEN
from test_workspace import command, real_docker, source
from test_workspace import docker_workspace as docker_workspace

from repopilot.domain.tasks import GoalContent
from repopilot.execution import container_helper
from repopilot.execution import engine as engine_module
from repopilot.execution import workspace as workspace_module
from repopilot.execution.engine import ExecutionEngine
from repopilot.execution.git import GitHubGitCredentials
from repopilot.execution.model import ModelClient, ModelError
from repopilot.execution.tools import ToolDispatcher, validate_batch
from repopilot.execution.workspace import CommandResult, ToolError, WorkspaceError
from repopilot.persistence.settings import StoredModelSettings

pytestmark = pytest.mark.asyncio
MODEL_TOKEN = 'execution-provider-secret-not-for-model-input'
CHECK = "python -c 'from calc import add; assert add(2, 3) == 5'"
GOAL = GoalContent(
    summary='Repair addition in calc.py', scope=['calc.py'], non_goals=['Dependency changes'],
    acceptance_criteria=['add(2, 3) equals 5'], plan=['Read source', 'Fix subtraction', 'Run check'],
    open_questions=[],
)


def settings():
    return StoredModelSettings(
        base_url='https://provider.invalid/v1', model='execution-fixture', api_key=MODEL_TOKEN,
    )


def call(call_id='call-1', name='shell', arguments=None):
    return {'id': call_id, 'type': 'function', 'function': {
        'name': name, 'arguments': json.dumps(arguments if arguments is not None else {'command': 'touch forbidden'}),
    }}


def reply(*calls, content=None, finish=None, **message_fields):
    message = {'role': 'assistant', 'content': content, **message_fields}
    if calls:
        message['tool_calls'] = list(calls)
    return httpx.Response(200, json={'choices': [{
        'message': message, 'finish_reason': finish or ('tool_calls' if calls else 'stop'),
    }]})


class Provider:
    """HTTP fixture preserving real adapter parsing, not a replacement model adapter."""
    def __init__(self, responses):
        self.responses = deque(responses)
        self.inputs = []

    def respond(self, request):
        self.inputs.append(json.loads(request.content))
        assert MODEL_TOKEN not in request.content.decode()
        assert GITHUB_TOKEN not in request.content.decode()
        if not self.responses:
            raise AssertionError('Unexpected model retry or extra turn')
        value = self.responses.popleft()
        return value(self.inputs[-1]) if callable(value) else value

    @property
    def transport(self):
        return httpx.MockTransport(self.respond)


class WorkspaceFixture:
    """Controllable check outcomes; mutation attempts remain observable on failure paths."""
    instances = []
    check_codes = (1, 0)
    capture_error = None
    setup_code = 0
    patch = 'diff --git a/calc.py b/calc.py\n--- a/calc.py\n+++ b/calc.py\n@@ -1 +1 @@\n-def add(a, b): return a - b\n+def add(a, b): return a + b\n'

    def __init__(self, run_id):
        self.run_id = run_id
        self.prepared = False
        self.image_id = None
        self.cleaned = False
        self.actions = []
        self.checks = deque(self.check_codes)
        self.capture_count = 0
        self.instances.append(self)

    async def prepare(self, source, github_credentials, git_auth_strategy, image):
        self.prepared = True
        self.image_id = 'sha256:fixture-image'
        return self.image_id

    async def setup(self):
        return CommandResult(self.setup_code, 'setup evidence', False)

    async def environment_info(self):
        return {'projects': [], 'setup_command': 'automatic fixture preparation', 'check_command': CHECK}

    async def shell(self, command, timeout=60, *, cwd='.', project=None):
        if command == CHECK:
            code = self.checks.popleft() if self.checks else 1
            return CommandResult(code, 'assertion failed' if code else 'check passed', False)
        self.actions.append(('shell', command))
        return CommandResult(0, 'command executed', False)

    async def tool(self, name, arguments):
        self.actions.append((name, arguments))
        return {'content': 'file evidence', 'sha256': 'a' * 64}

    async def capture(self):
        self.capture_count += 1
        if self.capture_error:
            raise WorkspaceError(self.capture_error)
        return self.patch

    async def cleanup(self):
        self.cleaned = True


@pytest.fixture
def workspace_fixture(monkeypatch):
    class IsolatedWorkspace(WorkspaceFixture):
        instances = []
    monkeypatch.setattr(engine_module, 'Workspace', IsolatedWorkspace)
    return IsolatedWorkspace


async def run_engine(provider, emit=None, **overrides):
    events = []
    async def record(kind, payload):
        events.append((kind, payload))
    parameters = dict(
        run_id=uuid4(), source=source(), goal=GOAL, model=settings(),
        github_credentials=GitHubGitCredentials(api_token=GITHUB_TOKEN, private_key=''),
        git_auth_strategy='https_token', image='repopilot-dev:local', emit=emit or record,
    )
    parameters.update(overrides)
    result = await ExecutionEngine(model_transport=provider.transport).run(**parameters)
    return result, events


@pytest.mark.parametrize('response', [
    reply(call(), finish='length'),
    reply(call(), {'id': 'bad', 'type': 'function', 'function': {'name': 'shell', 'arguments': '{broken'}}),
    reply(call(), call()),
    reply(call(), call('second', arguments={'command': 'mutate', 'extra': True})),
    reply(call(), {'id': '', 'type': 'function', 'function': {'name': 'shell', 'arguments': '{"command":"mutate"}'}}),
    httpx.Response(200, content=b'{"choices":[{"message":{"tool_calls":['),
    reply(call(), {'id': 'duplicate-json', 'type': 'function', 'function': {
        'name': 'shell', 'arguments': '{"command":"safe","command":"unsafe"}',
    }}),
], ids=['truncated-batch', 'invalid-arguments-json', 'duplicate-batch-id', 'extra-argument', 'empty-id', 'invalid-response-json', 'duplicate-json-key'])
async def test_incomplete_or_invalid_batch_executes_zero_tools(workspace_fixture, response):
    result, _ = await run_engine(Provider([response]))
    workspace = workspace_fixture.instances[-1]
    assert result.status == 'failed'
    assert workspace.actions == []
    assert workspace.cleaned


async def test_reused_call_id_rejects_entire_later_batch(workspace_fixture):
    provider = Provider([reply(call('used')), reply(call('new'), call('used'))])
    result, _ = await run_engine(provider)
    assert result.status == 'failed'
    assert workspace_fixture.instances[-1].actions == [('shell', 'touch forbidden')]


@pytest.mark.parametrize('name,arguments', [
    ('read_file', {'path': 'calc.py', 'start_line': True}),
    ('read_file', {'path': 'calc.py', 'start_line': 0}),
    ('read_file', {'path': 'calc.py', 'end_line': 201}),
    ('read_file', {'path': 'calc.py', 'start_line': 5, 'end_line': 4}),
    ('read_file', {'path': 'calc.py', 'unexpected': 1}),
    ('search', {'query': 123}),
    ('search', {'query': 'needle', 'regex': True}),
    ('edit_file', {'path': 'calc.py', 'expected_sha256': 'a' * 64, 'old_text': '', 'new_text': 'new'}),
    ('edit_file', {'path': 'calc.py', 'expected_sha256': 'invalid', 'old_text': 'old', 'new_text': 'new'}),
    ('write_file', {'path': 'calc.py', 'content': 42}),
    ('write_file', {'path': 'calc.py', 'content': 'x' * (1024 * 1024 + 1)}),
    ('shell', {'command': 'true', 'timeout': 60}),
    ('shell', {'command': 'echo\x00bad'}),
    ('unregistered', {}),
    ('read_file', {'path': '../outside'}),
    ('write_file', {'path': '/absolute', 'content': 'new'}),
])
async def test_strict_tool_arguments_prevent_side_effects(workspace_fixture, name, arguments):
    result, _ = await run_engine(Provider([reply(call('invalid', name, arguments))]))
    assert result.status == 'failed'
    assert workspace_fixture.instances[-1].actions == []


async def test_provider_401_is_safe_gateway_error_without_retry():
    provider = Provider([httpx.Response(401, text=MODEL_TOKEN + ' invalid credential')])
    with pytest.raises(ModelError) as error:
        await ModelClient(transport=provider.transport).complete(settings(), [{'role': 'user', 'content': 'Fix addition'}], set())
    assert error.value.status_code == 502
    assert MODEL_TOKEN not in str(error.value)
    assert len(provider.inputs) == 1


@pytest.mark.parametrize('response', [
    httpx.Response(302, headers={'Location': 'https://other.invalid/completion'}),
    httpx.Response(200, content=b'x' * (256 * 1024 + 1)),
])
async def test_model_transport_rejects_redirect_and_oversized_response(workspace_fixture, response):
    provider = Provider([response])
    result, _ = await run_engine(provider)
    assert result.status == 'failed'
    assert workspace_fixture.instances[-1].actions == []
    assert len(provider.inputs) == 1


async def test_failed_final_check_returns_evidence_and_continues_original_loop(workspace_fixture):
    workspace_fixture.check_codes = (1, 1, 0)
    def finish_after_failure(payload):
        evidence = json.dumps(payload['messages'])
        assert 'assertion failed' in evidence
        assert GOAL.summary in evidence
        return reply(content='Fixed after reviewing failing evidence')
    provider = Provider([reply(content='Finished'), finish_after_failure])
    result, _ = await run_engine(provider)
    assert result.status == 'completed'
    assert [(check.phase, check.exit_code) for check in result.checks] == [
        ('setup', 0), ('baseline', 1), ('final', 1), ('final', 0),
    ]
    assert workspace_fixture.instances[-1].cleaned


async def test_failed_final_check_cannot_reset_round_budget(workspace_fixture, monkeypatch):
    workspace_fixture.check_codes = (1, 1)
    monkeypatch.setattr(engine_module, 'MAX_ROUNDS', 2)
    provider = Provider([reply(content='Finished'), reply(content='Finished again')])
    result, _ = await run_engine(provider)
    assert result.status == 'exhausted'
    assert [check.exit_code for check in result.checks if check.phase == 'final'] == [1, 1]
    assert len(provider.inputs) == 2


async def test_event_failure_prevents_tool_side_effect_and_cleans_up(workspace_fixture):
    async def failing_emit(kind, payload):
        if kind == 'tool_start':
            raise OSError('database unavailable')
    provider = Provider([reply(call())])
    with pytest.raises(Exception, match='database unavailable|事件|日志'):
        await run_engine(provider, emit=failing_emit)
    workspace = workspace_fixture.instances[-1]
    assert workspace.actions == []
    assert workspace.cleaned


async def test_input_budget_exhaustion_never_sends_partial_goal(workspace_fixture, monkeypatch):
    monkeypatch.setattr(engine_module, 'MAX_INPUT_BYTES', 1)
    provider = Provider([])
    result, _ = await run_engine(provider)
    assert result.status == 'exhausted'
    assert provider.inputs == []
    assert workspace_fixture.instances[-1].actions == []


async def test_event_budget_preserves_stop_without_unlogged_mutation(workspace_fixture, monkeypatch):
    monkeypatch.setattr(engine_module, 'MAX_EVENTS', 2)
    provider = Provider([reply(call())])
    result, events = await run_engine(provider)
    assert result.status == 'exhausted'
    assert workspace_fixture.instances[-1].actions == []
    assert len(events) <= 2


async def test_wall_clock_budget_stops_waiting_model_and_cleans_up(workspace_fixture, monkeypatch):
    import asyncio
    async def waiting_response(request):
        await asyncio.sleep(1)
        return reply(call())
    monkeypatch.setattr(engine_module, 'MAX_RUN_SECONDS', 0.01)
    result = await ExecutionEngine(model_transport=httpx.MockTransport(waiting_response)).run(
        run_id=uuid4(), source=source(), goal=GOAL, model=settings(),
        github_credentials=GitHubGitCredentials(api_token=GITHUB_TOKEN, private_key=''),
        git_auth_strategy='https_token', image='repopilot-dev:local', emit=_discard,
    )
    assert result.status == 'exhausted'
    assert workspace_fixture.instances[-1].actions == []
    assert workspace_fixture.instances[-1].cleaned


async def _discard(kind, payload):
    pass


async def test_failed_run_preserves_existing_patch_and_visible_failure(workspace_fixture):
    provider = Provider([reply(call()), httpx.Response(500, text='provider failed')])
    result, _ = await run_engine(provider)
    assert result.status == 'failed'
    assert result.patch == workspace_fixture.patch
    assert result.report
    assert workspace_fixture.instances[-1].capture_count == 1


async def test_capture_failure_cannot_be_reported_as_no_changes(workspace_fixture):
    workspace_fixture.capture_error = 'Cannot obtain final archive'
    result, _ = await run_engine(Provider([reply(content='Finished')]))
    assert result.status == 'failed'
    assert 'Cannot obtain final archive' in result.report
    assert '未产生代码差异' not in result.report
    assert workspace_fixture.instances[-1].cleaned


async def test_oversized_patch_is_failed_not_truncated_or_claimed_unchanged(workspace_fixture, monkeypatch):
    monkeypatch.setattr(engine_module, 'MAX_PATCH_BYTES', 32)
    result, _ = await run_engine(Provider([reply(content='Finished')]))
    assert result.status == 'failed'
    assert result.patch == ''
    assert '成果超过保存上限' in result.report
    assert '未产生代码差异' not in result.report


async def test_report_and_event_envelopes_respect_utf8_storage_limits(workspace_fixture, monkeypatch):
    monkeypatch.setattr(engine_module, 'MAX_REPORT_BYTES', 128)
    content = '结果\\"\n' * 12000
    result, events = await run_engine(Provider([reply(content=content)]))
    assert result.status == 'completed'
    assert len(result.report.encode()) <= 128
    assert '截断' in result.report
    assistant = [payload for kind, payload in events if kind == 'assistant'][0]
    assert assistant['truncated'] is True
    assert len(json.dumps(assistant, ensure_ascii=False, separators=(',', ':')).encode()) <= 32 * 1024


async def test_recoverable_tool_failure_is_visible_evidence_not_success(workspace_fixture):
    async def missing_file(self, name, arguments):
        raise ToolError('Requested file does not exist')
    workspace_fixture.tool = missing_file
    def after_failure(payload):
        tools = [item for item in payload['messages'] if item['role'] == 'tool']
        evidence = json.loads(tools[-1]['content'])
        assert evidence['ok'] is False
        assert 'does not exist' in evidence['error']
        return reply(content='Could not read requested file; review coverage limitation')
    result, events = await run_engine(Provider([
        reply(call('missing', 'read_file', {'path': 'missing.py'})), after_failure,
    ]))
    assert result.status == 'completed'
    assert 'coverage limitation' in result.report
    failures = [payload for kind, payload in events if kind == 'tool_result']
    assert failures[0]['result']['ok'] is False


async def test_unknown_network_result_is_not_retried(workspace_fixture):
    requests = []
    def disconnected(request):
        requests.append(request)
        raise httpx.ReadError('Connection lost after submission', request=request)
    result = await ExecutionEngine(model_transport=httpx.MockTransport(disconnected)).run(
        run_id=uuid4(), source=source(), goal=GOAL, model=settings(),
        github_credentials=GitHubGitCredentials(api_token=GITHUB_TOKEN, private_key=''),
        git_auth_strategy='https_token', image='repopilot-dev:local', emit=_discard,
    )
    assert result.status == 'failed'
    assert '未自动重试' in result.report
    assert len(requests) == 1
    assert workspace_fixture.instances[-1].actions == []


async def test_cleanup_failure_cannot_return_completed(workspace_fixture):
    async def failed_cleanup(self):
        raise WorkspaceError('Container removal not confirmed')
    workspace_fixture.cleanup = failed_cleanup
    with pytest.raises(WorkspaceError, match='removal not confirmed'):
        await run_engine(Provider([reply(content='Finished')]))


async def test_setup_failure_blocks_before_model_and_preserves_check_evidence(workspace_fixture):
    workspace_fixture.setup_code = 7
    provider = Provider([])
    result, _ = await run_engine(provider)
    assert result.status == 'blocked'
    assert provider.inputs == []
    assert [(check.phase, check.exit_code) for check in result.checks] == [('setup', 7)]
    assert workspace_fixture.instances[-1].cleaned


async def test_visible_text_only_and_known_secrets_redacted(workspace_fixture):
    provider = Provider([reply(
        content=f'Visible summary {MODEL_TOKEN} {GITHUB_TOKEN}',
        reasoning_content='PRIVATE CHAIN OF THOUGHT', reasoning='PRIVATE REASONING',
    )])
    result, events = await run_engine(provider)
    saved = json.dumps({'report': result.report, 'events': events}, ensure_ascii=False)
    assert 'Visible summary' in saved
    assert MODEL_TOKEN not in saved and GITHUB_TOKEN not in saved
    assert 'PRIVATE CHAIN OF THOUGHT' not in saved and 'PRIVATE REASONING' not in saved
    for _, payload in events:
        assert len(json.dumps(payload, ensure_ascii=False).encode()) <= 32 * 1024


async def test_no_diff_completion_explicitly_reports_no_changes(workspace_fixture):
    workspace_fixture.patch = ''
    result, _ = await run_engine(Provider([reply(content='Already correct')]))
    assert result.status == 'completed'
    assert result.patch == ''
    assert '未产生代码差异' in result.report


@real_docker
async def test_actual_docker_rejects_stale_sha_after_shell_and_keeps_new_content(docker_workspace):
    workspace, _ = docker_workspace
    dispatcher = ToolDispatcher(workspace)
    def validated(call_id, name, arguments):
        return validate_batch([call(call_id, name, arguments)], set())[0]
    read = await dispatcher.execute(validated('read', 'read_file', {'path': 'calc.py'}))
    old_sha = read['sha256']
    shell = await dispatcher.execute(validated('shell', 'shell', {
        'command': "printf 'def add(a, b): return a * b\\n' > calc.py",
    }))
    assert shell['exit_code'] == 0
    failure = await dispatcher.execute(validated('stale', 'edit_file', {
        'path': 'calc.py', 'expected_sha256': old_sha,
        'old_text': 'return a - b', 'new_text': 'return a + b',
    }))
    assert failure.get('error') or failure.get('ok') is False
    reread = await dispatcher.execute(validated('reread', 'read_file', {'path': 'calc.py'}))
    assert 'return a * b' in reread['content']
    assert reread['sha256'] != old_sha


@real_docker
async def test_actual_engine_reads_edits_checks_captures_applicable_patch_and_removes_container(tmp_path, monkeypatch):
    assert shutil.which('docker'), 'Docker coverage enabled but Docker CLI is unavailable'
    code, output = await command('docker', 'info', '--format', '{{.ServerVersion}}')
    assert code == 0, f'Docker coverage enabled but Docker daemon is unavailable: {output}'
    original = b'def add(a, b): return a - b\n'
    sha = hashlib.sha256(original).hexdigest()
    provider = Provider([
        reply(call('read', 'read_file', {'path': 'calc.py'})),
        reply(call('edit', 'edit_file', {
            'path': 'calc.py', 'expected_sha256': sha,
            'old_text': 'return a - b', 'new_text': 'return a + b',
        })),
        reply(content='Repaired addition; review the patch'),
    ])
    upstream = tmp_path / 'upstream'
    upstream.mkdir()
    (upstream / 'calc.py').write_bytes(original)
    (upstream / 'pyproject.toml').write_bytes(b'[project]\nname="calc-fixture"\nversion="0.1.0"\nrequires-python=">=3.12,<3.13"\ndependencies=[]\n[tool.uv]\npackage=false\n')
    (upstream / 'test_calc.py').write_bytes(b'import unittest\nfrom calc import add\nclass TestAdd(unittest.TestCase):\n def test_add(self): self.assertEqual(add(2, 3), 5)\n')
    assert (await command('git', 'init', '--quiet', cwd=upstream))[0] == 0
    assert (await command('git', 'add', '.', cwd=upstream))[0] == 0
    assert (await command('git', '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
                          'commit', '--quiet', '-m', 'fixture', cwd=upstream))[0] == 0
    async def local_clone(repository_url, commit, destination, credentials, *, strategy, timeout=90):
        shutil.copytree(upstream, destination)
    monkeypatch.setattr(workspace_module, 'clone_fixed_commit', local_clone)
    events = []
    async def emit(kind, payload):
        events.append((kind, payload))
    run_id = uuid4()
    engine = ExecutionEngine(model_transport=provider.transport)
    try:
        result = await engine.run(
            run_id=run_id, source=source(), goal=GOAL, model=settings(),
            github_credentials=GitHubGitCredentials(api_token=GITHUB_TOKEN, private_key=''),
            git_auth_strategy='https_token',
            image=os.environ.get('REPOPILOT_TEST_IMAGE', 'repopilot-dev:local'), emit=emit,
        )
        assert result.status == 'completed', result.report
        assert [(check.phase, check.exit_code) for check in result.checks] == [('setup', 0), ('baseline', 1), ('final', 0)]
        assert result.checks[1].command == result.checks[2].command
        assert 'unittest' in result.checks[1].command
        patch_file = tmp_path / 'result.patch'
        patch_file.write_text(result.patch)
        fresh = tmp_path / 'fresh'
        fresh.mkdir()
        (fresh / 'calc.py').write_bytes(original)
        code, output = await command('git', 'apply', '--binary', str(patch_file), cwd=fresh)
        assert code == 0, output
        code, output = await command('python3', '-c', 'from calc import add; assert add(2, 3) == 5', cwd=fresh)
        assert code == 0, output
        code, _ = await command('docker', 'inspect', f'repopilot-run-{run_id}')
        assert code != 0, 'Container must be gone before completion is returned'
        starts = [payload['call_id'] for kind, payload in events if kind == 'tool_start']
        results = [payload['call_id'] for kind, payload in events if kind == 'tool_result']
        assert starts == results == ['read', 'edit']
        saved = json.dumps(events, ensure_ascii=False) + result.report + result.patch
        assert MODEL_TOKEN not in saved and GITHUB_TOKEN not in saved
    finally:
        await engine.cleanup(run_id)


async def test_file_reads_bound_utf8_content_but_hash_the_whole_file(tmp_path, monkeypatch):
    monkeypatch.setattr(container_helper, 'ROOT', tmp_path)
    data = ('第一行\n' + '内容' * 10000 + '\nlast line\n').encode()
    (tmp_path / 'large.txt').write_bytes(data)
    result = container_helper.execute('read_file', {'path': 'large.txt'})
    assert len(result['content'].encode()) <= 16 * 1024
    assert result['truncated'] is True
    assert result['sha256'] == hashlib.sha256(data).hexdigest()
    assert 'last line' not in result['content']


async def test_search_is_literal_and_bounded_not_regex(tmp_path, monkeypatch):
    monkeypatch.setattr(container_helper, 'ROOT', tmp_path)
    (tmp_path / 'source.txt').write_text('a.b\naxb\n' + 'a.b\n' * 120)
    result = container_helper.execute('search', {'query': 'a.b', 'path': '.'})
    assert len(result['matches']) == 100
    assert result['truncated'] is True
    assert all(match['content'] == 'a.b' for match in result['matches'])
    assert all(match['line'] != 2 for match in result['matches'])


async def test_existing_file_write_and_ambiguous_edit_do_not_destroy_source(tmp_path, monkeypatch):
    monkeypatch.setattr(container_helper, 'ROOT', tmp_path)
    target = tmp_path / 'source.txt'
    target.write_text('old old\n')
    with pytest.raises(FileExistsError):
        container_helper.execute('write_file', {'path': 'source.txt', 'content': 'overwrite'})
    with pytest.raises(ValueError, match='exactly once'):
        container_helper.execute('edit_file', {
            'path': 'source.txt', 'expected_sha256': hashlib.sha256(target.read_bytes()).hexdigest(),
            'old_text': 'old', 'new_text': 'new',
        })
    assert target.read_text() == 'old old\n'


async def test_links_and_binary_reads_cannot_expose_outside_data(tmp_path, monkeypatch):
    root = tmp_path / 'workspace'
    root.mkdir()
    monkeypatch.setattr(container_helper, 'ROOT', root)
    outside = tmp_path / 'secret.txt'
    outside.write_text('outside secret')
    (root / 'link').symlink_to(outside)
    (root / 'binary').write_bytes(b'\x00secret')
    for path in ('link', '../secret.txt', 'binary'):
        with pytest.raises(ValueError):
            container_helper.execute('read_file', {'path': path})
    assert outside.read_text() == 'outside secret'


async def test_cumulative_input_budget_stops_before_second_model_submission(workspace_fixture, monkeypatch):
    def first_response(payload):
        size = len(json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode())
        monkeypatch.setattr(engine_module, 'MAX_INPUT_BYTES', size + 1)
        return reply(call('first', 'shell', {'command': 'inspect source'}))
    provider = Provider([first_response])
    result, _ = await run_engine(provider)
    assert result.status == 'exhausted'
    assert len(provider.inputs) == 1
    assert workspace_fixture.instances[-1].actions == [('shell', 'inspect source')]


async def test_reasoning_and_tool_output_secrets_never_enter_next_model_turn(workspace_fixture):
    original_shell = workspace_fixture.shell
    async def shell_with_secrets(self, command, timeout=60, *, cwd='.', project=None):
        if command == CHECK:
            return await original_shell(self, command, timeout)
        return CommandResult(0, f'Visible tool output {MODEL_TOKEN} {GITHUB_TOKEN}', False)
    workspace_fixture.shell = shell_with_secrets
    def next_response(payload):
        messages = json.dumps(payload['messages'])
        assert 'Visible tool output' in messages
        assert MODEL_TOKEN not in messages and GITHUB_TOKEN not in messages
        assert 'HIDDEN REASONING' not in messages
        return reply(content='Finished after inspecting actual tool evidence')
    provider = Provider([
        reply(call(), reasoning_content='HIDDEN REASONING'), next_response,
    ])
    result, events = await run_engine(provider)
    assert result.status == 'completed'
    saved = json.dumps(events) + result.report
    assert MODEL_TOKEN not in saved and GITHUB_TOKEN not in saved
    assert 'HIDDEN REASONING' not in saved
