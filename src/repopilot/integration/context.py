from __future__ import annotations

import asyncio
import hashlib
import json
import re
from pathlib import PurePosixPath
from urllib.parse import quote

import httpx

from repopilot.domain.context import ContextFile, RepositoryContext
from repopilot.domain.tasks import CreateTask, TaskError, github_source_coordinates
from repopilot.integration.task_sources import _check_status, _invalid

TREE_RESPONSE_LIMIT = 2 * 1024 * 1024
TREE_ENTRY_LIMIT = 10_000
TREE_PATH_LIMIT = 500
BLOB_LIMIT = 12
BLOB_DOWNLOAD_LIMIT = 64 * 1024
FILE_CONTENT_LIMIT = 16 * 1024
TOTAL_CONTENT_LIMIT = 64 * 1024
EXCLUDED_DIRECTORIES = {
    '.git', 'node_modules', '.venv', 'venv', '__pycache__', 'dist', 'build',
    '.next', '.nuxt', 'vendor', '.pytest_cache', '.mypy_cache', '.ruff_cache',
}
ENTRY_FILES = (
    'pyproject.toml', 'package.json', 'requirements.txt', 'setup.py', 'setup.cfg',
    'Cargo.toml', 'go.mod', 'Makefile', 'tsconfig.json',
)
TEXT_SUFFIXES = {
    '.py', '.js', '.jsx', '.ts', '.tsx', '.vue', '.svelte', '.rs', '.go', '.java',
    '.c', '.h', '.cpp', '.css', '.scss', '.html', '.md', '.txt', '.json', '.toml',
    '.yaml', '.yml', '.sh',
}


def _safe_path(path: str) -> bool:
    parts = path.split('/')
    if any(not part or part in {'.', '..'} for part in parts) or '\\' in path:
        return False
    if any(ord(char) < 32 or ord(char) == 127 for char in path):
        return False
    if any(part.lower() in EXCLUDED_DIRECTORIES for part in parts):
        return False
    for part in parts:
        name = part.lower()
        if (
            name.startswith('.env') or name in {
                '.ssh', '.aws', '.gnupg', '.npmrc', '.pypirc', '.netrc', '.git-credentials',
            }
            or name.startswith(('id_rsa', 'id_ed25519', 'credentials', 'secret'))
            or name.endswith(('.pem', '.key', '.p12', '.pfx', '.keystore'))
        ):
            return False
    return True


def _mentions(text: str, path: str) -> bool:
    return re.search(r'(?<![\w./-])' + re.escape(path) + r'(?![\w./-])', text) is not None


class RepositoryContextClient:
    def __init__(self, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.transport = transport

    async def fetch(
        self, payload: CreateTask, api_token: str, issue_text: str,
    ) -> RepositoryContext:
        if not api_token.strip():
            raise TaskError(503, '请先在设置中配置 GitHub 凭据')
        owner, repository, _ = github_source_coordinates(payload.repository_url, payload.issue_url)
        prefix = f'/repos/{owner}/{repository}/git'
        headers = {
            'Authorization': f'Bearer {api_token}',
            'Accept': 'application/vnd.github+json',
            'X-GitHub-Api-Version': '2026-03-10',
            'User-Agent': 'RepoPilot',
        }
        try:
            async with httpx.AsyncClient(
                base_url='https://api.github.com', headers=headers, transport=self.transport,
                timeout=15.0, follow_redirects=False, trust_env=False,
            ) as client:
                raw = await self._download(
                    client, f'{prefix}/trees/{payload.baseline_commit}?recursive=1',
                    TREE_RESPONSE_LIMIT,
                )
                if raw is None:
                    raise TaskError(502, 'GitHub 文件树超过 2 MiB，无法安全读取仓库上下文')
                try:
                    tree = json.loads(raw)
                except (ValueError, UnicodeError):
                    raise _invalid() from None
                return await self._build(client, prefix, payload.baseline_commit, tree, issue_text)
        except (TimeoutError, httpx.TimeoutException):
            raise TaskError(504, '读取 GitHub 来源超时，请稍后重试') from None
        except httpx.RequestError:
            raise TaskError(502, '无法连接 GitHub 来源服务，请稍后重试') from None

    @staticmethod
    async def _download(
        client: httpx.AsyncClient, path: str, limit: int, *, blob: bool = False,
    ) -> bytes | None:
        headers = {'Accept': 'application/vnd.github.raw+json'} if blob else None
        async with asyncio.timeout(15):
            async with client.stream('GET', path, headers=headers) as response:
                _check_status(response, '仓库上下文')
                chunks: list[bytes] = []
                size = 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > limit:
                        return None
                    chunks.append(chunk)
                return b''.join(chunks)

    async def _build(
        self, client: httpx.AsyncClient, prefix: str, commit: str, tree: object, issue_text: str,
    ) -> RepositoryContext:
        if (
            not isinstance(tree, dict) or not isinstance(tree.get('tree'), list)
            or type(tree.get('truncated')) is not bool
            or not isinstance(tree.get('sha'), str)
            or re.fullmatch(r'[0-9a-fA-F]{40}', tree['sha']) is None
        ):
            raise _invalid()
        entries = tree['tree']
        truncated = tree['truncated'] or len(entries) > TREE_ENTRY_LIMIT
        omissions: list[str] = []
        if tree['truncated']:
            omissions.append('GitHub 文件树响应已截断，未提供的路径不可见')
        if len(entries) > TREE_ENTRY_LIMIT:
            omissions.append('文件树仅解析前 10000 条记录')
        paths: dict[str, dict] = {}
        skipped = 0
        seen: set[str] = set()
        for entry in entries[:TREE_ENTRY_LIMIT]:
            if not isinstance(entry, dict):
                raise _invalid()
            path, kind, mode, sha = (entry.get(key) for key in ('path', 'type', 'mode', 'sha'))
            if (
                not isinstance(path, str) or not path or path in seen
                or kind not in {'blob', 'tree', 'commit'}
                or not isinstance(sha, str) or re.fullmatch(r'[0-9a-fA-F]{40}', sha) is None
                or mode not in {'100644', '100755', '040000', '120000', '160000'}
                or (kind == 'tree' and mode != '040000')
                or (kind == 'commit' and mode != '160000')
                or (kind == 'blob' and mode not in {'100644', '100755', '120000'})
            ):
                raise _invalid()
            seen.add(path)
            if not _safe_path(path) or kind == 'commit' or mode == '120000':
                skipped += 1
                continue
            if kind == 'blob' and (type(entry.get('size')) is not int or entry['size'] < 0):
                raise _invalid()
            paths[path] = entry
        if skipped:
            omissions.append(f'跳过 {skipped} 条敏感、依赖/构建目录、链接或不安全路径记录')
        saved_paths = sorted(paths)[:TREE_PATH_LIMIT]
        if len(paths) > TREE_PATH_LIMIT:
            truncated = True
            omissions.append('保存的文件树仅含按路径排序的前 500 条安全路径')

        selected: dict[str, str] = {}

        def select(path: str, reason: str) -> None:
            if path in paths and paths[path]['type'] == 'blob':
                selected.setdefault(path, reason)

        select('AGENTS.md', '根目录项目约定')
        for path in sorted(paths):
            if '/' not in path and (path.lower() == 'readme' or path.lower().startswith('readme.')):
                select(path, '项目说明')
        for path in ENTRY_FILES:
            select(path, '项目入口配置')
        matches = sorted(path for path in paths if _mentions(issue_text, path))
        conventions: set[str] = set()
        for path in matches:
            parent = PurePosixPath(path)
            if paths[path]['type'] == 'blob':
                parent = parent.parent
            while str(parent) != '.':
                conventions.add(str(parent / 'AGENTS.md'))
                parent = parent.parent
        for path in sorted(conventions, key=lambda value: (value.count('/'), value)):
            select(path, 'Issue 路径适用的项目约定（根到叶）')
        for path in matches:
            select(path, 'Issue 中匹配的仓库路径')
        samples = [
            path for path in sorted(paths)
            if path.startswith('src/') and paths[path]['type'] == 'blob'
            and PurePosixPath(path).suffix.lower() in TEXT_SUFFIXES and path not in selected
        ]
        for path in samples[:3]:
            select(path, '按路径排序的源码样本，不代表已完成问题定位')

        files: list[ContextFile] = []
        total = 0
        downloads = 0
        for path, reason in selected.items():
            entry = paths[path]
            if downloads >= BLOB_LIMIT or total >= TOTAL_CONTENT_LIMIT:
                omissions.append(f'{path}：上下文文件数量或总注入预算已用尽')
                continue
            if entry['size'] > BLOB_DOWNLOAD_LIMIT:
                omissions.append(f'{path}：文件超过 64 KiB 下载上限')
                continue
            downloads += 1
            raw = await self._download(
                client, f"{prefix}/blobs/{quote(entry['sha'], safe='')}",
                BLOB_DOWNLOAD_LIMIT, blob=True,
            )
            if raw is None:
                omissions.append(f'{path}：下载超过 64 KiB 上限')
                continue
            actual = hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()
            if actual != entry['sha'].lower() or len(raw) != entry['size']:
                raise _invalid()
            try:
                content = raw.decode('utf-8')
            except UnicodeError:
                omissions.append(f'{path}：不是 UTF-8 文本')
                continue
            if any(ord(char) < 32 and char not in '\n\r\t' for char in content):
                omissions.append(f'{path}：二进制内容不注入目标')
                continue
            limit = min(FILE_CONTENT_LIMIT, TOTAL_CONTENT_LIMIT - total)
            limited = raw[:limit].decode('utf-8', errors='ignore')
            if raw and not limited:
                omissions.append(f'{path}：总注入预算已用尽，剩余额度不足以保存 UTF-8 字符')
                continue
            file_truncated = len(raw) > limit
            total += len(limited.encode('utf-8'))
            if file_truncated:
                omissions.append(f'{path}：内容已按单文件 16 KiB / 总量 64 KiB 预算截断')
            files.append(ContextFile(
                path=path, blob_sha=entry['sha'].lower(), content=limited,
                truncated=file_truncated, reason=reason,
            ))
        omissions.append('仅读取优先说明、配置、Issue 匹配路径与最多 3 个源码样本；其余文件未读取')
        return RepositoryContext(
            commit=commit, tree=saved_paths, files=files, omissions=omissions,
            tree_truncated=truncated,
        )
