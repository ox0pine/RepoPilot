# Repository Guidelines

## Project Overview

RepoPilot is a goal-driven coding workbench for Python, React, and Vue repositories. Implemented: shared access-key login, model/GitHub settings, conversation lists/statistics, verified GitHub Issue and bounded source-context snapshots, model-generated Goals, feedback revisions, human approval, Docker Runs, polling, cancellation, check/log/Report/Patch review and downloads, and explicit repair-branch delivery. **The UI's “批准并执行” action approves the Goal and then creates one Run using the returned revision/version.** The approval API itself only approves; failures/unknown outcomes never silently repeat either POST. Manual rerun/recovery remains in the Run panel. SSE, automatic PR creation/approval/merge, in-run feedback and inheritance of previous Run changes are not implemented; older roadmap designs and verification records are historical.

## Architecture & Data Flow

- Backend: async FastAPI → application services → domain contracts, PostgreSQL repositories, and external integration clients. `src/repopilot/api/app.py:create_app` is the composition root; it constructs dependencies, injects services into router builders, and manages database/Redis lifespan. Extend this explicit wiring rather than adding a parallel dependency container.
- Creation: validate GitHub HTTPS repository URL, full 40-character commit SHA, and same-repository Issue → read fixed GitHub API and bounded tree/blob context with Git hash verification → persist immutable source snapshot and initial message. Repository context is required; no historical Issue-only snapshot compatibility. Generation/revision reuses the saved snapshot.
- Generation: commit a generation attempt → call non-streaming Chat Completions outside the transaction → conditionally append an immutable Goal version and message. States: `draft → generating → awaiting_approval → approved`, with `generation_failed` and explicit manual retry.
- PostgreSQL is authoritative for settings, tasks, Goal versions, messages, Runs and bounded Run events. Redis retains the original 300-second model-list cache, isolated by normalized endpoint and API key; a cache hit avoids a provider request. Expired/invalid/unavailable cache falls through to the provider, never to stale data on provider failure. API startup requires PostgreSQL and Redis. No JSON fallback or old database/schema migration support.
- Correction (D-022): removing Redis was an unauthorized mistake, now reversed. The authorized PostgreSQL reset and current-format-only source/key/Goal contracts remain in force; do not restore old business data. Historical verification stays historical; report Redis restoration checks only after actually running them.
- Execution: Run creation only queues under the Task lock and increments revision; its request contains only expected_revision and goal_version. Image is server configuration, not a user field. A single `python -m repopilot.worker` owns session advisory lock 721804630 and executes FIFO outside transactions. System automatically detects Python/Node projects, provisions matching tools/dependencies, chooses real existing checks, then disconnects preparation networking before model tools. Containers have readonly roots, limited executable tmpfs/resources, no host mounts/socket/credentials. Token-fenced writes prevent late workers; cleanup must succeed before cancelled/interrupted. Startup retires orphan Runs rather than replaying unknown effects. Every Run starts from its fixed commit.
- Source preparation uses authenticated real Git clone and exact detached checkout, not archive downloads. A saved SSH private key selects strict pinned-host SSH; without a private key, HTTPS uses an ephemeral token helper. Credentials remain on the trusted control plane; the sandbox retains credential-free `.git`, excluded from Patch capture. Default Git identity is `RepoPilot <repopilot@users.noreply.github.com>`.
- Delivery is an explicit action for completed Runs with nonempty Patch: reclone the fixed baseline, apply Patch without host source execution, commit and push a deterministic repair branch to the saved-token user's own repository. `run_deliveries` fences concurrent attempts and reconciles explicit retries. Never automatically create/approve/merge PRs or write the default branch; the UI links to GitHub for human merge.
- Frontend: Vue composition API with typed API wrappers, a lightweight custom router, and module-level reactive stores—not Pinia or Vue Router. Requests flow through `web/src/api/client.ts`; `App.vue` coordinates authentication and page selection. Workbench credentials/data are shared, not per-user isolated.

## Key Directories

- `src/repopilot/{api,application,domain,persistence,integration}/`: HTTP boundaries, use-case orchestration, validated contracts, transactional storage, and external I/O respectively. Keep network work out of persistence transactions.
- `src/repopilot/execution/` and `worker.py`: bounded model protocol, validated tools, Docker workspace/artifact capture, sequential lifecycle. Shell executes only inside the container; host Git handles trusted file data for Patch generation, not source execution.
- `execution/environment.py` and `execution/lsp/`: automatic per-root Python `.venv` / Node environments and confined Pyright/Ruff/TS/Vue/HTML/CSS/JSON servers. Host RepoPilot development still uses Conda, never its own `.venv`. Preserve version/hash-fenced workspace edits, explicit unsupported/not-ready diagnostics, UTF16 conversion, and server shutdown before Shell/capture. Do not reintroduce manual environment/setup/check form fields.
- `web/src/{views,components,api,router,stores,styles}/`: pages, reusable UI, HTTP contracts, navigation, shared state, and theme ownership.
- `tests/`: backend domain/client, database-concurrency, and ASGI route tests.
- `deploy/`: API/frontend/worker/runtime Dockerfiles and Nginx configuration. Compose builds the runtime image and starts the worker by default; only worker mounts the Docker socket. The runtime initialization service exits successfully before worker starts. `development-plan/`: historical roadmap and product decisions; current operations are in README.
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

# Host worker alternative: do not run alongside the default Compose worker.
docker build -f deploy/runtime.Dockerfile -t repopilot-dev:local .
python -m repopilot.worker
# Full Compose startup below already builds runtime and starts worker; no profile needed.

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
- Keep current-only storage/protocol contracts: generated Ed25519/OpenSSH keypairs only, no RSA/ECDSA/PEM or incomplete-pair repair; Goal responses must be JSON, not Markdown fences. Empty settings are valid fresh-install state. A database reset requires explicit user authorization and clears all saved settings/history; never restore old data implicitly.
- Supported automatic environments are Python and Node frontend only. Retain project lockfiles as authoritative, record actual tools and check coverage, and expose missing/failed configuration instead of inventing successful checks. Environment network access is system-managed preparation only; model tools stay offline. Baseline tracked files remain in artifacts even beneath directories excluded for generated dependencies.

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
- PostgreSQL 16 and Redis 7 run as separate services; their client packages do not supply servers. Redis is only the model-list cache, not an execution queue. Docker host ports bind loopback. Container access to host model services uses `host.docker.internal`, not container-local `127.0.0.1`.
- Never overwrite `.env` or user credentials for verification. GitHub token/private key encryption requires the stable `GITHUB_CREDENTIALS_KEY`; replacing it cannot recover existing ciphertext. Model API keys currently remain plaintext in PostgreSQL, although responses omit them: protect database backups and never log secrets.

## Testing & QA

- Pytest + pytest-asyncio (`asyncio_mode = "auto"`); discovery defaults to `tests/`. Set `TEST_DATABASE_URL` (`postgresql+asyncpg://…`) and `TEST_REDIS_URL` (`redis://…`) to **dedicated test instances**, never workbench services. Database fixtures create/drop UUID schemas; route fixtures enter the real app lifespan with the dedicated Redis URL. Cache tests isolate endpoints/keys and delete only their own entries. Missing-service skips are not passing integration verification.
- Inject `httpx.MockTransport` for GitHub/model HTTP and use `ASGITransport` for API tests. Use real isolated PostgreSQL for persistence, locking, and rollback behavior. Keep production clients on real network paths; do not add fixed Goals or mock-source fallbacks.
- Prioritize revisions/approval invalidation, stale-result rejection, cancellation, lease recovery, pagination, secret redaction, credential replacement, upstream failures, and size/deadline boundaries. No numerical coverage threshold is configured; installed coverage tooling is not a coverage policy.
- Frontend build runs `vue-tsc --noEmit` plus Vite. UI changes also need browser checks at desktop/mobile sizes: auth races, deep links, drafts/errors, modal keyboard/focus behavior, and overflow. Controlled fixtures prove interaction only, not real GitHub/model/execution success. Report the checks actually run rather than quoting historical test counts.
