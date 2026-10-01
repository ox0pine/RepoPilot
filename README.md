# RepoPilot

## 项目现状

RepoPilot 正在从零重构。旧项目在目标和范围尚未明确时推进了过多功能，后续修改逐渐混杂，因此本仓库不继续沿用旧架构，而是重新确定目标、划分边界，再逐步实现。

旧项目资料已移动到本仓库的 `reference/` 目录，并通过 `.gitignore` 忽略。该目录只用于查阅和对比，不会自动并入新项目。

目前仍处于**目标澄清与开发环境准备阶段**，尚未开始业务功能实现。

## 重构原则

- **目标先于实现**：先确定核心用户、使用场景、输入输出和验收标准。
- **明确不做的事情**：每个阶段限定范围，不把旧项目的全部功能默认纳入新项目。
- **先跑通真实流程**：从一个核心场景开始，验证端到端结果，再扩展能力。
- **按需要选择技术**：依赖、模块划分和部署方式由已确认的需求决定，不照搬旧架构。
- **逐阶段验收**：完成一个阶段并取得可观察的验证结果后，再推进下一个阶段。

## 重构计划

| 阶段 | 工作内容 | 完成标准 | 状态 |
| --- | --- | --- | --- |
| 0. 参考资料与开发环境整理 | 将旧仓库参考资料集中到 `reference/`，直接复用已确定的 Python/uv 环境配置 | 参考资料可查阅且不污染新项目；Python 开发环境配置位于项目根目录 | 已完成 |
| 1. 明确目标与边界 | 确定首个核心场景、用户、输入输出，以及暂不支持的能力 | 有一个明确的任务示例及其验收标准，有清晰的非目标清单 | 待开始 |
| 2. 设计最小实现路径 | 围绕核心场景确定执行流程、必要模块、接口和技术选择 | 能说明每个模块的职责及存在理由，不预建无需求的子系统 | 待开始 |
| 3. 实现首个完整流程 | 按确认的设计实现核心功能，不以占位实现替代实际能力 | 使用真实输入运行，得到可检查的结果，覆盖关键失败路径 | 待开始 |
| 4. 验收并整理 | 按场景验收，补充必要回归测试与使用说明，清理临时代码 | 核心场景可重复运行，已知限制有记录，代码与文档一致 | 待开始 |
| 5. 按需求迭代 | 根据实际使用结果确定下一项能力 | 每次扩展都先确定范围和验收标准，再实施 | 待开始 |

## 当前进度

### 已完成

- [x] 明确采用从零重构的方式，旧仓库仅供参考。
- [x] 将旧仓库的 `reference/` 目录剪切到当前仓库的 `reference/`。
- [x] 在 `.gitignore` 中忽略整个 `reference/` 目录。
- [x] 直接复用并保留 Python 开发环境配置在项目根目录：
  - `pyproject.toml`
  - `uv.lock`
  - `environment.yml`
  - `requirements-dev.in`
  - `requirements-dev.txt`
- [x] 保留当前仓库原有的 `README.md` 和 `.gitignore`。

`reference/` 仅用于保存旧仓库中的参考项目；Python/uv 环境配置已经迁移到当前项目根目录，作为新项目当前采用的开发环境基础。

### 尚未完成

- [ ] 确定新项目首先解决的具体任务及验收标准。
- [ ] 确定本轮重构的功能边界与非目标。
- [ ] 基于已复用的 Python/uv 环境创建并验证新项目代码结构。
- [ ] 确定代码结构并实现首个核心场景。

### 当前环境状态

- `pyproject.toml`、`uv.lock`、`environment.yml`、`requirements-dev.in` 和 `requirements-dev.txt` 当前直接位于项目根目录。
- 当前采用 Python `>=3.12`，Conda 配置固定 Python `3.12.14`。
- `uv lock --check` 已通过，说明项目声明与锁定文件一致；这不代表业务代码已经完成或应用已经可运行。
- 已实现访问密钥认证、工作台和模型设置；代码任务执行尚未实现。

### Docker 全栈运行

`compose.yaml` 包含 PostgreSQL 16、Redis 7、FastAPI 和 Nginx 前端。PostgreSQL 保存模型配置，Redis 缓存模型列表（300 秒），两个数据库均使用命名数据卷。

首次运行时，复制 `.env.example` 为 `.env`（不要覆盖现有文件），设置数据库密码和随机的 `API_TOKEN`，然后：

```bash
docker compose up -d --build --wait
docker compose ps
```

访问 `http://127.0.0.1:8081/`，Nginx 将 `/api` 转发至 API 容器。所有宿主机端口只绑定 `127.0.0.1`；API 默认 8000、PostgreSQL 5432、Redis 6379，可通过 `.env` 调整。

容器内的模型服务地址若位于宿主机，使用 `http://host.docker.internal:端口/v1`，不要使用容器自己的 `127.0.0.1`。

停止服务使用 `docker compose down`，数据卷会保留；不要使用 `down -v`，除非确定要删除数据库数据。

当前项目已建立最小分层结构：

```text
src/repopilot/
├── api/              # FastAPI 应用、鉴权与 HTTP 路由
├── application/      # 设置读写与模型端点服务
├── domain/           # 设置领域模型与校验
├── persistence/      # PostgreSQL 设置存储和初始化
└── integration/      # 外部模型端点与 Redis 缓存
```

### Conda 本地开发

宿主机统一使用 `repopilot` Conda 环境，不使用 `.venv`。全栈 Docker 已占用 8000 时，先执行 `docker compose stop api web` 再启动本地 API。

```bash
conda activate repopilot
python -m pip install -e .
docker compose up -d --wait postgres redis
python -m repopilot
```

本地 API 使用 `.env` 的 `DATABASE_URL` 和 `REDIS_URL`；数据库用户名、密码、端口应与 Compose 配置一致。Docker API 使用 Compose 注入的容器网络地址，不使用宿主机地址。

启动前端：

```bash
npm --prefix web run dev
```

工作台访问密钥由 `.env` 中的 `API_TOKEN` 控制。登录进入 `/app` 后，点击左下角、位于「退出工作台」左侧的「设置」打开弹窗。弹窗左栏当前提供「模型提供方」，右侧可配置 `Base URL`、`API Key`，并从兼容端点刷新模型列表。已配置密钥显示 `*****` 遮罩，不回显实际密钥，遮罩不会随表单提交；留空保留同一端点的原密钥。支持关闭按钮和 Escape 关闭弹窗。

前端按 `views/`、`components/`、`api/`、`router/`、`stores/` 和 `styles/` 分工，`App.vue` 仅协调页面与会话。

模型配置存放在 PostgreSQL 的 `model_settings` 表，启动时创建初始表和默认行。旧 `.repopilot/settings.json` 已删除，不再读写。API Key 在数据库中为明文，不会在 HTTP 响应中回显；请保护数据库凭据和备份。切换端点不会复用旧密钥。访问令牌仅保留在浏览器内存中，刷新后需要重新登录。

Redis 模型列表缓存按端点与密钥的哈希隔离，最长保留 300 秒；不存储密钥原文。数据库或 Redis 无法连接时，API 启动失败，不回退到 JSON 文件。只有通过访问密钥认证的成员才能修改设置或查询模型列表。

验证：Conda 环境中的 Ruff 检查和前端生产构建通过；四个服务已通过 Docker Compose 启动。实际验证 Nginx 登录、设置页显示、通过临时兼容模型端点刷新列表、Redis 缓存命中及凭据隔离、PostgreSQL 保存，并在 API 容器重启后读取相同设置。验证结束后已恢复空模型配置、删除测试缓存并关闭临时端点；未使用第三方真实模型服务凭据。

## 下一步

先明确：**新的 RepoPilot 首先替用户完成哪一个具体任务？**

围绕这个任务确定输入、期望输出、成功与失败的判定方式，以及暂不支持的能力。目标确认后，再整理依赖和设计实现路径；不提前迁移旧业务代码或扩展部署设施。