# 环境变量

[文档首页](../README.md) · [项目首页](../../README.md)

RepoPilot 的配置分为两层：部署连接、访问密钥和执行镜像由环境变量管理；模型端点、模型选择及 GitHub 凭据在[工作台设置](integrations.md)中保存。

## 创建配置文件

在仓库根目录执行，只在文件不存在时复制：

```bash
test -f .env || cp .env.example .env
```

以根目录的 [.env.example](../../.env.example) 为模板。不要提交 `.env`，也不要直接使用示例密码和访问密钥。

## 配置项

| 变量 | 默认值或示例 | 说明 |
| --- | --- | --- |
| `API_TOKEN` | 必须自行设置 | 工作台共享访问密钥，使用随机长字符串；不是模型 API Key |
| `POSTGRES_USER` | `repopilot` | Compose 数据库用户 |
| `POSTGRES_PASSWORD` | 必须自行设置 | Compose 数据库密码；示例值必须替换 |
| `POSTGRES_DB` | `repopilot` | Compose 数据库名 |
| `DATABASE_URL` | 示例使用 `postgresql+asyncpg://…@127.0.0.1:5432/repopilot` | 宿主 API/Worker 的数据库连接；用户、密码、端口必须与实际数据库一致 |
| `REDIS_URL` | `redis://127.0.0.1:6379/0` | 宿主 API 的模型列表缓存连接 |
| `GITHUB_CREDENTIALS_KEY` | 空 | 保存和使用 GitHub 凭据前必须设置稳定的 Fernet 主密钥 |
| `EXECUTION_IMAGE` | `repopilot-dev:local` | 服务端执行镜像标签，同时用于 Compose 的 runtime 构建；不是任务表单参数 |
| `WEB_PORT` | `8081` | Compose 前端宿主端口 |
| `API_PORT` | `8000` | Compose API 宿主端口 |
| `POSTGRES_PORT` | `5432` | Compose PostgreSQL 宿主端口 |
| `REDIS_PORT` | `6379` | Compose Redis 宿主端口 |

后四个端口均绑定宿主 `127.0.0.1`。`WEB_PORT` 和 `API_PORT` 未列在示例文件中，需要覆盖时可自行添加。

Compose 直接给 API/Worker 注入容器网络中的数据库和 Redis 地址，不使用 `.env` 中面向宿主的 `DATABASE_URL` / `REDIS_URL`。宿主运行时从仓库根目录加载 `.env`，进程环境变量优先。修改宿主端口后，应同步修改宿主连接 URL；`API_PORT` 不会改变宿主 Python 服务自身的监听配置。

数据库 URL 中的特殊字符需要 URL 编码。Compose 将 `POSTGRES_PASSWORD` 直接拼入连接 URL，建议使用足够长的随机字母数字密码，避免 URI 分隔符造成解析错误。

## 生成 GitHub 加密主密钥

已有开发环境时，在安全终端运行：

```bash
conda run -n repopilot python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'
```

仅使用 Docker 时，可先构建 API 镜像，再在不启动依赖服务的临时容器中生成：

```bash
docker compose build api
docker compose run --rm --no-deps api python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'
```

运行 Compose 命令前须已填写 `POSTGRES_PASSWORD` 和 `API_TOKEN`，以通过配置解析。把生成结果写入 `.env` 的 `GITHUB_CREDENTIALS_KEY`，然后启动服务。不要公开终端输出。

该密钥加密 GitHub token 和私钥，应独立备份，并在 API 和 Worker 间保持一致。更换密钥不会迁移已有密文，丢失原密钥后无法解密旧凭据。模型 API Key 当前不使用此加密机制，而是明文存于 PostgreSQL。

## 容器访问宿主模型服务

模型服务在宿主运行时，端点不能填写容器内的 `127.0.0.1`。Docker Desktop 通常提供 `host.docker.internal`；Linux Docker Engine 需自行配置宿主网关映射或使用容器可达的宿主地址。当前 Compose 未配置 `extra_hosts`，不能假定该域名在所有 Linux 环境自动可用。

同时检查模型服务监听地址和防火墙，不要为连通性直接向公网暴露未鉴权的模型端点。
