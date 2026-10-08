# Docker 部署

[文档首页](../README.md) · [项目首页](../../README.md)

## 前置条件

- Docker Engine 或 Docker Desktop，以及 Docker Compose。
- 可访问镜像源、Python/Node 依赖源、GitHub 和模型服务的网络。
- 可信宿主机：Worker 需要挂载 Docker socket，具有高权限。

以下命令均在仓库根目录运行。Docker 部署不要求宿主安装 Conda 或 Node。

## 配置与启动

1. 复制环境模板，不覆盖现有配置：

   ```bash
   test -f .env || cp .env.example .env
   ```

2. 按[环境变量说明](../configuration/environment.md)设置 `POSTGRES_PASSWORD`、随机 `API_TOKEN`，并生成、备份 `GITHUB_CREDENTIALS_KEY`。没有此加密主密钥不能正常保存和使用 GitHub 凭据。
3. 构建并启动：

   ```bash
   docker compose up -d --build --wait
   docker compose ps -a
   ```

4. 访问 **http://127.0.0.1:8081/**，使用 `API_TOKEN` 登录，再完成[模型与 GitHub 设置](../configuration/integrations.md)。

首次构建包含 Python、Node 和语言服务器依赖下载，耗时取决于网络。Docker 守护进程的代理不会自动代理构建步骤中的依赖下载。

## 服务组成

| 服务 | 职责 | 默认宿主端口 |
| --- | --- | --- |
| `web` | Nginx 前端及 `/api` 反向代理 | `8081` |
| `api` | FastAPI、鉴权与业务接口 | `8000` |
| `postgres` | 设置、任务、方案、执行记录及成果 | `5432` |
| `redis` | 模型列表缓存，TTL 300 秒 | `6379` |
| `worker` | 单实例、顺序领取并执行 Run | 无 |
| `runtime` | 构建执行镜像并检查 Python 后退出 | 无 |

所有宿主端口默认绑定 `127.0.0.1`。API 启动要求 PostgreSQL 和 Redis 均可连接；Redis 使用独立数据卷与 AOF，但不是执行队列或业务数据权威存储。

`runtime` 退出码为 0 是正常状态；Worker 等待 API 健康、runtime 成功退出后启动。不需要额外 profile 或手动构建执行镜像。只有 Worker 挂载 Docker socket，API 与 Run 容器不挂载。

## 部署注意事项

- Worker 启动后自动处理已排队的 Run，无需重复提交；不要同时启动宿主 Worker。
- `EXECUTION_IMAGE` 必须与 Worker 可访问的镜像一致。语言、依赖和检查命令由系统识别，不由任务用户填写。
- 工作台采用共享访问密钥和共享数据，不提供多租户隔离。当前默认配置面向可信本地环境，不应直接作为公网部署配置。
- 容器访问宿主模型服务的地址要求见[环境变量说明](../configuration/environment.md)。
- 停止、备份及故障排查见[日常运维](maintenance.md)；不要使用 `docker compose down -v` 停止日常服务。
