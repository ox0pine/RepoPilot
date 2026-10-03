from __future__ import annotations

import asyncio
import os
import subprocess
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from pydantic import ValidationError
from test_tasks import database as database

from repopilot.domain.context import RepositoryContext
from repopilot.domain.runs import CreateDeliveryRequest, CreateRunRequest, RunDelivery
from repopilot.domain.tasks import GoalContent, SourceSnapshot, TaskError
from repopilot.execution.git import GitHubGitCredentials
from repopilot.integration import delivery as delivery_module
from repopilot.integration.delivery import DeliveryService
from repopilot.persistence.deliveries import DeliveryClaim, DeliveryRepository
from repopilot.persistence.runs import RunRepository
from repopilot.persistence.tasks import TaskRepository


PATCH = 'diff --git a/value.txt b/value.txt\n--- a/value.txt\n+++ b/value.txt\n@@ -1 +1 @@\n-old\n+new\n'


def test_delivery_request_is_explicit_and_option_free():
    assert CreateDeliveryRequest().model_dump() == {}
    with pytest.raises(ValidationError):
        CreateDeliveryRequest(branch='main')


def source() -> SourceSnapshot:
    now = datetime.now(UTC)
    return SourceSnapshot(
        repository_url='https://github.com/example/repo', baseline_commit='a' * 40,
        issue_number=1, issue_title='Repair value', issue_body='Body',
        issue_url='https://github.com/example/repo/issues/1', issue_updated_at=now,
        fetched_at=now, repository_context=RepositoryContext(
            commit='a' * 40, tree=[], files=[], omissions=['fixture'], tree_truncated=False,
        ),
    )


async def completed_run(database, patch=PATCH):
    tasks, runs = TaskRepository(database), RunRepository(database)
    task = await tasks.create(source())
    attempt = await tasks.start_generation(task.id, task.revision, 'generate')
    goal = GoalContent(
        summary='Repair', scope=['value'], non_goals=[], acceptance_criteria=['fixed'],
        plan=['edit'], open_questions=[],
    )
    task = await tasks.complete_generation(task.id, attempt.generation_id, goal, 'test')
    task = await tasks.approve(task.id, task.revision, task.current_goal_version)
    run = await runs.create(task.id, CreateRunRequest(
        expected_revision=task.revision, goal_version=task.current_goal_version,
    ))
    claimed = await runs.claim()
    assert claimed is not None
    finished = await runs.finish(
        run.id, claimed.worker_token, status='completed', report='done', patch=patch,
    )
    assert finished is not None
    return task, finished


async def test_delivery_rejects_noncompleted_empty_and_missing_artifacts(database):
    tasks, runs, deliveries = TaskRepository(database), RunRepository(database), DeliveryRepository(database)
    task = await tasks.create(source())
    attempt = await tasks.start_generation(task.id, task.revision, 'generate')
    goal = GoalContent(summary='Repair', scope=['value'], non_goals=[], acceptance_criteria=['fixed'], plan=['edit'], open_questions=[])
    task = await tasks.complete_generation(task.id, attempt.generation_id, goal, 'test')
    task = await tasks.approve(task.id, task.revision, 1)
    queued = await runs.create(task.id, CreateRunRequest(expected_revision=task.revision, goal_version=1))
    with pytest.raises(TaskError) as active:
        await deliveries.claim(task.id, queued.id)
    assert active.value.status_code == 409
    claimed = await runs.claim()
    assert claimed is not None
    await runs.finish(queued.id, claimed.worker_token, status='completed', patch='')
    with pytest.raises(TaskError) as empty:
        await deliveries.claim(task.id, queued.id)
    assert empty.value.status_code == 409
    with pytest.raises(TaskError) as missing:
        await deliveries.claim(task.id, uuid4())
    assert missing.value.status_code == 404


async def test_delivery_is_single_flight_and_completed_result_is_idempotent(database):
    task, run = await completed_run(database)
    repository = DeliveryRepository(database)
    claim = await repository.claim(task.id, run.id)
    assert isinstance(claim, DeliveryClaim)
    assert claim.source.baseline_commit == 'a' * 40
    assert claim.branch == f'repopilot/run-{run.id}'
    with pytest.raises(TaskError) as concurrent:
        await repository.claim(task.id, run.id)
    assert concurrent.value.status_code == 409
    delivered = await repository.complete(
        claim, commit_sha='b' * 40,
        branch_url=f'https://github.com/example/repo/tree/{claim.branch}',
        compare_url=f'https://github.com/example/repo/compare/main...{claim.branch}',
    )
    duplicate = await repository.claim(task.id, run.id)
    assert isinstance(duplicate, RunDelivery) and duplicate == delivered
    assert (await RunRepository(database).detail(task.id, run.id)).delivery == delivered


async def test_failed_delivery_can_be_explicitly_retried_on_same_branch(database):
    task, run = await completed_run(database)
    repository = DeliveryRepository(database)
    first = await repository.claim(task.id, run.id)
    assert isinstance(first, DeliveryClaim)
    await repository.fail(first, 'unknown remote result')
    second = await repository.claim(task.id, run.id)
    assert isinstance(second, DeliveryClaim)
    assert second.token != first.token and second.branch == first.branch


async def test_delivery_requires_token_owner_to_match_repository(database):
    task, run = await completed_run(database)
    repository = DeliveryRepository(database)
    claim = await repository.claim(task.id, run.id)
    assert isinstance(claim, DeliveryClaim)

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == '/user':
            return httpx.Response(200, json={'login': 'someone-else'})
        return httpx.Response(200, json={'default_branch': 'main'})

    service = DeliveryService(repository, object(), transport=httpx.MockTransport(handler))
    with pytest.raises(TaskError) as wrong_owner:
        await service._deliver(claim, GitHubGitCredentials(api_token='token', private_key=''))
    assert wrong_owner.value.status_code == 409

def git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ['git', *args], cwd=cwd, check=True, capture_output=True, text=True,
        env={**os.environ, 'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': os.devnull},
    ).stdout.strip()


def local_repository(tmp_path: Path) -> tuple[Path, Path, str]:
    source = tmp_path / 'source'
    remote = tmp_path / 'remote.git'
    source.mkdir()
    git(source, 'init', '--initial-branch=main')
    git(source, 'config', 'user.name', 'Fixture')
    git(source, 'config', 'user.email', 'fixture@example.invalid')
    (source / 'value.txt').write_text('old\n')
    git(source, 'add', 'value.txt')
    git(source, 'commit', '-m', 'baseline')
    baseline = git(source, 'rev-parse', 'HEAD')
    git(tmp_path, 'init', '--bare', str(remote))
    git(source, 'remote', 'add', 'origin', str(remote))
    git(source, 'push', 'origin', 'main')
    return source, remote, baseline


def local_boundaries(monkeypatch, remote: Path) -> None:
    async def clone(repository_url, commit, destination, credentials, **kwargs):
        def clone_sync() -> None:
            subprocess.run(
                ['git', 'clone', '--no-checkout', str(remote), str(destination)],
                check=True, capture_output=True,
            )
            git(destination, 'checkout', '--detach', commit)
            git(destination, 'config', 'user.name', 'RepoPilot')
            git(destination, 'config', 'user.email', 'repopilot@users.noreply.github.com')

        await asyncio.to_thread(clone_sync)

    @asynccontextmanager
    async def auth(repository_url, credentials, **kwargs):
        yield SimpleNamespace(repository_url=str(remote), environment={
            'PATH': os.environ.get('PATH', '/usr/bin:/bin'), 'HOME': str(remote.parent),
            'LC_ALL': 'C', 'GIT_CONFIG_NOSYSTEM': '1',
            'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_TERMINAL_PROMPT': '0',
            'GIT_CONFIG_COUNT': '1', 'GIT_CONFIG_KEY_0': 'core.hooksPath',
            'GIT_CONFIG_VALUE_0': os.devnull,
        })

    monkeypatch.setattr(delivery_module, 'clone_fixed_commit', clone)
    monkeypatch.setattr(delivery_module, 'github_git_auth', auth)


async def local_claim(database, baseline: str, patch: str = PATCH):
    snapshot = source().model_copy(update={'baseline_commit': baseline})
    tasks, runs = TaskRepository(database), RunRepository(database)
    task = await tasks.create(snapshot)
    attempt = await tasks.start_generation(task.id, task.revision, 'generate')
    goal = GoalContent(summary='Repair', scope=['value'], non_goals=[], acceptance_criteria=['fixed'], plan=['edit'], open_questions=[])
    task = await tasks.complete_generation(task.id, attempt.generation_id, goal, 'test')
    task = await tasks.approve(task.id, task.revision, 1)
    run = await runs.create(task.id, CreateRunRequest(expected_revision=task.revision, goal_version=1))
    running = await runs.claim()
    assert running is not None
    await runs.finish(run.id, running.worker_token, status='completed', patch=patch)
    claim = await DeliveryRepository(database).claim(task.id, run.id)
    assert isinstance(claim, DeliveryClaim)
    return claim


async def test_real_local_git_delivery_is_fixed_idempotent_and_never_moves_default(database, tmp_path, monkeypatch):
    _, remote, baseline = local_repository(tmp_path)
    local_boundaries(monkeypatch, remote)
    claim = await local_claim(database, baseline)

    async def metadata(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={'login': 'example'} if request.url.path == '/user' else {'default_branch': 'main'})

    service = DeliveryService(DeliveryRepository(database), object(), transport=httpx.MockTransport(metadata))
    credentials = GitHubGitCredentials(api_token='token', private_key='')
    # First delivery pushes but is deliberately not persisted; retry must reconcile it.
    first = await service._deliver(claim, credentials)
    second = await service._deliver(claim, credentials)
    assert first == second
    assert git(tmp_path, '--git-dir', str(remote), 'rev-parse', 'refs/heads/main') == baseline
    branch_sha = git(tmp_path, '--git-dir', str(remote), 'rev-parse', f'refs/heads/{claim.branch}')
    assert branch_sha == first['commit_sha']
    assert git(tmp_path, '--git-dir', str(remote), 'rev-parse', f'{branch_sha}^') == baseline
    assert git(tmp_path, '--git-dir', str(remote), 'show', f'{branch_sha}:value.txt') == 'new'


async def test_real_local_git_delivery_rejects_existing_different_branch(database, tmp_path, monkeypatch):
    _, remote, baseline = local_repository(tmp_path)
    local_boundaries(monkeypatch, remote)
    claim = await local_claim(database, baseline)
    git(tmp_path, '--git-dir', str(remote), 'branch', claim.branch, baseline)

    async def metadata(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={'login': 'example'} if request.url.path == '/user' else {'default_branch': 'main'})

    service = DeliveryService(DeliveryRepository(database), object(), transport=httpx.MockTransport(metadata))
    with pytest.raises(TaskError) as collision:
        await service._deliver(claim, GitHubGitCredentials(api_token='token', private_key=''))
    assert collision.value.status_code == 409
    assert git(tmp_path, '--git-dir', str(remote), 'rev-parse', f'refs/heads/{claim.branch}') == baseline


async def test_real_local_git_delivery_rejects_git_metadata_patch_without_hook_execution(database, tmp_path, monkeypatch):
    _, remote, baseline = local_repository(tmp_path)
    local_boundaries(monkeypatch, remote)
    marker = tmp_path / 'executed'
    malicious = (
        'diff --git a/.git/hooks/post-commit b/.git/hooks/post-commit\n'
        'new file mode 100755\n--- /dev/null\n+++ b/.git/hooks/post-commit\n'
        '@@ -0,0 +1,2 @@\n+#!/bin/sh\n+touch ' + str(marker) + '\n'
    )
    claim = await local_claim(database, baseline, malicious)

    async def metadata(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={'login': 'example'} if request.url.path == '/user' else {'default_branch': 'main'})

    service = DeliveryService(DeliveryRepository(database), object(), transport=httpx.MockTransport(metadata))
    with pytest.raises(TaskError):
        await service._deliver(claim, GitHubGitCredentials(api_token='token', private_key=''))
    assert not marker.exists()
    assert git(tmp_path, '--git-dir', str(remote), 'rev-parse', 'refs/heads/main') == baseline
