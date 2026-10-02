from __future__ import annotations

import base64
import binascii
import json
import re
from datetime import datetime, timezone
from typing import Annotated, Literal
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StrictStr,
    field_validator,
    model_validator,
)

from repopilot.domain.context import RepositoryContext


TaskStatus = Literal['draft', 'generating', 'awaiting_approval', 'approved', 'generation_failed']
MessageKind = Literal['source', 'feedback', 'goal', 'approval']
GenerationAction = Literal['generate', 'revise', 'retry']
UTCDateTime = Annotated[AwareDatetime, AfterValidator(lambda value: value.astimezone(timezone.utc))]
PositiveInt = Annotated[int, Field(strict=True, ge=1)]
NonNegativeInt = Annotated[int, Field(strict=True, ge=0)]


class TaskError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def _github_path(value: str) -> str:
    value = value.strip()
    if any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError('GitHub 链接不能包含空白或控制字符')
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        raise ValueError('请输入有效的 GitHub HTTPS 链接') from None
    if (
        parsed.scheme != 'https'
        or parsed.hostname != 'github.com'
        or parsed.username is not None
        or parsed.password is not None
        or port not in (None, 443)
        or parsed.netloc.endswith(':')
        or '?' in value
        or '#' in value
    ):
        raise ValueError('目前仅支持不含凭据、查询参数或片段的 GitHub HTTPS 链接')
    return parsed.path


def _repository_parts(path: str) -> tuple[str, str]:
    parts = path.split('/')
    if len(parts) != 3 or parts[0]:
        raise ValueError('仓库链接格式应为 https://github.com/owner/repo')
    owner, repository = parts[1:]
    if repository.endswith('.git'):
        repository = repository[:-4]
    if (
        not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?', owner)
        or not re.fullmatch(r'[A-Za-z0-9_.-]{1,100}', repository)
        or repository in {'.', '..'}
    ):
        raise ValueError('请输入有效的 GitHub 仓库名称')
    return owner.lower(), repository.lower()


def normalize_github_repository_url(value: str) -> str:
    path = _github_path(value)
    if path.endswith('/'):
        path = path[:-1]
    owner, repository = _repository_parts(path)
    return f'https://github.com/{owner}/{repository}'


def normalize_github_issue_url(value: str) -> str:
    path = _github_path(value)
    parts = path.split('/')
    if len(parts) != 5 or parts[3] != 'issues' or not re.fullmatch(r'[0-9]+', parts[4]):
        raise ValueError('Issue 链接格式应为 https://github.com/owner/repo/issues/正整数')
    if parts[2].endswith('.git'):
        raise ValueError('Issue 链接不能包含仓库克隆地址的 .git 后缀')
    owner, repository = _repository_parts('/'.join(parts[:3]))
    try:
        issue_number = int(parts[4])
    except ValueError:
        raise ValueError('请输入有效的 Issue 编号') from None
    if issue_number < 1:
        raise ValueError('Issue 编号必须为正整数')
    return f'https://github.com/{owner}/{repository}/issues/{issue_number}'


def normalize_baseline_commit(value: str) -> str:
    value = value.strip()
    if not re.fullmatch(r'[0-9a-fA-F]{40}', value):
        raise ValueError('请输入完整的 40 位十六进制 commit SHA')
    return value.lower()


def github_source_coordinates(repository_url: str, issue_url: str) -> tuple[str, str, int]:
    repository_url = normalize_github_repository_url(repository_url)
    issue_url = normalize_github_issue_url(issue_url)
    owner, repository = repository_url.removeprefix('https://github.com/').split('/')
    if issue_url.rsplit('/issues/', 1)[0] != repository_url:
        raise ValueError('Issue 必须属于所填写的 GitHub 仓库')
    return owner, repository, int(issue_url.rsplit('/', 1)[1])


class TaskModel(BaseModel):
    model_config = ConfigDict(extra='forbid', from_attributes=True)


class CreateTask(TaskModel):
    repository_url: StrictStr
    baseline_commit: StrictStr
    issue_url: StrictStr

    @field_validator('repository_url')
    @classmethod
    def normalize_repository(cls, value: str) -> str:
        return normalize_github_repository_url(value)

    @field_validator('baseline_commit')
    @classmethod
    def normalize_commit(cls, value: str) -> str:
        return normalize_baseline_commit(value)

    @field_validator('issue_url')
    @classmethod
    def normalize_issue(cls, value: str) -> str:
        return normalize_github_issue_url(value)

    @model_validator(mode='after')
    def require_matching_repository(self) -> CreateTask:
        github_source_coordinates(self.repository_url, self.issue_url)
        return self


class GenerateGoalRequest(TaskModel):
    expected_revision: PositiveInt
    action: GenerationAction
    feedback: StrictStr | None = None

    @field_validator('feedback')
    @classmethod
    def normalize_feedback(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if len(value) > 8000:
            raise ValueError('修改意见不能超过 8,000 字符')
        return value.strip() or None

    @model_validator(mode='after')
    def validate_action_feedback(self) -> GenerateGoalRequest:
        if self.action == 'revise' and not self.feedback:
            raise ValueError('请填写需要修改的目标、范围或验收标准')
        if self.action != 'revise' and self.feedback:
            raise ValueError('仅修改目标时可以提交新的修改意见')
        return self


class ApproveGoalRequest(TaskModel):
    expected_revision: PositiveInt
    goal_version: PositiveInt


class GoalContent(TaskModel):
    summary: StrictStr
    scope: list[StrictStr] = Field(min_length=1)
    non_goals: list[StrictStr]
    acceptance_criteria: list[StrictStr] = Field(min_length=1)
    plan: list[StrictStr] = Field(min_length=1)
    open_questions: list[StrictStr]

    @field_validator('summary')
    @classmethod
    def normalize_summary(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError('目标摘要不能为空')
        return value

    @field_validator('scope', 'non_goals', 'acceptance_criteria', 'plan', 'open_questions')
    @classmethod
    def normalize_items(cls, value: list[str]) -> list[str]:
        normalized = [item.strip() for item in value]
        if any(not item for item in normalized):
            raise ValueError('目标列表不能包含空白项')
        return normalized


class SourceSnapshot(TaskModel):
    repository_url: StrictStr
    baseline_commit: StrictStr
    issue_number: PositiveInt
    issue_title: StrictStr
    issue_body: StrictStr
    issue_url: StrictStr
    issue_updated_at: UTCDateTime
    fetched_at: UTCDateTime
    repository_context: RepositoryContext


class TaskGoal(TaskModel):
    version: PositiveInt
    content: GoalContent
    model: StrictStr
    created_at: UTCDateTime


class TaskMessage(TaskModel):
    id: PositiveInt
    kind: MessageKind
    text: StrictStr
    goal_version: PositiveInt | None = None
    created_at: UTCDateTime


class TaskSummary(TaskModel):
    id: UUID
    title: StrictStr
    repository_url: StrictStr
    baseline_commit: StrictStr
    issue_url: StrictStr
    status: TaskStatus
    revision: PositiveInt
    current_goal_version: NonNegativeInt
    approved_goal_version: PositiveInt | None = None
    created_at: UTCDateTime
    updated_at: UTCDateTime


class TaskDetail(TaskSummary):
    source_snapshot: SourceSnapshot
    goals: list[TaskGoal]
    messages: list[TaskMessage]
    approved_at: UTCDateTime | None = None
    last_error: StrictStr | None = None
    generation_deadline: UTCDateTime | None = None


class TaskList(TaskModel):
    items: list[TaskSummary]
    next_cursor: StrictStr | None = None


class TaskStats(TaskModel):
    total: NonNegativeInt = 0
    draft: NonNegativeInt = 0
    generating: NonNegativeInt = 0
    awaiting_approval: NonNegativeInt = 0
    approved: NonNegativeInt = 0
    generation_failed: NonNegativeInt = 0


def encode_cursor(updated_at: datetime, task_id: UUID) -> str:
    if updated_at.tzinfo is None or updated_at.utcoffset() is None:
        raise ValueError('Cursor timestamp must include a timezone')
    payload = json.dumps(
        [updated_at.astimezone(timezone.utc).isoformat(), str(task_id)],
        separators=(',', ':'),
    ).encode('utf-8')
    return base64.urlsafe_b64encode(payload).decode('ascii').rstrip('=')


def decode_cursor(cursor: str) -> tuple[datetime, UUID]:
    try:
        if not isinstance(cursor, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,512}', cursor):
            raise ValueError
        payload = base64.b64decode(cursor + '=' * (-len(cursor) % 4), altchars=b'-_', validate=True)
        decoded = json.loads(payload)
        if not isinstance(decoded, list) or len(decoded) != 2 or any(not isinstance(item, str) for item in decoded):
            raise ValueError
        timestamp = datetime.fromisoformat(decoded[0])
        task_id = UUID(decoded[1])
        if encode_cursor(timestamp, task_id) != cursor:
            raise ValueError
        return timestamp.astimezone(timezone.utc), task_id
    except (ValueError, TypeError, UnicodeError, binascii.Error, OverflowError):
        raise TaskError(422, '分页游标无效，请刷新对话列表') from None
