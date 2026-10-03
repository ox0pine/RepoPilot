from __future__ import annotations

import asyncio
import os
import re
import signal
import tempfile
from pathlib import Path
from urllib.parse import quote, urlsplit

import httpx

from repopilot.domain.runs import RunDelivery
from repopilot.domain.tasks import TaskError
from repopilot.execution.git import (
    GitHubGitCredentials,
    GitOperationError,
    clone_fixed_commit,
    github_git_auth,
)
from repopilot.persistence.deliveries import DeliveryClaim, DeliveryRepository
from repopilot.persistence.settings import GitHubSettingsRepository, SettingsStorageError

GIT_TIMEOUT = 90
OUTPUT_LIMIT = 16 * 1024


async def _git(
    *args: str, cwd: Path, environment: dict[str, str], data: bytes | None = None,
    allowed: tuple[int, ...] = (0,),
) -> tuple[int, str]:
    process = None
    reader = writer = waiter = None

    async def read_output(stream: asyncio.StreamReader) -> bytes:
        value = bytearray()
        while chunk := await stream.read(8192):
            if len(value) + len(chunk) > OUTPUT_LIMIT:
                raise TaskError(502, 'GitHub Git 操作输出超过安全上限')
            value.extend(chunk)
        return bytes(value)

    async def write_input(stream: asyncio.StreamWriter) -> None:
        if data is not None:
            stream.write(data)
            await stream.drain()
        stream.close()
        await stream.wait_closed()

    try:
        process = await asyncio.create_subprocess_exec(
            'git', *args, cwd=cwd, env=environment,
            stdin=asyncio.subprocess.PIPE if data is not None else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            start_new_session=True,
        )
        assert process.stdout is not None
        reader = asyncio.create_task(read_output(process.stdout))
        writer = asyncio.create_task(write_input(process.stdin)) if process.stdin is not None else None
        waiter = asyncio.create_task(process.wait())
        async with asyncio.timeout(GIT_TIMEOUT):
            results = await asyncio.gather(
                reader, waiter, *([writer] if writer is not None else []),
            )
        output = results[0]
    except TimeoutError:
        raise TaskError(504, 'GitHub 分支提交超时；结果可能未知，请重新提交以核对同一分支') from None
    except OSError:
        raise TaskError(503, '控制面缺少 Git，无法提交修复分支') from None
    finally:
        if process is not None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        if process is not None:
            await process.wait()
        tasks = [task for task in (reader, writer, waiter) if task is not None]
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
    if process.returncode not in allowed:
        raise TaskError(502, 'GitHub Git 操作失败；未提交或未确认修复分支')
    return process.returncode, output.decode('utf-8', errors='replace').strip()


def _coordinates(repository_url: str) -> tuple[str, str]:
    owner, repository = urlsplit(repository_url).path.strip('/').removesuffix('.git').split('/', 1)
    return owner, repository


def _headers(token: str) -> dict[str, str]:
    return {
        'Authorization': f'Bearer {token}', 'Accept': 'application/vnd.github+json',
        'X-GitHub-Api-Version': '2026-03-10', 'User-Agent': 'RepoPilot',
    }


def _json_object(response: httpx.Response) -> dict:
    try:
        result = response.json()
    except (ValueError, UnicodeError):
        raise TaskError(502, 'GitHub 返回了无效的仓库响应') from None
    if not isinstance(result, dict):
        raise TaskError(502, 'GitHub 返回了无效的仓库响应')
    return result


def _github_status(response: httpx.Response) -> None:
    if response.status_code == 200:
        return
    if response.status_code == 401:
        raise TaskError(422, 'GitHub token 无效或已过期，请在设置中更新')
    if response.status_code == 403:
        raise TaskError(422, 'GitHub 拒绝提交分支，请确认 token 具有 Contents 写权限')
    if response.status_code == 404:
        raise TaskError(422, 'GitHub 仓库不可访问，或 token 缺少提交权限')
    raise TaskError(502, 'GitHub 服务暂时不可用，请稍后重试')


class DeliveryService:
    def __init__(
        self, repository: DeliveryRepository, settings: GitHubSettingsRepository,
        *, transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.repository = repository
        self.settings = settings
        self.transport = transport

    async def create(self, task_id, run_id) -> RunDelivery:
        claimed = await self.repository.claim(task_id, run_id)
        if isinstance(claimed, RunDelivery):
            return claimed
        try:
            saved = await self.settings.load()
            token = saved.api_token.get_secret_value()
            if not token.strip():
                raise TaskError(503, '请先在设置中配置 GitHub token')
            credentials = GitHubGitCredentials(
                api_token=token, private_key=saved.private_key.get_secret_value(),
            )
            result = await self._deliver(claimed, credentials)
            return await self.repository.complete(claimed, **result)
        except TaskError as error:
            await self.repository.fail(claimed, error.detail)
            raise
        except (SettingsStorageError, GitOperationError):
            await self.repository.fail(claimed, '无法读取或使用已保存的 GitHub 凭据')
            raise TaskError(503, '无法读取或使用已保存的 GitHub 凭据') from None
        except httpx.RequestError:
            await self.repository.fail(claimed, 'GitHub 网络结果未知')
            raise TaskError(502, '无法连接 GitHub；结果可能未知，请重新提交以核对同一分支') from None
        except BaseException:
            # Client cancellation must not leave the Run locked until lease expiry.
            await asyncio.shield(self.repository.fail(claimed, '修复分支提交已取消'))
            raise

    async def _deliver(self, claim: DeliveryClaim, credentials: GitHubGitCredentials) -> dict:
        owner, repository = _coordinates(claim.source.repository_url)
        async with httpx.AsyncClient(
            base_url='https://api.github.com', headers=_headers(credentials.api_token), timeout=20,
            follow_redirects=False, trust_env=False, transport=self.transport,
        ) as client:
            user_response, repo_response = await asyncio.gather(
                client.get('/user'), client.get(f'/repos/{owner}/{repository}'),
            )
            _github_status(user_response)
            _github_status(repo_response)
            user, metadata = _json_object(user_response), _json_object(repo_response)
            login, default_branch = user.get('login'), metadata.get('default_branch')
            if not isinstance(login, str) or login.lower() != owner.lower():
                raise TaskError(409, '只能向当前 GitHub token 所属用户自己的仓库提交修复分支')
            if not isinstance(default_branch, str) or not default_branch:
                raise TaskError(502, 'GitHub 仓库响应缺少默认分支')

        with tempfile.TemporaryDirectory(prefix='repopilot-delivery-') as directory:
            root = Path(directory) / 'repository'
            try:
                await clone_fixed_commit(
                    claim.source.repository_url, claim.source.baseline_commit, root,
                    credentials, strategy='https_token', timeout=GIT_TIMEOUT,
                )
            except GitOperationError:
                raise TaskError(502, '无法从 Run 固定基线安全克隆仓库') from None
            async with github_git_auth(
                claim.source.repository_url, credentials, strategy='https_token',
            ) as auth:
                await _git('apply', '--check', '--whitespace=nowarn', '-', cwd=root, environment=auth.environment, data=claim.patch.encode())
                await _git('apply', '--index', '--whitespace=nowarn', '-', cwd=root, environment=auth.environment, data=claim.patch.encode())
                changed, _ = await _git('diff', '--cached', '--quiet', cwd=root, environment=auth.environment, allowed=(0, 1))
                if changed == 0:
                    raise TaskError(409, 'Patch 应用后没有代码变更，已停止交付')
                _, paths = await _git('diff', '--cached', '--name-only', '-z', cwd=root, environment=auth.environment)
                if any(path == '.git' or path.startswith('.git/') for path in paths.split('\0')):
                    raise TaskError(409, 'Patch 包含 Git 元数据变更，已停止交付')
                # clone_fixed_commit sets the requested identity; repeat at the commit boundary.
                await _git('config', 'user.name', 'RepoPilot', cwd=root, environment=auth.environment)
                await _git('config', 'user.email', 'repopilot@users.noreply.github.com', cwd=root, environment=auth.environment)
                commit_environment = {
                    **auth.environment,
                    'GIT_AUTHOR_DATE': claim.finished_at.isoformat(),
                    'GIT_COMMITTER_DATE': claim.finished_at.isoformat(),
                }
                await _git('commit', '--quiet', '--no-verify', '-m', f'RepoPilot Run {claim.run_id}', cwd=root, environment=commit_environment)
                _, commit_sha = await _git('rev-parse', 'HEAD', cwd=root, environment=auth.environment)
                if re.fullmatch(r'[0-9a-f]{40}', commit_sha) is None:
                    raise TaskError(502, 'Git 未返回有效的交付 commit')
                _, remote = await _git('ls-remote', '--heads', 'origin', claim.branch, cwd=root, environment=auth.environment)
                if remote:
                    remote_sha = remote.split()[0] if remote.split() else ''
                    if remote_sha != commit_sha:
                        raise TaskError(409, '修复分支已存在且内容不同，不会覆盖')
                else:
                    await _git('push', '--porcelain', 'origin', f'HEAD:refs/heads/{claim.branch}', cwd=root, environment=auth.environment)

        repository_url = f'https://github.com/{owner}/{repository}'
        branch_path = quote(claim.branch, safe='/')
        base_path = quote(default_branch, safe='/')
        return {
            'commit_sha': commit_sha,
            'branch_url': f'{repository_url}/tree/{branch_path}',
            'compare_url': f'{repository_url}/compare/{base_path}...{branch_path}',
        }
