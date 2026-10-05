# 业务 GitOps 设置

这里保存业务 AppProject 和 Application 模板，不会自动接入真实业务仓库。
`project.yaml.example` 定义 `platform-apps`，`storage-demo.yaml.example`
引用该项目并读取 `apps/storage-demo`，目标仅为本地集群的 `apps-dev`。

允许的资源种类在项目中显式列出。业务可以申请 PVC，不能创建 PV、Namespace、
StorageClass、CRD、ClusterRole 等任何集群级资源，也不能部署到 `argocd`。
Role、RoleBinding、ServiceAccount 等未列出的命名空间级资源同样不允许部署。
需要新增种类时，先由平台管理员审查项目权限。

AppProject 和 Application 对象自身存放在 `argocd`，应由管理员维护；
这与业务清单只能部署到项目对应 namespace 是两个不同的权限层次。
项目限制不能替代 Kubernetes RBAC、Pod Security、网络策略和资源配额，
也不能约束绕过 Argo CD 直接访问 Kubernetes 的账号。

## 本地存储示例

1. 按 `../argocd-sys-settings/README.md` 完成 Argo CD 与命名空间初始化。
2. 准备存储后端以及匹配示例 PVC 的 PV 或动态供应配置。
3. 替换仓库 URL，确认 `main` 分支和 `apps/storage-demo` 中要启用的清单。
4. 将审核过的 `.yaml.example` 复制为 `.yaml`，先创建项目，再创建应用。
5. 查看 diff 后手动同步；示例包含 PVC 与非 root 消费 Pod，按照
   `apps/storage-demo/README.md` 验收绑定与 Pod 重建后的数据读写。

所有项目命令继续通过 Docker runner，用 `--kubeconfig` 显式提供集群配置；kind 的内部
kubeconfig 还需要 `--network kind`，见[工具链](../docs/tooling.md)和[本地集群](../docs/local-cluster.md)。
模板省略自动同步、自动 prune 和级联删除 finalizer，不自动创建命名空间。
手工 prune、删除 PVC 或卸载存储组件仍可能丢失数据，应先验证备份和回收策略。

生产业务模板已在[隔离 GitOps 运行验收](../docs/gitops-validation.md)中由真实 Argo CD
同步过测试 Git commit，并验证漂移修复及 Secret/ClusterRole 拒绝。测试集群已清理，
这不代表下面所需的真实仓库凭据、业务账号和生产环境已经配置完成。

## 生产业务示例

`project-production.yaml.example` 定义 `platform-apps-production`，仅允许部署到 `apps-prod`。
`production-demo.yaml.example` 对应[生产业务接入探针](../apps/production-demo/README.md)，
包含受 Restricted 策略约束的两副本 Deployment、ClusterIP Service、PDB 和 ConfigMap。
与本地裸 Pod/PVC 示例分开，生产 Project 不放宽对 Pod、RBAC 和集群资源的限制。

管理员按以下顺序准备：

1. 将 `apps/production-demo` 复制到独立业务部署仓库的同一路径，审核、提交并推送。
2. 用编辑器在忽略提交的 `.local/gitops/` 保存配置好的 `business-project.yaml` 和
   `business-application.yaml`，分别来自两个 production 模板。
   替换 Project `sourceRepos` 与 Application `repoURL` 为同一个实际业务仓库地址。
3. 将 Application `targetRevision` 的 **40 个零**替换为实际审核过的完整 commit SHA。
   全零是明确的不存在提交占位符，不是已验证 revision。确认该提交确实包含本目录清单，
   并配置 Argo CD 的只读仓库凭据。校验器不会连接远端或证明该提交存在。
4. 平台管理员先创建 `apps-prod` 及资源/网络策略，再执行离线合同检查，最后创建 Project
   和 Application。示例不会自动创建 namespace、自动 sync/prune 或级联删除资源。

```sh
bash .agent/run.sh --toolbox python apps/production-demo/check.py --project .local/gitops/business-project.yaml --application .local/gitops/business-application.yaml
bash .agent/run.sh --kubeconfig /srv/platform/admin.kubeconfig kubectl --context production apply -f .local/gitops/business-project.yaml
bash .agent/run.sh --kubeconfig /srv/platform/admin.kubeconfig kubectl --context production apply -f .local/gitops/business-application.yaml
```

首次查看未修改模板的离线效果时，可以运行 `check.py --example`；不带 `--example` 会拒绝
示例仓库/全零 SHA。检查只针对当前工作区渲染，需要自行核对与审核提交一致。随后按业务
示例手册手动同步并验收 Service 请求、节点分布和实际业务账号权限。

## 权限边界

`sourceRepos` 允许的是整个仓库，不能限制到 `apps/` 或本目录。
目录分开、目录约定以及 CODEOWNERS 文件本身均不等于安全隔离。
正式生产应拆分业务部署库与平台配置库，配合仓库权限、受保护分支、
强制审查及 Argo CD RBAC；业务账号不得切换到系统或宽松的 `default` 项目。
不要提交真实 Secret 明文、访问令牌、证书私钥或 kubeconfig。

权限字段参考 [Argo CD Projects 官方文档](https://argo-cd.readthedocs.io/en/stable/user-guide/projects/)。
