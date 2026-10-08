# 本地开发与贡献

[文档首页](../README.md) · [项目首页](../../README.md)

## 准备环境

先按[环境变量说明](../configuration/environment.md)创建 `.env` 并设置宿主数据库、Redis 连接和访问密钥。以下命令在仓库根目录执行。

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

需要执行任务时，先构建运行镜像，再在另一个已激活 Conda 环境的终端启动 Worker：

```bash
docker build -f deploy/runtime.Dockerfile -t repopilot-dev:local .
python -m repopilot.worker
```

若修改了 `EXECUTION_IMAGE`，构建标签应与其一致。不要与 Compose Worker 同时运行。

Vite 默认将 `/api` 代理到 `127.0.0.1:8000`；切换到宿主 API 前确认端口未被 Docker API 占用，不要同时运行两份服务。宿主连接由 `.env` 的 `DATABASE_URL` 和 `REDIS_URL` 指定。

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

GitHub/模型协议测试使用注入的 HTTP transport，生产默认真实网络；真实 Docker 场景由 `REPOPILOT_DOCKER_TESTS=1` 启用。缺少服务而跳过测试不算验证通过。前端构建包含 `vue-tsc` 和 Vite，当前没有独立前端测试或 lint 脚本。历史测试计数和已知告警见 [更新日志](../../CHANGELOG.md)，不作为当前自动通过的保证。

## 提交改动

提交问题时提供复现步骤、预期与实际结果，以及去除凭据的错误信息。提交代码时说明影响范围与实际运行的验证，不把历史检查记录当作本次结果。

- Python 保持类型标注、snake_case 命名和现有分层；网络请求不放入持有数据库锁的事务。
- Vue 使用 `<script setup lang="ts">`、现有 typed API、轻量路由及模块级 store；复用 Naive UI 与 Lucide。
- 保留 revision/version 并发控制、会话隔离及结果未知时不自动重发写请求的行为。
- 行为变更同步更新对应文档和[更新日志](../../CHANGELOG.md)。涉及 UI 时检查桌面与移动端交互，而不只运行构建。
- 不提交 `.env`、凭据、本地运行产物或真实用户数据。

代码位置与数据流见[架构说明](architecture.md)。
