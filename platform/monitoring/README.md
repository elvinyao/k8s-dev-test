# Kubernetes 监控配置

`values-local.yaml` 与 `values-production.yaml` 使用 `platform/releases.yaml` 中固定版本的
kube-prometheus-stack。包含 Prometheus Operator、Prometheus、Alertmanager、Grafana、
node-exporter、kube-state-metrics 和默认规则。独立 Compose 路径见 `../../compose/monitoring/`。

生产配置为 Prometheus 2副本各50Gi、15天/40GB保留；Alertmanager 3副本各5Gi；
Grafana 单副本10Gi、Recreate。Grafana 当前用本地数据库，升级存在中断窗口；需要HA时必须
设计外部数据库和多副本配置。两个 Prometheus 是独立采集副本，没有内置全局查询去重层。

先供应 `monitoring` namespace 下的秘密：

| Secret | keys |
| --- | --- |
| `grafana-admin` | `admin-user`、`admin-password` |
| `alertmanager-config` | `alertmanager.yaml`，完整有效配置 |

`alertmanager.yaml.example` 是接收器示例，替换成实际通知端点后再写入 Secret；
验证外发 firing/resolved，不能只看 UI。真实 URL/token 不提交。Grafana 初始密码只用于
新数据库初始化，已有账号密码需要通过管理流程轮换。

修改生产 storageClass、Grafana域名、资源/保留量，手动同步 monitoring Application。
CRD 大小可能超过客户端 annotation 限制，因此 GitOps 使用 ServerSideApply；
升级 CRD 仍需按对应 chart 版本的迁移说明处理。

默认关闭 etcd/controller-manager/scheduler/kube-proxy targets，避免托管控制平面无法访问。
自建集群需按真实 TLS/认证/endpoint 启用，不能把关闭采集解释为这些组件已被监控。
Ingress 不公开 Prometheus/Alertmanager，Grafana 使用平台 Gateway。

验收 targets、告警、Grafana 查询、历史数据重挂以及独立恢复；WAL和压缩空间另留余量，
retentionSize 不是磁盘配额。PDB/副本也不替代备份或跨故障域存储。

参考：[chart 与升级说明](https://github.com/prometheus-community/helm-charts/tree/main/charts/kube-prometheus-stack)。
