# 本地 kind 环境

`clusters/local/kind.yaml` 定义一个 control-plane 和两个 worker，固定 Kubernetes 1.35.8
镜像 digest。多节点用于调度和卷验证；仍是一个宿主故障域，不是生产节点方案。

先按 `tooling.md` 构建工具箱，并给 Docker 分配足够资源。初期只运行 kind、Argo CD 和
PVC 示例，再按实际使用量增加监控/ELK。GitLab Hybrid 需要外部后端，不属于笔记本最小栈。

先通过 runner 创建被忽略的 `.local` 目录：

```sh
bash .agent/run.sh python -c 'from pathlib import Path; Path(".local").mkdir(mode=0o700, exist_ok=True)'
```

以下为容器化 kind 的创建与连接步骤，需要在目标 Docker 环境验证；本仓库尚未执行完整
cluster-up。先检查同名集群，已有集群不应重新创建。把 `/absolute/repo` 替换为宿主仓库路径：

```sh
bash .agent/run.sh --docker kind get clusters
bash .agent/run.sh --docker kind create cluster --config clusters/local/kind.yaml
bash .agent/run.sh --docker kind export kubeconfig --name platform-local --internal --kubeconfig /workspace/.local/kind-internal.kubeconfig
bash .agent/run.sh --network kind --kubeconfig /absolute/repo/.local/kind-internal.kubeconfig kubectl --context kind-platform-local get nodes
```

kind 创建过程若因工具容器访问默认 API 地址失败，先保留日志并检查 `kind get clusters`，
不能盲目重复创建或删除节点。导出的 internal kubeconfig 使用节点 DNS，需要同时加入
kind Docker 网络。不要让生产 kubeconfig 使用这个网络或复用本地 context。

kind 数据默认在节点容器内。删除节点/集群可能丢失本地卷；`Retain` 不改变这一点。
若配置宿主目录挂载，`extraMounts.hostPath` 必须是 daemon 可见的宿主绝对路径。

集群可访问后，按 `apps/storage-demo/README.md` 验收 PVC。部署 Argo CD 选择 Helm 路径，
不要再叠加 `bootstrap/argocd` 的原始 manifests：

```sh
bash .agent/run.sh --network kind --kubeconfig /absolute/repo/.local/kind-internal.kubeconfig helm upgrade --install argocd argo-cd --repo https://argoproj.github.io/argo-helm --version 10.9.2 --namespace argocd --create-namespace --kube-context kind-platform-local -f platform/argocd/values-local.yaml --wait --timeout 10m
```

本地管理员账户用于自举，生产 values 使用 OIDC 并关闭内置管理员。
ELK local overlay 会缩小副本和 PVC，且关闭 mmap 以免在本地修改宿主 sysctl；这不是生产性能配置。
