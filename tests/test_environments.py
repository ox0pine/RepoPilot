from __future__ import annotations

import json
import os
import shutil
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from unittest.mock import patch
from uuid import uuid4

import pytest

from repopilot.domain.context import RepositoryContext
from repopilot.domain.tasks import SourceSnapshot
from repopilot.execution.git import GitHubGitCredentials
from repopilot.execution.workspace import Workspace

DOCKER_ENABLED = os.environ.get('REPOPILOT_DOCKER_TESTS') == '1'
real_docker = pytest.mark.skipif(
    not DOCKER_ENABLED, reason='Set REPOPILOT_DOCKER_TESTS=1 for real Docker coverage',
)
COMMIT = 'a' * 40
TOKEN = 'semantic-fixture-secret-never-enters-run'


def fixture_source() -> SourceSnapshot:
    now = datetime.now(UTC)
    return SourceSnapshot(
        repository_url='https://github.com/example/semantic-fixture',
        baseline_commit=COMMIT, issue_number=1, issue_title='Semantic fixture',
        issue_body='', issue_url='https://github.com/example/semantic-fixture/issues/1',
        issue_updated_at=now, fetched_at=now,
        repository_context=RepositoryContext(commit=COMMIT, tree=[], files=[], omissions=[], tree_truncated=False),
    )


@asynccontextmanager
async def prepared_workspace(files: dict[str, str]):
    if not DOCKER_ENABLED:
        pytest.skip('Set REPOPILOT_DOCKER_TESTS=1 for real Docker coverage')
    assert shutil.which('docker'), 'Docker coverage enabled but Docker CLI is unavailable'

    async def clone_fixture(repository_url, commit, destination, credentials, *, strategy, timeout=90):
        destination.mkdir()
        (destination / '.git').mkdir()
        for name, content in files.items():
            target = destination / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content)

    workspace = Workspace(uuid4())
    try:
        with patch('repopilot.execution.workspace.clone_fixed_commit', clone_fixture):
            await workspace.prepare(
                source=fixture_source(),
                github_credentials=GitHubGitCredentials(api_token=TOKEN, private_key=''),
                git_auth_strategy='https_token',
                image=os.environ.get('REPOPILOT_TEST_IMAGE', 'repopilot-dev:local'),
            )
        result = await workspace.setup()
        assert result.exit_code == 0, result.output
        yield workspace
    finally:
        await workspace.cleanup()


def python_project(name: str = 'semantic-fixture', *, requires: str = '>=3.12,<3.13',
                   dependencies: tuple[str, ...] = ()) -> str:
    return ('[project]\n' + f'name = {json.dumps(name)}\nversion = "0.0.0"\n'
            + f'requires-python = {json.dumps(requires)}\n'
            + f'dependencies = {json.dumps(dependencies)}\n')


def node_project(*, engines: dict | None = None, dependencies: dict | None = None,
                 manager: str | None = None) -> str:
    result = {'name': 'semantic-fixture', 'version': '0.0.0', 'private': True}
    if engines is not None:
        result['engines'] = engines
    if dependencies is not None:
        result['dependencies'] = dependencies
    if manager is not None:
        result['packageManager'] = manager
    return json.dumps(result)


def test_discovery_routes_nested_monorepo_projects_without_generated_directories(tmp_path):
    from repopilot.execution.environment import discover_projects

    files = {
        'pyproject.toml': python_project('root-package'),
        'services/api/pyproject.toml': python_project('api-package'),
        'apps/web/package.json': node_project(),
        'apps/vue/package.json': node_project(dependencies={'vue': '3.5.13'}),
        'node_modules/vendor/package.json': node_project(),
        '.venv/vendor/pyproject.toml': python_project('ignored'),
    }
    for name, text in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    projects = discover_projects(tmp_path)
    assert {project['root'] for project in projects} == {'.', 'services/api', 'apps/web', 'apps/vue'}
    languages = {project['root']: project['language'] for project in projects}
    assert languages['.'] == languages['services/api'] == 'python'
    assert languages['apps/web'] == 'typescript'
    assert languages['apps/vue'] == 'vue'


def test_manifest_free_sources_remain_valid_without_invented_projects(tmp_path):
    from repopilot.execution.environment import discover_projects

    (tmp_path / 'hello.py').write_text('print("hello")\n')
    (tmp_path / 'hello.ts').write_text('export const hello = "world";\n')
    assert discover_projects(tmp_path) == []


@real_docker
@pytest.mark.asyncio
async def test_real_python_projects_have_independent_venvs_and_current_interpreters():
    files = {
        'first/pyproject.toml': python_project('first'),
        'second/pyproject.toml': python_project('second'),
    }
    async with prepared_workspace(files) as workspace:
        info = await workspace.environment_info()
        projects = {project['root']: project for project in info['projects']}
        assert set(projects) == {'first', 'second'}
        assert all(project['ready'] for project in projects.values()), info
        interpreters = [project['interpreter'] for project in projects.values()]
        assert len(set(interpreters)) == 2
        for root, project in projects.items():
            assert project['interpreter'].endswith(f'/{root}/.venv/bin/python')
        result = await workspace.shell(
            "python3 -c 'import pathlib,subprocess; "
            "paths=[pathlib.Path(p)/\".venv/bin/python\" for p in (\"first\",\"second\")]; "
            "assert all(p.exists() for p in paths); "
            "assert all(subprocess.check_output([str(p),\"-c\",\"import sys; print(sys.version_info[:2])\"]).strip()==b\"(3, 12)\" for p in paths); "
            "pathlib.Path(\"first/.venv/isolated-marker\").write_text(\"first\"); "
            "assert not pathlib.Path(\"second/.venv/isolated-marker\").exists()'"
        )
        assert result.exit_code == 0, result.output


@real_docker
@pytest.mark.asyncio
async def test_real_manifest_free_workspace_keeps_basic_tools_offline():
    async with prepared_workspace({'hello.py': 'print("hello")\n'}) as workspace:
        assert (await workspace.environment_info())['projects'] == []
        result = await workspace.tool('read_file', {'path': 'hello.py'})
        assert 'print("hello")' in result['content']
        network = await workspace._docker('inspect', '--format', '{{json .NetworkSettings.Networks}}', workspace.container_name)
        assert network[0] == 0
        assert set(json.loads(network[1])) <= {'none'}


@pytest.mark.parametrize('requirement,version,compatible', [
    ('>=3.12,<3.13', '3.12.11', True),
    ('>=3.12,<3.13', '3.11.9', False),
    ('>=3.12,<3.13', '3.13.0', False),
    ('==3.12.*', '3.12.11', True),
    ('>=99', '3.12.11', False),
])
def test_python_constraints_are_checked_not_silently_substituted(requirement, version, compatible):
    from repopilot.execution.environment import python_compatible

    assert python_compatible(requirement, version) is compatible


@pytest.mark.parametrize('locks,manager,reason', [
    ({'package-lock.json': '{}', 'pnpm-lock.yaml': 'lockfileVersion: 9'}, None, 'lockfile'),
    ({'package-lock.json': '{}'}, 'pnpm@9.15.9', 'conflict'),
    ({'yarn.lock': '# fixture'}, 'npm@10.9.2', 'conflict'),
    ({}, 'npm@invalid', 'packagemanager'),
])
def test_node_manager_conflicts_block_before_any_install(tmp_path, monkeypatch, locks, manager, reason):
    from repopilot.execution import environment

    (tmp_path / 'package.json').write_text(node_project(manager=manager))
    for name, text in locks.items():
        (tmp_path / name).write_text(text)
    monkeypatch.setattr(environment.subprocess, 'check_output', lambda *args, **kwargs: 'v22.16.0\n')
    monkeypatch.setattr(environment, 'node_compatible', lambda *args: True)

    def forbidden_install(*args, **kwargs):
        pytest.fail('Invalid project must be blocked before dependency installation')

    monkeypatch.setattr(environment, '_environment_command', forbidden_install)
    result = environment.prepare_environments(tmp_path)
    assert result['ready'] is False
    assert result['projects'][0]['ready'] is False
    assert reason in result['projects'][0]['blocked_reason'].lower()


def test_offline_missing_node_dependency_is_explicitly_blocked(tmp_path, monkeypatch):
    from repopilot.execution import environment

    (tmp_path / 'package.json').write_text(node_project(dependencies={'missing-fixture-package': '1.0.0'}))
    monkeypatch.setattr(environment.subprocess, 'check_output', lambda *args, **kwargs: 'v22.16.0\n')
    monkeypatch.setattr(environment, 'node_compatible', lambda *args: True)
    calls = []

    def offline_install(argv, cwd, network):
        calls.append((argv, cwd, network))
        assert network is False
        assert '--offline' in argv
        return False, 'dependency not present in offline cache'

    monkeypatch.setattr(environment, '_environment_command', offline_install)
    result = environment.prepare_environments(tmp_path, network=False)
    assert calls
    assert result['ready'] is False
    assert result['projects'][0]['ready'] is False
    assert result['projects'][0]['blocked_reason']


def test_node_engine_mismatch_is_blocked_before_install(tmp_path, monkeypatch):
    from repopilot.execution import environment

    (tmp_path / 'package.json').write_text(node_project(engines={'node': '>=99'}))
    monkeypatch.setattr(environment.subprocess, 'check_output', lambda *args, **kwargs: 'v22.16.0\n')
    monkeypatch.setattr(environment, 'node_compatible', lambda *args: False)
    result = environment.prepare_environments(tmp_path, network=False)
    assert result['ready'] is False
    assert '99' in result['projects'][0]['blocked_reason']


def test_project_discovery_rejects_manifest_links(tmp_path):
    from repopilot.execution.environment import discover_projects

    target = tmp_path / 'outside.json'
    target.write_text(node_project())
    (tmp_path / 'package.json').symlink_to(target)
    with pytest.raises(ValueError, match='Unsafe'):
        discover_projects(tmp_path)


@real_docker
@pytest.mark.asyncio
@pytest.mark.parametrize('files', [
    {'pyproject.toml': python_project(requires='>=99')},
    {'package.json': node_project(engines={'node': '>=99'})},
    {'package.json': node_project(), 'package-lock.json': '{}', 'yarn.lock': '# conflict'},
    {'package.json': node_project(manager='pnpm@9.15.9'), 'package-lock.json': '{}'},
], ids=['python-version', 'node-version', 'multiple-locks', 'manager-lock-mismatch'])
async def test_real_incompatible_environment_is_explicitly_blocked(files):
    from repopilot.execution.workspace import WorkspaceError

    with pytest.raises(WorkspaceError) as caught:
        async with prepared_workspace(files):
            pytest.fail('Incompatible environment must not become model-ready')
    assert caught.value.blocked


@real_docker
@pytest.mark.asyncio
async def test_real_python_compatible_runtime_is_automatically_provisioned():
    async with prepared_workspace({'pyproject.toml': python_project(requires='>=3.11,<3.12')}) as workspace:
        project = (await workspace.environment_info())['projects'][0]
        assert project['ready'], project
        assert project['python_version'].startswith('3.11.'), project
        result = await workspace.shell('.venv/bin/python -c "import sys; assert sys.version_info[:2] == (3, 11)"')
        assert result.exit_code == 0, result.output


@real_docker
@pytest.mark.asyncio
async def test_real_node_compatible_runtime_is_automatically_provisioned():
    async with prepared_workspace({'package.json': node_project(engines={'node': '>=20 <21'})}) as workspace:
        project = (await workspace.environment_info())['projects'][0]
        assert project['ready'], project
        assert project['node_version'].startswith('20.'), project
        result = await workspace.shell("node -e 'if(process.versions.node.split(\".\")[0]!==\"20\")process.exit(1)'")
        assert result.exit_code == 0, result.output


def test_offline_python_missing_dependency_is_explicitly_blocked(tmp_path, monkeypatch):
    from repopilot.execution import environment

    (tmp_path / 'pyproject.toml').write_text(python_project(dependencies=('missing-fixture-package==1.0.0',)))
    interpreter = tmp_path / '.venv/bin/python'
    interpreter.parent.mkdir(parents=True)
    interpreter.write_text('fixture interpreter path; never executed')
    monkeypatch.setattr(environment, '_python_toolchain', lambda requirement, network: ('python3', '3.12.11'))
    monkeypatch.setattr(environment.subprocess, 'check_output', lambda *args, **kwargs: '3.12.11\n')

    def offline_sync(argv, cwd, network):
        assert argv[:2] == ['uv', 'sync']
        assert '--offline' in argv
        assert network is False
        return False, 'dependency is not in offline cache'

    monkeypatch.setattr(environment, '_environment_command', offline_sync)
    result = environment.prepare_environments(tmp_path, network=False)
    assert result['ready'] is False
    assert result['projects'][0]['blocked_reason']


def test_existing_uv_lock_is_authoritative_and_not_rewritten(tmp_path, monkeypatch):
    from repopilot.execution import environment

    (tmp_path / 'pyproject.toml').write_text(python_project())
    lock = tmp_path / 'uv.lock'
    lock.write_text('version = 1\n# deliberately stale lock fixture\n')
    interpreter = tmp_path / '.venv/bin/python'
    interpreter.parent.mkdir(parents=True)
    interpreter.write_text('fixture interpreter path; never executed')
    monkeypatch.setattr(environment, '_python_toolchain', lambda requirement, network: ('python3', '3.12.11'))
    monkeypatch.setattr(environment.subprocess, 'check_output', lambda *args, **kwargs: '3.12.11\n')
    calls = []

    def locked_sync(argv, cwd, network):
        calls.append(argv)
        assert argv[:2] == ['uv', 'sync']
        assert '--locked' in argv
        assert '--offline' in argv
        return False, 'stale lock cannot be updated with --locked'

    monkeypatch.setattr(environment, '_environment_command', locked_sync)
    result = environment.prepare_environments(tmp_path, network=False)
    assert calls
    assert result['ready'] is False
    assert lock.read_text() == 'version = 1\n# deliberately stale lock fixture\n'


@real_docker
@pytest.mark.asyncio
async def test_real_capture_preserves_tracked_generated_directory_files_only():
    async with prepared_workspace({
        'hello.py': 'print("hello")\n',
        'dist/keep.txt': 'baseline tracked content\n',
    }) as workspace:
        changed = await workspace.shell(
            "printf 'edited tracked content\\n' > dist/keep.txt; "
            "printf 'new generated content\\n' > dist/generated.txt"
        )
        assert changed.exit_code == 0, changed.output
        patch = await workspace.capture()
        assert 'dist/keep.txt' in patch
        assert '+edited tracked content' in patch
        assert 'dist/generated.txt' not in patch
        assert 'new generated content' not in patch


@real_docker
@pytest.mark.asyncio
@pytest.mark.parametrize('manager,version', [('pnpm', '10.11.0'), ('yarn', '4.9.2')])
async def test_real_exact_package_manager_is_automatically_installed(manager, version):
    async with prepared_workspace({
        'package.json': node_project(manager=f'{manager}@{version}'),
    }) as workspace:
        project = (await workspace.environment_info())['projects'][0]
        assert project['ready'], project
        assert project['package_manager'] == f'{manager}@{version}', project
        result = await workspace.shell(f'{manager} --version')
        assert result.exit_code == 0, result.output
        assert result.output.strip() == version


@real_docker
@pytest.mark.asyncio
async def test_real_python_src_package_is_installed_even_without_runtime_dependencies():
    manifest = (
        python_project('tiny-src-fixture')
        + '\n[build-system]\nrequires = ["setuptools==80.9.0"]\n'
        + 'build-backend = "setuptools.build_meta"\n'
        + '\n[tool.setuptools.packages.find]\nwhere = ["src"]\n'
    )
    async with prepared_workspace({
        'pyproject.toml': manifest,
        'src/tiny_fixture/__init__.py': 'VALUE = 42\n',
    }) as workspace:
        project = (await workspace.environment_info())['projects'][0]
        assert project['ready'], project
        result = await workspace.shell(
            '.venv/bin/python -I -c "import tiny_fixture; assert tiny_fixture.VALUE == 42"'
        )
        assert result.exit_code == 0, result.output


NPM_WORKSPACE_FILES = {
    'package.json': json.dumps({
        'name': 'fixture-workspace', 'private': True, 'version': '0.0.0',
        'packageManager': 'npm@10.9.2', 'engines': {'node': '>=22 <23'},
        'workspaces': ['packages/*'], 'devDependencies': {'typescript': '5.8.3'},
    }),
    'tsconfig.json': json.dumps({'compilerOptions': {
        'strict': True, 'target': 'ES2022', 'module': 'ESNext',
        'moduleResolution': 'Bundler', 'noEmit': True,
    }, 'include': ['packages/**/*.ts']}),
    'packages/shared/package.json': json.dumps({
        'name': '@fixture/shared', 'version': '1.0.0', 'private': True,
        'type': 'module', 'exports': './src/index.ts', 'types': './src/index.ts',
    }),
    'packages/shared/src/index.ts': (
        'export function sharedGreeting(name: string): string { return "Hello " + name; }\n'
    ),
    'packages/app/package.json': json.dumps({
        'name': '@fixture/app', 'version': '1.0.0', 'private': True,
        'type': 'module', 'dependencies': {'@fixture/shared': '*'},
    }),
    'packages/app/src/index.ts': (
        'import { sharedGreeting } from "@fixture/shared";\n'
        'export const message = sharedGreeting("workspace");\n'
        'const invalid: number = "wrong";\n'
    ),
}


@real_docker
@pytest.mark.asyncio
async def test_real_npm_workspace_inherits_root_environment_and_resolves_crosspackage_import():
    async with prepared_workspace(NPM_WORKSPACE_FILES) as workspace:
        info = await workspace.environment_info()
        projects = {project['root']: project for project in info['projects']}
        assert set(projects) == {'.', 'packages/shared', 'packages/app'}, info
        assert all(project['ready'] for project in projects.values()), info
        for project in projects.values():
            assert project['package_manager'] == 'npm@10.9.2', project
            assert project['typescript_sdk'] == '/workspace/node_modules/typescript/lib', project
            assert project['install_root'] == '.', project
            assert project['lockfile'] == 'package-lock.json', project
        assert set(projects['.']['workspace_packages']) == {'packages/shared', 'packages/app'}
        result = await workspace.shell(
            "node -e 'const fs=require(\"fs\"); "
            "if(!fs.existsSync(\"package-lock.json\"))process.exit(1); "
            "if(fs.existsSync(\"packages/app/package-lock.json\"))process.exit(2); "
            "if(!fs.existsSync(\"node_modules/@fixture/shared/src/index.ts\"))process.exit(3); "
            "const ts=require(\"typescript\"); if(ts.version!==\"5.8.3\")process.exit(4); "
            "const r=ts.resolveModuleName(\"@fixture/shared\",\"/workspace/packages/app/src/index.ts\", "
            "{moduleResolution:ts.ModuleResolutionKind.Bundler},ts.sys).resolvedModule; "
            "if(!r || !r.resolvedFileName.endsWith(\"packages/shared/src/index.ts\"))process.exit(5)'"
        )
        assert result.exit_code == 0, result.output
        consumer = NPM_WORKSPACE_FILES['packages/app/src/index.ts'].splitlines()[1]
        definition = await workspace.lsp_tool('lsp_definition', {
            'path': 'packages/app/src/index.ts', 'line': 2,
            'column': consumer.index('sharedGreeting') + 1,
        })
        assert definition['status'] == 'ok', definition
        assert any(location.get('path') == 'packages/shared/src/index.ts'
                   for location in definition['data']), definition
