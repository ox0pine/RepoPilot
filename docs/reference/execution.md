# 执行环境与工具

[文档首页](../README.md) · [项目首页](../../README.md)

执行镜像提供 Python 3.12、Node 22、uv、npm/pnpm/yarn 和独立安装的 Pyright、Ruff、TypeScript/Vue、HTML/CSS/JSON 语言服务器。不同项目的依赖不会安装到 Worker 或语言服务器目录；LSP 绑定项目自己的解释器与 TypeScript SDK。

任务用户不再选择环境。`EXECUTION_IMAGE` 是服务端镜像配置，同时作为 Compose 构建的执行镜像标签，不是客户端表单字段；Worker 必须能访问该镜像，缺失则 Run 为 `blocked`。Python 可自动从 uv 管理的工具链中选择满足约束的版本；Node 可自动匹配 20/22 系列并校验官方下载。Worker 被手动停止或异常退出时，Run 仍可能保持排队；排除故障后用 `docker compose up -d worker` 启动。

## 语义工具与文件一致性

Agent 可调用 `lsp_status`、`lsp_diagnostics`、`lsp_hover`、定义/类型定义/实现/引用、文档/工作区符号、调用层级、重命名、代码操作和格式化。返回 `unsupported`、`not_ready` 或 `error` 时不视作“没有问题”。工具位置使用从 1 开始的行与 Unicode 字符列，内部转换 LSP 位置编码。

重命名、代码操作和格式化先返回不可伪造的修改计划 ID；`apply_workspace_edit` 校验文件 hash/版本、路径和修改冲突后应用，过期计划拒绝。不能通过工具提交任意 JSON-RPC 或执行任意语言服务器命令。TypeScript 导入绑定重命名可能只修改本文件别名；跨文件修改应选择真实声明。

LSP 在 Run 容器中按项目运行，查询前同步已变更文件。Shell、环境准备和成果捕获前关闭语言服务器，保留严格后台进程清理；后续语义请求重建会话，旧计划失效。LSP 安装脚本、插件或项目导入不等于可信代码，只能在沙箱中运行。模型上下文始终保留 Goal、来源及环境摘要，动态历史保留最近六个批次，不把整个语义索引发送给模型。

资源上限与隔离规则见[安全边界](security.md)。
