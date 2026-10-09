# Repository Guidelines

## Project Overview

RepoPilot is a self-hosted AI coding workbench for Python, React, and Vue repositories: pin a GitHub repository/commit/Issue, generate and revise a goal, obtain human approval, execute changes in Docker, then review checks and patches. Delivery to a repair branch requires a separate explicit action; do not automatically create PRs or merge changes.

## Architecture & Data Flow

- Backend layers: FastAPI routes → application services → domain contracts, persistence repositories, and integration adapters. `src/repopilot/api/app.py:create_app` is the composition root; inject dependencies through constructors and router factories.
- PostgreSQL owns settings, immutable source snapshots, versioned goals, messages, the run queue, events, and results. Redis only caches model lists—it is not the task queue.
- The API queues approved work. `worker.py` uses a PostgreSQL advisory lock to enforce one active worker, executes Docker work outside database transactions, and persists results. Every run starts from its pinned commit, not a previous run's workspace.
- `execution/engine.py` coordinates the model/tool loop, checks, and patch capture; `execution/workspace.py` owns Docker isolation and workspace lifecycle. Preserve whole-batch tool validation, hash/version-fenced edits, and confirmed cleanup before terminal run states.
- Browser → same-origin `/api` → FastAPI, via Vite in development or Nginx in deployment. Vue uses a custom History API router and module-level reactive stores, not Vue Router or Pinia.

## Key Directories

| Path | Responsibility |
| --- | --- |
| `src/repopilot/api/`, `application/`, `domain/` | HTTP/authentication, use-case orchestration, data/protocol contracts |
| `src/repopilot/persistence/`, `integration/` | PostgreSQL transactions and repositories; GitHub/model HTTP, Redis, delivery |
| `src/repopilot/execution/` | Docker workspaces, environment discovery, model tools, LSP sessions, patch capture |
| `web/src/` | Typed API adapters, Vue components/views, lightweight router and stores |
| `tests/` | Backend protocol, persistence, lifecycle, workspace, and Docker regressions |
| `deploy/`, `docs/` | Container/Nginx recipes; configuration, development, operations, and API references |

Do not import application code from ignored `reference/` materials.

## Development Commands

Run from the repository root. Prepare `.env` using `.env.example` and `docs/configuration/environment.md`; preserve existing values and never commit credentials.

```sh
conda env create --file environment.yml  # first-time setup
conda activate repopilot
python -m pip install --no-deps --editable .
docker compose up -d --wait postgres redis
python -m repopilot                     # API
```

Separate terminals for the frontend and, when executing tasks, the worker:

```sh
npm --prefix web ci
npm --prefix web run dev

docker build -f deploy/runtime.Dockerfile -t repopilot-dev:local .
python -m repopilot.worker              # activated repopilot environment
```

Match the runtime image tag to `EXECUTION_IMAGE`. Do not run host API/worker instances alongside their Compose equivalents. Vite proxies `/api` to `127.0.0.1:8000`; host database/Redis URLs must address the exposed services rather than Compose hostnames.

```sh
npm --prefix web run build              # vue-tsc --noEmit, then Vite
conda run -n repopilot ruff check src tests
conda run -n repopilot python -m pytest tests -q
# Narrow a regression run:
conda run -n repopilot python -m pytest tests/test_runs.py -q
```

Tests require the dedicated-service configuration below; a passing command with skipped integrations is not full verification.

## Code Conventions & Common Patterns

- Python: type annotations, `snake_case` functions/modules, existing service/repository boundaries. Ruff targets Python 3.12 with a 100-character line length. TypeScript is strict; Vue components use PascalCase filenames and `<script setup lang="ts">`. Reuse Naive UI and Lucide.
- Keep database lock transactions short; do not perform network or Docker work while holding them. Preserve revision/goal-version checks, generation/delivery leases, worker ownership tokens, and durable event ordering around side effects.
- Approval, run creation, and delivery are distinct operations. Retain conflict handling and reconciliation rather than silently retrying writes whose outcome is unknown.
- Use domain/application error handling and safe API errors; sanitize upstream failures and keep secrets out of logs, events, and model context. Inject HTTP transports for protocol tests instead of bypassing real parsing.
- Frontend calls go through `web/src/api/client.ts` and typed adapters. Tokens remain memory-only. Preserve abort controllers, request/session-generation guards, monotonic revision merges, and session-fenced 401 handling so stale responses cannot overwrite newer state.
- Preserve cancellation and cleanup behavior across async tasks; terminal status must not imply successful container cleanup without confirmation.
- Behavior changes update the relevant `docs/` page and `CHANGELOG.md`. Historical verification records are not evidence that current changes pass.

## Important Files

- `src/repopilot/__main__.py`, `cli.py`: API launch; `api/app.py`: dependency composition and resource lifecycle; `worker.py`: execution/recovery entry point.
- `persistence/tasks.py`, `persistence/runs.py` under `src/repopilot/`: revision-controlled lifecycle, queue ownership, and result persistence. `persistence/database.py` bootstraps tables; do not assume an existing migration workflow.
- `src/repopilot/execution/tools.py`, `execution/lsp/manager.py`: tool contracts and snapshot-validated semantic edits. `integration/delivery.py`: explicit repair-branch delivery and remote-conflict safeguards.
- `web/src/App.vue`, `router/index.ts`, `stores/session.ts`, `stores/tasks.ts`: routing, authentication, shared state. `views/ConversationView.vue` and `components/RunPanel.vue`: approval/run polling and uncertain-write handling.
- `pyproject.toml`, `environment.yml`, `requirements-dev.in`, `requirements-dev.txt`, `uv.lock`: Python packaging and dependency configuration. `web/package.json`, `web/package-lock.json`, `web/vite.config.ts`: frontend tooling.
- `compose.yaml`, `deploy/runtime.Dockerfile`: deployment topology and execution toolchain. Start with `docs/development/contributing.md` and `docs/development/architecture.md`; use `docs/reference/security.md` for isolation boundaries.

## Runtime/Tooling Preferences

- Python `>=3.12`; preferred host development uses the `repopilot` Conda environment, not `.venv`. `environment.yml` pins Python 3.12.14 and installs hash-locked host dependencies.
- Frontend uses npm and its committed lockfile, with Node `^20.19.0 || >=22.12.0`; do not replace this with Bun. Use `npm ci` for reproducible installation.
- Keep dependency declarations and their corresponding locks aligned. Host `requirements-dev.in`/`.txt`, application `pyproject.toml`/`uv.lock`, and sandbox tooling are separate dependency surfaces, not interchangeable environments.
- Git, Docker Engine/Compose, PostgreSQL, and Redis are required for the complete workflow. The worker mounts the Docker socket: deploy only on a trusted host, and never expose control-plane credentials to task containers.

## Testing & QA

- pytest and pytest-asyncio; `pyproject.toml` sets `testpaths = ["tests"]` and `asyncio_mode = "auto"`. Follow `tests/test_*.py`; shared fixtures live in owning modules such as `tests/test_tasks.py` and are imported by dependent suites.
- Set `TEST_DATABASE_URL` and `TEST_REDIS_URL` to **dedicated test services**, never the workbench's production/development data services. Database fixtures use UUID schemas; Redis fixtures delete only their own keys.
- Protocol tests use injected HTTP transports; filesystem tests use `tmp_path` and real local Git. Enable real Docker scenarios with `REPOPILOT_DOCKER_TESTS=1` after building the runtime image, e.g. `REPOPILOT_DOCKER_TESTS=1 conda run -n repopilot python -m pytest tests -q` with both test URLs exported.
- Missing infrastructure can cause skips. Report what actually ran and which integrations were skipped. No enforced coverage percentage is configured.
- There is no independent frontend test or lint script. Run the frontend build and exercise affected desktop/mobile interactions; compilation alone does not verify UI behavior.
