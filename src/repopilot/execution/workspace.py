from __future__ import annotations

import asyncio
import gzip
import io
import json
import os
import shutil
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit
from uuid import UUID

import httpx

from repopilot.domain.tasks import (
    SourceSnapshot,
    normalize_baseline_commit,
    normalize_github_repository_url,
)

MAX_COMPRESSED = 50 * 1024 * 1024
MAX_EXPANDED = 200 * 1024 * 1024
MAX_MEMBERS = 20_000
MAX_PATCH = 8 * 1024 * 1024
FIXED_IGNORES = {'.git', 'node_modules', '.venv', '__pycache__', 'dist', 'build'}


class WorkspaceError(Exception):
    def __init__(self, message: str, *, blocked: bool = False) -> None:
        super().__init__(message)
        self.blocked = blocked


class ToolError(WorkspaceError):
    pass


@dataclass(frozen=True)
class CommandResult:
    exit_code: int | None
    output: str
    truncated: bool


async def _process(args: list[str], *, data: bytes | None = None, timeout: float = 60,
                   limit: int = 1024 * 1024, cwd: Path | None = None,
                   env: dict[str, str] | None = None) -> tuple[int, bytes, bytes]:
    try:
        proc = await asyncio.create_subprocess_exec(*args, stdin=asyncio.subprocess.PIPE if data is not None else asyncio.subprocess.DEVNULL,
                                                   stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                                                   cwd=cwd, env=env, start_new_session=True)
    except OSError:
        raise WorkspaceError('执行环境缺少所需命令') from None

    async def collect(stream: asyncio.StreamReader) -> bytes:
        chunks = bytearray()
        while chunk := await stream.read(65536):
            if len(chunks) + len(chunk) > limit:
                raise WorkspaceError('执行输出超过安全上限')
            chunks.extend(chunk)
        return bytes(chunks)

    async def send() -> None:
        if proc.stdin is not None:
            try:
                proc.stdin.write(data or b'')
                await proc.stdin.drain()
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                proc.stdin.close()
                try:
                    await proc.stdin.wait_closed()
                except (BrokenPipeError, ConnectionResetError):
                    pass

    tasks = [asyncio.create_task(collect(proc.stdout)), asyncio.create_task(collect(proc.stderr)),
             asyncio.create_task(send()), asyncio.create_task(proc.wait())]
    try:
        async with asyncio.timeout(timeout):
            values = await asyncio.gather(*tasks)
        return proc.returncode, values[0], values[1]
    except BaseException:
        for task in tasks:
            task.cancel()
        if proc.returncode is None:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
        await proc.wait()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise

class _ArchiveReader:
    def __init__(self, data: bytes) -> None:
        raw = io.BytesIO(data)
        self.stream = gzip.GzipFile(fileobj=raw) if data.startswith(b'\x1f\x8b') else raw
        self.remaining = MAX_EXPANDED + MAX_MEMBERS * 2048

    def read(self, size: int = -1) -> bytes:
        if size < 0:
            size = self.remaining + 1
        result = self.stream.read(min(size, self.remaining + 1))
        self.remaining -= len(result)
        if self.remaining < 0:
            raise WorkspaceError('源码归档展开超过安全上限')
        return result


def extract_archive(data: bytes, destination: Path, *, strip_root: bool) -> None:
    """Validate the entire member set before creating any files; never use extractall."""
    try:
        with tarfile.open(fileobj=_ArchiveReader(data), mode='r|') as archive:
            members = []
            names: set[tuple[str, ...]] = set()
            roots: set[str] = set()
            total = 0
            for index, member in enumerate(archive):
                if index >= MAX_MEMBERS:
                    raise WorkspaceError('源码归档成员超过安全上限')
                name = member.name
                if not strip_root and name in ('.', './') and member.isdir():
                    continue
                name = name.removeprefix('./')
                parts = tuple(name.rstrip('/').split('/'))
                if name.startswith('/') or not parts or any(p in ('', '.', '..', '.git') for p in parts):
                    raise WorkspaceError('源码归档包含不安全路径')
                if parts in names or not (member.isdir() or member.isreg()):
                    raise WorkspaceError('源码归档包含重复路径、链接或特殊文件')
                names.add(parts)
                roots.add(parts[0])
                total += member.size
                if member.size < 0 or total > MAX_EXPANDED:
                    raise WorkspaceError('源码展开超过安全上限')
                members.append((member, parts))
            if strip_root and (len(roots) != 1 or any(len(p) == 1 and not m.isdir() for m, p in members)):
                raise WorkspaceError('源码归档必须具有唯一根目录')
            relative = [(m, p[1:] if strip_root else p) for m, p in members]
            files = {p for m, p in relative if m.isreg()}
            if any(p[:i] in files for _, p in relative for i in range(1, len(p))):
                raise WorkspaceError('源码归档路径类型冲突')
            if destination.is_symlink() or (destination.exists() and any(destination.iterdir())):
                raise WorkspaceError('源码展开目录必须为空且不是链接')
            destination.mkdir(parents=True, exist_ok=True)
        paths = {m.name: p for m, p in relative}
        with tarfile.open(fileobj=_ArchiveReader(data), mode='r|') as archive:
            for member in archive:
                parts = paths.get(member.name)
                if not parts:
                    continue
                target = destination.joinpath(*parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                if member.isdir():
                    target.mkdir(exist_ok=True)
                else:
                    stream = archive.extractfile(member)
                    if stream is None:
                        raise WorkspaceError('源码归档文件无法读取')
                    with stream, target.open('xb') as output:
                        shutil.copyfileobj(stream, output, 65536)
                    target.chmod(0o755 if member.mode & 0o111 else 0o644)
    except (tarfile.TarError, OSError, EOFError, ValueError):
        raise WorkspaceError('源码归档无效或无法安全展开') from None


async def download_source(source: SourceSnapshot, token: str, *, transport: httpx.AsyncBaseTransport | None = None) -> bytes:
    try:
        async with asyncio.timeout(60):
            return await _download_source(source, token, transport=transport)
    except TimeoutError:
        raise WorkspaceError('固定版本源码下载超时', blocked=True) from None


async def _download_source(source: SourceSnapshot, token: str, *, transport: httpx.AsyncBaseTransport | None = None) -> bytes:
    repository = normalize_github_repository_url(source.repository_url).removeprefix('https://github.com/')
    commit = normalize_baseline_commit(source.baseline_commit)
    url = f'https://api.github.com/repos/{repository}/tarball/{commit}'
    headers = {'Accept': 'application/vnd.github+json', 'Authorization': f'Bearer {token}',
               'X-GitHub-Api-Version': '2022-11-28', 'Accept-Encoding': 'identity'}
    try:
        async with httpx.AsyncClient(transport=transport, follow_redirects=False, timeout=35) as client:
            async with client.stream('GET', url, headers=headers) as response:
                if response.status_code == 302:
                    location = response.headers.get('location', '')
                    parsed = urlsplit(location)
                    if (parsed.scheme != 'https' or parsed.netloc != 'codeload.github.com'
                            or parsed.query or parsed.fragment or parsed.path not in (
                                f'/{repository}/legacy.tar.gz/{commit}', f'/{repository}/tar.gz/{commit}')):
                        raise WorkspaceError('源码下载重定向不安全')
                elif response.status_code == 200:
                    return await _response_bytes(response)
                else:
                    raise WorkspaceError('无法读取固定版本源码，请检查 GitHub 权限', blocked=True)
            # Explicitly omit all authorization headers at the cross-origin boundary.
            async with client.stream('GET', location, headers={'Accept': 'application/octet-stream', 'Accept-Encoding': 'identity'}) as response:
                if response.status_code != 200:
                    raise WorkspaceError('无法下载固定版本源码', blocked=True)
                return await _response_bytes(response)
    except httpx.HTTPError:
        raise WorkspaceError('固定版本源码下载失败', blocked=True) from None


async def _response_bytes(response: httpx.Response) -> bytes:
    if response.headers.get('content-encoding', 'identity') != 'identity':
        raise WorkspaceError('源码下载包含不支持的传输压缩')
    result = bytearray()
    async for chunk in response.aiter_bytes():
        if len(result) + len(chunk) > MAX_COMPRESSED:
            raise WorkspaceError('源码压缩归档超过安全上限')
        result.extend(chunk)
    return bytes(result)


def _patch_paths(patch: str) -> str:
    def normalize(value: str) -> str:
        offset = 1 if value.startswith('"') else 0
        if value[offset:offset + 4] not in {'a/a/', 'a/b/', 'b/a/', 'b/b/'}:
            raise WorkspaceError('Patch 路径无法安全规范化')
        return value[:offset + 2] + value[offset + 4:]

    result = []
    header = False
    for line in patch.splitlines(keepends=True):
        if line.startswith('diff --git '):
            header = True
            body = line[len('diff --git '):].rstrip('\n')
            # With rename detection disabled, both tokens carry the same filename
            # and equal-length prefixes, even for spaces and Git C-quoted paths.
            middle = len(body) // 2
            if len(body) % 2 != 1 or body[middle] != ' ':
                raise WorkspaceError('Patch 文件头无法安全规范化')
            line = f'diff --git {normalize(body[:middle])} {normalize(body[middle + 1:])}\n'
        elif header and line.startswith(('--- ', '+++ ')) and line[4:].rstrip('\n') != '/dev/null':
            line = line[:4] + normalize(line[4:])
        elif line.startswith(('@@ ', 'GIT binary patch')):
            header = False
        result.append(line)
    return ''.join(result)


async def generate_patch(baseline: Path, final: Path) -> str:
    """Only trusted host Git sees data; repository configuration/hooks are never used."""
    with tempfile.TemporaryDirectory(prefix='repopilot-diff-') as temporary:
        root = Path(temporary)
        before, after = root / 'a', root / 'b'
        shutil.copytree(baseline, before)
        shutil.copytree(final, after)
        tracked = {p.relative_to(before).as_posix() for p in before.rglob('*') if p.is_file()}
        candidates = [p.relative_to(after).as_posix() for p in after.rglob('*') if p.is_file() and p.relative_to(after).as_posix() not in tracked]
        ignore = root / 'ignore'
        ignore.mkdir()
        if (before / '.gitignore').is_file():
            shutil.copyfile(before / '.gitignore', ignore / '.gitignore')
        env = {'PATH': os.environ.get('PATH', '/usr/bin:/bin'), 'HOME': str(root), 'LC_ALL': 'C',
               'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_CONFIG_SYSTEM': os.devnull,
               'GIT_ATTR_NOSYSTEM': '1'}
        rc, _, _ = await _process(['git', 'init', '--quiet', str(ignore)], env=env)
        if rc:
            raise WorkspaceError('无法准备可信差异环境')
        ignored: set[str] = set()
        if candidates:
            rc, out, _ = await _process(['git', '-C', str(ignore), 'check-ignore', '--no-index', '-z', '--stdin'],
                                         data=('\0'.join(candidates) + '\0').encode(), env=env, limit=MAX_EXPANDED)
            if rc not in (0, 1):
                raise WorkspaceError('无法读取基准忽略规则')
            ignored = {v.decode() for v in out.split(b'\0') if v}
        for name in candidates:
            if name in ignored or any(part in FIXED_IGNORES for part in PurePosixPath(name).parts):
                (after / name).unlink()
        try:
            rc, out, _ = await _process(['git', '-c', 'core.attributesFile=/dev/null', '-c', 'core.fileMode=true',
                                        'diff', '--no-index', '--binary', '--no-ext-diff', '--no-textconv', '--no-renames',
                                        '--src-prefix=a/', '--dst-prefix=b/', '--', 'a', 'b'], cwd=root, env=env, limit=MAX_PATCH)
        except WorkspaceError as exc:
            if str(exc) == '执行输出超过安全上限':
                raise WorkspaceError('成果超过保存上限，未生成完整Patch') from None
            raise
        if rc not in (0, 1):
            raise WorkspaceError('无法生成完整 Patch')
        try:
            return _patch_paths(out.decode('utf-8'))
        except UnicodeError:
            raise WorkspaceError('Patch 路径无法安全编码') from None


class Workspace:
    def __init__(self, run_id: UUID, *, source_transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.run_id = UUID(str(run_id))
        self.container_name = f'repopilot-run-{self.run_id}'
        self.source_transport = source_transport
        self.image_id: str | None = None
        self.prepared = False
        self._frozen = False
        self._captured = False
        self._temporary: tempfile.TemporaryDirectory | None = None
        self.baseline: Path | None = None
        self.final: Path | None = None
        self._lock = asyncio.Lock()
        self._reads: dict[str, str] = {}

    async def _docker(self, *args: str, **kwargs) -> tuple[int, bytes, bytes]:
        return await _process(['docker', *args], **kwargs)

    async def prepare(self, *, source: SourceSnapshot, github_token: str, image: str) -> str:
        if self.prepared or self._temporary is not None:
            raise WorkspaceError('Workspace has already been prepared')
        rc, out, _ = await self._docker('image', 'inspect', '--format', '{{json .}}', '--', image, limit=1024 * 1024)
        if rc:
            raise WorkspaceError('执行镜像不存在，请先准备环境', blocked=True)
        try:
            inspected = json.loads(out)
            image_id = inspected['Id']
            if inspected.get('Config', {}).get('Volumes'):
                raise WorkspaceError('执行镜像声明了额外数据卷，请使用无挂载镜像', blocked=True)
            if not isinstance(image_id, str) or not image_id.startswith('sha256:') or len(image_id) != 71:
                raise ValueError
            int(image_id[7:], 16)
        except (ValueError, KeyError, TypeError):
            raise WorkspaceError('执行镜像标识无效', blocked=True) from None
        self.image_id = image_id
        self._temporary = tempfile.TemporaryDirectory(prefix=f'repopilot-{self.run_id}-')
        self.baseline = Path(self._temporary.name) / 'baseline'
        self.final = Path(self._temporary.name) / 'final'
        try:
            archive = await download_source(source, github_token, transport=self.source_transport)
            extract_archive(archive, self.baseline, strip_root=True)
            rc, _, _ = await self._docker('create', '--name', self.container_name, '--pull=never',
                '--read-only', '--cap-drop=ALL', '--security-opt=no-new-privileges', '--cpus=2',
                '--memory=2g', '--pids-limit=256', '--network=none', '--user=1000:1000',
                '--workdir=/workspace', '--env=HOME=/home/runner',
                '--tmpfs=/workspace:rw,nosuid,nodev,size=768m,uid=1000,gid=1000,mode=0755',
                '--tmpfs=/home/runner:rw,nosuid,nodev,size=512m,uid=1000,gid=1000,mode=0700',
                '--tmpfs=/tmp:rw,nosuid,nodev,size=256m,mode=1777', '--entrypoint=python3', image_id,
                '-I', '-S', '-c', 'import time; time.sleep(2147483647)')
            if rc:
                raise WorkspaceError('无法创建隔离执行容器', blocked=True)
            rc, _, _ = await self._docker('start', self.container_name)
            if rc:
                raise WorkspaceError('无法启动执行容器', blocked=True)
            buffer = io.BytesIO()
            with tarfile.open(fileobj=buffer, mode='w') as bundle:
                for file in sorted(self.baseline.rglob('*')):
                    info = bundle.gettarinfo(str(file), arcname=file.relative_to(self.baseline).as_posix())
                    info.uid = info.gid = 1000
                    info.uname = info.gname = 'runner'
                    if file.is_file():
                        with file.open('rb') as stream:
                            bundle.addfile(info, stream)
                    else:
                        bundle.addfile(info)
            program = Path(__file__).with_name('container_helper.py').read_text()
            rc, _, _ = await self._docker('exec', '-i', '--user=1000:1000', '--workdir=/workspace',
                self.container_name, 'python3', '-I', '-S', '-c', program, 'import',
                data=buffer.getvalue(), timeout=90)
            if rc:
                raise WorkspaceError('无法复制固定版本源码')
            self.prepared = True
            return image_id
        except BaseException:
            await asyncio.shield(self.cleanup())
            raise

    async def _helper(self, name: str, arguments: dict, *, timeout: float = 60) -> dict:
        if not self.prepared or self._frozen:
            raise WorkspaceError('Workspace is not available for tools')
        program = Path(__file__).with_name('container_helper.py').read_text()
        try:
            rc, out, _ = await self._docker('exec', '-i', '--user=1000:1000', '--workdir=/workspace',
                self.container_name, 'python3', '-I', '-S', '-c', program,
                data=json.dumps({'name': name, 'arguments': arguments}, ensure_ascii=False).encode(), timeout=timeout + 10,
                limit=128 * 1024)
            if rc:
                raise WorkspaceError('容器工具执行失败')
            response = json.loads(out)
            if not response.get('ok'):
                if response.get('fatal'):
                    raise WorkspaceError('无法确认容器进程清理或工具安全状态')
                raise ToolError(response.get('error', '工具执行失败'))
            return response['result']
        except ToolError:
            raise
        except BaseException:
            await asyncio.shield(self.cleanup())
            raise

    async def shell(self, command: str, *, timeout: float = 60) -> CommandResult:
        if not isinstance(command, str) or '\x00' in command or timeout <= 0 or timeout > 300:
            raise ToolError('Invalid shell command or timeout')
        async with self._lock:
            result = await self._helper('shell', {'command': command, 'timeout': timeout}, timeout=timeout)
            if result.get('timed_out'):
                await self.cleanup()
                raise WorkspaceError('命令超时，执行容器已停止')
            return CommandResult(result['exit_code'], result['output'], result['truncated'])

    async def setup(self, command: str) -> CommandResult:
        if not command:
            return CommandResult(0, '', False)
        try:
            rc, _, _ = await self._docker('network', 'disconnect', 'none', self.container_name)
            if rc:
                raise WorkspaceError('无法切换准备阶段网络', blocked=True)
            rc, _, _ = await self._docker('network', 'connect', 'bridge', self.container_name)
            if rc:
                raise WorkspaceError('无法启用准备阶段网络', blocked=True)
            result = await self.shell(command, timeout=300)
            rc, _, _ = await self._docker('network', 'disconnect', 'bridge', self.container_name)
            if rc:
                raise WorkspaceError('无法断开准备阶段网络')
            rc, out, _ = await self._docker('inspect', '--format', '{{json .NetworkSettings.Networks}}', self.container_name)
            networks = json.loads(out)
            if rc or not isinstance(networks, dict) or any(name != 'none' for name in networks):
                raise WorkspaceError('无法确认执行容器已隔离网络')
            return result
        except BaseException:
            await asyncio.shield(self.cleanup())
            raise

    async def tool(self, name: str, arguments: dict) -> dict:
        if name not in {'read_file', 'search', 'edit_file', 'write_file'}:
            raise ToolError('Unknown workspace tool')
        async with self._lock:
            if name == 'edit_file' and self._reads.get(arguments.get('path')) != arguments.get('expected_sha256'):
                raise ToolError('必须先读取文件并使用其最新 hash')
            result = await self._helper(name, arguments)
            if name in {'read_file', 'edit_file'}:
                self._reads[arguments['path']] = result['sha256']
            return result

    async def capture(self) -> str:
        async with self._lock:
            if not self.prepared or self.baseline is None or self.final is None:
                raise WorkspaceError('容器尚未准备成功，无法提取成果')
            if self._frozen and not self._captured:
                raise WorkspaceError('成果捕获先前失败，无法生成完整 Patch')
            if not self._frozen:
                # Serialize under the exclusive lock: no future tools can start.
                # The trusted serializer terminates and rechecks all namespace
                # writers before reading; PID1 is our immutable sleeping program.
                self._frozen = True
                program = Path(__file__).with_name('container_helper.py').read_text()
                try:
                    rc, archive, _ = await self._docker('exec', '--user=1000:1000', '--workdir=/workspace',
                        self.container_name, 'python3', '-I', '-S', '-c', program, 'capture',
                        limit=MAX_EXPANDED + MAX_MEMBERS * 2048, timeout=90)
                    if rc:
                        raise WorkspaceError('无法安全停止文件写入并读取最终成果')
                    rc, _, _ = await self._docker('pause', self.container_name)
                    if rc:
                        raise WorkspaceError('无法冻结成果文件写入')
                    extract_archive(archive, self.final, strip_root=False)
                    self._captured = True
                except BaseException:
                    await asyncio.shield(self.cleanup())
                    raise
            return await generate_patch(self.baseline, self.final)

    async def cleanup(self) -> None:
        rc, out, _ = await self._docker('container', 'ls', '--all', '--filter', f'name=^/{self.container_name}$', '--format', '{{.Names}}', limit=4096)
        if rc:
            raise WorkspaceError('无法确认执行容器清理状态')
        if self.container_name in out.decode().splitlines():
            rc, _, _ = await self._docker('rm', '--force', self.container_name, timeout=30)
            if rc:
                raise WorkspaceError('执行容器清理失败')
            rc, out, _ = await self._docker('container', 'ls', '--all', '--filter', f'name=^/{self.container_name}$', '--format', '{{.Names}}', limit=4096)
            if rc or self.container_name in out.decode().splitlines():
                raise WorkspaceError('无法确认执行容器已停止')
        self.prepared = False
        if self._temporary is not None:
            self._temporary.cleanup()
            self._temporary = None
