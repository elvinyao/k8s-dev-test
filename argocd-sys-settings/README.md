# 系统 GitOps 设置

生产平台入口已扩展到 `scripts/generate-gitops.py`：从 `platform/releases.yaml` 生成固定
版本、多源 values 的 Application，另包含网络和 ELK 数据面入口。部署顺序与凭据要求见
`docs/production.md`。生成文件默认放在被忽略的 `.local/gitops/`，不自动应用/同步。
下面两个 `.yaml.example` 仍是用于理解 namespace 自举的最小示例，不能代替生产平台 Project。

这里保存平台管理员负责的 Argo CD 模板，目前没有启用任何 Application。
`project.yaml.example` 定义 `platform-sys`；`namespaces.yaml.example` 引用它，
从本仓库 `platform/namespaces` 读取命名空间清单。

当前项目只允许管理集群级 `Namespace`，禁止所有命名空间级资源。
`destinations.namespace: '*'` 服务于平台命名空间初始化，不代表允许部署任意资源。
管理员后续应按组件建立独立项目和明确的资源清单，逐步接入存储、Argo CD、
Prometheus、ELK、GitLab；不要把该项目直接扩展为全资源通配授权。

## 启用前提与顺序

1. 建立本地集群并安装已锁定版本的 Argo CD，先准备 `argocd` 命名空间和 CRD。
2. 将所有示例中的仓库地址替换为真实 URL，确认 `main` 分支、源路径和仓库凭据。
3. 审查源路径中的清单；将要启用的 `.yaml.example` 复制为 `.yaml`。
   Argo CD 常规目录扫描不会把 `.yaml.example` 当作清单；源目录里的示例也需先启用。
4. 管理员先创建 AppProject，再创建 Application；查看 diff 后手动同步命名空间。
5. 命名空间及存储准备完成后，再启用业务项目与应用。

上述操作需要后续带 Kubernetes 工具及明确集群凭据的 Docker runner。
当前初始化 runner 不提供集群连接，也不会执行上述操作。
模板没有自动同步、自动 prune 或级联删除 finalizer；人工执行 prune 或删除
Namespace 仍会销毁资源，必须独立审查。

## 管理边界

只有平台管理员应能修改 AppProject、系统 Application、Argo CD RBAC 与仓库凭据。
初始化阶段不授予业务用户此项目或 `argocd` 命名空间写权限；业务权限必须限定到
`platform-apps`，不能沿用全局管理员权限或宽松的 `default` 项目。

`sourceRepos` 只限制仓库 URL，不限制仓库内部路径。两个目录只是代码组织，
不构成安全隔离；拥有整个仓库写权限的人仍可修改系统配置。
生产阶段计划拆成平台配置库、业务部署配置库和应用源码库，分别配置写权限、
受保护分支、强制审查与 Argo CD 项目权限。部署仓库凭据优先只读。

权限字段参考 [Argo CD Projects 官方文档](https://argo-cd.readthedocs.io/en/stable/user-guide/projects/)。
