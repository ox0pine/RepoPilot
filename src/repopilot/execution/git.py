from __future__ import annotations

import asyncio
import os
import shlex
import signal
import stat
import tempfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from repopilot.domain.tasks import normalize_baseline_commit, normalize_github_repository_url

GitAuthStrategy = Literal['https_token', 'ssh_key']

# Pinned from GitHub's published /meta ssh_keys (verified 2026-10-03); never refresh
# dynamically during a clone, because that would trust the same network path.
GITHUB_KNOWN_HOSTS = """github.com ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl
github.com ecdsa-sha2-nistp256 AAAAE2VjZHNhLXNoYTItbmlzdHAyNTYAAAAIbmlzdHAyNTYAAABBBEmKSENjQEezOmxkZMy7opKgwFB9nkt5YRrYMjNuG5N87uRgg6CLrbo5wAdT/y6v0mKV0U2w0WZ2YB/++Tpockg=
"""
MAX_SOURCE_BYTES = 200 * 1024 * 1024
MAX_SOURCE_MEMBERS = 20_000
MAX_GIT_OUTPUT = 4 * 1024 * 1024


class GitOperationError(Exception):
    pass


@dataclass(frozen=True)
class GitHubGitCredentials:
    api_token: str = field(repr=False)
    private_key: str = field(repr=False)


@dataclass(frozen=True)
class GitAuthContext:
    repository_url: str
    environment: dict[str, str]
    strategy: GitAuthStrategy


def _base_environment(home: Path) -> dict[str, str]:
    # This is a complete environment, not an overlay. Callers must pass it directly
    # so inherited Git directory, object-alternate, helper and askpass values cannot
    # redirect trusted control-plane operations.
    return {
        'PATH': '/usr/local/bin:/usr/bin:/bin',
        'HOME': str(home),
        'LC_ALL': 'C',
        'GIT_CONFIG_NOSYSTEM': '1',
        'GIT_CONFIG_GLOBAL': os.devnull,
        'GIT_CONFIG_SYSTEM': os.devnull,
        'GIT_ATTR_NOSYSTEM': '1',
        'GIT_TERMINAL_PROMPT': '0',
        'GIT_ASKPASS': '/bin/false',
        'SSH_ASKPASS': '/bin/false',
        'GIT_CONFIG_COUNT': '5',
        'GIT_CONFIG_KEY_0': 'core.hooksPath',
        'GIT_CONFIG_VALUE_0': os.devnull,
        'GIT_CONFIG_KEY_1': 'protocol.ext.allow',
        'GIT_CONFIG_VALUE_1': 'never',
        'GIT_CONFIG_KEY_2': 'protocol.file.allow',
        'GIT_CONFIG_VALUE_2': 'never',
        'GIT_CONFIG_KEY_3': 'submodule.recurse',
        'GIT_CONFIG_VALUE_3': 'false',
        'GIT_CONFIG_KEY_4': 'fetch.recurseSubmodules',
        'GIT_CONFIG_VALUE_4': 'false',
    }


@asynccontextmanager
async def github_git_auth(
    repository_url: str,
    credentials: GitHubGitCredentials,
    *,
    strategy: GitAuthStrategy,
) -> AsyncIterator[GitAuthContext]:
    """Prepare one explicit GitHub authentication method without embedding credentials in Git data."""
    repository_url = normalize_github_repository_url(repository_url)
    with tempfile.TemporaryDirectory(prefix='repopilot-git-auth-') as temporary:
        root = Path(temporary)
        root.chmod(0o700)
        environment = _base_environment(root)
        if strategy == 'https_token':
            if not credentials.api_token:
                raise GitOperationError('GitHub HTTPS clone requires a saved API token')
            token_file = root / 'token'
            token_file.write_text(credentials.api_token)
            token_file.chmod(0o600)
            helper = root / 'credential-helper'
            helper.write_text(
                '#!/usr/bin/env python3\n'
                'import os, sys\n'
                'fields = dict(line.rstrip("\\n").split("=", 1) for line in sys.stdin if "=" in line)\n'
                'if len(sys.argv) > 1 and sys.argv[1] == "get" and fields.get("protocol") == "https" and fields.get("host") == "github.com":\n'
                '    token = open(os.environ["REPOPILOT_GIT_TOKEN_FILE"], encoding="utf-8").read()\n'
                '    sys.stdout.write("username=x-access-token\\npassword=" + token + "\\n")\n'
            )
            helper.chmod(0o700)
            environment.update({
                'REPOPILOT_GIT_TOKEN_FILE': str(token_file),
                'GIT_CONFIG_COUNT': '7',
                'GIT_CONFIG_KEY_5': 'credential.helper',
                'GIT_CONFIG_VALUE_5': str(helper),
                'GIT_CONFIG_KEY_6': 'credential.useHttpPath',
                'GIT_CONFIG_VALUE_6': 'true',
            })
            yield GitAuthContext(repository_url, environment, strategy)
            return
        if strategy == 'ssh_key':
            if not credentials.private_key:
                raise GitOperationError('GitHub SSH clone requires a saved private key')
            private_key = root / 'id_ed25519'
            private_key.write_text(credentials.private_key)
            private_key.chmod(0o600)
            known_hosts = root / 'known_hosts'
            known_hosts.write_text(GITHUB_KNOWN_HOSTS)
            known_hosts.chmod(0o600)
            environment['GIT_SSH_COMMAND'] = shlex.join([
                'ssh', '-F', os.devnull, '-i', str(private_key), '-o', 'IdentitiesOnly=yes',
                '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
                '-o', f'UserKnownHostsFile={known_hosts}', '-o', f'GlobalKnownHostsFile={os.devnull}',
            ])
            path = repository_url.removeprefix('https://github.com/')
            yield GitAuthContext(f'git@github.com:{path}.git', environment, strategy)
            return
        raise GitOperationError('Unsupported GitHub Git authentication strategy')


def _source_usage(root: Path) -> tuple[int, int]:
    if not root.exists():
        return 0, 0
    members = 0
    total = 0
    pending = [root]
    while pending:
        directory = pending.pop()
        try:
            entries = list(os.scandir(directory))
        except FileNotFoundError:
            continue
        for entry in entries:
            try:
                info = entry.stat(follow_symlinks=False)
            except FileNotFoundError:
                continue
            members += 1
            if members > MAX_SOURCE_MEMBERS:
                raise GitOperationError('Cloned source has too many members')
            if stat.S_ISDIR(info.st_mode):
                pending.append(Path(entry.path))
            elif stat.S_ISREG(info.st_mode):
                total += info.st_size
                if total > MAX_SOURCE_BYTES:
                    raise GitOperationError('Cloned source exceeds size limit')
            else:
                raise GitOperationError('Cloned source contains links or special files')
    return members, total


async def _git(
    arguments: list[str],
    *,
    environment: dict[str, str],
    destination: Path,
    timeout: float,
) -> tuple[bytes, bytes]:
    try:
        process = await asyncio.create_subprocess_exec(
            'git', *arguments, stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            env=environment, start_new_session=True,
        )
    except OSError:
        raise GitOperationError('Git is unavailable on the trusted control plane') from None

    async def collect(stream: asyncio.StreamReader) -> bytes:
        value = bytearray()
        while chunk := await stream.read(65536):
            if len(value) + len(chunk) > MAX_GIT_OUTPUT:
                raise GitOperationError('Git output exceeds safety limit')
            value.extend(chunk)
        return bytes(value)


    stdout = asyncio.create_task(collect(process.stdout))
    stderr = asyncio.create_task(collect(process.stderr))
    waiter = asyncio.create_task(process.wait())
    try:
        async with asyncio.timeout(timeout):
            while not waiter.done():
                for task in (stdout, stderr):
                    if task.done():
                        task.result()
                _source_usage(destination)
                await asyncio.sleep(0.1)
            await waiter
            out, err = await asyncio.gather(stdout, stderr)
    except BaseException:
        for task in (stdout, stderr, waiter):
            task.cancel()
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        await process.wait()
        await asyncio.gather(stdout, stderr, waiter, return_exceptions=True)
        raise
    _source_usage(destination)
    if process.returncode:
        raise GitOperationError('Authenticated Git operation failed')
    return out, err


async def clone_fixed_commit(
    repository_url: str,
    commit: str,
    destination: Path,
    credentials: GitHubGitCredentials,
    *,
    strategy: GitAuthStrategy,
    timeout: float = 90,
) -> None:
    """Clone a GitHub repository and detach at an exact commit under bounded trusted-host I/O."""
    repository_url = normalize_github_repository_url(repository_url)
    commit = normalize_baseline_commit(commit)
    if destination.is_symlink() or destination.exists():
        raise GitOperationError('Git clone destination must not exist')
    try:
        async with asyncio.timeout(timeout):
            async with github_git_auth(repository_url, credentials, strategy=strategy) as auth:
                await _git([
                    'clone', '--no-checkout', '--no-tags', '--depth=1', '--single-branch', '--',
                    auth.repository_url, str(destination),
                ], environment=auth.environment, destination=destination, timeout=timeout)
                code_environment = auth.environment
                try:
                    await _git(['-C', str(destination), 'cat-file', '-e', f'{commit}^{{commit}}'],
                               environment=code_environment, destination=destination, timeout=15)
                except GitOperationError:
                    await _git(['-C', str(destination), 'fetch', '--no-tags', '--depth=1', 'origin', commit],
                               environment=code_environment, destination=destination, timeout=timeout)
                tree, _ = await _git(['-C', str(destination), 'ls-tree', '-r', '-z', commit],
                                     environment=code_environment, destination=destination, timeout=15)
                for entry in tree.split(b'\0'):
                    if entry and entry.split(b' ', 1)[0] in {b'120000', b'160000'}:
                        raise GitOperationError('Repository contains unsupported symlinks or submodules')
                await _git(['-C', str(destination), 'checkout', '--detach', '--force', commit],
                           environment=code_environment, destination=destination, timeout=timeout)
                out, _ = await _git(['-C', str(destination), 'rev-parse', '--verify', 'HEAD'],
                                    environment=code_environment, destination=destination, timeout=15)
                if out.decode('ascii', errors='strict').strip().lower() != commit:
                    raise GitOperationError('Git checkout did not resolve to the requested commit')
                await _git(['-C', str(destination), 'config', '--local', 'user.name', 'RepoPilot'],
                           environment=code_environment, destination=destination, timeout=15)
                await _git(['-C', str(destination), 'config', '--local', 'user.email',
                            'repopilot@users.noreply.github.com'],
                           environment=code_environment, destination=destination, timeout=15)
                _source_usage(destination)
    except TimeoutError:
        raise GitOperationError('Authenticated fixed-commit Git clone timed out') from None
