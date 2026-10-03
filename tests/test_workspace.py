from __future__ import annotations

import asyncio
import io
import json
import os
import shutil
import stat
import tarfile
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
import pytest_asyncio

from repopilot.domain.context import RepositoryContext
from repopilot.domain.tasks import SourceSnapshot
from repopilot.execution import workspace as workspace_module
from repopilot.execution.git import (
    GitAuthContext,
    GitHubGitCredentials,
    clone_fixed_commit,
)
from repopilot.execution.workspace import Workspace, WorkspaceError, extract_archive, generate_patch

COMMIT = 'a' * 40
TOKEN = 'workspace-fixture-secret-not-for-container'
PRIVATE_KEY = 'workspace-fixture-private-key-not-for-container'
ROOT = 'example-project-' + COMMIT
DOCKER_ENABLED = os.environ.get('REPOPILOT_DOCKER_TESTS') == '1'
real_docker = pytest.mark.skipif(
    not DOCKER_ENABLED, reason='Set REPOPILOT_DOCKER_TESTS=1 for real Docker coverage',
)


def source() -> SourceSnapshot:
    now = datetime.now(UTC)
    return SourceSnapshot(
        repository_url='https://github.com/example/project',
        baseline_commit=COMMIT, issue_number=7,
        issue_title='Fix addition', issue_body='Fix calc.py',
        issue_url='https://github.com/example/project/issues/7',
        issue_updated_at=now, fetched_at=now,
        repository_context=RepositoryContext(commit=COMMIT, tree=[], files=[],
                                             omissions=['Clone fixture is prepared separately'], tree_truncated=False),
    )


def member(name: str, content: bytes = b'', *, kind=tarfile.REGTYPE,
           mode: int = 0o644, link: str = '') -> tuple[tarfile.TarInfo, bytes]:
    entry = tarfile.TarInfo(name)
    entry.type = kind
    entry.mode = mode
    entry.linkname = link
    entry.size = len(content) if kind == tarfile.REGTYPE else 0
    return entry, content


def archive(entries: list[tuple[tarfile.TarInfo, bytes]], *, compressed=True) -> bytes:
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode='w:gz' if compressed else 'w') as tar:
        for entry, content in entries:
            tar.addfile(entry, io.BytesIO(content) if entry.isfile() else None)
    return output.getvalue()






@pytest.mark.asyncio
async def test_fixed_commit_clone_uses_real_local_git_and_preserves_metadata(tmp_path, monkeypatch):
    upstream = tmp_path / 'upstream'
    upstream.mkdir()
    assert (await command('git', 'init', '--quiet', '-b', 'main', cwd=upstream))[0] == 0
    (upstream / 'calc.py').write_text('first\n')
    assert (await command('git', 'add', 'calc.py', cwd=upstream))[0] == 0
    assert (await command(
        'git', '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
        'commit', '--quiet', '-m', 'first', cwd=upstream,
    ))[0] == 0
    first = (await command('git', 'rev-parse', 'HEAD', cwd=upstream))[1].strip()
    (upstream / 'calc.py').write_text('second\n')
    assert (await command(
        'git', '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
        'commit', '--quiet', '-am', 'second', cwd=upstream,
    ))[0] == 0

    @asynccontextmanager
    async def local_auth(repository_url, credentials, *, strategy):
        home = tmp_path / 'home'
        home.mkdir(exist_ok=True)
        environment = workspace_module.os.environ.copy()
        environment.update({
            'HOME': str(home), 'GIT_CONFIG_NOSYSTEM': '1',
            'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_CONFIG_SYSTEM': os.devnull,
            'GIT_TERMINAL_PROMPT': '0', 'GIT_ALLOW_PROTOCOL': 'file',
        })
        yield GitAuthContext(upstream.as_uri(), environment, strategy)

    monkeypatch.setattr('repopilot.execution.git.github_git_auth', local_auth)
    destination = tmp_path / 'clone'
    await clone_fixed_commit(
        'https://github.com/example/project', first, destination,
        GitHubGitCredentials(api_token=TOKEN, private_key=''), strategy='https_token',
    )
    assert (destination / '.git').is_dir()
    assert (destination / 'calc.py').read_text() == 'first\n'
    assert (await command('git', 'rev-parse', 'HEAD', cwd=destination))[1].strip() == first
    assert (await command('git', 'config', 'user.name', cwd=destination))[1].strip() == 'RepoPilot'
    assert (await command('git', 'config', 'user.email', cwd=destination))[1].strip() == 'repopilot@users.noreply.github.com'

@pytest.mark.asyncio
async def test_fixed_commit_clone_rejects_tracked_symlink(tmp_path, monkeypatch):
    upstream = tmp_path / 'symlink-upstream'
    upstream.mkdir()
    assert (await command('git', 'init', '--quiet', '-b', 'main', cwd=upstream))[0] == 0
    os.symlink('target.txt', upstream / 'linked.txt')
    (upstream / 'target.txt').write_text('target\n')
    assert (await command('git', 'add', '.', cwd=upstream))[0] == 0
    assert (await command(
        'git', '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
        'commit', '--quiet', '-m', 'symlink', cwd=upstream,
    ))[0] == 0
    commit = (await command('git', 'rev-parse', 'HEAD', cwd=upstream))[1].strip()

    @asynccontextmanager
    async def local_auth(repository_url, credentials, *, strategy):
        environment = {'PATH': '/usr/local/bin:/usr/bin:/bin', 'HOME': str(tmp_path),
                       'LC_ALL': 'C', 'GIT_CONFIG_NOSYSTEM': '1',
                       'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_CONFIG_SYSTEM': os.devnull,
                       'GIT_TERMINAL_PROMPT': '0', 'GIT_ALLOW_PROTOCOL': 'file'}
        yield GitAuthContext(upstream.as_uri(), environment, strategy)

    monkeypatch.setattr('repopilot.execution.git.github_git_auth', local_auth)
    from repopilot.execution.git import GitOperationError
    with pytest.raises(GitOperationError, match='symlinks or submodules'):
        await clone_fixed_commit(
            'https://github.com/example/project', commit, tmp_path / 'clone-symlink',
            GitHubGitCredentials(api_token=TOKEN, private_key=''), strategy='https_token',
        )


@pytest.mark.asyncio
async def test_https_auth_is_ephemeral_and_token_is_not_in_url_or_git_config(tmp_path):
    from repopilot.execution.git import github_git_auth
    credentials = GitHubGitCredentials(api_token=TOKEN, private_key='')
    assert TOKEN not in repr(credentials)
    async with github_git_auth(
        'https://github.com/example/project', credentials, strategy='https_token',
    ) as auth:
        assert auth.repository_url == 'https://github.com/example/project'
        assert TOKEN not in auth.repository_url
        assert TOKEN not in repr(auth.environment)
        assert auth.environment['GIT_CONFIG_NOSYSTEM'] == '1'
        assert auth.environment['GIT_CONFIG_GLOBAL'] == os.devnull
        helper = Path(auth.environment['GIT_CONFIG_VALUE_5'])
        token_file = Path(auth.environment['REPOPILOT_GIT_TOKEN_FILE'])
        assert helper.is_file() and token_file.read_text() == TOKEN
        assert stat.S_IMODE(token_file.stat().st_mode) == 0o600
    assert not token_file.exists()

@pytest.mark.asyncio
async def test_ssh_auth_uses_pinned_hosts_and_ephemeral_private_key():
    from repopilot.execution.git import github_git_auth
    credentials = GitHubGitCredentials(api_token=TOKEN, private_key=PRIVATE_KEY)
    assert PRIVATE_KEY not in repr(credentials)
    async with github_git_auth(
        'https://github.com/example/project', credentials, strategy='ssh_key',
    ) as auth:
        assert auth.repository_url == 'git@github.com:example/project.git'
        assert TOKEN not in repr(auth.environment)
        command_value = auth.environment['GIT_SSH_COMMAND']
        assert 'StrictHostKeyChecking=yes' in command_value
        assert 'IdentitiesOnly=yes' in command_value
        assert 'ssh-keyscan' not in command_value
        key_path = Path(command_value.split(' -i ', 1)[1].split(' ', 1)[0])
        assert key_path.read_text() == PRIVATE_KEY
        assert stat.S_IMODE(key_path.stat().st_mode) == 0o600
    assert not key_path.exists()

@pytest.mark.parametrize('entries', [
    [member('first/a'), member('second/b')],
    [member('loose-file', b'not inside an archive root')],
    [member('/absolute/file')],
    [member('../outside/file')],
    [member(f'{ROOT}/../../escape')],
    [member(f'{ROOT}/dir/../escape')],
    [member(f'{ROOT}/file'), member(f'{ROOT}/file')],
    [member(f'{ROOT}/.git/config', b'[core]')],
    [member(f'{ROOT}/nested/.git/config', b'[core]')],
    [member(f'{ROOT}/link', kind=tarfile.SYMTYPE, link='/tmp/outside')],
    [member(f'{ROOT}/link', kind=tarfile.LNKTYPE, link=f'{ROOT}/file')],
    [member(f'{ROOT}/device', kind=tarfile.CHRTYPE)],
    [member(f'{ROOT}/device', kind=tarfile.BLKTYPE)],
    [member(f'{ROOT}/pipe', kind=tarfile.FIFOTYPE)],
    [member(f'{ROOT}/same', kind=tarfile.DIRTYPE), member(f'{ROOT}/same', b'file')],
], ids=[
    'multiple-roots', 'missing-root-directory', 'absolute', 'root-traversal',
    'escape', 'internal-traversal', 'duplicate', 'git-metadata', 'nested-git',
    'symlink', 'hardlink', 'character-device', 'block-device', 'fifo', 'type-collision',
])
def test_source_archive_rejects_unsafe_members(tmp_path, entries):
    with pytest.raises(WorkspaceError):
        extract_archive(archive(entries), tmp_path / 'destination', strip_root=True)
    assert not (tmp_path / 'escape').exists()
    assert not (tmp_path / 'outside').exists()


@pytest.mark.parametrize('name,kind,link', [
    ('/absolute', tarfile.REGTYPE, ''),
    ('../escape', tarfile.REGTYPE, ''),
    ('dir/../../escape', tarfile.REGTYPE, ''),
    ('.git/config', tarfile.REGTYPE, ''),
    ('link', tarfile.SYMTYPE, '/tmp/outside'),
    ('hard', tarfile.LNKTYPE, 'file'),
    ('device', tarfile.CHRTYPE, ''),
])
def test_final_archive_rejects_unsafe_members(tmp_path, name, kind, link):
    with pytest.raises(WorkspaceError):
        extract_archive(archive([member(name, kind=kind, link=link)]),
                        tmp_path / 'final', strip_root=False)


def test_archive_import_strips_only_root_and_preserves_binary_and_mode(tmp_path):
    data = archive([
        member(ROOT, kind=tarfile.DIRTYPE),
        member(f'{ROOT}/pkg', kind=tarfile.DIRTYPE),
        member(f'{ROOT}/pkg/data.bin', b'\x00\xff\x01'),
        member(f'{ROOT}/script.sh', b'#!/bin/sh\nexit 0\n', mode=0o755),
    ])
    destination = tmp_path / 'baseline'
    extract_archive(data, destination, strip_root=True)
    assert (destination / 'pkg/data.bin').read_bytes() == b'\x00\xff\x01'
    assert (destination / 'script.sh').stat().st_mode & 0o777 == 0o755
    assert not (destination / ROOT).exists()


@pytest.mark.parametrize('limit,value,entries', [
    ('MAX_MEMBERS', 1, [member(f'{ROOT}/one'), member(f'{ROOT}/two')]),
    ('MAX_EXPANDED', 3, [member(f'{ROOT}/large', b'four')]),
])
def test_archive_expansion_limits_fail_explicitly(tmp_path, monkeypatch, limit, value, entries):
    monkeypatch.setattr(workspace_module, limit, value)
    with pytest.raises(WorkspaceError):
        extract_archive(archive(entries), tmp_path / 'baseline', strip_root=True)


def test_archive_rejects_malformed_compressed_data(tmp_path):
    with pytest.raises(WorkspaceError):
        extract_archive(b'not a tar archive', tmp_path / 'baseline', strip_root=True)




@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['list', 'remove', 'still-present'])
async def test_cleanup_never_claims_success_without_confirmed_container_absence(monkeypatch, failure):
    workspace = Workspace(uuid4())

    async def docker(*args, **kwargs):
        if args[:2] == ('container', 'ls'):
            if failure == 'list':
                return 1, b'', b'daemon unavailable'
            return 0, (workspace.container_name + '\n').encode(), b''
        assert args[:2] == ('rm', '--force')
        return (1 if failure == 'remove' else 0), b'', b''

    monkeypatch.setattr(workspace, '_docker', docker)
    workspace.prepared = True
    with pytest.raises(WorkspaceError):
        await workspace.cleanup()
    assert workspace.prepared is True


async def command(*args: str, cwd: Path | None = None) -> tuple[int, str]:
    process = await asyncio.create_subprocess_exec(
        *args, cwd=cwd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
    )
    output, _ = await asyncio.wait_for(process.communicate(), timeout=30)
    return process.returncode, output.decode(errors='replace')


@pytest.mark.asyncio
async def test_patch_applies_binary_new_deleted_mode_and_tracked_ignored_files(tmp_path):
    baseline = tmp_path / 'baseline'
    final = tmp_path / 'final'
    baseline.mkdir()
    (baseline / '.gitignore').write_text('ignored.txt\nignored-dir/\n*.cache\n')
    (baseline / 'ignored.txt').write_text('baseline tracked ignored file\n')
    (baseline / 'ignored-dir').mkdir()
    (baseline / 'ignored-dir/tracked.txt').write_text('baseline tracked nested file\n')
    (baseline / 'node_modules').mkdir()
    (baseline / 'node_modules/tracked.js').write_text('baseline vendored source\n')
    (baseline / 'deleted.txt').write_text('remove this\n')
    (baseline / 'binary.dat').write_bytes(b'\x00\xffold\x00')
    (baseline / 'script.sh').write_text('#!/bin/sh\nexit 0\n')
    (baseline / 'script.sh').chmod(0o644)
    shutil.copytree(baseline, final)
    (final / 'ignored.txt').write_text('changed tracked ignored file\n')
    (final / 'ignored-dir/tracked.txt').write_text('changed tracked nested file\n')
    (final / 'node_modules/tracked.js').write_text('changed vendored source\n')
    (final / 'deleted.txt').unlink()
    (final / 'binary.dat').write_bytes(b'\x00\xffnew\x01')
    (final / 'script.sh').chmod(0o755)
    (final / 'new.txt').write_text('new source\n')
    (final / 'new.bin').write_bytes(b'\x00\xffnew binary\x00')
    for path in ('node_modules/lib.js', '.venv/lib.py', '__pycache__/cached.pyc',
                 'dist/bundle.js', 'build/object.o', 'ignored-dir/cache.txt', 'scratch.cache'):
        target = final / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text('untracked setup output\n')
    # Final .gitignore is untrusted; only baseline rules determine exclusions.
    (final / '.gitignore').write_text('*\n')

    patch = await generate_patch(baseline, final)
    patch_file = tmp_path / 'result.patch'
    patch_file.write_text(patch)
    fresh = tmp_path / 'fresh'
    shutil.copytree(baseline, fresh)
    code, output = await command('git', 'apply', '--binary', str(patch_file), cwd=fresh)
    assert code == 0, output
    assert not (fresh / 'deleted.txt').exists()
    assert (fresh / 'binary.dat').read_bytes() == (final / 'binary.dat').read_bytes()
    assert (fresh / 'new.bin').read_bytes() == (final / 'new.bin').read_bytes()
    assert (fresh / 'new.txt').read_text() == 'new source\n'
    assert (fresh / 'script.sh').stat().st_mode & 0o777 == 0o755
    assert (fresh / 'ignored.txt').read_bytes() == (final / 'ignored.txt').read_bytes()
    assert (fresh / 'ignored-dir/tracked.txt').read_bytes() == (final / 'ignored-dir/tracked.txt').read_bytes()
    assert (fresh / 'node_modules/tracked.js').read_text() == 'changed vendored source\n'
    assert (fresh / '.gitignore').read_text() == '*\n'
    for path in ('node_modules/lib.js', '.venv', '__pycache__', 'dist', 'build',
                 'ignored-dir/cache.txt', 'scratch.cache'):
        assert not (fresh / path).exists(), path
    assert str(baseline) not in patch
    assert str(final) not in patch


@pytest.mark.asyncio
async def test_patch_limit_never_returns_truncated_applicable_patch(tmp_path, monkeypatch):
    baseline, final = tmp_path / 'baseline', tmp_path / 'final'
    baseline.mkdir()
    final.mkdir()
    (final / 'large.txt').write_text('new content\n' * 20)
    monkeypatch.setattr(workspace_module, 'MAX_PATCH', 32)
    with pytest.raises(WorkspaceError):
        await generate_patch(baseline, final)


@pytest_asyncio.fixture
async def docker_workspace(monkeypatch):
    if not DOCKER_ENABLED:
        pytest.skip('Set REPOPILOT_DOCKER_TESTS=1 for real Docker coverage')
    assert shutil.which('docker'), 'Docker tests enabled but Docker CLI is unavailable'
    code, output = await command('docker', 'info', '--format', '{{.ServerVersion}}')
    assert code == 0, f'Docker tests enabled but Docker daemon is unavailable: {output}'
    image = os.environ.get('REPOPILOT_TEST_IMAGE', 'repopilot-dev:local')
    async def clone_fixture(repository_url, commit, destination, credentials, *, strategy, timeout=90):
        destination.mkdir()
        (destination / '.git').mkdir()
        (destination / '.git/config').write_text('[core]\nrepositoryformatversion = 0\n')
        (destination / 'calc.py').write_text('def add(a, b): return a - b\n')
    monkeypatch.setattr(workspace_module, 'clone_fixed_commit', clone_fixture)
    workspace = Workspace(uuid4())
    try:
        image_id = await workspace.prepare(
            source=source(),
            github_credentials=GitHubGitCredentials(api_token=TOKEN, private_key=''),
            git_auth_strategy='https_token', image=image,
        )
        yield workspace, image_id
    finally:
        await workspace.cleanup()


@real_docker
@pytest.mark.asyncio
async def test_real_docker_source_import_permissions_mounts_and_network(docker_workspace):
    workspace, image_id = docker_workspace
    code, output = await command('docker', 'inspect', f'repopilot-run-{workspace.run_id}')
    assert code == 0, output
    container = json.loads(output)[0]
    assert container['Image'] == image_id
    assert container['Mounts'] == []
    host = container['HostConfig']
    assert host['ReadonlyRootfs'] is True
    assert 'ALL' in [item.upper() for item in host['CapDrop']]
    assert any(item.startswith('no-new-privileges') for item in host['SecurityOpt'])
    assert host['NanoCpus'] == 2_000_000_000
    assert 0 < host['Memory'] <= 4 * 1024**3
    assert 0 < host['PidsLimit'] <= 512
    assert host['NetworkMode'] == 'none'
    assert all('size=' in host['Tmpfs'][path] for path in ('/workspace', '/home/runner', '/tmp'))
    result = await workspace.shell(
        "python3 -c 'import os, pathlib; "
        "assert os.getuid() == 1000; assert os.getgid() == 1000; "
        "p=pathlib.Path(\"calc.py\"); assert p.stat().st_uid == 1000; "
        "assert pathlib.Path(\".git/config\").is_file(); "
        "assert p.read_text() == \"def add(a, b): return a - b\\n\"; "
        "assert os.environ[\"HOME\"] == \"/home/runner\"; "
        "assert not pathlib.Path(\"/var/run/docker.sock\").exists(); "
        "assert not any(\"workspace-fixture-secret\" in v for v in os.environ.values()); "
        "p.write_text(\"source writable\\n\"); "
        "pathlib.Path.home().joinpath(\"write-test\").write_text(\"home writable\"); "
        "pathlib.Path(\"/tmp/write-test\").write_text(\"tmp writable\"); "
        "print(\"sandbox-ok\")'"
    )
    assert result.exit_code == 0, result.output
    assert result.output.strip() == 'sandbox-ok'
    result = await workspace.shell("python3 -c 'import pathlib; pathlib.Path(\"/root-write-test\").write_text(\"no\")'")
    assert result.exit_code != 0
    result = await workspace.shell("python3 -c 'import socket; print(socket.if_nameindex())'")
    assert result.exit_code == 0, result.output
    assert "'lo'" in result.output and 'eth' not in result.output


@real_docker
@pytest.mark.asyncio
async def test_real_docker_setup_disconnects_before_later_commands(docker_workspace):
    workspace, _ = docker_workspace
    await workspace.tool('write_file', {'path': 'pyproject.toml', 'content': '[project]\nname="network-fixture"\nversion="0.1.0"\nrequires-python=">=3.12,<3.13"\ndependencies=[]\n[tool.uv]\npackage=false\n'})
    await workspace.tool('write_file', {'path': 'test_network_fixture.py', 'content': 'import unittest\nclass TestFixture(unittest.TestCase):\n def test_value(self): self.assertEqual(1 + 1, 2)\n'})
    result = await workspace.setup()
    assert result.exit_code == 0, result.output
    code, output = await command('docker', 'inspect', f'repopilot-run-{workspace.run_id}')
    assert code == 0, output
    networks = json.loads(output)[0]['NetworkSettings']['Networks']
    assert 'bridge' not in networks
    result = await workspace.shell("python3 -c 'import errno,socket\ns=socket.socket(socket.AF_INET,socket.SOCK_DGRAM)\ntry: s.connect((\"192.0.2.1\",9))\nexcept OSError as e: assert e.errno == errno.ENETUNREACH\nelse: raise AssertionError(\"external route remains\")\nprint(\"offline\")'")
    assert result.exit_code == 0, result.output
    assert result.output.strip() == 'offline'


@real_docker
@pytest.mark.asyncio
async def test_real_docker_shell_kills_residual_background_group(docker_workspace):
    workspace, _ = docker_workspace
    result = await workspace.shell('(sleep 2; echo escaped > /workspace/background-marker) & echo foreground-done')
    assert result.exit_code == 0, result.output
    await asyncio.sleep(3)
    result = await workspace.shell('test ! -e /workspace/background-marker && echo residual-stopped')
    assert result.exit_code == 0, result.output
    assert result.output.strip() == 'residual-stopped'


@real_docker
@pytest.mark.asyncio
async def test_real_docker_shell_timeout_stops_container(docker_workspace):
    workspace, _ = docker_workspace
    with pytest.raises(WorkspaceError):
        await workspace.shell('sleep 120 & wait', timeout=1)
    await workspace.cleanup()
    code, _ = await command('docker', 'inspect', f'repopilot-run-{workspace.run_id}')
    assert code != 0


@real_docker
@pytest.mark.asyncio
async def test_real_docker_cancel_stops_container_and_its_children(docker_workspace):
    workspace, _ = docker_workspace
    shell_task = asyncio.create_task(workspace.shell('sleep 120 & echo ready > /workspace/cancel-ready; wait'))
    try:
        # Observe actual command startup rather than assuming scheduling completed.
        for _ in range(50):
            code, output = await command('docker', 'top', f'repopilot-run-{workspace.run_id}', '-eo', 'pid,args')
            if code == 0 and any(row.split(maxsplit=1)[-1] == 'sleep 120' for row in output.splitlines()[1:]):
                break
            await asyncio.sleep(0.1)
        else:
            pytest.fail('The cancellation command did not start')
        code, output = await command('docker', 'top', f'repopilot-run-{workspace.run_id}')
        assert code == 0 and 'sleep 120' in output, output
        shell_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await shell_task
        await workspace.cleanup()
        code, _ = await command('docker', 'inspect', f'repopilot-run-{workspace.run_id}')
        assert code != 0
        # Cleanup remains idempotent only once Docker confirms absence.
        await workspace.cleanup()
    finally:
        if not shell_task.done():
            shell_task.cancel()
            await asyncio.gather(shell_task, return_exceptions=True)


@real_docker
@pytest.mark.asyncio
async def test_real_docker_capture_patch_uses_external_baseline(tmp_path, docker_workspace):
    workspace, _ = docker_workspace
    result = await workspace.shell("printf 'def add(a, b): return a + b\\n' > calc.py; mkdir -p node_modules; printf 'cache' > node_modules/cache")
    assert result.exit_code == 0, result.output
    patch = await workspace.capture()
    assert '.git/' not in patch
    baseline = tmp_path / 'fresh'
    baseline.mkdir()
    (baseline / 'calc.py').write_text('def add(a, b): return a - b\n')
    patch_file = tmp_path / 'result.patch'
    patch_file.write_text(patch)
    code, output = await command('git', 'apply', '--binary', str(patch_file), cwd=baseline)
    assert code == 0, output
    assert (baseline / 'calc.py').read_text() == 'def add(a, b): return a + b\n'
    assert not (baseline / 'node_modules').exists()
    with pytest.raises(WorkspaceError):
        await workspace.shell('echo cannot-run-after-capture')


@real_docker
@pytest.mark.asyncio
async def test_real_docker_missing_image_is_blocked_without_pull_or_container():
    assert shutil.which('docker'), 'Docker tests enabled but Docker CLI is unavailable'
    workspace = Workspace(uuid4())
    try:
        with pytest.raises(WorkspaceError) as error:
            await workspace.prepare(
                source=source(),
                github_credentials=GitHubGitCredentials(api_token=TOKEN, private_key=''),
                git_auth_strategy='https_token', image=f'repopilot-regression-absent:{uuid4().hex}',
            )
        assert error.value.blocked is True
        assert '执行镜像不存在' in str(error.value)
        code, _ = await command('docker', 'inspect', f'repopilot-run-{workspace.run_id}')
        assert code != 0
    finally:
        await workspace.cleanup()
