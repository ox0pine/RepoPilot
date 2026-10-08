# 模型与 GitHub 设置

[文档首页](../README.md) · [项目首页](../../README.md)

这些设置在 Web 工作台中保存，所有持有访问密钥的成员共享。部署前先配置[环境变量](environment.md)。

1. 在模型设置中填写 Chat Completions 端点和 API Key，刷新模型列表、选择模型并保存。支持无需 API Key 的端点。
2. 同一端点省略 API Key 时保留已保存密钥；更换端点不会复用旧密钥。模型列表按端点与密钥隔离，在 Redis 中缓存 300 秒。
3. 保存 GitHub token。私有仓库建议使用限定仓库的 fine-grained token：来源与执行需要 `Contents: Read-only` 和 `Issues: Read-only`；显式提交修复分支还需要 `Contents: Read and write`。组织可能另需批准。

保存 GitHub token 只做本地加密持久化，不发起 GitHub 写入，也不等于已经验证仓库权限。来源访问在创建对话时校验；只有用户在已完成 Run 上显式点击提交修复分支才会写入仓库。

Run 使用真实 Git clone 并检出固定 commit，不再下载 GitHub 源码归档。已保存 SSH 私钥时，Worker 使用该私钥并严格校验 GitHub 主机密钥；此时公钥必须已授权到可访问仓库的 GitHub 账号。没有私钥时使用已保存 API token 进行 HTTPS clone，不自动在两种认证方式之间回退。公钥注册仍是独立、显式的操作；保存凭据不会自动注册。Issue/上下文读取和修复分支推送使用 API token。

克隆工作区保留 `.git`，默认 Git 身份为 `RepoPilot <repopilot@users.noreply.github.com>`。凭据只在可信控制面临时使用，不写入仓库 URL/配置，也不进入执行容器；Patch 排除 `.git`。当前拒绝包含 symlink 或 submodule 的源码树，不会把它们静默转换为普通文件或空目录。
