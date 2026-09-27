# 业务 GitOps 设置

这里保存业务 AppProject 和 Application 模板，目前没有启用部署。
`project.yaml.example` 定义 `platform-apps`，`storage-demo.yaml.example`
引用该项目并读取 `apps/storage-demo`，目标仅为本地集群的 `apps-dev`。

允许的资源种类在项目中显式列出。业务可以申请 PVC，不能创建 PV、Namespace、
StorageClass、CRD、ClusterRole 等任何集群级资源，也不能部署到 `argocd`。
Role、RoleBinding、ServiceAccount 等未列出的命名空间级资源同样不允许部署。
需要新增种类时，先由平台管理员审查项目权限。

AppProject 和 Application 对象自身存放在 `argocd`，应由管理员维护；
这与业务清单只能部署到 `apps-dev` 是两个不同的权限层次。
项目限制不能替代 Kubernetes RBAC、Pod Security、网络策略和资源配额，
也不能约束绕过 Argo CD 直接访问 Kubernetes 的账号。

## 后续启用

1. 按 `../argocd-sys-settings/README.md` 完成 Argo CD 与命名空间初始化。
2. 准备存储后端以及匹配示例 PVC 的 PV 或动态供应配置。
3. 替换仓库 URL，确认 `main` 分支和 `apps/storage-demo` 中要启用的清单。
4. 将审核过的 `.yaml.example` 复制为 `.yaml`，先创建项目，再创建应用。
5. 查看 diff 后手动同步；示例包含 PVC 与非 root 消费 Pod，按照
   `apps/storage-demo/README.md` 验收绑定与 Pod 重建后的数据读写。

所有项目命令继续通过 Docker runner；初始化 runner 尚不具备集群连接能力。
模板省略自动同步、自动 prune 和级联删除 finalizer，不自动创建命名空间。
手工 prune、删除 PVC 或卸载存储组件仍可能丢失数据，应先验证备份和回收策略。

`sourceRepos` 允许的是整个仓库，不能限制到 `apps/` 或本目录。
目录分开、目录约定以及 CODEOWNERS 文件本身均不等于安全隔离。
正式生产应拆分业务部署库与平台配置库，配合仓库权限、受保护分支、
强制审查及 Argo CD RBAC；业务账号不得切换到系统或宽松的 `default` 项目。
不要提交真实 Secret 明文、访问令牌、证书私钥或 kubeconfig。

权限字段参考 [Argo CD Projects 官方文档](https://argo-cd.readthedocs.io/en/stable/user-guide/projects/)。
