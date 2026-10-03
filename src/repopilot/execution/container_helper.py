"""Trusted program sent by the controller to python3; never loaded from the repository."""
from __future__ import annotations

import hashlib
import json
import os
import selectors
import signal
import stat
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path('/workspace')
MAX_FILE = 1024 * 1024


def path(value: str, *, new: bool = False) -> Path:
    if not isinstance(value, str) or not value or '\x00' in value:
        raise ValueError('Invalid workspace path')
    parts = value.split('/')
    if value.startswith('/') or any(p in ('', '..') for p in parts):
        raise ValueError('Path must be relative without traversal')
    target = ROOT
    for index, part in enumerate(parts):
        if part == '.':
            continue
        target = target / part
        try:
            mode = target.lstat().st_mode
        except FileNotFoundError:
            if new and index == len(parts) - 1:
                return target
            raise ValueError('Path does not exist') from None
        if stat.S_ISLNK(mode) or not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
            raise ValueError('Links and special files are forbidden')
        if index != len(parts) - 1 and not stat.S_ISDIR(mode):
            raise ValueError('Parent is not a directory')
    return target


def file_bytes(target: Path) -> bytes:
    if not target.is_file() or target.stat().st_size > MAX_FILE:
        raise ValueError('Expected a regular file at most 1 MiB')
    data = target.read_bytes()
    if len(data) > MAX_FILE:
        raise ValueError('File exceeds 1 MiB')
    return data


def residuals() -> list[int]:
    result = []
    for entry in Path('/proc').iterdir():
        if not entry.name.isdigit() or int(entry.name) in (1, os.getpid()):
            continue
        try:
            fields = (entry / 'stat').read_text().rsplit(')', 1)[1].split()
            if fields[0] != 'Z':
                result.append(int(entry.name))
        except (FileNotFoundError, ProcessLookupError):
            pass
    return result


def stop_residuals() -> None:
    # Also catch detached sessions/double forks, not just the original process group.
    for _ in range(20):
        remaining = residuals()
        if not remaining:
            return
        for pid in remaining:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            except PermissionError:
                raise RuntimeError('Unable to terminate residual container process') from None
        time.sleep(.05)
    raise RuntimeError('Unable to confirm command process cleanup')


def shell(command: str, timeout: float, cwd: str = '.', environment: dict | None = None) -> dict:
    directory = path(cwd)
    if not directory.is_dir():
        raise ValueError('Shell cwd must be a directory')
    stop_residuals()
    env = os.environ.copy()
    env.update(environment or {})
    proc = subprocess.Popen(['/bin/sh', '-c', command], cwd=directory, env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            start_new_session=True)
    assert proc.stdout is not None
    os.set_blocking(proc.stdout.fileno(), False)
    selector = selectors.DefaultSelector()
    selector.register(proc.stdout, selectors.EVENT_READ)
    data = bytearray()
    total = 0
    deadline = time.monotonic() + timeout
    timed_out = False
    try:
        while proc.poll() is None:
            if time.monotonic() >= deadline:
                timed_out = True
                break
            for key, _ in selector.select(.05):
                chunk = os.read(key.fd, 65536)
                total += len(chunk)
                data.extend(chunk[:max(0, 32768 - len(data))])
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.wait(timeout=2)
        stop_residuals()
        while True:
            chunk = os.read(proc.stdout.fileno(), 65536)
            if not chunk:
                break
            total += len(chunk)
            data.extend(chunk[:max(0, 32768 - len(data))])
    finally:
        selector.close()
        proc.stdout.close()
    return {'exit_code': None if timed_out else proc.returncode,
            'output': data.decode('utf-8', errors='replace'), 'truncated': total > len(data),
            'timed_out': timed_out}


def execute(name: str, args: dict) -> dict:
    if name == 'shell':
        return shell(args['command'], args['timeout'], args.get('cwd', '.'), args.get('environment'))
    if name == 'environment':
        stop_residuals()
        namespace = {'__name__': 'repopilot_environment'}
        try:
            exec(compile(args['program'], '<trusted-environment>', 'exec'), namespace)
            return namespace['prepare_environments'](ROOT, network=args.get('network', False), verify=args.get('verify', False))
        finally:
            stop_residuals()
    if name == 'lsp_read':
        data = file_bytes(path(args['path']))
        text = data.decode('utf-8')
        if '\x00' in text:
            raise ValueError('Binary file cannot be read')
        return {'content': text, 'sha256': hashlib.sha256(data).hexdigest()}
    if name == 'lsp_snapshot':
        files = []
        count = 0
        for directory, dirs, names in os.walk(ROOT, followlinks=False):
            dirs[:] = sorted(d for d in dirs if d not in {'.git', '.venv', 'node_modules', '__pycache__', 'dist', 'build', '.yarn'} and not (Path(directory) / d).is_symlink())
            count += len(dirs) + len(names)
            if count > 20000:
                raise ValueError('Snapshot exceeds 20000 entries')
            for filename in sorted(names):
                candidate = Path(directory) / filename
                try:
                    safe = path(candidate.relative_to(ROOT).as_posix())
                    data = file_bytes(safe)
                except ValueError:
                    continue
                files.append({'path': candidate.relative_to(ROOT).as_posix(), 'sha256': hashlib.sha256(data).hexdigest()})
        return {'files': files}
    if name == 'lsp_apply':
        stop_residuals()
        import tempfile
        operations = args.get('operations', [])
        expected = args.get('expected', {})
        if not isinstance(operations, list) or len(operations) > 100 or not isinstance(expected, dict) or len(expected) > 20000:
            raise ValueError('Workspace edit exceeds bounds')
        backups = {}
        for name, digest in expected.items():
            target = path(name, new=True)
            if digest is None:
                if target.exists():
                    raise ValueError('Workspace edit target already exists')
            elif not target.is_file() or hashlib.sha256(file_bytes(target)).hexdigest() != digest:
                raise ValueError('Workspace edit is stale; files changed')
        touched = set()
        for operation in operations:
            kind = operation.get('kind')
            names = [operation['path']] + ([operation['old_path']] if kind == 'rename' else [])
            if kind not in {'text', 'create', 'rename', 'delete'}:
                raise ValueError('Unsupported workspace edit operation')
            for name in names:
                target = path(name, new=True)
                if name not in expected:
                    raise ValueError('Every affected path requires an expected version')
                touched.add(name)
                backups[name] = (file_bytes(target), stat.S_IMODE(target.stat().st_mode)) if target.exists() else None
            if kind in {'text', 'create'} and len(operation['content'].encode('utf-8')) > MAX_FILE:
                raise ValueError('Workspace edit file exceeds 1 MiB')
        def replace(target, data, mode=0o644):
            fd, temporary = tempfile.mkstemp(prefix='.repopilot-lsp-', dir=target.parent)
            try:
                with os.fdopen(fd, 'wb') as stream:
                    stream.write(data)
                    os.fchmod(stream.fileno(), mode)
                os.replace(temporary, target)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
        try:
            for operation in operations:
                target = path(operation['path'], new=True)
                kind = operation['kind']
                source = path(operation.get('old_path', operation['path']), new=True)
                if kind != 'create' and (not source.is_file() or hashlib.sha256(file_bytes(source)).hexdigest() != operation.get('expected_sha256')):
                    raise ValueError('Operation intermediate version disagrees with plan')
                if kind in {'text', 'create'}:
                    if kind == 'create' and target.exists():
                        raise ValueError('Create target exists')
                    replace(target, operation['content'].encode('utf-8'), stat.S_IMODE(target.stat().st_mode) if target.exists() else 0o644)
                elif kind == 'delete':
                    target.unlink()
                else:
                    if target.exists():
                        raise ValueError('Rename target exists')
                    os.replace(path(operation['old_path']), target)
        except BaseException:
            for name, backup in backups.items():
                target = path(name, new=True)
                if backup is None:
                    if target.exists():
                        target.unlink()
                else:
                    replace(target, *backup)
            raise
        return {'applied': True, 'files': [{'path': name, 'sha256': hashlib.sha256(file_bytes(path(name))).hexdigest() if path(name, new=True).exists() else None} for name in sorted(touched)]}
    target = path(args.get('path', '.'), new=name == 'write_file')
    if name == 'read_file':
        data = file_bytes(target)
        text = data.decode('utf-8')
        if '\x00' in text:
            raise ValueError('Binary file cannot be read')
        start, end = args.get('start_line', 1), args.get('end_line', 200)
        if type(start) is not int or type(end) is not int or start < 1 or end < start or end - start >= 200:
            raise ValueError('Read range must contain at most 200 lines')
        lines = text.splitlines(keepends=True)
        chosen = []
        size = 0
        partial = False
        for line in lines[start - 1:end]:
            encoded = line.encode('utf-8')
            if size + len(encoded) > 16384:
                remaining = encoded[:16384 - size].decode('utf-8', errors='ignore')
                if remaining:
                    chosen.append(remaining)
                partial = True
                break
            chosen.append(line)
            size += len(encoded)
        return {'content': ''.join(chosen), 'sha256': hashlib.sha256(data).hexdigest(),
                'start_line': start, 'end_line': start + len(chosen) - 1,
                'truncated': partial or start - 1 + len(chosen) < len(lines)}
    if name == 'search':
        query = args['query']
        if not isinstance(query, str) or not query or '\x00' in query:
            raise ValueError('Search requires a nonempty literal string')
        candidates = [target] if target.is_file() else sorted(target.rglob('*'))
        matches = []
        size = 0
        for candidate in candidates:
            try:
                safe = path(candidate.relative_to(ROOT).as_posix())
                if not safe.is_file():
                    continue
                text = file_bytes(safe).decode('utf-8')
                if '\x00' in text:
                    continue
            except (ValueError, UnicodeError):
                continue
            for number, line in enumerate(text.splitlines(), 1):
                if query in line:
                    row = {'path': safe.relative_to(ROOT).as_posix(), 'line': number, 'content': line}
                    row_size = len(json.dumps(row, ensure_ascii=False).encode())
                    if len(matches) == 100 or size + row_size > 15000:
                        return {'matches': matches, 'truncated': True}
                    matches.append(row)
                    size += row_size
        return {'matches': matches, 'truncated': False}
    if name == 'edit_file':
        data = file_bytes(target)
        if hashlib.sha256(data).hexdigest() != args['expected_sha256']:
            raise ValueError('File changed; read it again before editing')
        text = data.decode('utf-8')
        old = args['old_text']
        if not old or text.count(old) != 1:
            raise ValueError('old_text must match exactly once')
        result = text.replace(old, args['new_text'], 1).encode('utf-8')
        if len(result) > MAX_FILE:
            raise ValueError('File exceeds 1 MiB')
        # O_EXCL prevents colliding with repository-controlled temporary paths.
        import tempfile
        fd, temporary = tempfile.mkstemp(prefix='.repopilot-edit-', dir=target.parent)
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(result)
                os.fchmod(stream.fileno(), stat.S_IMODE(target.stat().st_mode))
            os.replace(temporary, target)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return {'sha256': hashlib.sha256(result).hexdigest()}
    if name == 'write_file':
        result = args['content'].encode('utf-8')
        if len(result) > MAX_FILE:
            raise ValueError('File exceeds 1 MiB')
        with target.open('xb') as stream:
            stream.write(result)
        return {'sha256': hashlib.sha256(result).hexdigest()}
    raise ValueError('Unknown workspace tool')


def transfer(mode: str) -> None:
    import shutil
    import tarfile

    if mode == 'import':
        total = 0
        seen = set()
        with tarfile.open(fileobj=sys.stdin.buffer, mode='r|') as archive:
            for index, member in enumerate(archive):
                if index >= 20000 or member.name in seen:
                    raise ValueError('Invalid source archive member set')
                seen.add(member.name)
                if not (member.isdir() or member.isreg()) or '.git' in member.name.split('/'):
                    raise ValueError('Unsafe source archive member')
                total += member.size
                if total > 200 * 1024 * 1024:
                    raise ValueError('Source archive exceeds limit')
                target = path(member.name, new=True)
                if member.isdir():
                    target.mkdir(exist_ok=True)
                else:
                    stream = archive.extractfile(member)
                    if stream is None:
                        raise ValueError('Missing archive content')
                    with stream, target.open('xb') as output:
                        shutil.copyfileobj(stream, output, 65536)
                    target.chmod(0o755 if member.mode & 0o111 else 0o644)
        return
    if mode != 'capture':
        raise ValueError('Unknown archive transfer mode')
    # The controller holds its exclusive operation lock and forbids future tools.
    # Terminate all potential writers, including detached sessions, before reading.
    # Only this trusted read-only serializer and the trusted sleeping PID1 remain.
    stop_residuals()
    total = 0
    payload = sys.stdin.buffer.read(4 * 1024 * 1024 + 1)
    if len(payload) > 4 * 1024 * 1024:
        raise ValueError('Baseline capture allowlist exceeds safety bound')
    tracked = json.loads(payload or b'[]')
    if not isinstance(tracked, list) or len(tracked) > 20000 or any(not isinstance(name, str) or name.startswith('/') or any(part in {'', '..', '.git'} for part in name.split('/')) for name in tracked):
        raise ValueError('Unsafe baseline capture allowlist')
    tracked = set(tracked)
    ancestors = {('/'.join(name.split('/')[:index])) for name in tracked for index in range(1, len(name.split('/')))}
    def candidates():
        ignores = {'.venv', 'node_modules', '__pycache__', 'dist', 'build', '.yarn', '.pnpm-store'}
        for directory, dirs, files in os.walk(ROOT, followlinks=False):
            relative = Path(directory).relative_to(ROOT)
            inside_ignored = any(part in ignores for part in relative.parts)
            dirs[:] = sorted(d for d in dirs if (not inside_ignored and d not in ignores) or (relative / d).as_posix() in ancestors)
            for filename in sorted(dirs + files):
                candidate = Path(directory) / filename
                if filename not in dirs and inside_ignored and candidate.relative_to(ROOT).as_posix() not in tracked:
                    continue
                yield candidate
    with tarfile.open(fileobj=sys.stdout.buffer, mode='w|') as archive:
        for index, candidate in enumerate(candidates()):
            if index >= 20000:
                raise ValueError('Final archive has too many members')
            name = candidate.relative_to(ROOT).as_posix()
            safe = path(name)
            if '.git' in name.split('/'):
                raise ValueError('Final archive contains Git metadata')
            info = archive.gettarinfo(str(safe), arcname=name)
            total += info.size
            if total > 200 * 1024 * 1024:
                raise ValueError('Final archive exceeds limit')
            if safe.is_file():
                with safe.open('rb') as stream:
                    archive.addfile(info, stream)
            else:
                archive.addfile(info)


def main() -> None:
    try:
        if len(sys.argv) == 2:
            try:
                transfer(sys.argv[1])
            except Exception:
                print('Trusted archive transfer failed', file=sys.stderr)
                raise SystemExit(1)
            return
        request = json.load(sys.stdin)
        result = execute(request['name'], request['arguments'])
        print(json.dumps({'ok': True, 'result': result}, ensure_ascii=False))
    except (ValueError, UnicodeError, OSError) as exc:
        print(json.dumps({'ok': False, 'error': str(exc)}, ensure_ascii=False))
    except Exception:
        print(json.dumps({'ok': False, 'fatal': True, 'error': 'Container helper failed'}))


if __name__ == '__main__':
    main()
