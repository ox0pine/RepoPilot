"""Fixture data shared by task, run, execution and delivery tests."""

from __future__ import annotations

from datetime import UTC, datetime

from repopilot.domain.context import RepositoryContext
from repopilot.domain.runs import CreateRunRequest, RunDetail
from repopilot.domain.tasks import GoalContent, SourceSnapshot, TaskDetail
from repopilot.persistence.runs import RunRepository
from repopilot.persistence.tasks import TaskRepository

PATCH = (
    'diff --git a/value.txt b/value.txt\n'
    '--- a/value.txt\n'
    '+++ b/value.txt\n'
    '@@ -1 +1 @@\n'
    '-old\n'
    '+new\n'
)


def make_source(number: int = 1, **overrides) -> SourceSnapshot:
    now = datetime.now(UTC)
    source = SourceSnapshot(
        repository_url='https://github.com/example/repo',
        baseline_commit='a' * 40,
        issue_number=number,
        issue_title=f'Issue {number}',
        issue_body='Fixed snapshot',
        issue_url=f'https://github.com/example/repo/issues/{number}',
        issue_updated_at=now,
        fetched_at=now,
        repository_context=RepositoryContext(
            commit='a' * 40, tree=[], files=[],
            omissions=['Fixture contains no source files'], tree_truncated=False,
        ),
    )
    return source.model_copy(update=overrides)


def make_goal(**overrides) -> GoalContent:
    goal = GoalContent(
        summary='解决问题', scope=['修复 Issue'], non_goals=[],
        acceptance_criteria=['复现问题后验证修复'], plan=['分析来源'], open_questions=[],
    )
    return goal.model_copy(update=overrides)


def make_request(task: TaskDetail, **changes) -> CreateRunRequest:
    values = dict(
        expected_revision=task.revision, goal_version=task.current_goal_version,
    )
    values.update(changes)
    return CreateRunRequest(**values)


async def ready(repo: TaskRepository, task: TaskDetail) -> TaskDetail:
    attempt = await repo.start_generation(task.id, task.revision, 'generate')
    completed = await repo.complete_generation(
        task.id, attempt.generation_id, make_goal(), 'test-model',
    )
    assert completed is not None
    return completed


async def approved_task(database, number: int = 1) -> TaskDetail:
    tasks = TaskRepository(database)
    task = await ready(tasks, await tasks.create(make_source(number)))
    return await tasks.approve(task.id, task.revision, task.current_goal_version)


async def completed_run(
    database, patch: str = PATCH, *, baseline: str | None = None,
) -> tuple[TaskDetail, RunDetail]:
    source = make_source()
    if baseline is not None:
        source = source.model_copy(update={'baseline_commit': baseline})
    tasks, runs = TaskRepository(database), RunRepository(database)
    task = await ready(tasks, await tasks.create(source))
    task = await tasks.approve(task.id, task.revision, task.current_goal_version)
    run = await runs.create(task.id, make_request(task))
    claimed = await runs.claim()
    assert claimed is not None
    finished = await runs.finish(
        run.id, claimed.worker_token, status='completed', report='done', patch=patch,
    )
    assert finished is not None
    return task, finished
