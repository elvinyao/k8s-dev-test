# 发布、备份与恢复

每次发布记录：配置 Git commit、chart 版本/归档 SHA256、镜像 digest、集群版本、执行人、
变更窗口、备份编号、实测结果和回退步骤。先在独立恢复环境验证，再推进生产。

## 变更流程

1. 更新环境 values 与 `platform/releases.yaml`，检查组件兼容矩阵和迁移要求。
2. 在 Docker runner 内执行语法、Compose 模型、Helm lint/render 与 Kustomize 验证。
3. 检查渲染 diff，特别是 PVC、CRD、权限、Service、证书、Secret 引用和镜像来源。
4. 验证新备份可恢复；数据库升级应记录不可逆迁移和支持的回退方式。
5. 明确实际 context/项目名，单组件发布，等待 controller、工作负载及业务检查成功。
6. 记录告警、错误率、延迟、磁盘与内存变化，确认没有持续 OOM、CrashLoop 或队列积压。

Argo CD 配置不默认启用 prune；不要给持久数据或 CRD 增加删除级联后直接同步。
删除 ECK/Prometheus CRD 可能删除依赖资源。升级 CRD 按上游文档单独处理；
`helm template --include-crds` 不是 CRD 升级方案。Helm rollback 也不会逆转数据库迁移。

## 数据与恢复对象

| 服务 | 必须备份/记录 | 恢复验证 |
| --- | --- | --- |
| 集群/Argo CD | 外部 Git 副本、集群恢复资料、repo/SSO/签名密钥 | 无集群内 GitLab 依赖地重新引导 |
| Prometheus | 配置、TSDB 一致性备份或受支持的远程存储方案 | 查询历史时间段、标签与规则一致 |
| Alertmanager | 配置、通知凭据、silences | 通知 firing/resolved、消音正确 |
| Grafana | 数据库、provisioning、插件版本、稳定加密 key | 用户登录、面板、数据源凭据可解密 |
| Elasticsearch | snapshot repository、SLM/ILM、角色/模板与受控凭据 | 隔离集群恢复索引并对比数据 |
| Logstash/Filebeat | pipeline、queue/DLQ 处置记录、registry/checkpoint | 中断重连、积压回放、重复/丢失范围 |
| GitLab | DB、Gitaly、对象 bucket、Rails Secret、TLS/config | clone/push、制品、Registry、CI及权限 |

备份必须位于独立故障域并加密、限制权限、监控最近成功时间。仅复制 PVC 清单、依赖
Retain 或在同一磁盘留一份 tar 都不足以应对宿主损坏。秘密应与业务备份关联保存，
却不能放进公开备份日志/仓库。RPO/RTO 由业务确定并由演练测量，不由示例副本数推算。

## 故障处理

- Pod Pending：检查资源请求、硬反亲和、taints、PVC/可用区和调度事件；不要直接降低所有 requests。
- PVC Pending：确认 provisioner、StorageClass 与消费 Pod；WaitForFirstConsumer 会等待调度。
- Argo CD 无法同步：检查固定 Git revision、仓库凭据、AppProject 允许资源、CRD/健康与 Webhook。
- Gateway 502/路由未接入：检查 backend Service/port、namespace 标签、证书及 Route conditions。
- ES 磁盘水位或 queue 积压：先限制流量并扩容/调整保留策略；保留故障证据，不直接删除数据目录。
- GitLab 升级失败：先检查数据库迁移和外部后端；不要盲目切回旧镜像写已迁移数据库。

所有排查命令继续通过 runner，并带明确 context 或 Compose project name。
不要将 `down -v`、删除 PVC、删除 namespace 或删除 kind 集群作为普通重启操作。

## 尚需在目标环境完成的证据

记录节点故障/重调度、Secret 和证书轮换、实际告警投递、权限拒绝测试、数据恢复及升级演练。
本仓库的离线检查不包含这些现场结果。只有真实验收和恢复演练通过，才可以接受对应生产风险。
