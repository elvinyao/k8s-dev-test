# 本地 Kubernetes 管理平台架构

## 目标与当前范围

目标是在本地构建可重复部署、由 Git 管理配置的 Kubernetes 平台，逐步覆盖节点、Argo CD、Prometheus、ELK、PV/PVC、GitLab，以及网络、权限和备份管理。

仓库已从初始化骨架扩展为 Compose、Helm values、ECK/Gateway 配置和部署运维手册。
最新实施入口为 `docs/production.md` 和 `docs/tooling.md`。仍未安装完整平台，也未完成
目标生产环境的运行与恢复验证；每个阶段都应独立部署、验收，再进入下一阶段。

这里的“生产级”指逐步建立版本锁定、变更审核、权限隔离、监控告警、备份恢复和升级演练能力。本地 kind 节点共享一台宿主机及其 Docker 环境，不能据此证明跨主机高可用，也不能代替真实生产环境的容量和故障测试。

## 集群拓扑

默认规划为一个 kind 集群：

| 节点 | 数量 | 初始用途 |
| --- | --- | --- |
| control-plane | 1 | API Server、etcd、调度与控制器 |
| worker | 2 | 平台服务与示例业务工作负载 |

kind 节点是 Docker 容器，多 worker 用于验证调度、节点约束与工作负载分布。一个 control-plane 不具备控制平面高可用；同一宿主机上的多个 control-plane 也无法覆盖宿主机故障。[kind 配置文档](https://kind.sigs.k8s.io/docs/user/configuration/)

初期保留 kind 的基础网络，入口控制器、NetworkPolicy 实施和存储实现分别选型。后续启用网络策略前，必须验证选定 CNI 确实执行策略，不能仅以 YAML 已创建作为验收依据。

## 配置分层与所有权

首次 bootstrap 负责让 Argo CD 运行并能访问配置仓库；后续再由 Argo CD 管理平台与应用。bootstrap 与持续管理需要约定清晰的交接点，避免脚本和 Argo CD 同时修改同一对象。

| 层 | 职责 | 权限边界 |
| --- | --- | --- |
| `argocd-sys-settings` | 管理员 AppProject、平台 Application、Argo CD 系统配置和平台组件接入 | 由平台管理员维护；必要时可管理集群资源 |
| `argocd-app-settings` | 业务 AppProject、业务 Application 或 ApplicationSet、环境部署入口 | 限定 Git 来源与业务 namespace；不授予任意集群资源权限 |
| `platform/*` | 每个组件的清单、Helm values、环境差异和组件说明 | 按组件明确负责人及资源所有权 |

名称和目录本身不提供权限隔离。落地时必须配置 AppProject 的 `sourceRepos`、`destinations`、资源白名单和 Argo CD RBAC。能够向 Argo CD 自身 namespace 部署资源的 Project 实际具有管理员能力；能够修改 App-of-Apps 父应用来源的人也能影响子应用权限，因此系统层需要受控的合并权限。[Argo CD 集群引导文档](https://argo-cd.readthedocs.io/en/stable/operator-manual/cluster-bootstrapping/)

平台目录的计划职责如下；目录存在不表示对应组件已经实现：

| 目录 | 计划内容 |
| --- | --- |
| `platform/namespaces` | 平台 namespace、统一标签及后续资源配额 |
| `platform/storage` | StorageClass、PV/PVC 示例、数据保留策略与存储验收 |
| `platform/monitoring` | Prometheus、Alertmanager、Grafana、采集与告警规则 |
| `platform/logging` | ECK Operator、Elasticsearch、Kibana、Logstash 和日志采集配置 |
| `platform/gitlab` | GitLab、Runner 接入、镜像仓库和相关服务配置 |
| `platform/networking` | Gateway API、入口实现、域名解析和服务访问配置 |
| `platform/security` | RBAC、密钥管理、证书管理和网络策略 |
| `platform/backup` | 备份配置、恢复流程、恢复演练记录 |

组件版本需要显式锁定，并结合 Kubernetes、Helm chart 和 Operator 支持矩阵升级。资源清单、版本和非敏感环境配置可提交到 Git；私钥、访问令牌、kubeconfig、明文 Secret 和备份数据不得作为仓库内容提交。

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

## 本地资源预算

以下是针对宿主机总内存的初步规划，未经本仓库实测，不是组件官方最低要求。系统、桌面应用、Docker 虚拟机和镜像缓存都会占用资源；实际可分配给容器的内存小于宿主机总内存。CPU、磁盘性能、日志吞吐和保留时间也会改变结果。

| 宿主机内存 | 建议范围 | 取舍 |
| --- | --- | --- |
| 8 GB | 仓库与工具链验证；根据余量尝试缩小后的 kind 和 Argo CD | 不以完整平台常驻为目标；节点数和服务需按实际压力减少；监控、日志、GitLab 分开验证 |
| 16 GB | kind、Argo CD、基础设施与较轻的监控配置 | ELK 和 GitLab 按需分阶段启用；减少副本、采样量与保留时间，持续观察内存和磁盘 |
| 32 GB | 作为尝试整套本地实验平台的起点 | 仍采用实验规模和受控保留时间；不能保证 ELK、GitLab 与 CI 并发运行有足够余量 |

GitLab 官方对内存受限的单节点安装给出至少 8 GB 内存；该数字不等于 GitLab Helm chart 加上整个 Kubernetes 平台的总预算。部署 GitLab 前应单独核对所选版本、部署方式和各子组件的 requests/limits。[GitLab 安装要求](https://docs.gitlab.com/install/requirements/)

每个有状态组件上线前，需要记录 PVC 容量、数据增长预估、保留时间、备份目标和删除行为。先以小规模数据验证完整链路，再根据测量扩大资源，不通过无限降低 requests 来掩盖实际内存需求。

## 关键实现约束

### Docker 执行与网络

遵循仓库的 Docker 执行策略：宿主机仅进行源码检查和编辑，项目命令统一通过 `bash .agent/run.sh ...` 执行。Docker 不可用或 runner 失败时，记录问题并停止对应执行，不回退到宿主机直接运行。

后续若工具容器通过宿主 Docker socket 创建 kind 节点，需要处理以下问题：

- Docker daemon 解析 bind mount 的源路径。kind 的 `extraMounts.hostPath` 必须指向 daemon 可访问的宿主路径，不能直接使用仅存在于工具容器内的 `/workspace` 路径。
- 默认 kubeconfig 中的 `127.0.0.1` 是调用者所在网络的回环地址。工具容器访问集群时需要明确 kind 网络、API 地址和证书匹配方案，不能假定宿主 kubeconfig 在容器内原样可用。
- 挂载 Docker socket 会让工具容器获得管理该 daemon 的能力，因此只应由显式需要的命令使用，不能作为不相关检查的默认依赖。

macOS 上的宿主目录还要满足 Docker 文件共享要求。kind 的 extra mounts 与额外端口映射可作为后续实现依据，但本轮没有验证集群创建和访问链路。[kind 配置文档](https://kind.sigs.k8s.io/docs/user/configuration/)

### 存储与恢复

PV/PVC 是 Kubernetes 资源接口，不自动提供复制、备份或跨节点恢复。静态 local PV 需要节点亲和性；Pod 只能调度到能够访问数据的节点。动态卷的回收行为由 StorageClass/PV 配置决定，`Delete` 可能删除后端数据，`Retain` 则需要人工管理回收。[Kubernetes Persistent Volumes](https://kubernetes.io/docs/concepts/storage/persistent-volumes/)

本地需要分别验证 Pod 重建、节点重建和集群删除后的数据行为。数据如果仅位于 kind 节点容器内部，不应假定删除节点后仍存在；若挂载宿主目录，也必须记录路径与清理流程。关键 PVC/PV 的删除和 Argo CD pruning 应经过明确设计。生产环境应另行评估 CSI、快照、异地备份和恢复目标。

### 入口与 ELK 选型

新平台入口层优先规划 Gateway API，并在实施阶段选定受维护的控制器。社区 `ingress-nginx` 已公告于 2026 年 3 月退役，不能作为本项目的新增长期基础组件。[Kubernetes 官方公告](https://kubernetes.io/blog/2025/11/11/ingress-nginx-retirement/)

ELK 规划通过 ECK Operator 管理 Elasticsearch、Kibana 和 Logstash，日志采集链路在日志阶段选择并验证。需要核对各镜像的 CPU 架构支持，尤其是本地 Apple Silicon 环境；不能将某个组件支持 arm64 推断为整个 Helm chart 的所有子组件都支持。[ECK 安装说明](https://www.elastic.co/docs/deploy-manage/deploy/cloud-on-k8s/install)、[ECK Logstash 文档](https://www.elastic.co/docs/deploy-manage/deploy/cloud-on-k8s/logstash)

### GitLab 的引导依赖

Argo CD 首次引导需要一个已经可访问的配置源。若 GitLab 也部署在本集群中，就不能把尚未启动的 GitLab 作为恢复整套集群的唯一依赖。应先准备外部可访问的 Git 源或明确的离线引导材料；迁移到本地 GitLab 后，仍保留独立于该集群的配置副本与恢复所需凭据备份。

GitLab 的数据库、Redis、对象存储、镜像仓库和 Runner 都有独立的容量、版本与恢复要求。实验环境和生产环境的依赖布局需要分别定义，不能把 chart 能启动当作生产部署验收。[GitLab chart 前置条件](https://docs.gitlab.com/charts/installation/tools/)
