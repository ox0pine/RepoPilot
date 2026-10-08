# 项目结构与架构

[文档首页](../README.md) · [项目首页](../../README.md)

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
docs/                 # 配置、使用、运维与开发文档
```

后端沿用 FastAPI → 应用服务 → 领域契约与基础设施分层，`api/app.py:create_app` 是组合根。前端使用 composition API、轻量自定义路由和模块级 reactive stores，不引入 Vue Router 或 Pinia。`reference/` 是忽略的参考材料，不作为应用导入来源。

PostgreSQL 是业务数据的权威存储；Redis 仅缓存模型列表。API 创建排队记录，单个 Worker 在数据库事务外执行 Docker 任务。每次执行从固定 commit 开始，批准、执行、修复分支交付是分开的操作。
