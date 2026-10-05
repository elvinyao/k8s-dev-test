# 本地 kind 环境

本地入口会创建 1 个 control-plane、2 个 worker，安装 Argo CD，并部署一个用于验证 PV/PVC
的消费 Pod。Kubernetes 1.35.8 节点镜像使用固定 digest，Argo CD 使用固定版本且校验归档
SHA256 的 Helm chart。三个节点共享一个宿主故障域，适合开发与部署流程验证。

## 准备与启动

先按 [容器工具链](tooling.md) 构建工具箱并启动 Docker。本仓库已在 ARM64、Docker 分配
8 CPU、约 8 GiB 内存的环境运行这个最小基线；该记录不代表同一资源能承载监控、完整 ELK
和 GitLab。先检查 Docker 的可用资源与磁盘，再按实际使用量逐项增加服务。

在仓库根目录运行：

```sh
bash .agent/run.sh --docker --toolbox python scripts/local_cluster.py up
bash .agent/run.sh --docker --toolbox python scripts/local_cluster.py verify
```

默认集群名为 `platform-local`。`up` 创建或更新本仓库管理的集群，等待节点、PVC 消费 Pod
及 Argo CD 安装就绪；成功后保留集群和数据。`verify` 检查 PVC 已绑定、PV 回收策略为
`Retain`，删除并重建 `apps-dev/storage-demo` Pod，比较卷内 marker，并检查 Argo CD 工作
负载。它会短暂中断这个示例 Pod，不会删除 PVC。详细结果与步骤日志保存在
`.local/clusters/platform-local/report.json` 及同目录中。

两个命令都支持 `--name platform-example`：名称必须以 `platform-` 开头，只包含小写字母、
数字和分隔用的连字符，最长 40 字符。自定义名称后，下面命令中的目录与
`kind-platform-local` context 也要对应更换。目前没有互斥锁，同名集群的 `up` / `verify`
必须串行执行。

## 归属与失败处理

`.local/clusters/<name>/state.json` 记录 kind 配置 SHA256 和节点容器 ID。重复执行 `up`
会核对记录并复用节点。以下情况会停止，由操作者检查原因，不会自动接管、重建或升级：

- 同名集群存在，但没有本仓库归属记录。
- kind 配置文件内容发生变化，或实际节点 ID 与记录不同。
- 集群已不存在，但此前的归属记录仍在；应使用新名字，或检查后归档旧记录。
- 上一次创建只完成了一部分，无法确认完整归属。

普通 `up` / `verify` 失败时保留集群与数据，先阅读报告及日志再处理。不要把删除状态记录
当作修复运行中集群的方法。状态目录还包含敏感 kubeconfig，已被 Git 忽略；不要提交、分享
或复制到公开日志中。

脚本使用显式授权的 Docker socket 创建或确认节点，并将当前工具容器接入现有 `kind`
网络。集群 API 在宿主绑定 `127.0.0.1`；内部 kubeconfig 使用节点 DNS，因此后续工具容器
必须同时使用 `--network kind`。这里没有配置宿主持久卷挂载；kind 数据位于节点容器中，
删除节点/集群可能丢失本地卷，`Retain` 无法防止这种损失。

需要手工销毁时，先核对状态记录与当前节点容器的归属，确认数据不再需要或已另行备份。
以下命令会删除所指定的集群及其节点中的本地数据：

```sh
bash .agent/run.sh --docker kind delete cluster --name platform-local
```

删除后旧状态记录仍保留。下次启动选用新的 `--name`，或在确认集群已删除后归档旧状态
目录；不要直接使用删除命令来解决创建或验收失败。

## 查看节点与访问 Argo CD

把 `/absolute/repo` 替换为宿主上的仓库绝对路径。所有集群操作显式选择 context：

```sh
bash .agent/run.sh --network kind --kubeconfig /absolute/repo/.local/clusters/platform-local/internal.kubeconfig kubectl --context kind-platform-local get nodes
bash .agent/run.sh --network kind --kubeconfig /absolute/repo/.local/clusters/platform-local/internal.kubeconfig kubectl --context kind-platform-local -n apps-dev get pod,pvc
bash .agent/run.sh --network kind --kubeconfig /absolute/repo/.local/clusters/platform-local/internal.kubeconfig kubectl --context kind-platform-local -n argocd get deployments,statefulsets,pods
```

Argo CD 由 `up` 的 Helm 路径管理，不要再叠加 `bootstrap/argocd` 的原始 manifests。
需要浏览器访问时，在一个终端保持下面命令运行：

```sh
bash .agent/run.sh --network kind --kubeconfig /absolute/repo/.local/clusters/platform-local/internal.kubeconfig --publish 127.0.0.1:8443:8443 kubectl --context kind-platform-local -n argocd port-forward --address 0.0.0.0 service/argocd-server 8443:443
```

容器内监听 `0.0.0.0` 供 Docker 转发，宿主端口只发布到 `127.0.0.1`。打开
`https://localhost:8443`，本地默认使用自签名证书。结束该终端命令会停止转发。

初次使用 `admin` 账户时，由操作者手工运行以下命令，把初始密码写入被忽略的私有文件。
重定向和解码都在工具容器中执行，不会把密码打印到终端：

```sh
bash .agent/run.sh --toolbox --network kind --kubeconfig /absolute/repo/.local/clusters/platform-local/internal.kubeconfig bash -c '
set -euo pipefail
umask 077
kubectl --context kind-platform-local -n argocd get secret argocd-initial-admin-secret -o jsonpath="{.data.password}" | base64 --decode > .local/clusters/platform-local/argocd-admin-password
chmod 600 .local/clusters/platform-local/argocd-admin-password
test -s .local/clusters/platform-local/argocd-admin-password
'
```

通过自己的安全方式读取该文件用于登录，使用后删除文件并更换初始密码；不要把文件内容
提交到 Git 或发送到聊天。如果初始 Secret 已被删除，请使用已设置的凭据，不要假定可重新
取回初始密码。生产 values 使用 OIDC 并关闭内置管理员。

目前已验证节点、Argo CD 工作负载及 PVC Pod 重建；另一个[隔离 GitOps 检查](gitops-validation.md)
已通过真实测试 Git 仓库同步、手工漂移修复和资源种类拒绝。浏览器访问、登录和用户私有仓库
接入尚未验证。端口转发说明是操作步骤，不是浏览器验收记录。

## 一次性运行验收

下面命令创建随机名称的临时集群，执行两次 `up` 流程确认节点身份保持，再进行 PVC Pod
重建及 Argo CD 工作负载检查。它在完成后清理自己的临时集群，报告和日志保留在
`.local/clusters/platform-smoke-<随机值>/`，已执行范围见 [验证记录](verification.md)：

```sh
bash .agent/run.sh --docker --toolbox python scripts/smoke-local-cluster.py
```

清理前会再次核对节点归属；如果创建未完成或身份核对失败，会保留现场供检查。成功创建
后的验收失败也会尝试清理该临时集群，并保留诊断记录。报告中的 `idempotentUp` 目前直接
证明重复启动没有替换节点；数据验收覆盖随后删除并重建消费 Pod，不据此声称节点故障、
备份恢复或跨两次 `up` 的数据保持均已验证。
