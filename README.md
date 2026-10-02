# RepoPilot

## 项目现状

RepoPilot 正在从零重构。旧项目在目标和范围尚未明确时推进了过多功能，后续修改逐渐混杂，因此本仓库不继续沿用旧架构，而是重新确定目标、划分边界，再逐步实现。

旧项目资料已移动到本仓库的 `reference/` 目录，并通过 `.gitignore` 忽略。该目录只用于查阅和对比，不会自动并入新项目。

当前已有首页、访问密钥登录、工作台外壳、模型服务设置与 GitHub 授权。前端已整体重设计为 Vue / TypeScript + Naive UI + Lucide（`@lucide/vue`）的浅色工作台。真实代码任务执行、SSE 对话、多轮反馈、恢复与 PR 交付仍未实现；首页流程卡明确标为「规划中」，工作台不提供假聊天或无后端的任务提交。

## 重构原则

- **目标先于实现**：先确定核心用户、使用场景、输入输出和验收标准。
- **明确不做的事情**：每个阶段限定范围，不把旧项目的全部功能默认纳入新项目。
- **先跑通真实流程**：从一个核心场景开始，验证端到端结果，再扩展能力。
- **按需要选择技术**：依赖、模块划分和部署方式由已确认的需求决定，不照搬旧架构。
- **逐阶段验收**：完成一个阶段并取得可观察的验证结果后，再推进下一个阶段。

## 重构计划

现行阶段与门禁以 [development-plan/03-roadmap.md](development-plan/03-roadmap.md) 为准：阶段 0 固定真实任务基线，阶段 1–4 证明执行与恢复，阶段 5 接入 SSE 对话，阶段 6 实现受控 PR，阶段 7 评测扩展。现有界面统一重构是独立 UI 专项，不等于执行链阶段完成。

产品目标与架构见 [项目定义](development-plan/01-project-definition.md)、[核心流程](development-plan/02-core-flow.md) 和 [决策记录](development-plan/04-decisions.md)。

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
- [x] 建立 FastAPI 设置服务、PostgreSQL 持久化、Redis 模型列表缓存与 Vue 前端。
- [x] 实现访问密钥认证、模型设置和 token-only GitHub 授权。
- [x] 统一首页、登录、工作台、侧栏、设置弹窗与两类表单的 Naive UI / Lucide 视觉与交互。

`reference/` 仅用于保存旧仓库中的参考项目；Python/uv 环境配置已经迁移到当前项目根目录，作为新项目当前采用的开发环境基础。

### 尚未完成

- [ ] 固定 Python、React、Vue 各类真实任务及独立验收基线。
- [ ] 实现并验证单一 Agent Loop、隔离工作区、Tool Executor 和首个 CLI 执行闭环。
- [ ] 按现行路线接入持久化恢复、SSE 多轮对话和受控 PR 交付。

### 当前环境状态

- `pyproject.toml`、`uv.lock`、`environment.yml`、`requirements-dev.in` 和 `requirements-dev.txt` 当前直接位于项目根目录。
- 当前采用 Python `>=3.12`，Conda 配置固定 Python `3.12.14`。
- 历史环境检查中 `uv lock --check` 已通过；该记录只证明当时依赖声明与锁文件一致，不证明任务执行能力完成。
- 已实现访问密钥认证、工作台、模型设置与 GitHub 授权；任务执行链尚未实现。

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
npm --prefix web ci
npm --prefix web run dev
```

工作台访问密钥由 `.env` 中的 `API_TOKEN` 控制。首页 `/` 的「进入工作台」由 App 按内存会话分发到 `/auth` 或 `/app`。登录页可显示/隐藏密钥并用 Enter 提交，不提供记住登录。工作台只展示配置准备卡和「本地会话」，不声称服务在线或任务执行可用。

首页保留产品介绍、工作台入口与规划流程，不再展示「从配置开始」的模型服务/GitHub 授权介绍卡；实际配置仍在登录后的设置窗口中进行。

登录后，桌面左下角「设置」或准备卡「打开设置」均打开同一个窗口；宽度不超过 768px 时侧栏改为顶部品牌与操作条，无固定底栏。窗口提供「模型提供方」与「GitHub 授权」，小屏分类横排。桌面尺寸上限 960×720px、视口留边 48px；不超过 640px 时留边 24px。分类切换保持边界不变，内容独立滚动，标题、关闭按钮和分类始终可见；Tab 限制在弹窗内，关闭后焦点返回原入口。GitHub 保存授权期间禁止关闭、Escape 和切换分类。

模型提供方可配置 `Base URL`、`API Key`，从兼容端点刷新并筛选可用模型。仅允许保存列表中的模型；空列表或刷新失败不能保存，旧端点迟到响应不会覆盖新端点。密钥遮罩不提交；同一端点留空省略 `api_key` 保留旧值，更换端点留空发送空字符串，不复用旧密钥。

前端按 `views/`、`components/`、`api/`、`router/`、`stores/` 和 `styles/` 分工。`App.vue` 保持页面与认证竞态协调，根部 `NConfigProvider` 使用中文 locale 与统一 `themeOverrides`；`styles/theme.ts` 的同一 palette 导出 CSS 变量，`main.ts` 在 mount 前写入 document 根，使 body 与 Teleport 弹窗共享颜色。`tokens.css` 仅保留非颜色变量。组件与图标显式导入，无第二套组件库、自动导入插件或主题切换。未来流式分层见 [核心流程](development-plan/02-core-flow.md)，本轮未安装 Markdown/流式依赖或创建空聊天模块。

模型配置存放在 PostgreSQL 的 `model_settings` 表，启动时创建初始表和默认行。旧 `.repopilot/settings.json` 已删除，不再读写。API Key 在数据库中为明文，不会在 HTTP 响应中回显；请保护数据库凭据和备份。切换端点不会复用旧密钥。访问令牌仅保留在浏览器内存中，刷新后需要重新登录。

Redis 模型列表缓存按端点与密钥的哈希隔离，最长保留 300 秒；不存储密钥原文。数据库或 Redis 无法连接时，API 启动失败，不回退到 JSON 文件。只有通过访问密钥认证的成员才能修改设置或查询模型列表。

### GitHub 授权设置

「GitHub 授权」只需填写 GitHub API token，点击「保存并授权」即可。后端在首次配置时自动生成 Ed25519 SSH 公私钥对，将私钥和 token 加密保存，并自动向 GitHub 注册公钥；不再提供手动导入公私钥或单独注册接口。已有完整匹配的密钥对会复用，包括旧版保存的 RSA/ECDSA 密钥；修改 token 或重试不会重新生成密钥。配置属于整个工作台，持有工作台访问令牌的成员均可修改，并非按用户隔离的 OAuth 登录。已有 token 时留空复用，切换分类或关闭弹窗丢弃未保存草稿。

部署时注入稳定的 `GITHUB_CREDENTIALS_KEY`，由部署者在安全终端生成，并与数据库备份独立保管：

```bash
conda run -n repopilot python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'
```

不要提交生成结果。私钥和 token 使用该 Fernet 主密钥分别加密后存入 PostgreSQL `github_settings` 表，读取接口只返回公钥及两个配置状态。未配置有效主密钥时 GitHub 接口返回 503，既有登录和模型设置仍可用；密钥不匹配现有密文时返回错误，不覆盖原数据。恢复时必须恢复同一主密钥及数据库备份，不自动生成替代密钥。模型 API Key 的原有存储方式不变。

「保存并授权」先原子保存 token 与密钥对，提交后再向固定 `https://api.github.com` 注册账户 SSH authentication key；先查询避免重复，永不上传私钥。推荐 fine-grained PAT 账户权限 `Git SSH keys: Read and write`，或 classic PAT 的 `read:public_key` + `write:public_key`，无需 repo/admin 权限。GitHub 拒绝或网络失败不会撤销本地保存，页面会明确提示并允许再次授权；超时结果可能未知，重试使用同一密钥先查询确认。配置状态不是持久授权状态，可在 <https://github.com/settings/keys> 核对。

后端回归验证使用专用 PostgreSQL/Redis，不得指向现有工作台数据库；每个数据库测试创建并清理独立 UUID schema：

```bash
TEST_DATABASE_URL='postgresql+asyncpg://测试用户:测试密码@127.0.0.1:测试端口/测试库' \
TEST_REDIS_URL='redis://127.0.0.1:测试端口/0' \
conda run -n repopilot python -m pytest tests/test_github_settings.py tests/test_github_registration.py -q
```

测试中的 GitHub HTTP 使用隔离 MockTransport；生产 client 默认使用真实 transport，不提供模拟 GitHub 主机配置。

历史验证记录（非本次 UI 重构结果）：Conda 环境中的 Ruff 检查和当时的前端生产构建通过；四个服务曾通过 Docker Compose 启动。曾验证 Nginx 登录、设置页、临时兼容模型端点刷新、Redis 缓存与凭据隔离、PostgreSQL 保存及 API 重启后读取；结束时恢复空模型配置、删除测试缓存并关闭临时端点，未使用第三方真实模型服务凭据。

本次 UI 重构验证：`npm --prefix web run build`（vue-tsc + Vite）通过。独立 headless Chromium 中拦截 `/api/**`，用纯测试凭据检查了 1440×900、390×844、768×900 三页和设置入口、无横向溢出、登录错误/Enter/显示隐藏/退出/刷新内存会话、skip-link、迟到登录和旧会话 401 竞态及跨源 redirect 防护。模型请求验证同端点省略 key、新端点空字符串、键盘选择 model-b、下拉点击、旧刷新丢弃、空列表和 502 禁止保存。GitHub fixture 覆盖创建/已有公钥、先保存后注册失败、503 加载重试、草稿丢弃和 1 秒保存期间关闭/分类/Escape 守卫。390×640 下内容真实滚动而标题/分类/外壳不动；分类边界实测一致、焦点限制及两个入口恢复通过，reduced-motion 可用，最终组件控制台无异常。这些 fixture 证明前端交互，不证明真实 GitHub 注册；本轮未运行后端测试。

部署验证：仅执行 `docker compose up -d --build --no-deps web`，构建与替换成功；没有重建 API、修改 `.env` 或删除数据卷。在实际映射 `http://127.0.0.1:8081` 使用内存读取的工作台令牌登录，随后仅 GET 两类设置摘要，认证与 GET 均返回 200。桌面弹窗 960×720、390×844 下 366×820，分类切换尺寸一致且无横向溢出，组件控制台无异常；没有替换用户凭据或向真实 GitHub 注册新公钥。

## 下一步

下一步按 [现行路线](development-plan/03-roadmap.md#当前执行顺序) 固定 Python、React、Vue 的实际任务基线与验收条件，然后实现并验证最小执行链。已有设置界面不替代 Agent Loop、隔离执行、流式对话或 PR 交付能力。