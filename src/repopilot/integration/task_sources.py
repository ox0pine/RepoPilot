from __future__ import annotations

import asyncio
import re
from datetime import datetime, timezone

import httpx

from repopilot.domain.tasks import (
    CreateTask,
    SourceSnapshot,
    TaskError,
    github_source_coordinates,
    normalize_github_issue_url,
)


ISSUE_BODY_LIMIT = 64 * 1024


def _invalid() -> TaskError:
    return TaskError(502, "GitHub 返回的来源数据无效，请稍后重试")


def _check_status(response: httpx.Response, source: str) -> None:
    if response.status_code == 401:
        raise TaskError(422, "GitHub 凭据无效或已过期，请在设置中更新")
    if response.status_code == 403:
        raise TaskError(422, "GitHub 拒绝访问，请检查仓库权限与 API 请求限额")
    if response.status_code in (404, 422):
        raise TaskError(422, f"无法访问所填写的{source}，请检查链接、commit 与 GitHub 读取权限")
    if response.status_code == 429:
        raise TaskError(502, "GitHub 请求限额已用尽，请稍后重试")
    if response.status_code != 200:
        raise TaskError(502, "GitHub 来源服务暂时不可用，请稍后重试")


def _json(response: httpx.Response) -> dict:
    try:
        value = response.json()
    except (ValueError, UnicodeError):
        raise _invalid() from None
    if not isinstance(value, dict):
        raise _invalid()
    return value


class GitHubSourceClient:
    def __init__(self, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.transport = transport

    async def fetch(self, payload: CreateTask, api_token: str) -> SourceSnapshot:
        if not api_token.strip():
            raise TaskError(503, "请先在设置中配置 GitHub 凭据")
        owner, repository, issue_number = github_source_coordinates(
            payload.repository_url, payload.issue_url
        )
        headers = {
            "Authorization": f"Bearer {api_token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2026-03-10",
            "User-Agent": "RepoPilot",
        }
        try:
            async with asyncio.timeout(35):
                async with httpx.AsyncClient(
                    base_url="https://api.github.com",
                    headers=headers,
                    timeout=15.0,
                    follow_redirects=False,
                    trust_env=False,
                    transport=self.transport,
                ) as client:
                    async with asyncio.timeout(15):
                        response = await client.get(
                            f"/repos/{owner}/{repository}/commits/{payload.baseline_commit}"
                        )
                    _check_status(response, "仓库或 commit")
                    commit = _json(response)
                    sha = commit.get("sha")
                    if (
                        not isinstance(sha, str)
                        or re.fullmatch(r"[0-9a-fA-F]{40}", sha) is None
                        or sha.lower() != payload.baseline_commit
                    ):
                        raise _invalid()
                    async with asyncio.timeout(15):
                        response = await client.get(
                            f"/repos/{owner}/{repository}/issues/{issue_number}"
                        )
                    _check_status(response, "Issue")
                    issue = _json(response)
                    return self._snapshot(payload, issue_number, issue)
        except (TimeoutError, httpx.TimeoutException):
            raise TaskError(504, "读取 GitHub 来源超时，请稍后重试") from None
        except httpx.RequestError:
            raise TaskError(502, "无法连接 GitHub 来源服务，请稍后重试") from None

    @staticmethod
    def _snapshot(payload: CreateTask, issue_number: int, issue: dict) -> SourceSnapshot:
        if "pull_request" in issue:
            raise TaskError(422, "目前仅支持 GitHub Issue，不支持 Pull Request")
        number = issue.get("number")
        title = issue.get("title")
        body = issue.get("body")
        html_url = issue.get("html_url")
        updated_at = issue.get("updated_at")
        if (
            type(number) is not int
            or number != issue_number
            or not isinstance(title, str)
            or not title.strip()
            or "body" not in issue
            or (body is not None and not isinstance(body, str))
            or not isinstance(html_url, str)
            or not isinstance(updated_at, str)
        ):
            raise _invalid()
        try:
            normalized_url = normalize_github_issue_url(html_url)
            timestamp = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
        except ValueError:
            raise _invalid() from None
        if normalized_url != payload.issue_url or timestamp.utcoffset() is None:
            raise _invalid()
        body = body if body is not None else ""
        try:
            body_size = len(body.encode("utf-8"))
        except UnicodeError:
            raise _invalid() from None
        if body_size > ISSUE_BODY_LIMIT:
            raise TaskError(422, "Issue 正文超过 64 KiB，请缩短内容后重新创建对话")
        return SourceSnapshot(
            repository_url=payload.repository_url,
            baseline_commit=payload.baseline_commit,
            issue_number=issue_number,
            issue_title=title,
            issue_body=body,
            issue_url=normalized_url,
            issue_updated_at=timestamp,
            fetched_at=datetime.now(timezone.utc),
        )
