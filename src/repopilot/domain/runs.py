from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictStr, field_validator

from repopilot.domain.tasks import GoalContent, PositiveInt, SourceSnapshot, UTCDateTime

RunStatus = Literal['queued', 'running', 'completed', 'failed', 'blocked', 'exhausted', 'cancelled', 'interrupted']
EventKind = Literal['context', 'assistant', 'tool_start', 'tool_result', 'check', 'artifact', 'state']
TERMINAL_STATUSES = frozenset({'completed', 'failed', 'blocked', 'exhausted', 'cancelled', 'interrupted'})
EVENT_KINDS = frozenset({'context', 'assistant', 'tool_start', 'tool_result', 'check', 'artifact', 'state'})


class CreateRunRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')

    expected_revision: PositiveInt
    goal_version: PositiveInt
    image: StrictStr = Field(min_length=1, max_length=256)
    setup_command: StrictStr = Field(default='', max_length=4000)
    check_command: StrictStr = Field(min_length=1, max_length=4000)

    @field_validator('image', 'setup_command', 'check_command')
    @classmethod
    def reject_nul(cls, value: str) -> str:
        if '\x00' in value:
            raise ValueError('执行参数不能包含 NUL')
        return value

    @field_validator('image', 'check_command')
    @classmethod
    def require_nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError('镜像和检查命令不能为空')
        return value


class RunCheck(BaseModel):
    model_config = ConfigDict(extra='forbid')

    phase: Literal['setup', 'baseline', 'final']
    command: str
    exit_code: int | None = None
    output: str
    truncated: bool = False


class RunEvent(BaseModel):
    id: int
    kind: EventKind
    payload: dict
    created_at: UTCDateTime


class RunSummary(BaseModel):
    id: UUID
    task_id: UUID
    goal_version: int
    status: RunStatus
    cancel_requested: bool
    created_at: UTCDateTime
    started_at: UTCDateTime | None = None
    finished_at: UTCDateTime | None = None
    error: str | None = None


class RunDetail(RunSummary):
    image: str
    image_id: str | None = None
    setup_command: str
    check_command: str
    report: str = ''
    patch: str = ''
    checks: list[RunCheck] = Field(default_factory=list)
    events: list[RunEvent] = Field(default_factory=list)


@dataclass(frozen=True)
class ClaimedRun:
    id: UUID
    task_id: UUID
    worker_token: UUID
    source_snapshot: SourceSnapshot
    goal_content: GoalContent
    image: str
    setup_command: str
    check_command: str
