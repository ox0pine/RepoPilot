# RepoPilot

## 项目现状

RepoPilot 正在从零重构。旧项目在目标和范围尚未明确时推进了过多功能，后续修改逐渐混杂，因此本仓库不继续沿用旧架构，而是重新确定目标、划分边界，再逐步实现。

旧项目资料已移动到本仓库的 `reference/` 目录，并通过 `.gitignore` 忽略。该目录只用于查阅和对比，不会自动并入新项目。

当前已有首页、访问密钥登录、对话列表与统计、模型/GitHub 设置，以及真实 Issue 来源驱动的 Goal 生成、反馈修改和人工批准。前端沿用 Vue / TypeScript + Naive UI + Lucide（`@lucide/vue`）浅色工作台。批准只确认目标：代码执行、源码检索、SSE、成果审阅和 PR 交付尚未接入，不创建 Run 或启动执行进程。

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
- [x] 建立 PostgreSQL 对话、不可变 Goal 版本及消息历史，支持真实来源、模型生成、修改、批准和租期恢复。

`reference/` 仅用于保存旧仓库中的参考项目；Python/uv 环境配置已经迁移到当前项目根目录，作为新项目当前采用的开发环境基础。

### 尚未完成

- [ ] 固定 Python、React、Vue 各类真实任务及独立验收基线。
- [ ] 实现并验证单一 Agent Loop、隔离工作区、Tool Executor 和首个 CLI 执行闭环。
- [ ] 按现行路线接入持久化恢复、SSE 多轮对话和受控 PR 交付。

### 当前环境状态

- `pyproject.toml`、`uv.lock`、`environment.yml`、`requirements-dev.in` 和 `requirements-dev.txt` 当前直接位于项目根目录。
- 当前采用 Python `>=3.12`，Conda 配置固定 Python `3.12.14`。
- 历史环境检查中 `uv lock --check` 已通过；该记录只证明当时依赖声明与锁文件一致，不证明任务执行能力完成。
- 已实现认证、设置与 Goal 审批闭环；任务执行链尚未实现。

### Docker 全栈运行

`compose.yaml` 包含 PostgreSQL 16、Redis 7、FastAPI 和 Nginx 前端。PostgreSQL 保存设置、对话、Goal 版本与消息，Redis 仅缓存模型列表（300 秒），两个数据库均使用命名数据卷。

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
├── application/      # 设置与对话/Goal 用例编排
├── domain/           # 设置、来源、Goal 与状态契约
├── persistence/      # PostgreSQL 设置、对话、版本与消息
└── integration/      # GitHub 只读来源、Chat Completions 与 Redis 缓存
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

工作台访问密钥由 `.env` 中的 `API_TOKEN` 控制。登录会话仅在内存保存，刷新后需要重新登录，并恢复合法的 `/app`、`/app/new` 或 `/app/tasks/{UUID}` 深链接。概览提供真实数据库统计，侧栏支持最近对话和加载更多；错误明确显示，不以零值或空列表掩盖读取失败。

首页保留产品介绍、工作台入口与规划流程，不再展示「从配置开始」的模型服务/GitHub 授权介绍卡；实际配置仍在登录后的设置窗口中进行。

登录后，桌面左下角「设置」或准备卡「打开设置」均打开同一个窗口；宽度不超过 768px 时采用品牌顶栏及菜单抽屉。窗口提供「模型服务」与「GitHub」，小屏分类横排。桌面尺寸上限 1000×780px、视口留边 48px；不超过 640px 时留边 24px。内容独立滚动，Tab 限制在弹窗内，关闭后焦点返回入口。模型保存、GitHub 保存或 SSH 授权期间禁止关闭、Escape 和切换分类。

模型设置按「连接信息 → 选择模型 → 保存」组织。已保存 API Key 显示状态摘要而不是虚假密码框，点击「更换」进入编辑，可取消并恢复已保存的模型。仅允许保存列表中的模型；空列表或刷新失败不能保存，旧端点迟到响应不会覆盖新端点。同一端点留空省略 `api_key` 保留旧值，更换端点留空发送空字符串，不复用旧密钥。

前端按 `views/`、`components/`、`api/`、`router/`、`stores/` 和 `styles/` 分工。`App.vue` 保持页面与认证竞态协调，根部 `NConfigProvider` 使用中文 locale 与统一 `themeOverrides`；`styles/theme.ts` 的同一 palette 导出 CSS 变量，`main.ts` 在 mount 前写入 document 根，使 body 与 Teleport 弹窗共享颜色。`tokens.css` 仅保留非颜色变量。组件与图标显式导入，无第二套组件库、自动导入插件或主题切换。未来流式分层见 [核心流程](development-plan/02-core-flow.md)，本轮未安装 Markdown/流式依赖或创建空聊天模块。

模型配置存放在 PostgreSQL 的 `model_settings` 表，启动时创建初始表和默认行。旧 `.repopilot/settings.json` 已删除，不再读写。API Key 在数据库中为明文，不会在 HTTP 响应中回显；请保护数据库凭据和备份。切换端点不会复用旧密钥。访问令牌仅保留在浏览器内存中，刷新后需要重新登录。

Redis 模型列表缓存按端点与密钥的哈希隔离，最长保留 300 秒；不存储密钥原文。数据库或 Redis 无法连接时，API 启动失败，不回退到 JSON 文件。只有通过访问密钥认证的成员才能修改设置或查询模型列表。

### GitHub 仓库访问与可选 SSH 授权

「GitHub」先配置仓库访问 token。私有仓库推荐 fine-grained token：选择目标仓库，并授予 `Contents: Read-only`、`Issues: Read-only`；组织可能要求管理员批准。Classic token 的 `repo` scope 包含广泛写权限，优先选择最小权限方案。「保存 token」只做本地加密持久化，不请求 GitHub、不注册公钥，也不表示仓库权限已经验证；真实访问权限在创建对话时校验。已保存凭据只显示摘要，点击「更换 token」后必须填写非空新值，支持取消。保存结果不明时保留草稿，先明确重新读取，不自动重发。

凭据属于整个工作台，并非每用户 OAuth 登录。首次保存仍在本地生成 Ed25519 SSH 公私钥对；已有完整密钥对复用，包括旧版 RSA/ECDSA 密钥。token 和私钥加密保存，私钥不上传。关闭窗口或切换分类会丢弃未保存草稿。

部署时注入稳定的 `GITHUB_CREDENTIALS_KEY`，由部署者在安全终端生成，并与数据库备份独立保管：

```bash
conda run -n repopilot python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'
```

不要提交生成结果。私钥和 token 使用该 Fernet 主密钥分别加密后存入 PostgreSQL `github_settings` 表，读取接口只返回公钥及两个配置状态。未配置有效主密钥时 GitHub 接口返回 503，既有登录和模型设置仍可用；密钥不匹配现有密文时返回错误，不覆盖原数据。恢复时必须恢复同一主密钥及数据库备份，不自动生成替代密钥。模型 API Key 的原有存储方式不变。

生成 Goal 不需要 SSH 授权。「高级 · SSH 公钥（可选）」提供查看、复制公钥及单独授权。只有明确点击授权，才使用已保存 token 向固定 `https://api.github.com` 查询并注册现有公钥，不修改本地凭据。该操作额外需要 fine-grained PAT 的 `Git SSH keys: Read and write`，或 classic PAT 的 `read:public_key` + `write:public_key`；失败不影响已保存 token。存在未保存草稿时不能授权，避免误用旧凭据。配置状态不是持久授权状态，可在 <https://github.com/settings/keys> 核对。

接口：`GET /api/settings/github` 返回配置摘要；`PUT /api/settings/github` 接受 token 并直接返回配置摘要；`POST /api/settings/github/authorize` 无需请求体，返回配置及公钥注册结果。均要求工作台 Bearer。旧的「保存并自动授权」返回结构与错误前缀已移除，调用方应使用独立操作。

设置重构验证：245 项后端测试通过，覆盖保存零外部请求、授权不修改凭据、缺配置与上游失败；桌面/手机浏览器验证了已保存摘要、编辑取消与焦点、首次保存、保存与授权独立调用、结果不明保留草稿、忙碌关闭守卫和无横向溢出。交互写入使用隔离 fixture，未更改用户凭据或向真实 GitHub 注册公钥。

### 对话与 Goal 审批

新建对话填写 GitHub HTTPS 仓库链接、完整 40 位 commit SHA 和同仓库 Issue 链接。目前不接受 SSH URL、PR、其他托管平台、链接凭据或 query/fragment。私有仓库需要 token 的 Contents/Issues 读取权限；SSH 公钥注册成功不代表已经有来源读取权限。创建仅 GET 固定 GitHub API，验证 commit/Issue 后保存快照，不再注册 key，不修改远端。

Goal 包含目标、修改范围、不包含、验收标准、建议执行计划、待确认事项。它是基于 Issue 快照的草案，不表示已经检索源码、修改代码或跑过测试。修改始终使用相同快照、上一版本与本次反馈；历史版本及批准记录保留。修改会立即撤销旧批准，失败也不会恢复。存在待确认事项仍可明确批准，但不代表执行前置条件已满足。

「提出修改」默认收起，点击展开并聚焦输入框，再次点击「收起修改」隐藏；收起保留未提交草稿，切换对话或登录会话才清空。展开/收起本身不会发起生成，也不会撤销批准。生成中使用轻量草案卡与简短说明，不显示虚假进度或阶段；减少动态效果偏好下关闭加载动画。桌面与手机已验证草稿保留、键盘展开、对话切换隔离和无横向溢出，前端生产构建通过。

状态为 `draft → generating → awaiting_approval → approved`；生成失败为 `generation_failed`，仅手动重试。非流式 Chat Completions 总时限 60 秒，响应上限 256 KiB；来源读取最多 35 秒，Issue 正文超过 64 KiB 明确拒绝。90 秒数据库租期在读取或写入时恢复中断生成，无后台队列或自动重试。Nginx `/api/` 读取超时为 120 秒。

所有 `/api/tasks` 接口要求工作台 Bearer：`POST /api/tasks` 创建；`GET /api/tasks` 游标分页；`GET /api/tasks/stats` 聚合统计；`GET /api/tasks/{id}` 详情；`POST /api/tasks/{id}/goal` 携带 `expected_revision`、`action`（generate/revise/retry）及修改反馈；`POST /api/tasks/{id}/approve` 携带 `expected_revision`、`goal_version`。冲突返回 409，重新读取后人工确认，不静默覆盖。供应商认证失败返回安全 502，不触发本地会话退出。

新增表沿用同一个 Base 和 `create_all`，只加表，不修改旧表。对话共享工作台访问权限，不是每用户隔离。列表使用 `(updated_at,id)` 倒序 keyset；活动记录可能前移，前端按 id 去重，刷新重建首屏与游标，并非快照分页。

后端回归验证使用专用 PostgreSQL/Redis，不得指向现有工作台数据库；每个数据库测试创建并清理独立 UUID schema：

```bash
TEST_DATABASE_URL='postgresql+asyncpg://测试用户:测试密码@127.0.0.1:测试端口/测试库' \
TEST_REDIS_URL='redis://127.0.0.1:测试端口/0' \
conda run -n repopilot python -m pytest tests/test_tasks.py tests/test_goal_generation.py tests/test_task_sources.py tests/test_github_settings.py tests/test_github_registration.py -q
```

测试中的 GitHub/模型 HTTP 使用注入的隔离 MockTransport；生产路径默认真实网络，不提供模拟来源或固定 Goal。缺测试数据库而 skip 不算通过；测试必须使用独立实例及 UUID schema。

历史验证记录（非本次 UI 重构结果）：Conda 环境中的 Ruff 检查和当时的前端生产构建通过；四个服务曾通过 Docker Compose 启动。曾验证 Nginx 登录、设置页、临时兼容模型端点刷新、Redis 缓存与凭据隔离、PostgreSQL 保存及 API 重启后读取；结束时恢复空模型配置、删除测试缓存并关闭临时端点，未使用第三方真实模型服务凭据。

历史 UI 重构验证：当时 `npm --prefix web run build` 通过；headless Chromium fixture 覆盖多尺寸设置表单、认证竞态、焦点限制及模型列表交互。该记录不代表真实模型生成或 GitHub 注册。

历史部署验证：曾仅重建 web 容器并验证真实登录及只读设置查询；不代表本次 Goal 后端已部署到用户运行中的服务。

Goal 审批闭环验收：独立 `repopilot-goal-test` PostgreSQL/Redis（55432/56379）及 UUID schema 上，240 项后端测试通过，无跳过；前端 `vue-tsc + vite build` 通过。已有 SSH DSA 弃用警告与 Vite 单 chunk 超过 500 kB 提示保留，未隐藏。

真实来源使用用户指定的私有仓库 `https://github.com/qfpqhyl/repopilot-e2e-private`、commit `2e3384843902b2ee5a46f96863032788704db176`、Issue `https://github.com/qfpqhyl/repopilot-e2e-private/issues/1`。现有 GitHub/模型设置只读复制至独立 smoke schema，未修改用户设置。真实服务及 1440×900 / 390×844 浏览器均完成创建、生成 v1、反馈“只修复该 Issue，不做依赖升级；验收包含问题复现”、生成 v2、批准及明确未执行提示；刷新重新登录恢复深链接，独立 API 重启后版本与批准仍在。未运行目标仓库代码、注册 SSH key 或修改远端 Issue。

补充隔离 fixture 验证了 14 条列表分页与长标题、过期批准 409 保留草稿、读取失败保留历史、生成中切页停止轮询并丢弃迟到响应、阅读上文不强制拉底、统计/列表错误、旧会话及当前会话 401、移动抽屉和设置焦点返回。真实不存在 commit 与不可连接模型端点也分别显示来源/生成错误；所有检查页面无横向溢出，控制台无组件异常。桌面/手机概览、新建、待批准和已批准截图保留在本地 `tmp/goal-acceptance/`；fixture 仅证明交互，不代替上述真实模型链路。

验收结束后关闭浏览器标签及独立 API/Vite/PostgreSQL/Redis 服务，删除 smoke schema 中复制的凭据和临时验证脚本；独立测试卷保留，用户原有服务与数据卷未动。模型继续使用系统已保存设置，没有新增 `.env` 模型凭据覆盖项；本次未重启或替换用户正在运行的部署。

## 下一步

下一步按 [现行路线](development-plan/03-roadmap.md#当前执行顺序) 固定 Python、React、Vue 的实际任务基线与验收条件，然后实现并验证最小执行链。已有设置界面不替代 Agent Loop、隔离执行、流式对话或 PR 交付能力。