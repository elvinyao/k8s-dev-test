# local 环境

`kind.yaml` 定义 1 个 control-plane 和 2 个 worker，默认 API 绑定宿主回环接口。
本地基线入口会安装 Argo CD 和 PV/PVC 示例，正常启动及验收后保留集群与数据：

```sh
bash .agent/run.sh --docker --toolbox python scripts/local_cluster.py up
bash .agent/run.sh --docker --toolbox python scripts/local_cluster.py verify
```

先按 [工具链说明](../../docs/tooling.md) 构建工具箱。默认名称为 `platform-local`；
归属记录、kubeconfig、日志和报告保存在被忽略的 `.local/clusters/<name>/`。已有集群无归属
记录、节点身份改变、kind 配置改变或旧状态未处理时，脚本会拒绝继续；不会自动接管或重建。
目前没有互斥锁，同名集群的 `up` / `verify` 必须串行执行。
完整命令、访问 Argo CD、失败处理与清理步骤见 [本地环境文档](../../docs/local-cluster.md)。

已在 ARM64、Docker 分配 8 CPU / 约 8 GiB 内存的环境验证三节点、重复启动复用节点、
Argo CD 工作负载就绪，以及 PVC 消费 Pod 重建后 marker 保持。一次性 smoke 会清理自己的
随机临时集群；这个结果不代表监控、完整 ELK 或 GitLab 已在该资源下运行。

基线版本为 kind `v0.33.0`、Kubernetes `v1.35.8`；节点镜像使用固定多架构 digest。
三个节点仍共享同一台宿主机，没有配置宿主持久卷挂载。删除节点/集群可能丢失本地卷，
PV 的 `Retain` 策略不能提供宿主机灾难恢复或生产可用性。隔离测试 Git 的实际同步及权限
拒绝已由[GitOps 验收](../../docs/gitops-validation.md)验证；浏览器登录与用户私有仓库接入仍待验收。
