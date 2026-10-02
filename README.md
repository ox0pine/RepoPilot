# RepoPilot

RepoPilot 是面向 Python、React 和 Vue 仓库的目标驱动编码工作台：从固定 GitHub Issue 与 commit 建立上下文，生成人工确认的 Goal，再显式启动 Docker 内的 Agent，审阅检查记录、代码差异和报告。

```text
固定仓库 / commit / Issue → 生成与修改 Goal → 人工批准
                                              ↓ 单独确认启动
                           Docker Run → 实际检查 → Patch / 报告审阅
```

**批准 Goal 不会自动执行。检查通过不等于独立验收，结果仍需人工审阅。**

- 当前功能、安装和操作说明：本文。
- 功能演进、修复和历史验收：[CHANGELOG.md](CHANGELOG.md)。
- 产品规划与架构决策：[development-plan/](development-plan/README.md)。其中长期路线不代表已实现能力；当前交付范围以决策 D-021、D-022 为准。

## 当前能力与边界

- 共享访问密钥登录、模型与 GitHub 设置、对话列表及统计。
- 校验 GitHub HTTPS 仓库、完整 commit SHA 和同仓库 Issue；保存不可变来源与有限源码上下文。
- 生成、反馈修改和批准 Goal；保留版本与消息记录。
- 显式创建 Run，由单个顺序 Worker 在独占 Docker 容器中执行。
- 轮询执行状态，查看工具日志、实际命令、退出码、报告与 Diff，下载 `.patch` 和 `.json`。
- 支持取消与中断清理；切换页面、刷新或退出登录不会取消 Run。

不支持 SSE、执行中反馈、继承上一轮成果继续修改或 PR 发布。每次 Run 都从固定 commit 重新开始。通用镜像提供 Python/Node 工具，但不保证任意项目依赖自动可用；Python、React、Vue 的完整真实项目任务集与独立验收仍需扩展。

## 快速开始：Docker 部署

### 1. 配置环境

需要 Docker Engine / Docker Desktop 和 Docker Compose。仅在 `.env` 不存在时创建配置：

```bash
test -f .env || cp .env.example .env
```

编辑 `.env`，至少设置数据库密码和随机的 `API_TOKEN`。使用 GitHub 凭据前，还需设置稳定的 `GITHUB_CREDENTIALS_KEY`。可在安全终端用已配置的 Conda 环境生成：

```bash
conda run -n repopilot python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'
```

将结果保存到 `.env` 并独立备份，不要提交或公开。更换加密主密钥无法解密已有 GitHub 凭据。

### 2. 启动工作台

```bash
docker compose up -d --build --wait
docker compose ps
```

访问 **http://127.0.0.1:8081/**，使用 `API_TOKEN` 登录，然后在设置中配置模型和 GitHub token。

| 服务 | 职责 | 默认宿主端口 |
| --- | --- | --- |
| `web` | Nginx 前端与 `/api` 反向代理 | `8081` |
| `api` | FastAPI、鉴权与业务接口 | `8000` |
| `postgres` | 设置、对话、Goal、Run、日志和成果 | `5432` |
| `redis` | 模型列表缓存，TTL 300 秒 | `6379` |
| `worker` | 顺序领取和执行 Run，需启用 `execution` profile | 不暴露端口 |

宿主端口均绑定 `127.0.0.1`。API 启动要求 PostgreSQL 和 Redis 均可连接。Redis 使用独立数据卷与 AOF，不承担执行队列或业务数据的权威存储。

### 3. 准备执行环境并启用 Worker

```bash
docker build -f deploy/runtime.Dockerfile -t repopilot-dev:local .
docker compose --profile execution up -d --build worker
docker compose --profile execution ps
```

默认开发镜像包含 Python 3.12、Node 22、Git、ripgrep 和 pytest。也可使用预装项目依赖的镜像，但须提供 `python3`、`/bin/sh` 及项目所需工具。

Worker 必须能通过 Docker daemon 找到指定镜像。`EXECUTION_IMAGE` 只提供表单默认值，不会自动构建或拉取镜像；镜像缺失时 Run 为 `blocked`。没有 Worker 时，Run 保持排队。

### 主要配置

完整示例见 [.env.example](.env.example)。

| 变量 | 用途 |
| --- | --- |
| `API_TOKEN` | 工作台共享访问密钥 |
| `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` | Compose 中的数据库配置 |
| `DATABASE_URL` | 宿主运行 API/Worker 时使用的 PostgreSQL 连接 |
| `REDIS_URL` | 宿主运行 API 时使用的 Redis 连接 |
| `GITHUB_CREDENTIALS_KEY` | GitHub token 和私钥的稳定 Fernet 加密主密钥 |
| `EXECUTION_IMAGE` | 执行表单默认镜像，默认 `repopilot-dev:local` |
| `WEB_PORT` / `API_PORT` / `POSTGRES_PORT` / `REDIS_PORT` | 对应宿主端口覆盖值 |

Compose 会给 API/Worker 注入容器网络连接地址。容器访问宿主机模型服务时，应使用 `host.docker.internal`，不是容器内的 `127.0.0.1`。

## 使用流程

### 模型与 GitHub 设置

1. 在模型设置中填写 Chat Completions 端点和 API Key，刷新模型列表、选择模型并保存。支持无需 API Key 的端点。
2. 同一端点省略 API Key 时保留已保存密钥；更换端点不会复用旧密钥。模型列表按端点与密钥隔离，在 Redis 中缓存 300 秒。
3. 保存 GitHub token。私有仓库建议使用限定仓库的 fine-grained token，授予 `Contents: Read-only` 和 `Issues: Read-only`；组织可能另需批准。

保存 GitHub token 只做本地加密持久化，不发起 GitHub 写入，也不等于已经验证仓库权限。来源访问在创建对话时校验。

SSH 公钥授权是单独的可选操作，**生成 Goal 和运行 Agent 不需要 SSH 授权**。仅明确点击授权才向 GitHub 注册公钥，该操作需要额外的 SSH keys 写权限。

### 创建和批准 Goal

1. 填写 GitHub HTTPS 仓库链接、完整 40 位 commit SHA，以及同仓库 Issue 链接。不接受分支名、短 SHA、PR 链接或其他托管平台。
2. 创建时读取固定来源，保存 Issue、文件树、实际读取的文件及覆盖限制。来源卡可展开查看；文件树和源码样本不代表已经完成问题定位。
3. 审阅 Goal 的目标、范围、不包含事项、验收标准、执行计划和待确认问题；需要调整时提交反馈。
4. 批准最新 Goal。修改 Goal 会立即撤销旧批准，即使后续生成失败也不会恢复批准。

生成和修改复用同一来源快照，不重新获取漂移内容。生成失败仅手动重试；409 表示状态或版本已变化，需要重新读取并人工确认，不能静默覆盖。

### 执行与审阅

在“执行与成果审阅”中填写：

- **执行镜像**：已准备好的 Docker 镜像。
- **准备命令**：可空。填写代表可信用户允许依赖安装脚本在准备阶段联网。
- **检查命令**：必填。执行器先运行一次记录 baseline，模型提出结束后再次运行同一命令。

点击“开始执行”只创建排队记录，不在 API 请求内运行 Docker。同一对话只能有一个排队或运行中的 Run；活跃执行期间不能修改 Goal。

只有 final 检查退出码为 0、完整成果捕获和容器清理成功，Run 才会 `completed`，界面显示“检查通过，待人工审阅”。final 失败会回填原循环，不重置预算。无变更时报告明确标注“未产生代码差异”。

取消排队 Run 可立即结束；取消运行中 Run 会先显示正在停止，确认进程与容器清理成功后才进入终态。Worker 重启会清理遗留容器并标记中断，不重放未知工具动作；清理失败则保留 `running`，停止领取新任务。

提交结果不明时只重新读取，不自动重发。读取失败保留已有内容，并要求显式重读。报告、日志和 Patch 均按纯文本展示。

## 执行限制与安全边界

| 项目 | 上限或规则 |
| --- | --- |
| 来源读取 | 总计 35 秒；Issue 正文 64 KiB |
| 文件树 | 响应 2 MiB，解析最多 10,000 条，保存最多 500 条路径 |
| 源码上下文 | 最多 12 个 blob；单个下载 64 KiB、注入 16 KiB，总注入 64 KiB |
| Goal 生成 | 单次 60 秒，响应 256 KiB，只接受 JSON 对象 |
| 执行模型循环 | 全 Run 900 秒、24 轮、累计模型输入 256 KiB；每次调用 60 秒、响应 256 KiB |
| 命令 | 准备命令 300 秒；普通 Shell 60 秒，超时停止容器 |
| 容器资源 | 2 CPU、2 GiB 内存、256 PID；工作区、HOME、临时目录为限额 tmpfs |
| 保存成果 | 最多 256 个事件，每个 32 KiB；报告 64 KiB；Patch 8 MiB |

- 只有可信 Worker 挂载 Docker socket。API 和 Run 容器不挂载；Run 容器没有宿主目录或用户模型/GitHub 凭据，根文件系统只读，并限制 capabilities。
- 准备命令为空时无外网；准备命令结束后断开网络，再进入模型循环。准备阶段允许第三方脚本联网，不宣称可以防御任意网络攻击；更高隔离需求应使用预装镜像并留空准备命令。
- 工具为 `read_file`、固定文本 `search`、带已读 hash 校验的 `edit_file`、仅新建的 `write_file` 和 `shell`。整批调用先校验，非法或截断响应不会执行半批工具；未知网络结果不自动重试。
- Patch 从控制面基线与最终文件生成，不依赖 Agent 可改写的 `.git`。支持新增、删除、二进制与权限变更；排除未跟踪依赖/缓存产物，基线已有文件始终参与比较。超限不会保存截断的可应用 Patch。
- 工作台数据和凭据由所有持有访问密钥的成员共享，不是多租户隔离。GitHub token/私钥加密保存；模型 API Key 目前在 PostgreSQL 中为明文，HTTP 响应不回显，需保护数据库及备份。
- 仅保存可见模型文本，不保存内部思维链。安全校验与固定检查不是独立验收，下载成果后仍应人工审查。

## 数据与日常运维

普通停止保留数据卷：

```bash
docker compose --profile execution down
```

**不要使用 `down -v`，除非明确要删除数据。** 删除 PostgreSQL 卷会清空模型/GitHub 设置、对话、Goal、Run 和日志，重建后需重新配置。

当前只维护现行存储与协议格式，没有旧数据库升级、字段补齐或旧密钥迁移逻辑。来源快照必须含源码上下文；GitHub 密钥只支持完整、匹配的 Ed25519/OpenSSH 格式。普通重启不是清库，不能将“只支持当前格式”理解为可以自动删除数据。

Worker 使用 PostgreSQL session advisory lock 保证单实例，按 FIFO 顺序领取任务。每两秒检查锁连接、每秒检查取消请求；第二个 Worker 会退出。Worker 不配置自动重启，异常停止后应先排查，再显式启动。不要同时运行容器 Worker 和宿主 Worker。

Redis 只缓存模型列表：运行中缓存读取失败会请求提供方，写入失败不丢弃已获取列表；提供方失败不会回退到过期结果。API 启动时仍要求 Redis 可连接。

## 本地开发

要求 Python `>=3.12`、Conda、Git、Docker，以及 Node `^20.19.0 || >=22.12.0`。宿主开发使用 `repopilot` Conda 环境，不使用 `.venv`；已有环境可跳过创建。

```bash
conda env create --file environment.yml
conda activate repopilot
python -m pip install --no-deps --editable .
docker compose up -d --wait postgres redis
python -m repopilot
```

另开终端启动前端：

```bash
npm --prefix web ci
npm --prefix web run dev
```

需要宿主执行 Worker 时，在 Conda 环境运行 `python -m repopilot.worker`。Vite 默认将 `/api` 代理到 `127.0.0.1:8000`；切换到宿主 API 前确认端口未被 Docker API 占用，不要同时运行两份服务。宿主连接由 `.env` 的 `DATABASE_URL` 和 `REDIS_URL` 指定。

### 验证命令

```bash
npm --prefix web run build
conda run -n repopilot ruff check src tests
```

后端测试必须使用**专用 PostgreSQL 和 Redis**，不可指向工作台服务。数据库测试使用独立 UUID schema；缓存测试仅清理本测试的键。配置专用测试地址并准备执行镜像后：

```bash
TEST_DATABASE_URL='postgresql+asyncpg://测试用户:测试密码@127.0.0.1:测试端口/测试库' \
TEST_REDIS_URL='redis://127.0.0.1:测试Redis端口/0' \
REPOPILOT_DOCKER_TESTS=1 conda run -n repopilot python -m pytest tests -q
```

GitHub/模型协议测试使用注入的 HTTP transport，生产默认真实网络；真实 Docker 场景由 `REPOPILOT_DOCKER_TESTS=1` 启用。缺少服务而跳过测试不算验证通过。前端构建包含 `vue-tsc` 和 Vite，当前没有独立前端测试或 lint 脚本。历史测试计数和已知告警见 [CHANGELOG.md](CHANGELOG.md)，不作为当前自动通过的保证。

## 项目结构

```text
src/repopilot/
├── api/              # HTTP、鉴权与组合根
├── application/      # 设置、Goal 与 Run 用例
├── domain/           # 数据与协议契约
├── persistence/      # PostgreSQL 表、事务和存储
├── integration/      # GitHub、模型 HTTP 与 Redis 缓存
├── execution/        # 模型工具循环、Docker 工作区、成果捕获
└── worker.py         # 顺序执行、取消与中断清理
web/src/              # Vue / TypeScript、Naive UI、Lucide
tests/                # 协议、数据库、API 和 Docker 回归
deploy/               # Dockerfile 与 Nginx 配置
development-plan/     # 产品规划与架构决策
```

后端沿用 FastAPI → 应用服务 → 领域契约与基础设施分层，`api/app.py:create_app` 是组合根。前端使用 composition API、轻量自定义路由和模块级 reactive stores，不引入 Vue Router 或 Pinia。`reference/` 是忽略的参考材料，不作为应用导入来源。

## 主要 API

下列业务接口均要求工作台 Bearer；完整请求结构以领域契约和前端 API 类型为准。

| 接口 | 用途 |
| --- | --- |
| `GET /api/settings`、`PUT /api/settings/model` | 模型设置摘要与保存 |
| `POST /api/models/refresh` | 获取模型列表 |
| `GET /api/settings/github`、`PUT /api/settings/github` | GitHub 凭据摘要与保存 |
| `POST /api/settings/github/authorize` | 显式注册 SSH 公钥 |
| `POST /api/tasks`、`GET /api/tasks` | 创建与分页读取对话 |
| `GET /api/tasks/stats`、`GET /api/tasks/{id}` | 统计与详情 |
| `POST /api/tasks/{id}/goal` | 生成、修改或重试 Goal，携带 `expected_revision` |
| `POST /api/tasks/{id}/approve` | 批准最新 `goal_version`，携带 `expected_revision` |
| `GET /api/execution` | 执行镜像默认值 |
| `POST /api/tasks/{id}/runs` | 显式排队，返回 202；提交版本、镜像和命令 |
| `GET /api/tasks/{id}/runs`、`GET /api/tasks/{id}/runs/{run_id}` | 最近 50 条 Run 与单次详情 |
| `POST /api/tasks/{id}/runs/{run_id}/cancel` | 请求取消 |

409 冲突需重新读取并人工确认。模型服务认证失败返回安全 502，不作为工作台 401 清除登录会话。`GET /api/health` 用于服务健康检查。
