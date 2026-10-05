# 本地 Kubernetes 管理平台架构

## 目标与当前范围

目标是在本地构建可重复部署、由 Git 管理配置的 Kubernetes 平台，逐步覆盖节点、Argo CD、Prometheus、ELK、PV/PVC、GitLab，以及网络、权限和备份管理。

仓库提供本地 kind 启动入口、独立 Compose 部署、生产 Helm values、ECK/Gateway 配置和
部署运维手册。本地最小集群、实际业务 GitOps，以及 Monitoring Compose 的空卷恢复已有
隔离运行证据；完整 ELK、GitLab 与目标生产环境仍未完成运行和恢复验收。
实施入口为[本地集群](local-cluster.md)、[生产部署](production.md)和[容器工具链](tooling.md)，
已完成的检查及边界统一记录在[验证记录](verification.md)。每个阶段独立部署、验收。

这里的“生产级”指逐步建立版本锁定、变更审核、权限隔离、监控告警、备份恢复和升级演练能力。本地 kind 节点共享一台宿主机及其 Docker 环境，不能据此证明跨主机高可用，也不能代替真实生产环境的容量和故障测试。

## 集群拓扑

本地启动脚本创建一个 kind 集群：

| 节点 | 数量 | 用途 |
| --- | --- | --- |
| control-plane | 1 | API Server、etcd、调度与控制器 |
| worker | 2 | 平台服务与示例业务工作负载 |

kind 节点是 Docker 容器，多 worker 用于验证调度、节点约束与工作负载分布。一个 control-plane 不具备控制平面高可用；同一宿主机上的多个 control-plane 也无法覆盖宿主机故障。[kind 配置文档](https://kind.sigs.k8s.io/docs/user/configuration/)

本地基线保留 kind 的默认网络，使用 `local-retain` 验证 PV/PVC。默认网络不执行
NetworkPolicy；已创建策略或请求成功均不能证明网络隔离。生产入口已选择 Envoy Gateway，
但本地最小基线不自动安装完整入口、监控、日志或 GitLab。

生产路径以已有多节点 Kubernetes 为前提，至少需要 3 个符合调度条件、有剩余容量的 Linux
worker、真正执行 NetworkPolicy 的 CNI、LoadBalancer 和经过验收的 CSI。生产控制平面、
节点安装升级和外部数据库等由目标环境供应。当前按 hostname 的硬反亲和不能证明跨可用区
隔离；两副本业务与 Envoy 数据面滚动升级还需要第三个 worker 容纳 surge Pod。

## 配置分层与所有权

Argo CD 自身与 GitLab 保留原生 Helm release 管理，避免双重所有权，并保留 GitLab chart
的升级检查流程。GitOps 生成器创建一个管理员 AppProject 和六个 Application，管理
cert-manager、Envoy Gateway、Monitoring、ECK Operator、入口资源与 ELK 数据面。
首次按依赖阶段初始化的资源，在明确接管后通过 Git 持续管理。业务使用另一个受限 Project，
当前示例均由人工同步，未启用自动同步、自动 prune 或级联删除 finalizer。

| 层 | 职责 | 权限边界 |
| --- | --- | --- |
| `argocd-sys-settings` | 管理员 AppProject、平台 Application 模板及系统层接入说明 | 平台管理员维护；含 Argo CD namespace 和所需集群资源权限 |
| `argocd-app-settings` | 受限业务 AppProject、固定 Git SHA 的 Application 和业务部署入口 | 限定 Git 来源、`apps-prod` 及资源类别；禁止 Secret 和集群资源 |
| `platform/*` | 每个组件的清单、Helm values、环境差异和组件说明 | 按组件明确负责人及资源所有权 |

名称和目录本身不提供权限隔离。落地时必须配置 AppProject 的 `sourceRepos`、`destinations`、资源白名单和 Argo CD RBAC。能够向 Argo CD 自身 namespace 部署资源的 Project 实际具有管理员能力；能够修改 App-of-Apps 父应用来源的人也能影响子应用权限，因此系统层需要受控的合并权限。[Argo CD 集群引导文档](https://argo-cd.readthedocs.io/en/stable/operator-manual/cluster-bootstrapping/)

平台目录已有以下配置与说明；配置存在不表示对应组件已经通过生产验收：

| 目录 | 当前内容 |
| --- | --- |
| `platform/namespaces` | 本地平台 namespace 和统一标签；生产 namespace 在 `clusters/production` |
| `platform/storage` | StorageClass、PV/PVC 示例、数据保留策略与存储验收 |
| `platform/monitoring` | Prometheus、Alertmanager、Grafana、采集与告警规则 |
| `platform/logging` | ECK Operator、Elasticsearch、Kibana、Logstash 和日志采集配置 |
| `platform/gitlab` | 使用外部后端的生产 GitLab values、可选 Registry overlay 与接入说明 |
| `platform/networking` | Gateway API、入口实现、域名解析和服务访问配置 |
| `platform/security` | cert-manager、可选 DNS01 证书、业务配额与网络策略；秘密由外部流程供应 |
| `platform/backup` | 各组件备份、独立密钥保管与恢复验收要求 |

组件版本已显式锁定，chart 索引与归档 SHA256 见 [releases.yaml](../platform/releases.yaml)。
升级仍需核对 Kubernetes、Helm chart 和 Operator 的支持矩阵。资源清单、版本和非敏感环境
配置可提交到 Git；私钥、访问令牌、kubeconfig、明文 Secret 和备份数据不得作为仓库内容提交。

`compose/monitoring`、`compose/logging` 和 `compose/gitlab` 是各自独立的单机部署路径。
它们不自动接入 kind，也不与 Kubernetes 部署共用活跃数据目录。Compose 的运行证据不能
代替同组件 Helm/ECK 路径的调度、CSI、网络或控制器验收。

## 分阶段实施与验收

| 阶段 | 实施内容 | 验收条件 |
| --- | --- | --- |
| 0：仓库初始化 | Docker runner、版本配置、kind 拓扑、GitOps 分层、文档和基础清单 | 仓库结构清晰；静态验证结果明确；不将静态验证描述为集群验证 |
| 1：集群与引导 | 创建 kind 节点、验证容器工具链访问集群、部署 Argo CD、连接配置仓库 | 三个节点 Ready；系统 Pod 正常；Argo CD 能读取指定仓库并同步最小应用 |
| 2：基础设施 | namespace、入口、证书、StorageClass 与 PV/PVC | 入口请求可达；证书链符合预期；PVC Bound；Pod 重建后数据仍可读取；存储生命周期有记录 |
| 3：监控 | Prometheus、Alertmanager、Grafana | 节点及工作负载指标可查询；测试告警完整触发并恢复；监控数据的保留时间和容量明确 |
| 4：日志 | 安装 ECK 与 CRD，再部署 Elasticsearch、Kibana、Logstash 和采集组件 | 测试日志能够采集、处理和检索；日志轮转、保留时间、磁盘告警和重启恢复经过验证 |
| 5：GitLab | GitLab、Runner、仓库凭据和 Argo CD 集成 | 完成一次提交、CI 构建、镜像发布、GitOps 配置变更和部署验证；明确 Runner 权限及凭据范围 |
| 6：运维能力 | 最小权限、策略验证、版本升级、备份和恢复演练 | 未授权操作被拒绝；备份实际恢复成功；故障及升级演练留下结果；恢复时间与允许数据损失有明确目标 |

Operator 的 CRD 与依赖它的自定义资源必须按顺序就绪。Argo CD 的 sync wave 用于安排资源顺序，但父应用中子 Application 的创建完成，不等于子应用内的 Operator 已就绪。需要显式健康检查或分阶段同步，并验证 CRD Established 和控制器可用。[Argo CD sync waves](https://argo-cd.readthedocs.io/en/stable/user-guide/sync-waves/)、[ECK 安装说明](https://www.elastic.co/docs/deploy-manage/deploy/cloud-on-k8s/install)

## 当前运行证据与资源边界

2026-10-05 的隔离检查在 OrbStack Docker Linux ARM64 环境执行，Docker 报告 8 CPU、
约 7.8 GiB 总内存。这里记录的是 Docker 可见资源，不是宿主机总内存，也不是平台容量保证。
各检查分开执行，临时服务和集群完成后清理；不能据此声称它们已同时常驻。

| 路径 | 已完成 | 尚需验证及资源条件 |
| --- | --- | --- |
| 本地 kind / Argo CD / PVC | 三节点 Ready、重复启动保持节点身份、六个 Argo CD 工作负载就绪；Pod 重建后卷内 marker 保留 | 未覆盖节点或宿主故障、卷备份恢复、浏览器和 SSO |
| 实际业务 GitOps | 真实 Git SHA 同步，两副本跨节点与 Service 响应，发现漂移并手工修复，Project 明确拒绝 Secret / ClusterRole | 未覆盖私有外部 Git、业务用户 RBAC、CNI 策略执行或版本滚动升级 |
| Monitoring Compose | 服务、采集、Grafana 和 Watchdog 检查；另一项目空卷恢复历史指标、文件夹和 silence | 未覆盖外部告警投递、TLS 入口、跨主机灾备或生产负载 |
| ELK Compose | 证书初始化、原生 Logstash 配置和完整验收模型检查 | 全链路未启动；三服务默认内存上限合计 8 GiB，验收要求 Docker 至少 12 GiB、建议 16 GiB，并满足现有负载之外的可用内存要求 |
| GitLab Compose | 固定镜像内原生配置加载与错误输入拒绝 | 默认 GitLab 容器内存上限 16g，当前机器未完成初始化、服务运行、CI 或恢复；目标主机还须为系统、Runner 和既有负载留余量 |

ELK 全链路检查已因容量不足在启动前停止，未通过调低堆大小绕过条件。详细前提见
[ELK 运行验收](../compose/logging/validation.md)。GitLab 应在符合负载要求的专用 Linux
环境按[部署手册](gitlab-production.md)验收；配置解析通过不证明服务能启动。

生产 Kubernetes 需单独合计 requests/limits、存储增长与故障后的剩余容量。现有 ES 配置
请求 12Gi、Logstash 请求 4Gi、GitLab 应用请求超过 10Gi，Monitoring、Argo CD、系统和
外部后端另计；三 worker 仅是调度前提，不是容量规格。CPU、磁盘性能、日志吞吐、CI 并发
和保留时间均需在目标环境测量。

每个有状态组件上线前，需要记录 PVC 容量、数据增长预估、保留时间、备份目标和删除行为。先以小规模数据验证完整链路，再根据测量扩大资源，不通过无限降低 requests 来掩盖实际内存需求。

## 关键实现约束

### Docker 执行与网络

遵循仓库的 Docker 执行策略：宿主机仅进行源码检查和编辑，项目命令统一通过 `bash .agent/run.sh ...` 执行。Docker 不可用或 runner 失败时，记录问题并停止对应执行，不回退到宿主机直接运行。

现有本地入口通过显式 `--docker` 访问 Docker socket，创建或确认节点归属后，将自己的
工具容器接入 `kind` 网络。集群 API 在宿主绑定 `127.0.0.1`，工具容器使用节点 DNS 的
内部 kubeconfig；后续访问需同时提供 `--network kind` 和该 kubeconfig。以下约束仍适用：

- Docker daemon 解析 bind mount 的源路径。kind 的 `extraMounts.hostPath` 必须指向 daemon 可访问的宿主路径，不能直接使用仅存在于工具容器内的 `/workspace` 路径。
- 默认 kubeconfig 中的 `127.0.0.1` 是调用者所在网络的回环地址。工具容器访问集群时需要明确 kind 网络、API 地址和证书匹配方案，不能假定宿主 kubeconfig 在容器内原样可用。
- 挂载 Docker socket 会让工具容器获得管理该 daemon 的能力，因此只应由显式需要的命令使用，不能作为不相关检查的默认依赖。

macOS 上的宿主目录还要满足 Docker 文件共享要求。当前基线没有为本地卷配置宿主持久化
挂载；新增挂载时应独立验证路径、权限与删除行为。现有集群创建和容器内 API 访问已通过
隔离检查，操作步骤见[本地集群](local-cluster.md)。

### 存储与恢复

PV/PVC 是 Kubernetes 资源接口，不自动提供复制、备份或跨节点恢复。静态 local PV 需要节点亲和性；Pod 只能调度到能够访问数据的节点。动态卷的回收行为由 StorageClass/PV 配置决定，`Delete` 可能删除后端数据，`Retain` 则需要人工管理回收。[Kubernetes Persistent Volumes](https://kubernetes.io/docs/concepts/storage/persistent-volumes/)

本地已验证 PVC Bound、PV Retain 和消费 Pod 重建后的数据保持，尚未验证节点重建或集群
删除后的恢复。当前数据位于 kind 节点容器内部，`Retain` 不能防止删除节点导致的数据丢失。
若增加宿主目录挂载，也必须记录路径与清理流程。关键 PVC/PV 的删除和 Argo CD pruning
应经过明确设计。生产环境应另行验收 CSI、快照、异地备份和恢复目标。

### 入口与 ELK 选型

入口已采用 Gateway API 与固定版本 Envoy Gateway，包含 LoadBalancer Gateway、两副本
数据面、PDB 和 Argo CD/Grafana HTTPRoute；GitLab chart 复用该 Gateway。cert-manager
提供证书管理配置，域名、可信证书、负载均衡和真实入口维护演练仍由目标环境完成。
Kibana 保持 ECK 内部 HTTPS；公开访问需另行配置可信后端 TLS。见[平台入口](../platform/networking/README.md)。

Kubernetes 日志配置使用 ECK 管理 Elasticsearch、Kibana、Logstash 和 Filebeat。Filebeat
DaemonSet 读取节点容器日志，经 mTLS 发送到 Logstash，再写入 Elasticsearch。生产与本地
overlay 已提供，但尚无该 Kubernetes 日志链路的运行证据；Compose 原生配置检查不能替代
ECK 关联、webhook、节点日志权限及实际采集验收。见[日志配置](../platform/logging/README.md)。
各镜像仍需核对目标 CPU 架构；不能将单个组件支持 arm64 推断为整个 chart 的所有子组件都支持。

### GitLab 的引导依赖

Argo CD 首次引导需要一个已经可访问的配置源。若 GitLab 也部署在本集群中，就不能把尚未启动的 GitLab 作为恢复整套集群的唯一依赖。应先准备外部可访问的 Git 源或明确的离线引导材料；迁移到本地 GitLab 后，仍保留独立于该集群的配置副本与恢复所需凭据备份。

GitLab 的数据库、Redis、对象存储、镜像仓库和 Runner 都有独立的容量、版本与恢复要求。实验环境和生产环境的依赖布局需要分别定义，不能把 chart 能启动当作生产部署验收。[GitLab chart 前置条件](https://docs.gitlab.com/charts/installation/tools/)
