# Argo CD 实际同步验收

离线渲染能检查清单和显式权限合同，不能证明 Argo CD 会获取 Git、创建资源或拒绝越权同步。
`scripts/smoke-gitops.py` 在独立 kind 集群内使用真实 Argo CD 控制器验证这条交付链路。
它不连接用户现有 GitLab，不需要外部仓库凭据，也不接管已有集群。

## 执行

先按[工具链](tooling.md)构建工具箱，确认 Docker 有本地最小基线所需资源。
不要与 ELK/GitLab 等大型运行检查同时执行。命令从仓库根目录运行：

```sh
bash .agent/run.sh --docker --toolbox python scripts/smoke-gitops.py
```

脚本创建随机 `platform-gitops-*` 三节点集群，并通过同一本地入口安装固定版本的 Argo CD。
测试用的只读 Git HTTP 服务、namespace 和业务对象都位于该临时集群内；Git 服务没有宿主
发布端口。测试结束后删除自己完整创建且节点身份仍匹配的集群及 kubeconfig，保留报告和日志。
创建未完成或归属变化时保留现场，由操作者检查，不能通过清理其他集群来恢复。

该 Git 服务只承载公开测试文件，使用集群内部 HTTP，不能照搬为生产 Git 仓库方案。
生产仍需独立可用的 Git 服务、受验证的 TLS、只读凭据和仓库权限。

## 验收步骤

1. 复制当前业务示例到全新的临时 Git 仓库，生成真实提交，记录完整 commit SHA。
   不复制本仓库 `.git`、历史、remote 或凭据。
2. 管理员预建带 Restricted Pod Security 标签的 `apps-prod`，应用配额和网络策略示例。
   Project 的资源白名单/黑名单保持生产模板原样，仅把允许仓库改为测试仓库。
3. 创建指向该 SHA 的 Application，要求 Argo CD 自己拉取和渲染 Git，手工同步到
   `Synced` / `Healthy`。不会用 `kubectl apply` 预先创建业务 Deployment/Service。
4. 检查四个业务资源均已同步；两副本 Ready 且位于不同节点，通过 ClusterIP Service
   请求获得与 Git 中 `index.html` 完全相同的内容。
5. 管理员将业务 Deployment 缩为一副本，确认 Application 为 `OutOfSync`；再次手工
   同步同一 SHA，要求恢复两副本。没有启用自动同步或自动 prune。
6. 用同一个 Git commit 的两个独立目录分别请求创建 Secret 和 ClusterRole。两个负例
   Application 沿用原 Project，必须出现对应对象的具体 Project 拒绝信息，并确认对象不存在。

每次同步使用独立 UUID 写入 `operation.info`，只接受同 UUID、同 SHA 的操作结果，避免
把上一次 `Succeeded` 当作本次同步成功。负例仅有 `Failed` 或资源不存在不够；仓库下载、
渲染或网络失败也可能造成这些结果，因此检查会匹配资源 group/kind/name/namespace 和
`SyncFailed` 中的明确 Project 拒绝消息。
当前固定 Argo CD 版本的操作结果会给被拒绝的 ClusterRole 记录目的地 namespace；
验收按该返回格式核对，但查询 ClusterRole 是否存在时仍使用其真实的集群级作用域。

## 报告与边界

报告位于 `.local/clusters/<随机集群名>/report.json`，保存源文件摘要、fixture SHA、
各阶段结果及清理状态。`gitops/` 保存逐次 Application 状态和公开的测试仓库。
报告为 `passed` 才表示该次完整流程通过；当前完成证据见[验证记录](verification.md)。
修改相关源码后应重新运行，不应拿旧报告证明新配置。

2026-10-05 已在 OrbStack Docker Linux ARM64 的临时三节点环境通过完整流程，报告为
`.local/clusters/platform-gitops-9586b9d10b06/report.json`。该结果包括真实 Git 获取、同步、
Service 响应、手工漂移修复以及两类具体资源拒绝；测试集群已删除。

这个检查证明的是管理员提交同步请求后，Argo CD 控制器执行资源种类限制及交付流程。
同步通过 Kubernetes API 发起，没有测试真实业务用户的 Argo CD RBAC、浏览器、SSO、
私有仓库凭据、Webhook、生产 Gateway 或 TLS。

kind 默认网络不执行 NetworkPolicy，所以创建网络策略并请求成功不能证明网络隔离生效。
测试还不覆盖真实生产 CSI、节点故障、跨可用区可用性或版本滚动升级。缩容再恢复没有修改
Pod 模板，两 worker 足够；业务模板的版本滚动发布仍需第三个合格 worker 容纳 surge Pod。

AppProject 的资源类别限制也不能替代 Pod Security、Kubernetes RBAC、CNI 和仓库权限。
业务用户不能获得修改 Project、切换到宽松项目或绕过 Argo CD 直接管理集群的权限。
配置语义见 [Argo CD Projects](https://argo-cd.readthedocs.io/en/stable/user-guide/projects/)。
