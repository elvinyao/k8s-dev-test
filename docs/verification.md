# 配置验证记录

验证日期：2026-10-05。以下是当前工作区配置的检查范围；离线源码基线和修改状态以
`rendered/verification/report.json` 为准。本记录不代表任何环境已经部署或通过生产验收。
修改配置后需重新执行对应检查，不能把旧报告当作新版本证据。

| 检查 | 结果与范围 |
| --- | --- |
| 源文件检查 | Python/shell 语法及本地文档链接，数量见 source 日志 |
| 回归测试 | 覆盖生产前置检查、归档完整性、旧报告失效、组件衔接，以及 ELK 初始化和恢复保护；数量见 regressions 日志 |
| YAML 静态检查 | 源码目录中的 YAML、重复 key 与本地资源引用通过；数量见 yaml 日志 |
| Compose 模型解析 | GitLab 2、Logging 5、Monitoring 4 个服务通过；包含可选 profile，共 11 个服务 |
| 生产 Helm lint/render | 6 个 chart，共 325 个对象；归档 SHA256 强制匹配 |
| 本地 Helm lint/render | 5 个 chart，共 283 个对象通过；不包含 GitLab |
| Kustomize | 8 个入口、53 个对象通过，包含业务示例及 local/production 两套日志配置 |
| 自定义资源 schema | 23 个对象通过所渲染 chart 提供的 CRD schema；跳过 30 个内置对象 |
| GitOps 生成 | 示例生成 1 个 AppProject 与 6 个 Application；默认无自动同步，Argo CD/GitLab 保留 Helm 管理 |
| 组件衔接 | 核对实际资源与 Project 授权、server 入口策略叠加，以及 Registry 关闭/启用两种渲染 |
| 业务 GitOps | apps-prod 的 Deployment/Service/PDB/ConfigMap 均匹配受限 Project；实际部署拒绝示例仓库/全零 SHA |
| 输入拦截 | 生产模式拒绝示例仓库地址；生产 preflight 对未替换的环境占位符返回失败 |

执行入口和命令见 [容器工具链](tooling.md)。Helm 归档校验值及对象数量保存在
忽略提交的 `rendered/production/summary.json` 和 `rendered/local/summary.json`；
发布时应将其与实际 Git commit 一起归档到受控的发布记录中。

## 检查边界

- Compose 检查只解析配置并检查资源约束等规则，未启动这些服务。
- Helm 与 Kustomize 渲染未调用 Kubernetes API，不验证调度、CSI、网络或实际控制器行为。
- CRD 检查不包含 Kubernetes 内置 schema、CEL、admission webhook 和运行期约束。
- 原始 Argo CD bootstrap 引用的远程 manifest 未在该静态检查中展开；已验证的是 Helm 路径。
- preflight 的占位符拦截是预期保护行为。示例当前不能不经环境配置直接用于生产。

## 已完成的隔离运行检查

2026-10-05 在 OrbStack 提供的 Docker Linux ARM64 环境执行，测试资源与正式项目完全分开：

| 检查 | 已观察到的证据 | 未覆盖 |
| --- | --- | --- |
| Monitoring Compose | promtool/amtool 通过；三服务 healthy；3 个 scrape target up；Watchdog 到达 Alertmanager；Grafana 鉴权、数据源和面板正常 | 真实负载、外部通知、TLS 代理 |
| Monitoring 备份/恢复 | 停止写入后 tar 备份，恢复到另一项目的空卷；恢复前历史指标、自定义 Grafana 文件夹和 active silence 可读 | 跨主机灾备、外部数据源秘密解密、升级降级 |
| ELK 初始化 | 使用实际 OpenSSL 检查证书链/SAN/EKU、key 匹配、文件访问权限和重复执行保护 | 运行期证书轮换 |
| Logstash 原生配置 | 9.5.4 镜像执行实际 keystore 入口及 `--config.test_and_exit`，返回 Configuration OK | Elasticsearch/Kibana 启动、日志写入与 ES snapshot 恢复 |
| GitLab 原生配置 | 19.4.1 镜像内 SettingsDSL 接受 12 个配置选项；错误 Ruby、未知选项、非整数端口均被拒绝；无网络且非 root | reconfigure、迁移、服务启动、CI、备份恢复 |
| 本地 kind 基线 | 3 个节点 Ready；两次自举保持节点身份；6 个 Argo CD 工作负载 Ready；PVC Bound、PV Retain，重建 Pod 后 marker 不变 | Argo CD 登录/SSO、实际 GitOps 同步、完整平台、节点故障及卷恢复 |
| Argo CD 实际 GitOps | 从集群内只读 Git 获取完整 SHA，四资源 Synced/Healthy；Restricted namespace 中两副本跨节点 Ready，Service 内容匹配；缩容后发现 OutOfSync，手工同步恢复；Secret/ClusterRole 明确被 Project 拒绝且未创建 | 真实业务身份 RBAC/SSO、私有外部 Git、CNI 策略执行、版本滚动升级及 HA |
| 业务示例镜像 | 固定摘要的 BusyBox 以 UID65532、只读根文件系统、无 capabilities/外部网络运行实际 httpd 命令，返回预期页面 | Kubernetes 调度、Service、NetworkPolicy 的实际执行 |

监控证据位于 `.local/monitoring-smoke-7703778496c3/report.json`，包括镜像 ID、配置摘要、
备份 SHA256 和清理结果。Logstash 证据位于
`.local/logging-config-check-2ef3a45f6a37/report.json`；GitLab 证据位于
`.local/gitlab-config-check-eb30ee158a58/report.json`；kind 证据位于
`.local/clusters/platform-smoke-2a15ef1c3efe/report.json`。这些本地证据不提交，复测命令见
[工具链](tooling.md)。测试服务和节点已清理；kind 共享网络、镜像缓存及报告保留。
业务镜像检查报告为 `rendered/business-demo/smoke-image.json`，命令见业务示例 README。
GitOps 实际同步报告为 `.local/clusters/platform-gitops-9586b9d10b06/report.json`，复测步骤见
[GitOps 运行验收](gitops-validation.md)。该临时集群已清理，真实业务环境没有接入或修改。

## 尚未运行的 ELK 全链路

已新增 `scripts/smoke-logging.py` 与独立 API 探针，覆盖真实 Filebeat/HTTP 采集、权限拒绝、
Kibana 对象、服务重建及空 ES 卷上的日志快照恢复。两套测试 Compose 模型可通过
`--check-model` 解析；容量和资源隔离的回归测试不等同服务运行证据。

当前 Docker 约 7.8 GiB 内存，原三服务上限合计 8 GiB。前置检查已按预期拒绝完整启动，
未降低堆大小或修改宿主内核。需要容量足够的 Docker 环境才能取得真实数据链路及恢复结果。
命令、资源条件、清理和恢复边界见 [ELK 运行验收](../compose/logging/validation.md)。
模型检查报告为 `.local/logging-smoke-3d9f3dfe2444/report.json`（`model-checked`）；
容量拒绝记录为 `.local/logging-smoke-bec2617384d3/report.json`（`failed`，无服务启动步骤）。

## 目标环境验收

按照 [生产部署手册](production.md) 填入域名、固定 Git commit、凭据、CSI 与外部后端，
逐层部署并记录以下证据：

1. 节点、DNS、负载均衡、证书链、NetworkPolicy 与 PVC 供给正常。
2. Argo CD SSO/权限、监控采集和告警 firing/resolved 投递成功。
3. 容器日志经 Filebeat/Logstash 到达 Elasticsearch，保留策略及磁盘告警生效。
4. GitLab clone/push、CI、制品及启用时的 Registry 可用，外部依赖连接受保护。
5. 节点故障、凭据和证书轮换、升级及独立环境恢复演练通过，记录实测 RPO/RTO。

本地 kind 的最小自举和 PVC 验收已完成；完整 ELK/GitLab 运行及上述目标环境验收仍未完成。自建生产节点的
安装与升级自动化也不在当前配置范围内；生产手册以已有多节点 Kubernetes 集群为前提。
