# PVC 读写及 Pod 重建验收

应用包含一个 1Gi PVC 和一个非 root 消费 Pod。Pod 仅在数据卷为空时写入 UTC 时间 marker，
因此重建 Pod 后 marker 必须保持相同。这里只验证 Pod 生命周期，不验证宿主机/节点灾难恢复。

在已配置正确 kubeconfig 的情况下（所有命令显式选择 context）：

```sh
bash .agent/run.sh --kubeconfig /absolute/local.kubeconfig kubectl --context kind-platform-local apply -k platform/namespaces
bash .agent/run.sh --kubeconfig /absolute/local.kubeconfig kubectl --context kind-platform-local apply -k platform/storage
bash .agent/run.sh --kubeconfig /absolute/local.kubeconfig kubectl --context kind-platform-local apply -k apps/storage-demo
bash .agent/run.sh --kubeconfig /absolute/local.kubeconfig kubectl --context kind-platform-local -n apps-dev wait --for=condition=Ready pod/storage-demo --timeout=180s
bash .agent/run.sh --kubeconfig /absolute/local.kubeconfig kubectl --context kind-platform-local -n apps-dev exec storage-demo -- cat /data/marker
bash .agent/run.sh --kubeconfig /absolute/local.kubeconfig kubectl --context kind-platform-local -n apps-dev delete pod storage-demo
bash .agent/run.sh --kubeconfig /absolute/local.kubeconfig kubectl --context kind-platform-local apply -k apps/storage-demo
```

再次等待 Ready 并读取 marker，应与第一次一致。不要删除 PVC 来测试 Pod 重建。
在工具容器内使用 kind 网络时，还需 runner 的 `--network kind` 与 internal kubeconfig；
详见 `docs/local-cluster.md`。真实存储后端必须支持非 root 挂载所需权限。
