# RepoPilot 文档

[返回项目首页](../README.md)

## 从这里开始

- **首次部署**：[Docker 部署](operations/deployment.md) → [环境变量](configuration/environment.md) → [模型与 GitHub 设置](configuration/integrations.md)。
- **开始使用**：[任务、方案、执行与交付](guide/workflow.md)。
- **参与开发**：[本地开发与贡献](development/contributing.md) → [项目结构与架构](development/architecture.md)。

## 文档目录

| 分类 | 文档 | 内容 |
| --- | --- | --- |
| 配置 `configuration/` | [环境变量](configuration/environment.md) | 服务端配置、端口、主密钥与宿主连接 |
| 配置 `configuration/` | [外部服务](configuration/integrations.md) | 模型端点、GitHub token 权限与 SSH 授权 |
| 使用 `guide/` | [工作流程](guide/workflow.md) | 创建任务、批准执行、审阅和修复分支交付 |
| 运维 `operations/` | [部署](operations/deployment.md) | Docker 启动与服务职责 |
| 运维 `operations/` | [维护与排障](operations/maintenance.md) | 停止、Worker 恢复、数据保护和常见问题 |
| 开发 `development/` | [开发与贡献](development/contributing.md) | Conda/npm 环境、检查命令与贡献约定 |
| 开发 `development/` | [架构](development/architecture.md) | 代码目录、组件职责和数据流 |
| 参考 `reference/` | [执行环境与工具](reference/execution.md) | 自动环境、语言服务器和文件一致性 |
| 参考 `reference/` | [安全与限制](reference/security.md) | 凭据边界、容器隔离与资源上限 |
| 参考 `reference/` | [API](reference/api.md) | 业务接口与并发、错误语义 |

本文档描述当前实现；[更新日志](../CHANGELOG.md)保留历史变更和当时的验证结果，不代表当前自动通过。
