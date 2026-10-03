"""Trusted per-project planning. Executed inside Run, never on host source trees."""
from __future__ import annotations

import json
import os
import re
import stat
import subprocess
import sys
import tomllib
from pathlib import Path

ENV_IGNORES = {'.git', '.venv', 'node_modules', '__pycache__', 'dist', 'build', 'vendor', '.yarn', '.pnpm-store'}
PACKAGE_MANAGERS = {'npm': '10.9.2', 'pnpm': '9.15.9', 'yarn': '1.22.22'}
MAX_PROJECTS = 16
ENV_ACTIONS: list[dict] = []


def discover_projects(root: Path) -> list[dict]:
    projects = []
    visited = 0
    for directory, dirs, files in os.walk(root, followlinks=False):
        current = Path(directory)
        dirs[:] = sorted(d for d in dirs if d not in ENV_IGNORES and not (current / d).is_symlink())
        visited += len(files) + len(dirs)
        if visited > 20000:
            raise ValueError('Project discovery exceeds 20000 entries')
        for manifest, language in [('pyproject.toml', 'python'), ('package.json', 'typescript')]:
            if manifest not in files:
                continue
            target = current / manifest
            if target.is_symlink() or not stat.S_ISREG(target.stat().st_mode) or target.stat().st_size > 1024 * 1024:
                raise ValueError('Unsafe or oversized project manifest')
            data = tomllib.loads(target.read_text()) if language == 'python' else json.loads(target.read_text())
            if not isinstance(data, dict):
                raise ValueError('Project manifest must contain an object')
            if language == 'typescript' and any('vue' in data.get(key, {}) for key in ('dependencies', 'devDependencies')):
                language = 'vue'
            projects.append({'root': current.relative_to(root).as_posix(), 'language': language, 'manifest': manifest, 'data': data})
            if len(projects) > MAX_PROJECTS:
                raise ValueError('Project discovery exceeds 16 manifests')
    if not projects:
        tests = _test_files(root)
        if tests:
            data = {'project': {'requires-python': '>=3.11'}, 'tool': {'uv': {'package': False}}}
            if not any('unittest' in p.read_text(errors='replace') for p in tests):
                data['inferred_pytest'] = True
            projects.append({'root': '.', 'language': 'python', 'manifest': None, 'inferred': True, 'data': data})
    _assign_node_workspaces(root, projects)
    return projects


def python_compatible(requirement: str, version: str = '3.12.11') -> bool:
    if not isinstance(requirement, str):
        raise ValueError('requires-python must be a string')
    if '/opt/lsp/python' not in sys.path:
        sys.path.append('/opt/lsp/python')
    from packaging.specifiers import SpecifierSet
    return SpecifierSet(requirement).contains(version, prereleases=True)


def node_compatible(requirement: str, version: str = '22.16.0') -> bool:
    if not isinstance(requirement, str):
        raise ValueError('Node engine must be a string')
    result = subprocess.run(['node', '-e', 'const s=require("/opt/lsp/node_modules/semver");const r=process.argv[1];if(s.validRange(r)===null)process.exit(2);process.exit(s.satisfies(process.argv[2],r)?0:1)', requirement, version], capture_output=True, timeout=10)
    if result.returncode == 2:
        raise ValueError('Invalid Node semver range')
    return result.returncode == 0


def _environment_command(argv: list[str], cwd: Path, network: bool) -> tuple[bool, str]:
    ENV_ACTIONS.append({'cwd': str(cwd), 'argv': list(argv)})
    env = os.environ.copy()
    env.update({'UV_PYTHON_DOWNLOADS': 'automatic' if network else 'never', 'UV_NO_PROGRESS': '1', 'UV_CACHE_DIR': '/home/runner/.cache/uv', 'UV_PYTHON_INSTALL_DIR': '/home/runner/.local/share/uv/python', 'COREPACK_ENABLE_NETWORK': '0', 'npm_config_cache': '/home/runner/.npm'})
    if str(cwd).startswith('/workspace'):
        env['UV_PROJECT_ENVIRONMENT'] = str(cwd / '.venv')
    env['UV_LINK_MODE'] = 'copy'
    import selectors
    import time
    proc = subprocess.Popen(argv, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True)
    assert proc.stdout is not None
    os.set_blocking(proc.stdout.fileno(), False)
    selector = selectors.DefaultSelector()
    selector.register(proc.stdout, selectors.EVENT_READ)
    output = bytearray()
    total = 0
    deadline = time.monotonic() + 240
    try:
        while proc.poll() is None:
            if time.monotonic() > deadline or total > 8 * 1024 * 1024:
                raise ValueError('Automatic preparation exceeds command time/output bound')
            for key, _ in selector.select(.05):
                chunk = os.read(key.fd, 65536)
                total += len(chunk)
                output.extend(chunk)
                if len(output) > 8192:
                    del output[:-8192]
        while chunk := os.read(proc.stdout.fileno(), 65536):
            output.extend(chunk)
            if len(output) > 8192:
                del output[:-8192]
        return proc.returncode == 0, output.decode(errors='replace')
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait()
        selector.close()
        proc.stdout.close()


def _python_toolchain(requirement: str, network: bool) -> tuple[str, str]:
    current = subprocess.check_output(['python3', '-I', '-S', '-c', 'import platform;print(platform.python_version())'], text=True, timeout=10).strip()
    if python_compatible(requirement, current):
        return 'python3', current
    inventory = subprocess.check_output(['uv', 'python', 'list', '--all-versions', '--no-config', '--color', 'never'], text=True, timeout=15, cwd='/tmp')
    if len(inventory) > 1024 * 1024:
        raise ValueError('Python runtime inventory exceeds safety bound')
    versions = set(re.findall(r'cpython-(3\.(?:11|12|13)\.\d+)-', inventory))
    for version in sorted(versions, key=lambda v: tuple(map(int, v.split('.'))), reverse=True):
        if not python_compatible(requirement, version):
            continue
        found = subprocess.run(['uv', 'python', 'find', '--no-python-downloads', version], capture_output=True, text=True, timeout=15)
        if found.returncode and network:
            ok, output = _environment_command(['uv', 'python', 'install', version], Path('/tmp'), True)
            if not ok:
                raise ValueError('Automatic Python provisioning failed: ' + output)
            found = subprocess.run(['uv', 'python', 'find', '--no-python-downloads', version], capture_output=True, text=True, timeout=15)
        if found.returncode == 0:
            return found.stdout.strip(), version
        raise ValueError('Compatible Python is not installed and offline provisioning is unavailable')
    raise ValueError('Unsupported requires-python: ' + requirement + '; no compatible official managed Python 3.11/3.12/3.13 release is available')


def _node_toolchain(requirement: str, network: bool) -> tuple[str, str]:
    import hashlib
    import platform
    import tarfile
    import urllib.request
    import urllib.parse
    current = subprocess.check_output(['node', '--version'], text=True, timeout=10).strip().removeprefix('v')
    if node_compatible(requirement, current):
        return '/usr/local/bin', current
    class OfficialRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            parsed = urllib.parse.urlsplit(newurl)
            if parsed.scheme != 'https' or parsed.netloc != 'nodejs.org':
                raise ValueError('Unsafe Node download redirect')
            return super().redirect_request(req, fp, code, msg, headers, newurl)
    opener = urllib.request.build_opener(OfficialRedirect())
    inventory_path = Path('/home/runner/.local/share/node/releases.json')
    if network:
        with opener.open('https://nodejs.org/dist/index.json', timeout=45) as response:
            inventory_bytes = response.read(2 * 1024 * 1024 + 1)
        if len(inventory_bytes) > 2 * 1024 * 1024:
            raise ValueError('Official Node release inventory exceeds safety bound')
        inventory_path.parent.mkdir(parents=True, exist_ok=True)
        inventory_path.write_bytes(inventory_bytes)
    elif inventory_path.is_file():
        inventory_bytes = inventory_path.read_bytes()
    else:
        raise ValueError('Node engine ' + requirement + ' requires automatic official provisioning (unavailable offline)')
    inventory = json.loads(inventory_bytes)
    versions = [r['version'].removeprefix('v') for r in inventory if isinstance(r, dict) and re.fullmatch(r'v(?:20|22)\.\d+\.\d+', r.get('version', ''))]
    selected = subprocess.check_output(['node', '-e', 'const s=require("/opt/lsp/node_modules/semver");const vs=JSON.parse(process.argv[2]);console.log(vs.filter(v=>s.satisfies(v,process.argv[1])).sort(s.rcompare)[0]||"")', requirement, json.dumps(versions)], text=True, timeout=10).strip()
    for version in ([selected] if selected else []):
        if not node_compatible(requirement, version):
            continue
        arch = {'x86_64': 'x64', 'aarch64': 'arm64'}.get(platform.machine())
        if arch is None:
            raise ValueError('Unsupported Node runtime architecture')
        basename = f'node-v{version}-linux-{arch}'
        destination = Path('/home/runner/.local/share/node') / basename
        if (destination / 'bin/node').is_file():
            return str(destination / 'bin'), version
        if not network:
            raise ValueError('Compatible Node is unavailable offline')
        def download(name, maximum):
            with opener.open(f'https://nodejs.org/dist/v{version}/{name}', timeout=45) as response:
                data = response.read(maximum + 1)
            if len(data) > maximum:
                raise ValueError('Node download exceeds safety bound')
            return data
        checksums = download('SHASUMS256.txt', 65536).decode('ascii')
        filename = basename + '.tar.xz'
        expected = next((line.split()[0] for line in checksums.splitlines() if line.split()[-1] == filename), None)
        archive = download(filename, 64 * 1024 * 1024)
        if expected is None or hashlib.sha256(archive).hexdigest() != expected:
            raise ValueError('Node official archive checksum mismatch')
        import io
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tarfile.open(fileobj=io.BytesIO(archive), mode='r:xz') as bundle:
            members = bundle.getmembers()
            if len(members) > 10000 or sum(m.size for m in members) > 256 * 1024 * 1024:
                raise ValueError('Node archive exceeds extraction bounds')
            for member in members:
                parts = member.name.split('/')
                if parts[0] != basename or '..' in parts or member.name.startswith('/') or not (member.isdir() or member.isfile() or member.issym()):
                    raise ValueError('Unsafe official Node archive')
            bundle.extractall(destination.parent, filter='data')
        actual = subprocess.check_output([str(destination / 'bin/node'), '--version'], text=True, timeout=10).strip().removeprefix('v')
        if actual != version:
            raise ValueError('Provisioned Node version mismatch')
        return str(destination / 'bin'), version
    raise ValueError('Unsupported Node engine: ' + requirement + '; managed Node releases are 20.19.2 and 22.16.0')


def _test_files(root: Path) -> list[Path]:
    result = []
    count = 0
    for directory, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = sorted(d for d in dirs if d not in ENV_IGNORES and not (Path(directory) / d).is_symlink())
        count += len(dirs) + len(files)
        if count > 20000:
            raise ValueError('Test discovery exceeds 20000 entries')
        for name in sorted(files):
            target = Path(directory) / name
            if name.startswith('test_') and name.endswith('.py') and not target.is_symlink() and target.stat().st_size <= 1024 * 1024:
                result.append(target)
                if len(result) > 200:
                    raise ValueError('Test discovery exceeds 200 files')
    return result


def _workspace_patterns(root: Path, project: dict) -> list[str]:
    declaration = project['data'].get('workspaces', [])
    if isinstance(declaration, dict):
        declaration = declaration.get('packages', [])
    if not isinstance(declaration, list) or any(not isinstance(value, str) for value in declaration):
        raise ValueError('package.json workspaces must contain bounded package patterns')
    patterns = list(declaration)
    yaml = root / project['root'] / 'pnpm-workspace.yaml'
    if yaml.exists():
        if yaml.is_symlink() or not yaml.is_file() or yaml.stat().st_size > 65536:
            raise ValueError('Unsafe pnpm-workspace.yaml')
        import ast
        in_packages = False
        try:
            for line in yaml.read_text().splitlines():
                if re.match(r'^packages\s*:', line):
                    tail = line.split(':', 1)[1].strip()
                    if tail and not tail.startswith('#'):
                        parsed = ast.literal_eval(tail)
                        if not isinstance(parsed, list) or any(not isinstance(v, str) for v in parsed):
                            raise ValueError('Invalid pnpm workspace packages list')
                        patterns.extend(parsed)
                        in_packages = False
                    else:
                        in_packages = True
                elif in_packages:
                    if line and not line[0].isspace() and not line.startswith('-') and not line.startswith('#'):
                        in_packages = False
                        continue
                    matched = re.match(r'^\s*-\s*(.*?)\s*(?:\s+#.*)?$', line)
                    if matched:
                        value = matched.group(1).strip()
                        if value.startswith(('"', "'")):
                            value = ast.literal_eval(value)
                        if not isinstance(value, str):
                            raise ValueError('Invalid pnpm workspace package pattern')
                        patterns.append(value)
        except SyntaxError:
            raise ValueError('Invalid pnpm workspace packages syntax') from None
    if len(patterns) > 128 or any(not p or len(p) > 512 or p.lstrip('!').startswith('/') or '..' in p.split('/') for p in patterns):
        raise ValueError('Workspace patterns exceed bounds or contain traversal')
    return patterns


def _workspace_match(relative: str, pattern: str) -> bool:
    import fnmatch
    from functools import lru_cache
    names, parts = relative.split('/'), pattern.removeprefix('./').rstrip('/').split('/')
    @lru_cache(maxsize=None)
    def match(i: int, j: int) -> bool:
        if j == len(parts):
            return i == len(names)
        if parts[j] == '**':
            return match(i, j + 1) or (i < len(names) and match(i + 1, j))
        return i < len(names) and fnmatch.fnmatchcase(names[i], parts[j]) and match(i + 1, j + 1)
    return match(0, 0)


def _assign_node_workspaces(root: Path, projects: list[dict]) -> None:
    frontend = [p for p in projects if p['language'] != 'python']
    owners = [(p, _workspace_patterns(root, p)) for p in frontend]
    for project in frontend:
        possible = []
        for owner, patterns in owners:
            if owner is project:
                continue
            try:
                relative = (root / project['root']).relative_to(root / owner['root']).as_posix()
            except ValueError:
                continue
            included = False
            for pattern in patterns:
                if _workspace_match(relative, pattern.removeprefix('!')):
                    included = not pattern.startswith('!')
            if included:
                possible.append(owner)
        owner = max(possible, key=lambda p: len(p['root'].split('/'))) if possible else project
        project['install_root'] = owner.get('install_root', owner['root'])
    for project in frontend:
        project['workspace_packages'] = [p['root'] for p in frontend if p['root'] != project['root'] and p['install_root'] == project['root']]


def _node_module(cwd: Path, install_root: Path, name: str) -> Path | None:
    current = cwd
    while True:
        candidate = current / 'node_modules' / name
        if (candidate / 'package.json').is_file():
            return candidate
        if current == install_root or current.parent == current:
            return None
        current = current.parent


def _package_manager(manager: str, version: str, node_bin: str, network: bool) -> str:
    if version == PACKAGE_MANAGERS[manager]:
        return '/usr/local/bin/' + manager
    destination = Path('/home/runner/.local/share/package-managers') / (manager + '-' + version)
    executable = destination / 'node_modules/.bin' / manager
    package = '@yarnpkg/cli-dist' if manager == 'yarn' and int(version.split('.')[0]) >= 2 else manager
    if not executable.is_file():
        argv = ['/usr/bin/env', 'PATH=' + node_bin + ':/usr/local/bin:/usr/bin:/bin', '/usr/local/bin/npm', 'install', '--prefix', str(destination), '--registry=https://registry.npmjs.org', '--userconfig=/dev/null', '--globalconfig=/opt/lsp/repopilot-empty-npmrc', '--ignore-scripts', '--no-audit', '--no-fund', '--save-exact', package + '@' + version]
        if not network:
            argv.append('--offline')
        ok, output = _environment_command(argv, Path('/tmp'), network)
        if not ok:
            raise ValueError('Automatic exact package-manager provisioning failed: ' + output)
    actual = subprocess.check_output(['/usr/bin/env', 'PATH=' + node_bin + ':/usr/local/bin:/usr/bin:/bin', str(executable), '--version'], text=True, timeout=15, cwd='/tmp').strip()
    if actual != version:
        raise ValueError('Provisioned package-manager version differs from packageManager')
    return str(executable)


def _manager_version(manager: str, requirement: str, network: bool) -> str:
    import urllib.request
    import urllib.parse
    class OfficialRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            parsed = urllib.parse.urlsplit(newurl)
            if parsed.scheme != 'https' or parsed.netloc != 'registry.npmjs.org':
                raise ValueError('Unsafe package-manager registry redirect')
            return super().redirect_request(req, fp, code, msg, headers, newurl)
    opener = urllib.request.build_opener(OfficialRedirect())
    package = '@yarnpkg%2fcli-dist' if manager == 'yarn' else manager
    inventory = Path('/home/runner/.local/share/package-managers') / (manager + '-versions.json')
    if not inventory.is_file():
        if not network:
            raise ValueError(f'engines.{manager} {requirement} needs automatic registry provisioning (unavailable offline)')
        request = urllib.request.Request('https://registry.npmjs.org/' + package, headers={'Accept': 'application/vnd.npm.install-v1+json'})
        with opener.open(request, timeout=45) as response:
            metadata = response.read(8 * 1024 * 1024 + 1)
        if len(metadata) > 8 * 1024 * 1024:
            raise ValueError('Package manager registry inventory exceeds safety bound')
        versions = [v for v in json.loads(metadata).get('versions', {}) if re.fullmatch(r'\d+\.\d+\.\d+', v)]
        if len(versions) > 10000:
            raise ValueError('Package manager version inventory exceeds safety bound')
        inventory.parent.mkdir(parents=True, exist_ok=True)
        inventory.write_text(json.dumps(versions))
    versions = json.loads(inventory.read_text())
    selected = subprocess.check_output(['node', '-e', 'const s=require("/opt/lsp/node_modules/semver");console.log(JSON.parse(process.argv[2]).filter(v=>s.satisfies(v,process.argv[1])).sort(s.rcompare)[0]||"")', requirement, json.dumps(versions)], text=True, timeout=10).strip()
    if not selected:
        raise ValueError(f'No official {manager} release satisfies engines.{manager} {requirement}')
    return selected


def prepare_environments(root: Path, *, network: bool = False, verify: bool = False) -> dict:
    projects = []
    discovered_projects = discover_projects(root)
    for discovered in discovered_projects:
        data = discovered['data']
        cwd = root / discovered['root']
        language = discovered['language']
        project = {key: value for key, value in discovered.items() if key != 'data'}
        project.update({'ready': False, 'blocked_reason': None, 'interpreter': None, 'typescript_sdk': None, 'server_argv': [], 'servers': {}, 'commands': []})
        try:
            for local in ('.venv', 'node_modules', 'uv.lock', 'package-lock.json', 'npm-shrinkwrap.json', 'pnpm-lock.yaml', 'yarn.lock'):
                if (cwd / local).is_symlink():
                    raise ValueError('Project environment/lock root cannot be a symlink: ' + local)
            if language == 'python':
                requirement = data.get('project', {}).get('requires-python', '')
                python, version = _python_toolchain(requirement, network and not verify)
                project['python_version'] = version
                interpreter = str(cwd / '.venv/bin/python')
                project['interpreter'] = interpreter
                project['servers'] = {'pyright': ['/opt/lsp/node_modules/.bin/pyright-langserver', '--stdio'], 'ruff': ['/opt/lsp/bin/ruff', 'server']}
                project['server_argv'] = project['servers']['pyright']
                has_dependencies = bool(data.get('project', {}).get('dependencies') or data.get('dependency-groups') or data.get('tool', {}).get('uv', {}).get('dev-dependencies'))
                is_package = data.get('tool', {}).get('uv', {}).get('package') is not False and (bool(data.get('build-system')) or data.get('tool', {}).get('uv', {}).get('package') is True)
                locked = (cwd / 'uv.lock').exists()
                project['resolution'] = 'uv.lock (locked)' if locked else 'explicit pyproject resolution (no uv.lock at discovery)'
                if not verify and not (cwd / '.venv').exists():
                    argv = ['uv', 'venv', '--python', python, '--no-python-downloads', '--offline', '.venv']
                    project['commands'].append(argv)
                    ok, output = _environment_command(argv, cwd, False)
                    if not ok:
                        raise ValueError('Cannot create project .venv: ' + output)
                if not Path(interpreter).is_file():
                    raise ValueError('Automatic project .venv preparation failed')
                actual = subprocess.check_output([interpreter, '-I', '-c', 'import platform;print(platform.python_version())'], text=True, timeout=10).strip()
                if not python_compatible(requirement, actual):
                    raise ValueError('Existing project .venv interpreter violates requires-python')
                if data.get('inferred_pytest'):
                    if not verify:
                        argv = ['uv', 'pip', 'install', '--python', interpreter, 'pytest==8.4.2']
                        if not network:
                            argv.append('--offline')
                        project['commands'].append(argv)
                        ok, output = _environment_command(argv, cwd, network)
                        if not ok:
                            raise ValueError('Automatic inferred-test dependency preparation failed: ' + output)
                    if not any((cwd / '.venv').glob('lib/python*/site-packages/pytest/__init__.py')):
                        raise ValueError('Inferred-test pytest dependency missing')
                if locked or has_dependencies or is_package:
                    argv = ['uv', 'sync', '--python', interpreter, '--no-python-downloads']
                    if locked or verify:
                        argv.append('--locked')
                    if not network or verify:
                        argv.append('--offline')
                    if verify:
                        argv.append('--check')
                    project['commands'].append(argv)
                    ok, output = _environment_command(argv, cwd, network and not verify)
                    if not ok:
                        raise ValueError('Automatic dependency preparation failed (missing packages or stale lock): ' + output)
                project['ready'] = True
            else:
                install_root = root / discovered.get('install_root', discovered['root'])
                owner = next(p for p in discovered_projects if p['root'] == discovered.get('install_root', discovered['root']) and p['language'] != 'python')
                owner_data = owner['data']
                leaf = install_root != cwd
                if leaf and data.get('packageManager') and data['packageManager'] != owner_data.get('packageManager'):
                    raise ValueError('Workspace packageManager conflicts with authoritative root')
                if leaf and any((cwd / name).exists() for name in ('package-lock.json', 'npm-shrinkwrap.json', 'pnpm-lock.yaml', 'yarn.lock')):
                    raise ValueError('Workspace member lockfile conflicts with authoritative root installation')
                import itertools
                members = [p for p in discovered_projects if p['language'] != 'python' and p.get('install_root') == owner['root']]
                ranges = [p['data'].get('engines', {}).get('node', '*') for p in members]
                for requirement in ranges:
                    node_compatible(requirement)
                combinations = list(itertools.islice(itertools.product(*(r.split('||') for r in ranges)), 129))
                if len(combinations) > 128:
                    raise ValueError('Workspace Node engine intersection exceeds bounds')
                engine = ' || '.join(' '.join(c) for c in combinations)
                node_bin, version = _node_toolchain(engine, network and not verify)
                project['node_bin'] = node_bin
                locks = [(name, manager) for name, manager in [('package-lock.json', 'npm'), ('npm-shrinkwrap.json', 'npm'), ('pnpm-lock.yaml', 'pnpm'), ('yarn.lock', 'yarn')] if (install_root / name).exists()]
                if len(locks) > 1:
                    raise ValueError('Conflicting Node lockfiles; retain exactly one authoritative lockfile')
                declared = owner_data.get('packageManager')
                manager = locks[0][1] if locks else 'pnpm' if (install_root / 'pnpm-workspace.yaml').is_file() else 'npm'
                requested = PACKAGE_MANAGERS[manager]
                if declared:
                    if not isinstance(declared, str) or not re.fullmatch(r'(npm|pnpm|yarn)@[0-9]+\.[0-9]+\.[0-9]+(?:\+sha(?:224|256|384|512)\.[a-f0-9]+)?', declared):
                        raise ValueError('packageManager must name an exact npm/pnpm/yarn version')
                    chosen, requested = declared.split('@', 1)
                    requested = requested.split('+', 1)[0]
                    if locks and chosen != manager:
                        raise ValueError('packageManager conflicts with Node lockfile')
                    manager = chosen
                engine_range = owner_data.get('engines', {}).get(manager, '*')
                if not node_compatible(engine_range, requested):
                    if declared:
                        raise ValueError(f'packageManager {manager}@{requested} conflicts with engines.{manager} {engine_range}')
                    requested = _manager_version(manager, engine_range, network and not verify)
                manager_executable = _package_manager(manager, requested, node_bin, network and not verify)
                project.update({'node_version': version, 'package_manager': f'{manager}@{requested}', 'package_manager_executable': manager_executable, 'lockfile': locks[0][0] if locks else None, 'install_root': owner['root']})
                if not node_compatible(data.get('engines', {}).get(manager, '*'), requested):
                    raise ValueError('Workspace member manager engine conflicts with root version')
                if leaf:
                    prepared_owner = next((p for p in projects if p['root'] == owner['root'] and p['language'] != 'python'), None)
                    if not prepared_owner or not prepared_owner['ready']:
                        raise ValueError('Authoritative workspace root environment is not ready')
                dependencies = {**data.get('dependencies', {}), **data.get('devDependencies', {}), **data.get('optionalDependencies', {})}
                if any(not isinstance(name, str) or not re.fullmatch(r'(?:@[a-zA-Z0-9_.-]+/)?[a-zA-Z0-9_.-]+', name) or name in {'.', '..'} for name in dependencies):
                    raise ValueError('Unsafe Node dependency name')
                if not leaf and (dependencies or discovered.get('workspace_packages')) and not verify:
                    modern_yarn = manager == 'yarn' and int(requested.split('.')[0]) >= 2
                    argv = [manager_executable, *({'npm': ['ci' if locks else 'install', '--no-audit', '--no-fund'], 'pnpm': ['install', '--frozen-lockfile' if locks else '--no-frozen-lockfile'], 'yarn': ['install', *(['--immutable'] if locks and modern_yarn else ['--frozen-lockfile'] if locks else ['--no-lockfile'] if not modern_yarn else [])]}[manager])]
                    if not discovered.get('workspace_packages') and manager == 'npm':
                        argv.append('--workspaces=false')
                    if not discovered.get('workspace_packages') and manager == 'pnpm':
                        argv.append('--ignore-workspace')
                    if not network and not modern_yarn:
                        argv.append('--offline')
                    if modern_yarn:
                        argv = ['/usr/bin/env', 'YARN_NODE_LINKER=node-modules', 'YARN_ENABLE_NETWORK=' + ('1' if network else '0'), 'YARN_ENABLE_IMMUTABLE_INSTALLS=' + ('true' if locks else 'false'), *argv]
                    argv = ['/usr/bin/env', 'PATH=' + node_bin + ':/usr/local/bin:/usr/bin:/bin', *argv]
                    project['commands'].append(argv)
                    ok, output = _environment_command(argv, install_root, network)
                    if not ok:
                        raise ValueError('Automatic Node dependency preparation failed: ' + output)
                required_dependencies = {**data.get('dependencies', {}), **data.get('devDependencies', {})}
                missing = [name for name in required_dependencies if _node_module(cwd, install_root, name) is None]
                if missing:
                    raise ValueError('Missing local Node dependencies: ' + ', '.join(missing[:20]))
                typescript_package = _node_module(cwd, install_root, 'typescript')
                local_sdk = typescript_package / 'lib' if typescript_package else None
                project['typescript_sdk'] = str(local_sdk) if local_sdk and (local_sdk / 'tsserver.js').is_file() else '/opt/lsp/node_modules/typescript/lib'
                tls = ['/opt/lsp/node_modules/.bin/typescript-language-server', '--stdio']
                project['servers'] = {'javascript': tls, 'typescript': tls, 'vue': ['/opt/lsp/node_modules/.bin/vue-language-server', '--stdio']}
                project['servers'].update({key: ['/opt/lsp/node_modules/.bin/vscode-' + key + '-language-server', '--stdio'] for key in ('html', 'css', 'json')})
                project['server_argv'] = project['servers']['vue' if language == 'vue' else 'typescript']
                project['companion_argv'] = tls
                project['vue_plugin'] = '/opt/lsp/node_modules'
                project['ready'] = True
        except (ValueError, KeyError, TypeError, OSError, subprocess.SubprocessError) as exc:
            project['blocked_reason'] = str(exc)
        projects.append(project)
    for project in projects:
        sibling = next((p for p in projects if p['root'] == project['root'] and p.get('node_bin')), None)
        node_bin = sibling['node_bin'] if sibling else '/usr/local/bin'
        prefix = str(root / project['root'])
        manager_bin = str(Path(sibling['package_manager_executable']).parent) if sibling and sibling.get('package_manager_executable') else '/usr/local/bin'
        dependency_bins = [prefix + '/node_modules/.bin']
        if sibling:
            install_root = root / sibling.get('install_root', sibling['root'])
            ancestor = root / project['root']
            while ancestor != install_root and ancestor.parent != ancestor:
                ancestor = ancestor.parent
                dependency_bins.append(str(ancestor / 'node_modules/.bin'))
        path_value = ':'.join(dependency_bins) + ':' + prefix + '/.venv/bin:' + (node_bin + ':' if node_bin != '/usr/local/bin' else '') + manager_bin + ':/usr/local/bin:/usr/bin:/bin'
        project['environment'] = {'PATH': path_value, 'REPOPILOT_PROJECT_ROOT': prefix, 'YARN_NODE_LINKER': 'node-modules', 'YARN_ENABLE_NETWORK': '0'}
        python = next((p for p in projects if p['root'] == project['root'] and p.get('interpreter')), None)
        if python:
            project['environment']['VIRTUAL_ENV'] = prefix + '/.venv'
            project['environment']['REPOPILOT_PYTHON'] = python['interpreter']
        if sibling:
            project['node_bin'] = node_bin
        for name, argv in project['servers'].items():
            project['servers'][name] = ['/usr/local/bin/node', *argv] if argv[0].startswith('/opt/lsp/node_modules/') else argv
        if project.get('server_argv'):
            argv = project['server_argv']
            project['server_argv'] = ['/usr/local/bin/node', *argv] if argv[0].startswith('/opt/lsp/node_modules/') else argv
        if project.get('companion_argv'):
            project['companion_argv'] = ['/usr/local/bin/node', *project['companion_argv']]
    checks = []
    import shlex
    for project in projects:
        if not project['ready']:
            continue
        cwd = root / project['root']
        manifest = next(d['data'] for d in discovered_projects if d['root'] == project['root'] and d['language'] == project['language'])
        command = None
        reason = None
        if project['language'] == 'python':
            tests = _test_files(cwd)
            dependencies = str(manifest.get('project', {}).get('dependencies', [])) + str(manifest.get('dependency-groups', {})) + str(manifest.get('tool', {}).get('uv', {}))
            if tests and (manifest.get('inferred_pytest') or 'pytest' in dependencies or 'pytest' in manifest.get('tool', {})):
                command = shlex.quote(project['interpreter']) + ' -m pytest'
                reason = 'Declared pytest configuration/dependency and discovered tests'
            elif tests and any('unittest' in p.read_text(errors='replace') for p in tests if p.stat().st_size <= 1024 * 1024):
                command = shlex.quote(project['interpreter']) + ' -m unittest discover -s ' + ('tests' if (cwd / 'tests').is_dir() else '.') + ' -v'
                reason = 'Discovered stdlib unittest tests'
        else:
            scripts = manifest.get('scripts', {})
            selected = next((key for key in ('test', 'typecheck', 'build') if isinstance(scripts.get(key), str) and scripts[key].strip() and not re.search(r'^\s*(?:echo\b|true\b|exit\s+0)|echo\s+.*(?:no test|not implemented)|exit\s+1', scripts[key], re.I)), None)
            if selected:
                manager = project['package_manager'].split('@')[0]
                command = shlex.quote(project['package_manager_executable']) + ' run ' + selected
                reason = 'Declared package.json scripts.' + selected
        if command:
            prefix = '/workspace' if project['root'] == '.' else '/workspace/' + project['root']
            environment_assignments = ' '.join(key + '=' + shlex.quote(value) for key, value in project['environment'].items())
            checks.append({'root': project['root'], 'language': project['language'], 'command': command, 'reason': reason, 'shell': '(cd ' + shlex.quote(prefix) + ' && ' + environment_assignments + ' ' + command + ')'})
    coverage = [{'root': p['root'], 'language': p['language'], 'covered': any(c['root'] == p['root'] and c['language'] == p['language'] for c in checks), 'reason': next((c['reason'] for c in checks if c['root'] == p['root'] and c['language'] == p['language']), 'No declared test/typecheck/build or recognized Python tests; not claimed verified')} for p in projects]
    setup_command = ' ; '.join('(cd ' + shlex.quote(action['cwd']) + ' && ' + shlex.join(action['argv']) + ')' for action in ENV_ACTIONS)
    return {'projects': projects, 'ready': all(p['ready'] for p in projects), 'network_allowed': network and not verify, 'checks': checks, 'check_coverage': coverage, 'uncovered_roots': sorted({p['root'] for p in coverage if not p['covered']}), 'setup_command': setup_command, 'executed': list(ENV_ACTIONS), 'check_command': ' && '.join(c['shell'] for c in checks), 'check_blocked_reason': None if checks else 'No credible declared test/typecheck/build command or stdlib unittest tests discovered'}
