# API 概览

[文档首页](../README.md) · [项目首页](../../README.md)

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
| `POST /api/tasks/{id}/runs` | 显式排队，返回 202；仅提交 `expected_revision`、`goal_version`，环境与检查由系统选择 |
| `GET /api/tasks/{id}/runs`、`GET /api/tasks/{id}/runs/{run_id}` | 最近 50 条 Run 与单次详情 |
| `POST /api/tasks/{id}/runs/{run_id}/cancel` | 请求取消 |
| `POST /api/tasks/{id}/runs/{run_id}/delivery` | 显式交付已完成的非空 Patch，创建或核对唯一 Commit 与修复分支，返回分支及 compare 链接；空 JSON 请求体，不自动创建 PR 或合并 |

409 冲突需重新读取并人工确认。模型服务认证失败返回安全 502，不作为工作台 401 清除登录会话。`GET /api/health` 用于服务健康检查。

“批准并执行”是前端依次调用批准和创建 Run，并非单个原子 API；批准接口本身不会创建 Run。结果未知时先读取状态，不自动重复 POST。
