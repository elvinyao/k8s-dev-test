# PVC 读写及 Pod 重建验收

应用包含一个 1 Gi PVC 和一个非 root 消费 Pod。Pod 仅在数据卷中不存在 marker 时写入
UTC 时间，重建 Pod 后应读到相同内容。这里只验证 Pod 生命周期，不验证节点或宿主机故障
恢复，也不验证备份恢复。`local-retain` 的 PV 使用 `Retain`，但删除 kind 节点仍可能丢失
实际数据。

推荐先按 [本地环境说明](../../docs/local-cluster.md) 准备工具箱，再从仓库根目录运行：

```sh
bash .agent/run.sh --docker --toolbox python scripts/local_cluster.py up
bash .agent/run.sh --docker --toolbox python scripts/local_cluster.py verify
```

`up` 部署本示例；`verify` 检查 PVC 已绑定和 PV 的回收策略，记录 marker，删除并重建
`apps-dev/storage-demo` Pod，再比较 marker。它保留 PVC 和集群，报告位于
`.local/clusters/platform-local/report.json`。验收期间该示例 Pod 会短暂中断。目前没有互斥锁，
同名集群的 `up` / `verify` 必须串行运行，不要删除 PVC 来测试 Pod 重建。

如需手工检查，把 `/absolute/repo` 换成宿主仓库路径。internal kubeconfig 需要工具容器
加入 `kind` 网络，并显式指定 context：

```sh
bash .agent/run.sh --network kind --kubeconfig /absolute/repo/.local/clusters/platform-local/internal.kubeconfig kubectl --context kind-platform-local -n apps-dev get pod,pvc
bash .agent/run.sh --network kind --kubeconfig /absolute/repo/.local/clusters/platform-local/internal.kubeconfig kubectl --context kind-platform-local get pv
bash .agent/run.sh --network kind --kubeconfig /absolute/repo/.local/clusters/platform-local/internal.kubeconfig kubectl --context kind-platform-local -n apps-dev exec storage-demo -- cat /data/marker
```

使用自定义 `--name` 时，同步更换 kubeconfig 目录与 `kind-<name>` context。真实存储后端
还必须支持非 root 挂载所需权限；本例的 local-path 存储不能替代生产 CSI 后端的验收。
