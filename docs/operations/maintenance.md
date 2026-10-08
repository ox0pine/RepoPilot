# 日常运维与故障排查

[文档首页](../README.md) · [项目首页](../../README.md)

普通停止保留数据卷：

```bash
docker compose down
```

**不要使用 `down -v`，除非明确要删除数据。** 删除 PostgreSQL 卷会清空模型/GitHub 设置、对话、Goal、Run 和日志，重建后需重新配置。

当前只维护现行存储与协议格式，没有旧数据库升级、字段补齐或旧密钥迁移逻辑。来源快照必须含源码上下文；GitHub 密钥只支持完整、匹配的 Ed25519/OpenSSH 格式。普通重启不是清库，不能将“只支持当前格式”理解为可以自动删除数据。

Worker 使用 PostgreSQL session advisory lock 保证单实例，按 FIFO 顺序领取任务。每两秒检查锁连接、每秒检查取消请求；第二个 Worker 会退出。Worker 不配置自动重启，异常停止后应先排查，再显式启动。不要同时运行容器 Worker 和宿主 Worker。

Redis 只缓存模型列表：运行中缓存读取失败会请求提供方，写入失败不丢弃已获取列表；提供方失败不会回退到过期结果。API 启动时仍要求 Redis 可连接。

## 查看服务状态

```bash
docker compose ps -a
docker compose logs --tail=100 api worker
```

`runtime` 是一次性镜像检查服务，退出码 0 是正常结果。Worker 异常退出后先查看日志、排除问题，再显式启动：

```bash
docker compose up -d worker
```

不要为了让队列继续执行而重复提交 Run。Worker 重启会清理遗留容器并标记中断，不重放结果未知的工具操作。

## 常见问题

| 现象 | 检查方向 |
| --- | --- |
| API 无法启动 | 核对访问密钥及 PostgreSQL、Redis 连通性；两者都是启动依赖 |
| Run 一直排队 | 检查 Worker 是否启动、是否被另一实例占用单实例锁 |
| 执行镜像不可用 | 核对 `EXECUTION_IMAGE`，按部署步骤重新构建运行镜像 |
| 模型端点在宿主机，容器访问失败 | 不要使用容器内的 `127.0.0.1`；检查宿主地址解析与监听地址 |
| GitHub clone 失败 | 已保存私钥时使用 SSH，不自动回退 HTTPS；检查公钥授权与仓库权限 |
| 无法解密 GitHub 凭据 | 确认使用原有 `GITHUB_CREDENTIALS_KEY`；新密钥不能解密旧数据 |
| 收到 409 或提交结果未知 | 重新读取当前状态并人工确认，不盲目重发写请求 |
| 环境准备或检查受阻 | 查看实际命令、退出码和日志，检查锁文件、版本约束及检查入口 |

## 数据保护

备份应包含 PostgreSQL 业务数据和独立保管的 GitHub 加密主密钥；模型 API Key 当前以明文存于数据库，备份同样需要访问控制。不要把 `.env`、token、私钥或包含凭据的日志附到公开 Issue。

当前没有旧数据库自动升级流程。更新前应核对[更新日志](../../CHANGELOG.md)，备份数据并确认格式兼容性，不以删除数据卷代替升级。
