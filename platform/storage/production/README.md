# 生产 CSI 存储配置

所有生产 values 中的 `production-rwo` 是目标集群中需要实际存在的 StorageClass 名称，
不是本仓库自动供应的通用存储。可以统一改为已验收的 CSI 类。

`storageclass-ebs.yaml.example` 是 AWS EBS 的可配置示例；只能在具备 EBS CSI、IAM 权限及
正确可用区配置的集群使用。其他环境应使用各自维护的 CSI 驱动及参数，不能替换 provisioner
字符串就假定兼容。不要把 local-retain / hostPath 用作生产数据库的 HA 存储。

RWO 卷一般有单节点/可用区约束，Prometheus/Elasticsearch 各副本需独立 PVC。
Grafana 当前采用单副本+RWO+Recreate，升级可能短暂中断；要实现 Grafana HA，需要外部数据库，
插件/仪表板供应方式和多副本会话配置，也不能让多个副本共用 SQLite。

上线前验收：卷供应、权限、扩容、Pod 重建、节点迁移、快照/备份恢复、磁盘满时告警与回收。
`Retain` 不代表自动备份；`allowVolumeExpansion` 也不代表可缩容。
PVC 缩小、存储类变更、跨可用区迁移必须走数据迁移流程。

来源：[Kubernetes PV](https://kubernetes.io/docs/concepts/storage/persistent-volumes/)、
[AWS EBS CSI](https://github.com/kubernetes-sigs/aws-ebs-csi-driver)。
