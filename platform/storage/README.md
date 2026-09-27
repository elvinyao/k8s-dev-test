# 本地存储

`storageclass.yaml` 为 kind 的 `rancher.io/local-path` provisioner 定义独立的
`local-retain` StorageClass。部署前须检查 provisioner 已就绪，配置没有将它设为默认类。
`apps/storage-demo` 提供动态 PVC；PV 由 provisioner 创建，无须手写。

`WaitForFirstConsumer` 会等待实际使用 PVC 的 Pod 出现；`apps/storage-demo` 已提供消费 Pod
和 marker 读写/重建验收步骤。长期 Pending 应检查调度、provisioner 及磁盘权限。

`examples/static-pv.yaml.example` 单独演示静态 PV/PVC，不在 kustomization 的资源列表中。
使用前先在带 `platform.local/storage-demo=true` 标签的节点内准备目录及权限，
并确认标签只对应一个节点。PV 用 nodeAffinity 绑定节点，PVC 用 volumeName 预绑定。

`Retain` 保留的是释放后的 PV/底层数据处理责任，不能阻止删除 kind 节点导致数据丢失。
当前数据仍在节点容器内；在配置宿主持久目录和外部备份前，不要存放需要保留的数据。
生产环境应单独设计 CSI、故障域、快照、容量告警和恢复演练。
生产配置入口见 `production/README.md`。

来源：[Kubernetes 持久卷](https://kubernetes.io/docs/concepts/storage/persistent-volumes/)、
[Local Path Provisioner](https://github.com/rancher/local-path-provisioner)。
