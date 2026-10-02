# Repository Guidelines

## Project Overview

RepoPilot is a goal-driven coding workbench being rebuilt from scratch for Python, React, and Vue repositories. Currently implemented: shared access-key login, model/GitHub settings, conversation lists/statistics, verified GitHub Issue snapshots, model-generated Goals, feedback revisions, and human approval. **Approval confirms a Goal only**; code execution, source-code search, SSE, artifact review, and PR delivery are not implemented. Treat roadmap designs and historical verification records as distinct from current behavior.

## Architecture & Data Flow

- Backend: async FastAPI → application services → domain contracts, PostgreSQL repositories, and external integration clients. `src/repopilot/api/app.py:create_app` is the composition root; it constructs dependencies, injects services into router builders, and manages database/Redis lifespan. Extend this explicit wiring rather than adding a parallel dependency container.
- Creation: validate GitHub HTTPS repository URL, full 40-character commit SHA, and same-repository Issue → read fixed GitHub API → persist verified source snapshot and initial message. Generation/revision reuses that snapshot; it does not inspect repository code.
- Generation: commit a generation attempt → call non-streaming Chat Completions outside the transaction → conditionally append an immutable Goal version and message. States: `draft → generating → awaiting_approval → approved`, with `generation_failed` and explicit manual retry.
- PostgreSQL is authoritative for settings, tasks, Goal versions, and messages. Redis only caches model lists for 300 seconds, keyed by endpoint/credential hash. Startup fails if either service is unavailable; no JSON fallback.
- Frontend: Vue composition API with typed API wrappers, a lightweight custom router, and module-level reactive stores—not Pinia or Vue Router. Requests flow through `web/src/api/client.ts`; `App.vue` coordinates authentication and page selection. Workbench credentials/data are shared, not per-user isolated.

## Key Directories

- `src/repopilot/{api,application,domain,persistence,integration}/`: HTTP boundaries, use-case orchestration, validated contracts, transactional storage, and external I/O respectively. Keep network work out of persistence transactions.
- `web/src/{views,components,api,router,stores,styles}/`: pages, reusable UI, HTTP contracts, navigation, shared state, and theme ownership.
- `tests/`: backend domain/client, database-concurrency, and ASGI route tests.
- `deploy/`: API/frontend Dockerfiles and Nginx configuration; `development-plan/`: product boundaries, roadmap, and decisions.
- `reference/` is ignored comparison material, not application code or an import source. `.repopilot/` and `tmp/` are local runtime/acceptance material, not maintained source. No root scripts directory or Makefile is provided.

## Development Commands

Run from the repository root. Create `.env` from `.env.example` only if absent; configure database credentials and `API_TOKEN` without exposing secrets.

```bash
# Host setup; use the existing environment if already provisioned.
conda env create --file environment.yml
conda activate repopilot
python -m pip install --no-deps --editable .

# Local backend with Docker-hosted dependencies.
docker compose up -d --wait postgres redis
python -m repopilot

# Frontend development / production type-check and build.
npm --prefix web ci
npm --prefix web run dev
npm --prefix web run build

# Backend lint and tests (set dedicated test URLs first; see Testing & QA).
conda run -n repopilot ruff check src tests
conda run -n repopilot python -m pytest tests -q

# Full-stack image build and startup.
docker compose up -d --build --wait
docker compose ps
```

Full-stack UI: `http://127.0.0.1:8081/`; API defaults to port 8000. Vite proxies `/api` to `127.0.0.1:8000`; adjust its config if changing the local API port. Avoid collisions with running Docker API/web services; stop them only when intentionally switching to local development. `docker compose down` preserves volumes; **do not use `down -v` against user data**.

## Code Conventions & Common Patterns

- Python: typed models/functions, `snake_case` modules/functions and `PascalCase` classes; Ruff targets Python 3.12 with a 100-character line length. Follow local formatting rather than reformatting unrelated code. Domain contracts use Pydantic validation, strict revisions/versions, forbidden extra fields, and UTC-aware timestamps.
- Vue: `<script setup lang="ts">`, PascalCase component files, camelCase functions/state, explicit component/icon imports, and existing single-quote/no-semicolon style. Keep API field names consistent with backend contracts.
- Preserve concurrency controls: short row-locked transactions, `expected_revision` conflicts, generation UUIDs, and 90-second database-clock leases. Late generation results cannot overwrite newer attempts. Lease recovery occurs during reads/writes, not in a worker; semantic rejection must not roll back already-recorded lease recovery.
- Goal revision immediately revokes prior approval, even if generation fails. A 409 requires rereading and human confirmation, not silent overwrite. Preserve cancellation propagation and shielded generation cleanup.
- Return safe domain/API errors, never raw provider/driver exceptions or credentials. Provider authentication failures are safe 502s, not workbench 401s. Preserve history/drafts on failures; do not disguise failed reads as empty lists or zero statistics.
- Frontend tokens stay in memory. Guard asynchronous results using session versions, request sequences, and `AbortController`; token equality alone cannot distinguish logout/re-login. Only a current-session 401 may clear authentication. Deduplicate paginated tasks by ID because updated records can move forward.
- Use Naive UI and official `@lucide/vue`, not a second component library. `styles/theme.ts` owns the shared palette/theme overrides; `main.ts` installs CSS variables before mount, including for Teleport dialogs. Preserve responsive layouts, focus return/trapping, and busy-state close guards.
- GitHub token saving is local encrypted persistence; optional SSH authorization is a separate explicit network-write action. Never introduce automatic registration into source/Goal flows. Preserve model-key semantics: omitted key retains it for the same endpoint; changing endpoints must not reuse the old key.

## Important Files

- `src/repopilot/__main__.py`, `cli.py`: launch the API, not a task-execution CLI; `api/app.py`: application factory/lifespan; `config.py`: environment settings.
- `application/tasks.py`, `persistence/tasks.py`, `domain/tasks.py`: orchestration, state transitions/locking, and task/Goal contracts.
- `persistence/database.py`: shared SQLAlchemy Base/table definitions and initialization. Current schema setup uses `create_all`, not an established migration workflow.
- `web/src/api/client.ts`, `stores/session.ts`, `stores/tasks.ts`, `router/index.ts`: request/auth races, session lifetime, pagination, and deep links.
- `pyproject.toml`, `environment.yml`, `requirements-dev.in`, `requirements-dev.txt`, `uv.lock`, `web/package.json`, `web/package-lock.json`: tooling/dependencies. `compose.yaml`, `.env.example`, `deploy/nginx.conf`, `web/vite.config.ts`: deployment/proxy settings.
- `README.md`: current operations and implemented behavior; `development-plan/03-roadmap.md`: phase gates; `development-plan/04-decisions.md`: architectural decisions. Older planning descriptions can lag implementation.

## Runtime/Tooling Preferences

- Python `>=3.12`; host development uses the `repopilot` Conda environment (pinned Python 3.12.14), **not `.venv`**. Keep development requirements input/output aligned. `uv.lock` exists, but host setup uses Conda/pip and the API image installs with pip; do not assume `uv sync` is the standard workflow.
- Frontend uses npm with committed package lock, not Bun. Node requirement: `^20.19.0 || >=22.12.0`; Docker builds with Node 22. TypeScript is strict. No frontend lint/test script is currently defined.
- PostgreSQL 16 and Redis 7 run as separate services; client packages do not supply servers. Docker host ports bind loopback. Container access to host model services uses `host.docker.internal`, not container-local `127.0.0.1`.
- Never overwrite `.env` or user credentials for verification. GitHub token/private key encryption requires the stable `GITHUB_CREDENTIALS_KEY`; replacing it cannot recover existing ciphertext. Model API keys currently remain plaintext in PostgreSQL, although responses omit them: protect database backups and never log secrets.

## Testing & QA

- Pytest + pytest-asyncio (`asyncio_mode = "auto"`); discovery defaults to `tests/`. Set `TEST_DATABASE_URL` (`postgresql+asyncpg://…`) and `TEST_REDIS_URL` (`redis://…`) to **dedicated test instances**, never the workbench database. Database fixtures create/drop UUID schemas; route fixtures enter the real app lifespan. Missing-service skips are not passing integration verification.
- Inject `httpx.MockTransport` for GitHub/model HTTP and use `ASGITransport` for API tests. Use real isolated PostgreSQL for persistence, locking, and rollback behavior. Keep production clients on real network paths; do not add fixed Goals or mock-source fallbacks.
- Prioritize revisions/approval invalidation, stale-result rejection, cancellation, lease recovery, pagination, secret redaction, credential replacement, upstream failures, and size/deadline boundaries. No numerical coverage threshold is configured; installed coverage tooling is not a coverage policy.
- Frontend build runs `vue-tsc --noEmit` plus Vite. UI changes also need browser checks at desktop/mobile sizes: auth races, deep links, drafts/errors, modal keyboard/focus behavior, and overflow. Controlled fixtures prove interaction only, not real GitHub/model/execution success. Report the checks actually run rather than quoting historical test counts.
