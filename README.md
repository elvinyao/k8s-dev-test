# Kubernetes 与独立服务管理平台

提供可以按环境配置的部署示例：Kubernetes nodes、Argo CD、Prometheus/Alertmanager/Grafana、
ELK、PV/PVC、GitLab/Runner/Registry，以及 GitOps、TLS、权限和备份恢复。

**当前交付是配置与运维文档，不是已经上线的生产集群。** Compose 配置解析与 Helm/Kustomize
渲染用于检查配置；生产使用前还需填入真实域名、秘密、存储及后端，并完成运行与恢复验收。

## 从哪里开始

| 目标 | 入口 |
| --- | --- |
| 构建工具箱与执行所有校验 | [容器工具链](docs/tooling.md) |
| 已完成的检查与待验收范围 | [配置验证记录](docs/verification.md) |
| 部署到已有多节点 Kubernetes | [生产部署手册](docs/production.md) |
| 本地 kind 的拓扑与执行限制 | [本地集群](docs/local-cluster.md) |
| 单机独立监控栈 | [Monitoring Compose](compose/monitoring/README.md) |
| 单机独立 ELK（TLS/认证） | [Logging Compose](compose/logging/README.md) |
| 单机 GitLab 与独立 Runner | [GitLab Compose](compose/gitlab/README.md) |
| GitLab 外部后端的 K8s Hybrid 部署 | [GitLab values](platform/gitlab/README.md) |
| 集群内 ELK 与容器日志采集 | [ECK 部署](platform/logging/README.md) |
| 变更、备份与恢复 | [运维手册](docs/operations.md) |

Compose 是独立单机服务方案，不提供跨主机 HA。Kubernetes 示例面向已有可靠节点、CSI、
负载均衡及网络策略能力的集群；kind 仅用于本地验证。Argo CD 依赖 Kubernetes API，
不使用一个伪装成独立 Argo CD 平台的 Compose 服务。

## 主要文件

```text
.agent/                     Docker runner 与固定版本工具箱
clusters/local/             kind：1 control-plane + 2 workers
clusters/production/        生产 namespace 基础层
bootstrap/argocd/           原始 manifest 自举参考（与 Helm 二选一）
platform/releases.yaml     Helm chart 版本与 values 索引
platform/argocd/            local / production Argo CD values
platform/monitoring/        Prometheus Stack values 与告警示例
platform/logging/           ECK Operator、ES/Kibana/Logstash/Filebeat
platform/gitlab/            Hybrid values 与可选 Registry overlay
platform/networking/       Envoy Gateway、共享 TLS 入口与路由
platform/security/         证书示例、业务配额和网络策略
platform/storage/          本地与生产 CSI 示例
argocd-sys-settings/        管理员平台入口
argocd-app-settings/        受限业务项目
compose/                    monitoring / logging / gitlab
scripts/                    渲染、校验、GitOps 生成与只读 preflight
```

所有执行通过 `bash .agent/run.sh ...`；宿主只检查与编辑源码，详见 [AGENTS.md](AGENTS.md)。
真实 `.env`、密码、kubeconfig、运行数据、缓存和渲染文件不进入 Git。
仓库当前在 `main`，尚未提交初始 commit 或配置 remote；GitOps 使用前须提交并推送。

组件不自动一次性启动，不默认启用自动同步/prune。先完成基础设施，再逐层接入服务；
源版本、凭据与恢复材料需要保留独立于集群内 GitLab 的副本。
